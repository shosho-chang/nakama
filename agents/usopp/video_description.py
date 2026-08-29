"""影片描述欄組裝（video-publishing-plan Q5、ADR-055 slice 2）。

描述欄四段結構（修修 2026-08-27 收旂）：

    ┌─ 變動（LLM 產、修修在審核頁改）  hook 1–2 個短段
    ├─ 變動（長片才有）              ⏱ 分章（從轉場卡自動生成）
    ├─ 變動（僅人類可讀的公開 source citations） 本集引用
    └─ 固定（templates/video_description_footer.md，精簡共用版）

設計要點：

- **分章零人工**：長片的章節 = broll.json 的 transition_title items
  （t0 + title）——視覺轉場卡與描述欄分章天生同源（同一份企劃檔），
  不可能漂移。00:00 固定為「開場」。
- **provenance 不對外公開**：`packages.json.citations` 可保留內部查證索引，
  但 SRT/VTT/JSON 路徑、時間區間與 vault 路徑只是 provenance，不得進入對外
  description。只有人類可讀的論文、書籍或公開 URL 會顯示。
- **固定段獨立成檔**：改一次 CTA 套用全部，不重生 40 支文案。
- hook 由 LLM（Claude session，吃 `data/brook/style-profiles-fable5/`
  voice profile）代筆——Stage 6 平台文案在 LLM 代筆邊界內
  （ADR-027 只管 Stage 4 原子文章正文；先例：FB/IG renderer）。

與 WP 那條線（publisher.py）平行、不共用零件（ADR-055 D2）。
Tests：tests/test_video_description.py。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

_TEMPLATES = Path(__file__).resolve().parent / "templates"
FOOTER_FILE = _TEMPLATES / "video_description_footer.md"
_DESCRIPTION_MODEL = "claude-sonnet-4-6"
_AI_SLOP_PATTERNS = (
    re.compile(r"不是[^。！？\n]{0,40}[，,、]?\s*而是"),
    re.compile(r"不只[^。！？\n]{0,40}[，,、]?\s*更(?:是|要|能)?"),
    re.compile(r"這一段會"),
    re.compile(r"帶你看"),
    re.compile(r"深入探討"),
)
_HOOK_MIN_CHARS = 180
_HOOK_MAX_CHARS = 320
_PUBLIC_URL_PATTERN = re.compile(r"^https?://(?!localhost(?:[:/]|$)|127\.)", re.I)
_INTERNAL_CITATION_PATTERNS = (
    re.compile(r"(?:^|[/\\])(?:highlights|attachments|kb|data|cache)(?:[/\\]|$)", re.I),
    re.compile(r"\.(?:srt|vtt|json|ya?ml|txt|pdf|docx?)(?:#|$)", re.I),
    re.compile(r"(?:^|\s)transcript@", re.I),
    re.compile(r"#[0-9]{2}:[0-9]{2}(?::[0-9]{2})?(?:[.,][0-9]{3})?"),
    re.compile(r"^[a-z]:[/\\]", re.I),
)


def fmt_ts(sec: float) -> str:
    """YT 分章時間戳：MM:SS（>1h 才 H:MM:SS——長片 8–12min 用不到但防呆）。"""
    s = int(sec)
    h, rem = divmod(s, 3600)
    m, ss = divmod(rem, 60)
    return f"{h}:{m:02d}:{ss:02d}" if h else f"{m:02d}:{ss:02d}"


def chapters_from_broll(broll_items: list[dict]) -> list[tuple[float, str]]:
    """長片分章 = 滿版轉場卡的 (t0, title)，前加 00:00 開場。

    YT 分章規則：首章必須 00:00、至少 3 章、遞增——轉場卡 <2 個時回空
    （寧可不分章也不出殘缺章節表）。"""
    marks = [
        (float(it["t0"]), str(it["vars"]["title"]))
        for it in broll_items
        if it.get("comp") == "transition_title"
    ]
    marks.sort()
    if len(marks) < 2:
        return []
    return [(0.0, "開場")] + marks


def resolve_chapters(episode_dir: Path, cut_id: str) -> list[tuple[float, str]]:
    """分章來源：有 Release 對應表就以 Release 為準，否則才回退舊的 broll 檔。

    一旦該集建了 publish-timelines 對應表，Release 就是唯一權威——它說沒有分章
    就是沒有分章，不可以回頭撿 broll，那份是 ADR-065 製作線的舊時間軸
    （見 agents/usopp/publish_timeline.release_chapters 的實測）。
    """
    from agents.usopp.publish_timeline import load_timeline_map, release_chapters

    episode_dir = Path(episode_dir)
    if load_timeline_map(episode_dir) is not None:
        return release_chapters(episode_dir, cut_id)
    broll_path = episode_dir / "highlights" / "tighten" / f"{cut_id}_broll.json"
    if not broll_path.exists():
        return []
    items = json.loads(broll_path.read_text(encoding="utf-8"))["items"]
    return chapters_from_broll(items)


def public_citations(citations: list[object]) -> list[str]:
    """Keep human-readable public sources; leave internal evidence as provenance.

    Packaging historically used one string list for both concepts.  Filtering here is
    deliberately conservative: an uncertain path is omitted from public copy, while
    the original package record remains untouched for internal review.
    """
    public: list[str] = []
    for value in citations:
        citation = str(value).strip()
        if not citation:
            continue
        if _PUBLIC_URL_PATTERN.match(citation):
            if citation not in public:
                public.append(citation)
            continue
        if any(pattern.search(citation) for pattern in _INTERNAL_CITATION_PATTERNS):
            continue
        if citation not in public:
            public.append(citation)
    return public


def load_citations(packages: dict, cut_id: str) -> list[str]:
    """Return only public citations from the packaging handoff."""
    cut = next((c for c in packages.get("cuts", []) if c.get("cut_id") == cut_id), None)
    if cut is None:
        raise ValueError(f"{cut_id} 不在 packages.json——packaging 段還沒跑這支")
    return public_citations(list(cut.get("citations") or []))


def chosen_package(packages: dict, approval: dict, cut_id: str) -> dict:
    """approval.primary_package 指向的那組（title + 縮圖）——「已決定」而非候選。"""
    ap = next((a for a in approval.get("approvals", []) if a.get("cut_id") == cut_id), None)
    if ap is None or not ap.get("approved"):
        raise ValueError(f"{cut_id} 未在 approval.json 核准——先過 packaging gate")
    rank = int(ap["primary_package"])
    cut = next(c for c in packages["cuts"] if c["cut_id"] == cut_id)
    title = next((t for t in cut["titles"] if t.get("rank") == rank), None)
    pkg = next((p for p in cut.get("packages", []) if p.get("title_rank") == rank), None)
    if title is None:
        raise ValueError(f"{cut_id} primary_package={rank} 對不到 titles")
    return {
        "title": title["text"],
        "thumbnail": (pkg or {}).get("thumbnail_png"),
        "title_rank": rank,
    }


def find_packaging_dir(vault: Path, episode: str) -> Path:
    """episode 資料夾名 → packaging 目錄（slug 不可推導，scan packages.json 的
    episode 欄位機器對應——「20260723 謝伯讓」↔「20260723-xieboran」）。"""
    root = vault / "Attachments" / "packaging"
    for d in sorted(root.iterdir()) if root.exists() else []:
        pj = d / "packages.json"
        if pj.exists():
            try:
                if json.loads(pj.read_text(encoding="utf-8")).get("episode") == episode:
                    return d
            except (json.JSONDecodeError, OSError):
                continue
    raise ValueError(f"vault 找不到 episode「{episode}」的 packaging 交接檔（{root}）")


def build_description(
    hook: str,
    chapters: list[tuple[float, str]],
    citations: list[str],
    footer: str,
) -> str:
    """四段組裝。空段整段省略（短片無分章；沒引用就沒有「本集引用」）。"""
    blocks = [hook.strip()]
    if chapters:
        blocks.append("\n".join(f"⏱ {fmt_ts(t)} {title}" for t, title in chapters))
    visible_citations = public_citations(list(citations))
    if visible_citations:
        blocks.append("本集引用：\n" + "\n".join(f"・{c}" for c in visible_citations))
    if footer.strip():
        blocks.append(footer.strip())
    return "\n\n".join(b for b in blocks if b)


def validate_description_hook(hook: str) -> str:
    """Enforce the compact 1–2 paragraph public-copy contract."""
    cleaned = hook.strip()
    if not cleaned:
        raise ValueError("description hook 不可為空")
    matches = [pattern.pattern for pattern in _AI_SLOP_PATTERNS if pattern.search(cleaned)]
    if matches:
        raise ValueError(f"description hook 命中 AI slop：{', '.join(matches)}")
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", cleaned) if part.strip()]
    if not 1 <= len(paragraphs) <= 2:
        raise ValueError("description hook 必須是 1–2 個短段落")
    char_count = len(re.sub(r"\s+", "", cleaned))
    if not _HOOK_MIN_CHARS <= char_count <= _HOOK_MAX_CHARS:
        raise ValueError(
            f"description hook 需約 200–300 字（目前 {char_count} 字；"
            f"允許 {_HOOK_MIN_CHARS}–{_HOOK_MAX_CHARS}）"
        )
    return cleaned


def build_description_prompt(
    episode_dir: Path,
    *,
    cut_id: str,
    title: str,
    citations: list[str],
    chapters: list[tuple[float, str]],
) -> str:
    """Build the bounded, evidence-fed request used by the subscription LLM seam."""
    from agents.usopp.publish_timeline import release_subtitle

    # 逐字稿必須是**成品那一份**。tight SRT 是 ADR-065 製作線的殘留，punch-L04 的
    # 只有 260 秒舊剪輯而成品是 492 秒——照它寫等於替一支不存在的影片寫文案。
    source = release_subtitle(episode_dir, cut_id)
    if source is None:
        srt_dir = episode_dir / "highlights" / "srt"
        srt_files = sorted(srt_dir.glob(f"{cut_id}_tight_r*.srt")) if srt_dir.exists() else []
        if not srt_files:
            raise FileNotFoundError(f"找不到 {cut_id} 的字幕；不可只看標題腦補 description")
        source = srt_files[-1]
    transcript = source.read_text(encoding="utf-8")[:12000]
    chapter_text = "、".join(title for _, title in chapters) or "（無章節）"
    citation_text = "；".join(citations) or "（無引用）"
    return f"""請替 YouTube 長 highlight 寫 description 最前面的 1–2 個短段落。

規則：
- 用繁體中文、第一人稱、口語但精確；直接說這支影片談了什麼，以及觀眾為什麼值得看。
- hook 總長 200–300 個繁體中文字（不含空白），最多兩段；每段只推進一件事。
- 不要重複標題，不要虛構逐字稿沒有的內容，不要下醫療承諾。
- 禁用「不是 X，而是 Y」「不只 X，更是 Y」「這一段會」「帶你看」「深入探討」。
- 只輸出 hook 本文，不要標題、條列、Markdown 或 CTA。固定 CTA 由程式另外接上。

集數：{episode_dir.name}
cut：{cut_id}
核准標題：{title}
章節：{chapter_text}
引用：{citation_text}

本支 tight 字幕（唯一內容依據）：
{transcript}
"""


def subscription_hook_generator(prompt: str) -> str:
    """Generate through the Claude subscription path; never fall back to API billing."""
    from shared.anthropic_client import ask_claude

    return ask_claude(
        prompt,
        model=_DESCRIPTION_MODEL,
        max_tokens=500,
        auth_policy="subscription_required",
    )


def generate_description_draft(
    episode_dir: Path,
    packages: dict,
    approval: dict,
    cut_id: str,
    *,
    hook_generator: Callable[[str], str] | None = None,
) -> tuple[dict, str]:
    """Generate and assemble one editable description draft from approved evidence."""
    package = chosen_package(packages, approval, cut_id)
    citations = load_citations(packages, cut_id)
    chapters = resolve_chapters(episode_dir, cut_id)
    prompt = build_description_prompt(
        episode_dir,
        cut_id=cut_id,
        title=package["title"],
        citations=citations,
        chapters=chapters,
    )
    hook = validate_description_hook((hook_generator or subscription_hook_generator)(prompt))
    return package, build_description(hook, chapters, citations, load_footer())


def load_footer() -> str:
    """固定段模板。剝掉 <!-- --> 註解——YT 描述不解析 HTML，註解會原樣顯示。"""
    if not FOOTER_FILE.exists():
        raise FileNotFoundError(f"固定段模板不存在: {FOOTER_FILE}")
    text = FOOTER_FILE.read_text(encoding="utf-8")
    import re

    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL).strip()
