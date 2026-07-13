"""Render the human-readable Literature Note from the V3 annotation store.

Centaur Zettelkasten — Literature Note 統一規格 v0.1 (§4 frontmatter, §5 body)
+ 規格 v0.2 §9 (idempotent re-render). N521.

雙檔制 (D1)：``KB/Annotations/{slug}.md`` 是機器資料源 (Reader 擁有，**不動**)；
本 module 從那份 V3 set render 出人讀的 ``KB/Literature/{slug}.md`` 快照。

三路 body 版型 (D3)：
- 書 (``source_kind: book``)：按章分組 (CFI spine) + ``^cfi-...`` 錨 + 章末心得。
- 文章 / 論文 (``article`` / ``paper``)：平鋪 + ``^p-N`` 段落錨 (D6，render-time
  deterministic numbering：N = 該 item 在 article items 中的 1-based 序位)。
- 影片 (``video``)：按時間軸 + 講者 + ``▶ [跳到此刻]`` 的 ``t=`` seek 連結。

**idempotent re-render (v0.2 §9 / D-16)**：render 區與記帳區用 HTML comment marker
分隔。re-render 時：
- frontmatter 的 ``status`` / ``mined_concepts`` 從舊檔讀回保留 (AI 記帳區，render
  不得倒退)；
- 記帳區 (``<!-- LITERATURE:LEDGER ... -->``) 逐字保留 (含「✓ 已開卡」標記)；
- **只重畫 render 區** (劃線內容 + 🔗 KB 相關)。
連續兩次 render 相同輸入 → byte-identical (測試斷言)。

邊界 (task prompt §6)：``KB/Annotations/`` 機器檔零改動；``🔗 KB 相關`` pilot 先純
FTS5 (D-17，無 LLM-judge)；👍/👎 不實作 (D-21)。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from shared.annotation_store import get_annotation_store
from shared.config import get_vault_path
from shared.schemas.annotations import (
    AnnotationSetV3,
    AnnotationV3,
    HighlightV3,
    ReflectionV3,
)
from shared.utils import extract_frontmatter
from shared.vault_rules import assert_reader_can_write

# ---------------------------------------------------------------------------
# Markers (idempotency boundary — v0.2 §9)
# ---------------------------------------------------------------------------

#: render 區起訖。本區每次 render 全量重畫，不保留任何手改。
RENDER_BEGIN = "<!-- LITERATURE:RENDER:BEGIN -->"
RENDER_END = "<!-- LITERATURE:RENDER:END -->"

#: 記帳區起訖。本區逐字保留 (含「✓ 已開卡」標記與 Phase 5 善後寫入)。
LEDGER_BEGIN = "<!-- LITERATURE:LEDGER:BEGIN -->"
LEDGER_END = "<!-- LITERATURE:LEDGER:END -->"

#: 記帳區的預設骨架 (首次 render 時寫入，之後不再覆蓋)。
_LEDGER_DEFAULT = (
    f"{LEDGER_BEGIN}\n"
    "## 記帳（AI 善後 / 人標記，re-render 不覆蓋）\n\n"
    "<!-- 已開卡標記、Phase 5 鏡像連結等寫在這裡；render 區重畫時保留此段 -->\n"
    f"{LEDGER_END}"
)

#: 三路 anchor_type 對映 (規格 §7)。
_ANCHOR_TYPE = {
    "book": "cfi",
    "article": "excerpt",
    "paper": "excerpt",
    "video": "timestamp",
}

_T_LOCATOR_RE = re.compile(r"t=([0-9]+(?:\.[0-9]+)?)(?:-([0-9]+(?:\.[0-9]+)?))?")


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


@dataclass
class LiteratureReport:
    """``write_literature_note`` 一次 render 的摘要。"""

    slug: str
    source_kind: str
    items_rendered: dict = field(default_factory=lambda: {"h": 0, "a": 0, "r": 0})
    status: str = "digested"
    rendered: bool = False
    errors: list[str] = field(default_factory=list)


def _now_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Anchor helpers
# ---------------------------------------------------------------------------


def _cfi_anchor(cfi: str | None) -> str:
    """``epubcfi(/6/14!/4[A-6]/2/116,...)`` → ``^cfi-6-14-116`` (規格 §5.1)。

    取 spine path 的前兩組數字 (``/6/14`` → ``6-14``) + 最末的 leaf offset
    (``116``) 組成穩定且具辨識度的 anchor。少於兩組數字時退而取全部。
    None / 無法解析 → ``^cfi-unknown``。
    """
    if not cfi:
        return "^cfi-unknown"
    nums = re.findall(r"\d+", cfi)
    if not nums:
        return "^cfi-unknown"
    if len(nums) <= 3:
        return "^cfi-" + "-".join(nums)
    return "^cfi-" + "-".join(nums[:2] + [nums[-1]])


def _chapter_of(cfi: str | None) -> str:
    """從 EPUB CFI 推章節分組鍵。沿用 book_digest_writer 的慣例。"""
    if not cfi:
        return "unknown"
    m = re.search(r"/6/\d+\[([^\]]+)\]!", cfi)
    if m:
        return m.group(1)
    m = re.search(r"/6/(\d+)!", cfi)
    if m:
        return f"spine-{m.group(1)}"
    return "unknown"


def _book_chapter_titles(book_id: str) -> dict[str, str]:
    """``{spine-N: 真章節標題}``（key 與 :func:`_chapter_of` 輸出一致），由 EPUB TOC 解析。

    CFI ``/6/N`` 對到第 ``N//2`` 個 spine item（1-based）；該 item 的 href stem 去
    TOC 查標題（reuse #833 的 helper）。任何失敗（查無此書 / EPUB 壞 / 無 TOC）→ 回
    ``{}``，呼叫端 fallback 回 spine-N，render 永不中斷。
    """
    if not book_id:
        return {}
    try:
        from pathlib import PurePosixPath  # noqa: PLC0415

        from shared import book_storage  # noqa: PLC0415
        from shared.epub_metadata import (  # noqa: PLC0415
            build_toc_title_map,
            extract_metadata,
            extract_spine_items,
        )

        blob = book_storage.read_book_blob(book_id, lang="bilingual")
        toc = build_toc_title_map(extract_metadata(blob).toc)
        spine = extract_spine_items(blob)
        return {
            f"spine-{i * 2}": toc[PurePosixPath(href).name]
            for i, (href, _xhtml) in enumerate(spine, start=1)
            if PurePosixPath(href).name in toc
        }
    except Exception:  # noqa: BLE001 — EPUB 問題不該中斷 render；fallback spine-N
        return {}


def _video_display_title(slug: str, vault_path: Path) -> str:
    """影片文獻筆記的人讀標題：``頻道｜影片標題｜cast``，由 watchlist manifest 組。

    cast 是扁平名單（慣例首位為主持，其餘來賓）。manifest 缺失 / 壞檔 → fallback
    回 ``slug``，render 永不中斷（比照 :func:`_book_chapter_titles`）。
    """
    if not slug.startswith("youtube_"):
        return slug
    video_id = slug[len("youtube_") :]
    manifest = vault_path / "Watchlist" / "youtube" / video_id / "manifest.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        channel = str(data.get("channel") or "").strip()
        title = str(data.get("title") or "").strip()
        cast = [str(c).strip() for c in (data.get("cast") or []) if str(c).strip()]
    except Exception:  # noqa: BLE001 — manifest 問題不該中斷 render；fallback slug
        return slug
    parts: list[str] = []
    if channel:
        parts.append(channel)
    parts.append(title or slug)
    if cast:
        parts.append("、".join(cast))
    return "｜".join(parts)


def _seek_seconds(cfi: str | None) -> float | None:
    """從 ``t=<起>-<迄>`` locator 取起始秒 (影片 seek link)。"""
    if not cfi:
        return None
    m = _T_LOCATOR_RE.search(cfi)
    if not m:
        return None
    try:
        return float(m.group(1))
    except (TypeError, ValueError):
        return None


def _fmt_seconds(total: float) -> str:
    """``750.0`` → ``12:30`` (時間軸標籤)。"""
    secs = int(total)
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


# ---------------------------------------------------------------------------
# Frontmatter
# ---------------------------------------------------------------------------


def _quote(value: str) -> str:
    """最小 YAML 引號 (wikilink / 含特殊字元值)。"""
    escaped = value.replace('"', '\\"')
    return f'"{escaped}"'


def _render_frontmatter(
    ann_set: AnnotationSetV3,
    source_kind: str,
    *,
    status: str,
    mined_concepts: list[str],
    captured: str,
    ingested: str,
    display_title: str | None = None,
) -> str:
    """組 ``type: literature`` frontmatter (規格 §4)。

    ``status`` / ``mined_concepts`` 由 caller 傳入 (re-render 時從舊檔讀回保留)。
    ``display_title`` 是人讀標題 (影片＝``頻道｜標題｜cast``)；``slug`` /
    ``annotations`` 永遠維持 ``ann_set.slug`` (識別碼 + 註記連結不可變)。
    """
    slug = ann_set.slug
    title = display_title or slug
    anchor_type = _ANCHOR_TYPE.get(source_kind, "excerpt")

    lines = ["---", "type: literature", f"source_kind: {source_kind}"]
    lines.append(f"slug: {slug}")
    lines.append(f"title: {_quote(title)}")
    lines.append(f"annotations: {_quote(f'[[Annotations/{slug}]]')}")
    if mined_concepts:
        lines.append("mined_concepts:")
        for c in mined_concepts:
            lines.append(f"  - {_quote(c)}")
    else:
        lines.append("mined_concepts: []")
    lines.append(f"status: {status}")
    lines.append(f"anchor_type: {anchor_type}")
    if ann_set.book_version_hash:
        lines.append(f"book_version_hash: {ann_set.book_version_hash}")
    lines.append(f"captured: {captured}")
    lines.append(f"ingested: {ingested}")
    lines.append("schema_version: 3")
    lines.append("---")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Body — per-route render zones
# ---------------------------------------------------------------------------


def _render_highlight_body(item: HighlightV3 | AnnotationV3) -> tuple[str, str]:
    """回傳 (引文文字, note 文字 or '')。"""
    if item.type == "annotation":
        return item.text_excerpt, item.note
    return item.text, ""


def _bq(text: str) -> str:
    """多段文字 → 單一 blockquote（每行 ``> `` 前綴、空行 ``>``）。

    讓有換行的引文整段落在同一個 blockquote（一個灰框 + 一條橘線），而不是只有
    第一段進框、其餘段落跑成框外的普通段落。
    """
    return "\n".join(f"> {ln}" if ln.strip() else ">" for ln in text.strip().split("\n"))


def _note_block(note: str) -> str:
    """我的筆記 → 同一區塊（plain，非框，與原文區隔）。

    多段 note 用 ``<br>`` 接成單一段落，避免第二段跑成獨立段落、跟原文/下一條混淆。
    保留 ``note::`` 供 Obsidian/Dataview；viewer 顯示時換成「💭 我的筆記」。
    """
    joined = note.strip().replace("\n", "<br>")
    return f"**note::** {joined}"


def _render_book_zone(ann_set: AnnotationSetV3, slug: str) -> str:
    """書：按章分組 + CFI 錨 + 章末心得 (規格 §5.1)。"""
    # Group highlights / annotations by chapter (preserve first-occurrence order).
    chapters: dict[str, list] = {}
    reflections_by_chapter: dict[str, list[ReflectionV3]] = {}
    for item in ann_set.items:
        if item.type == "reflection":
            ch = item.chapter_ref or _chapter_of(item.cfi_anchor)
            reflections_by_chapter.setdefault(ch, []).append(item)
        else:
            ch = _chapter_of(item.cfi)
            chapters.setdefault(ch, []).append(item)

    # Ensure chapters that only have reflections still get a section.
    for ch in reflections_by_chapter:
        chapters.setdefault(ch, [])

    # 把 spine-N 分組鍵換成真章節標題（從 EPUB TOC）；查無 → 維持 spine-N。
    # book_id 優先（精確的 EPUB 落點鍵）；為 None 時退回 slug（書的 slug == book_id）。
    chapter_titles = _book_chapter_titles(getattr(ann_set, "book_id", None) or slug)

    sections: list[str] = []
    for ch_idx, ch in enumerate(chapters, start=1):
        blocks: list[str] = []
        for item in chapters[ch]:
            quote, note = _render_highlight_body(item)
            anchor = _cfi_anchor(item.cfi)
            block = f"{_bq(quote)} {anchor}"  # 多段引文整段進同一 blockquote
            if note:
                block += f"\n\n{_note_block(note)}"  # 空行分隔 → 筆記不被摺進引文框
            blocks.append(block)

        reflections = reflections_by_chapter.get(ch, [])
        if reflections:
            refl_text = "\n\n".join(r.body for r in reflections)
            blocks.append(f"**章末心得**：{refl_text}")

        # Deep link back to Reader at the first cfi in the chapter.
        first_cfi = next(
            (i.cfi for i in chapters[ch] if getattr(i, "cfi", None)),
            None,
        )
        deep = f"/robin/books/{slug}#cfi={first_cfi}" if first_cfi else f"/robin/books/{slug}"
        blocks.append(f"📖 [開回 Reader]({deep})")

        body = "\n\n".join(blocks) if blocks else "_（本章無劃線）_"
        # 真章節名（EPUB TOC 解得出）優先；否則用乾淨的「章節 N」，不外露 spine-N / ch 鍵
        heading = chapter_titles.get(ch) or f"章節 {ch_idx}"
        sections.append(f"### {heading}\n\n{body}")

    return "\n\n".join(sections) if sections else "_（尚無劃線）_"


def _render_article_zone(ann_set: AnnotationSetV3, slug: str) -> str:
    """文章 / 論文：平鋪 + ``^p-N`` 段落錨 (規格 §5.2 / D6)。

    ``^p-N`` 在 render 時依 article item 序位 deterministic 編號 (N = 1-based)，
    re-render 相同 set → 相同 anchor。reflection 放最後當「整體心得」。
    """
    blocks: list[str] = []
    counter = 0
    for item in ann_set.items:
        if item.type == "reflection":
            continue
        counter += 1
        quote, note = _render_highlight_body(item)
        block = f"{_bq(quote)} ^p-{counter}"  # 多段引文整段進同一 blockquote
        if note:
            block += f"\n\n{_note_block(note)}"  # 空行分隔 → 筆記不被摺進引文框
        blocks.append(block)

    reflections = [i for i in ann_set.items if i.type == "reflection"]
    if reflections:
        refl_text = "\n\n".join(r.body for r in reflections)
        blocks.append(f"**整體心得**：{refl_text}")

    return "\n\n".join(blocks) if blocks else "_（尚無劃線）_"


def _render_video_zone(ann_set: AnnotationSetV3, slug: str) -> str:
    """影片：按時間軸 + 講者 + ``t=`` seek 連結 (規格 §5.3)。

    ``slug`` 形如 ``youtube_{video_id}``；seek link 用 ``youtu.be/{id}?t=<秒>``。
    """
    video_id = slug[len("youtube_") :] if slug.startswith("youtube_") else slug

    # Sort by seek seconds so the timeline is monotonic; items without a
    # parseable t= locator keep insertion order at the end.
    indexed = list(enumerate(ann_set.items))
    indexed.sort(key=lambda pair: (_seek_seconds(getattr(pair[1], "cfi", None)) or 1e18, pair[0]))

    blocks: list[str] = []
    for _orig_idx, item in indexed:
        if item.type == "reflection":
            quote, note = item.body, ""
            speaker = ""
            secs = _seek_seconds(item.cfi_anchor)
        else:
            quote, note = _render_highlight_body(item)
            speaker = getattr(item, "speaker", "") or ""
            secs = _seek_seconds(item.cfi)

        ts_label = _fmt_seconds(secs) if secs is not None else "--:--"
        header = f"**[{ts_label}]"
        header += f" {speaker}**" if speaker else "**"

        block = f"{header}\n> {quote}"
        if note:
            block += f"\n**note::** {note}"
        if secs is not None:
            block += f"\n▶ [跳到此刻](https://youtu.be/{video_id}?t={int(secs)})"
        blocks.append(block)

    return "\n\n".join(blocks) if blocks else "_（尚無劃線）_"


_ROUTE_RENDERERS = {
    "book": _render_book_zone,
    "article": _render_article_zone,
    "paper": _render_article_zone,
    "video": _render_video_zone,
}


def _is_own_source_path(path: str, slug: str) -> bool:
    """True 若 KB 命中是來源自身的頁面（自己的 Annotations / Wiki Source 子頁）。

    自我命中（例：``KB/Annotations/{slug}``、``KB/Wiki/Sources/Books/{slug}/digest``）
    對「🔗 KB 相關」毫無跨來源價值、反成噪音，故 render 時濾掉。判準：slug 作為
    完整路徑段出現即視為來源自身頁面（別的頁面不會剛好以整段書名 slug 命名）。
    """
    return slug in path.split("/")


def _render_kb_related_zone(ann_set: AnnotationSetV3, slug: str, vault_path: Path) -> str:
    """``## 🔗 KB 相關`` — pilot 純 FTS5 (D-17，無 LLM-judge / 無 👍👎 D-21)。

    每條有內容的 item 用 ``kb_search`` (hybrid / FTS5) 撈相關卡，列 wikilink。
    來源自身的頁面（自己的 Annotations / digest）會被濾掉，只留跨來源關聯。
    search 失敗或無命中 → 該條標 ``_（無 KB 命中）_``，不中斷 render。
    """
    from agents.robin.kb_search import search_kb

    lines: list[str] = []
    for item in ann_set.items:
        if item.type == "highlight":
            query = item.text
        elif item.type == "annotation":
            query = f"{item.text_excerpt}\n{item.note}"
        else:
            query = item.body
        query = (query or "").strip()
        if not query:
            continue

        try:
            # 多撈幾筆，濾掉來源自身頁面後再取 top 3（自我命中無跨來源價值）。
            raw_hits = search_kb(query[:500], vault_path, top_k=6, engine="hybrid")
        except Exception as exc:  # noqa: BLE001 — bridge must render even if KB down
            lines.append(f"- {query[:40]}… — _（KB 檢索失敗：{exc}）_")
            continue

        hits = [h for h in raw_hits if not _is_own_source_path(h["path"], slug)][:3]
        label = query[:40].replace("\n", " ")
        if hits:
            hit_links = ", ".join(f"[[{h['path']}]]" for h in hits)
            lines.append(f"- {label}… → {hit_links}")
        else:
            lines.append(f"- {label}… — _（無 KB 命中）_")

    return "\n".join(lines) if lines else "_（尚無可撈條目）_"


# ---------------------------------------------------------------------------
# Idempotent merge (v0.2 §9)
# ---------------------------------------------------------------------------


def _extract_ledger(content: str) -> str | None:
    """從舊檔取記帳區 (含 marker)，逐字保留。無則 None。"""
    start = content.find(LEDGER_BEGIN)
    end = content.find(LEDGER_END)
    if start == -1 or end == -1 or end < start:
        return None
    return content[start : end + len(LEDGER_END)]


def _read_existing_bookkeeping(path: Path) -> tuple[str, list[str]]:
    """從舊 Literature 檔讀回 (status, mined_concepts) — render 不得倒退記帳。

    無舊檔 / 無欄位 → ("digested", [])。
    """
    if not path.exists():
        return "digested", []
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return "digested", []
    fm, _ = extract_frontmatter(content)
    status = str(fm.get("status") or "digested")
    mined = fm.get("mined_concepts") or []
    if not isinstance(mined, list):
        mined = []
    mined = [str(m) for m in mined]
    return status, mined


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_literature_markdown(
    ann_set: AnnotationSetV3,
    source_kind: str,
    vault_path: Path,
    *,
    status: str = "digested",
    mined_concepts: list[str] | None = None,
    ledger: str | None = None,
    captured: str | None = None,
    ingested: str | None = None,
) -> str:
    """純函式：V3 set → Literature Note markdown 全文字串 (無 I/O 寫檔)。

    給測試與 ``write_literature_note`` 共用。``ledger`` 為 None 時用預設骨架。
    """
    slug = ann_set.slug
    mined_concepts = mined_concepts or []
    captured = captured or _now_date()
    ingested = ingested or _now_date()
    # Human-readable title: video → 頻道｜標題｜cast (from watchlist manifest);
    # other sources keep the slug. slug / annotation-link stay on ann_set.slug.
    display_title = _video_display_title(slug, vault_path) if source_kind == "video" else slug

    fm = _render_frontmatter(
        ann_set,
        source_kind,
        status=status,
        mined_concepts=mined_concepts,
        captured=captured,
        ingested=ingested,
        display_title=display_title,
    )

    renderer = _ROUTE_RENDERERS.get(source_kind, _render_article_zone)
    highlights_zone = renderer(ann_set, slug)
    kb_zone = _render_kb_related_zone(ann_set, slug, vault_path)

    render_block = (
        f"{RENDER_BEGIN}\n"
        f"## 劃線與心得\n\n"
        f"{highlights_zone}\n\n"
        f"## 🔗 KB 相關（AI 撈，FTS5）\n\n"
        f"{kb_zone}\n"
        f"{RENDER_END}"
    )

    ledger_block = ledger if ledger is not None else _LEDGER_DEFAULT

    heading = f"# 文獻筆記：{display_title}"
    lede = f"> 來源：[[Annotations/{slug}]] · {source_kind}"

    return f"{fm}\n\n{heading}\n\n{lede}\n\n{render_block}\n\n{ledger_block}\n"


def write_literature_note(
    slug: str,
    *,
    source_kind: str | None = None,
    ingested: str | None = None,
) -> LiteratureReport:
    """從 ``KB/Annotations/{slug}.md`` (V3) render ``KB/Literature/{slug}.md``。

    idempotent (v0.2 §9)：re-render 保留 frontmatter ``status`` / ``mined_concepts``
    與記帳區，只重畫 render 區。連續兩次相同輸入 → byte-identical。

    Args:
        slug: annotation set slug (= literature 檔名 stem)。
        source_kind: ``book`` / ``article`` / ``paper`` / ``video``；None 時自動推斷
            (book set → book；youtube_ 前綴 → video；其餘 → article)。
        ingested: ingest 日 (YYYY-MM-DD)；None → 今天。

    Returns:
        ``LiteratureReport``。找不到 annotation / 非 V3 → ``rendered=False`` + error。
    """
    vault_path = get_vault_path()
    ann_set = get_annotation_store().load(slug)
    if ann_set is None:
        return LiteratureReport(
            slug=slug,
            source_kind=source_kind or "unknown",
            rendered=False,
            errors=[f"no annotations found for slug={slug!r}"],
        )
    if not isinstance(ann_set, AnnotationSetV3):
        # Legacy v1/v2 stores upgrade in-memory (idempotent for already-v3);
        # the on-disk machine file is NOT mutated (邊界: KB/Annotations 零改動).
        from shared.annotation_store import upgrade_to_v3

        ann_set = upgrade_to_v3(ann_set)

    if source_kind is None:
        source_kind = _infer_source_kind(ann_set, slug)

    relative = f"KB/Literature/{slug}.md"
    assert_reader_can_write(relative)
    dest = vault_path / relative

    # Idempotency: read back bookkeeping (status / mined_concepts) + ledger zone.
    status, mined_concepts = _read_existing_bookkeeping(dest)
    existing_ledger: str | None = None
    captured: str | None = None
    if dest.exists():
        existing = dest.read_text(encoding="utf-8")
        existing_ledger = _extract_ledger(existing)
        fm, _ = extract_frontmatter(existing)
        captured = str(fm["captured"]) if fm.get("captured") else None
        # Preserve the original ingested date on re-render (re-ingest keeps the
        # first ingest date unless the caller explicitly overrides it).
        if ingested is None and fm.get("ingested"):
            ingested = str(fm["ingested"])

    markdown = render_literature_markdown(
        ann_set,
        source_kind,
        vault_path,
        status=status,
        mined_concepts=mined_concepts,
        ledger=existing_ledger,
        captured=captured,
        ingested=ingested,
    )

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(markdown, encoding="utf-8")

    h = sum(1 for i in ann_set.items if i.type == "highlight")
    a = sum(1 for i in ann_set.items if i.type == "annotation")
    r = sum(1 for i in ann_set.items if i.type == "reflection")
    return LiteratureReport(
        slug=slug,
        source_kind=source_kind,
        items_rendered={"h": h, "a": a, "r": r},
        status=status,
        rendered=True,
    )


def _infer_source_kind(ann_set: AnnotationSetV3, slug: str) -> str:
    """無顯式 source_kind 時的推斷 (規格 §7 對照)。"""
    if ann_set.base == "books" or ann_set.book_id is not None:
        return "book"
    if slug.startswith("youtube_"):
        return "video"
    return "article"
