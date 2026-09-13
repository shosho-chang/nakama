"""影片描述欄組裝（video-publishing-plan Q5、ADR-055 slice 2）。

描述欄四段結構（修修 2026-08-27 收旂）：

    ┌─ 變動（LLM 產、修修在審核頁改）  hook 1–4 個短段
    ├─ 變動（長片才有）              分章時間戳（行首，YouTube 才認得）
    ├─ 變動（僅人類可讀的公開 source citations） 本集引用
    └─ 固定（templates/video_description_footer.md，精簡共用版）

設計要點：

- **分章零人工**：長片的章節 = 核准剪輯登錄檔 `sections` 裡標了轉場卡的節
  （t0 + transition_title）——視覺轉場卡與描述欄分章天生同源（同一份
  企劃檔），不可能漂移。00:00 固定為「開場」。ADR-065 的 broll.json
  只留給還沒走 ADR-066 的舊集數。
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
import os
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
# 段落上限：修修實際上架的描述是 3–4 段，原本寫死 2 段會把合格稿退掉。
_HOOK_MAX_PARAGRAPHS = 4
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


def _registration_paths(episode_id: str, cut_id: str) -> tuple[Path, ...]:
    """核准剪輯登錄檔的查找順序——ADR-066 的 runtime store，不是 episode 目錄。

    `cut_id` 是 miner **每集各自**產生的（`punch-L03` 這一集有，20260901 蘇予昕
    那集也有），所以扁平的 `registrations/<cut_id>.json` 跨集必然撞名——而且不會
    報錯，因為檔案存在、schema 也對，章節表就這樣安靜地接到別集去。2026-09-12
    補上 episode 這一層；舊的扁平檔仍然讀得到，但下面會核對 payload 的
    `episode_id`，不是自己那一集就不採。
    """
    from shared.config import get_runtime_data_dir

    root = os.environ.get("NAKAMA_FINISHED_CUT_RUNTIME", "").strip()
    base = (
        Path(root) if root else get_runtime_data_dir() / "finished-cut-runtime"
    ) / "registrations"
    return (base / episode_id / f"{cut_id}.json", base / f"{cut_id}.json")


def chapters_from_registration(episode_id: str, cut_id: str) -> list[tuple[float, str]]:
    """分章 = 核准剪輯 `sections` 裡標了轉場卡的那幾節，前加 00:00 開場。

    規則跟 `chapters_from_broll` 一模一樣（轉場卡 + 開場），只是問對了來源。
    ADR-066 之後轉場卡不再寫進 `tighten/<cut>_broll.json`——20260901 那份只剩一張
    名牌卡，於是 `len(marks) < 2` 讓每一集的章節都回空。實測 20260805 與 20260901
    兩集、full 與三支長片全部 0 章：這個功能從來沒有在任何一集亮過。

    只採 `transition_before` 為真的節：末節常常沒有轉場卡，而它的 `chapter_title`
    是整段摘要（實測 punch-L02/L03 末節都是七十幾字的段落），當章節名會很難看。
    """
    payload = None
    for path in _registration_paths(episode_id, cut_id):
        if not path.is_file():
            continue
        try:
            candidate = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        # 撿到的檔必須自己說它是這一集的。沒有 `episode_id` 的 payload 一律不採——
        # 「看起來對」不是來歷，寧可沒有章節也不要接到別集的時間軸。
        if not isinstance(candidate, dict) or candidate.get("episode_id") != episode_id:
            continue
        payload = candidate
        break
    if payload is None:
        return []
    if not payload.get("human_approved"):
        return []  # 沒過人審的規劃不是分章來源
    marks = sorted(
        (float(section["t0"]), " ".join(str(section["transition_title"]).split()))
        for section in payload.get("sections") or []
        if section.get("transition_before") and str(section.get("transition_title") or "").strip()
    )
    if len(marks) < 2:
        return []
    return [(0.0, "開場"), *marks]


#: agent 切好的章節表落在這裡，一支 cut 一個檔。
AUTHORED_CHAPTERS_RELDIR = Path("publish") / "chapters"


def chapters_from_authored(episode_dir: Path, cut_id: str) -> list[tuple[float, str]]:
    """讀 agent 切好的章節表（`publish/chapters/<cut_id>.json`）。

    完整版**沒有轉場卡**，所以推不出章節；長片也不保險——轉場卡少於兩張就回空
    （20260721 的 story-L02 與 value-L02 各只有一張）。這條來源補的就是那個缺口：
    章節由讀過逐字稿的 agent 切，schema 擋 YouTube 的硬性規則。

    缺檔回空，不是錯誤——舊集數本來就沒有。**壞損就吵**：靜靜地當成沒有章節，
    等於讓一份切好的表無聲消失。
    """
    from shared.schemas.publish_chapters import PublishChaptersFileV1

    path = Path(episode_dir) / AUTHORED_CHAPTERS_RELDIR / f"{cut_id}.json"
    if not path.is_file():
        return []
    try:
        return PublishChaptersFileV1.model_validate_json(
            path.read_text(encoding="utf-8")
        ).as_pairs()
    except (OSError, ValueError) as error:
        raise ValueError(f"{path} 不是合法的章節表：{error}") from error


def resolve_chapters(episode_dir: Path, cut_id: str) -> list[tuple[float, str]]:
    """分章來源，由權威到回退：plan record → 對應表 → 核准剪輯登錄 → 章節表 → 舊 broll。

    **權威是 plan record 本身，不是「這一集有沒有對應表」。** ADR-069 把 timeline
    與 component 寫進紀錄之後，`plan_chapters` 不再需要對應表先指路；但這裡的入口
    條件沒跟著改，於是「有紀錄、沒有對應表」的長片整個跳過紀錄、掉回 broll 檔——
    那正是 20260805 value-L02 分章全錯的來源（broll 只到 326.7s，成品 563.7s）。
    同一個檔裡的 `build_description_prompt` 直接問 `plan_subtitle`，沒有這道閘，
    所以那個組合產出的是字幕取自成品、分章取自舊時間軸的自相矛盾描述。

    紀錄一旦存在就是唯一權威——它說沒有分章就是沒有分章，不可以回頭撿 broll。
    沒有紀錄時，對應表的存在仍然代表「這一集走過發布線」，同樣不回退。
    再往下才輪到登錄檔：它是修修按過核准的那份規劃（`human_approved`），跟成品
    同源；broll 檔留在最後只為了還沒走 ADR-066 的舊集數。
    """
    from agents.usopp.publish_timeline import load_timeline_map, plan_chapters

    episode_dir = Path(episode_dir)
    recorded = plan_chapters(episode_dir, cut_id)
    if recorded is not None:
        return recorded
    if load_timeline_map(episode_dir) is not None:
        return []
    registered = chapters_from_registration(episode_dir.name, cut_id)
    if registered:
        return registered
    # 轉場卡是畫面上真的有的東西，所以排在 agent 切的表前面；但它常常湊不到兩張，
    # 而完整版根本沒有。接不上就換這一條，不要讓描述裡一個時間戳都沒有。
    authored = chapters_from_authored(episode_dir, cut_id)
    if authored:
        return authored
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
        # **時間戳必須在行首。** YouTube 靠它認章節，而且認不出來的時候整份靜靜地
        # 不生效，不會有任何提示。舊版在前面掛了一個 `⏱`，雖然多數情況仍然解析得
        # 出來，但那是在拿修修的影片賭平台的寬容度——沒有理由賭（2026-09-12 裁決）。
        blocks.append("\n".join(f"{fmt_ts(t)} {title}" for t, title in chapters))
    visible_citations = public_citations(list(citations))
    if visible_citations:
        blocks.append("本集引用：\n" + "\n".join(f"・{c}" for c in visible_citations))
    if footer.strip():
        blocks.append(footer.strip())
    return "\n\n".join(b for b in blocks if b)


def _hook_rejected(reason: str, hook: str) -> ValueError:
    """把被退掉的稿子附在錯誤裡——不然重跑的人不知道生成端到底寫了什麼。

    20260901 punch-L04：草稿因「必須是 1–2 個短段落」失敗，而 `ensure_description_draft`
    只把這句話存進 target.error，LLM 的稿子當場蒸發。看得到才知道是規格太緊還是稿子真的爛。
    """
    return ValueError(f"{reason}\n--- 被退回的 hook ---\n{hook}")


def validate_description_hook(hook: str) -> str:
    """Enforce the compact public-copy contract for the description hook.

    段落上限 4 不是 2：修修實際上架的描述一貫是 3–4 段（見
    memory/claude/feedback_shosho_title_description_edits.md），規格寫 2 是當初照
    ADR-055 草案抄的，跟成品從來對不上。字數上限本來就會擋住長度，段落數只該擋
    「整篇糊成一塊」與「碎成條列」。
    """
    cleaned = hook.strip()
    if not cleaned:
        raise ValueError("description hook 不可為空")
    matches = [pattern.pattern for pattern in _AI_SLOP_PATTERNS if pattern.search(cleaned)]
    if matches:
        raise _hook_rejected(f"description hook 命中 AI slop：{', '.join(matches)}", cleaned)
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", cleaned) if part.strip()]
    if not 1 <= len(paragraphs) <= _HOOK_MAX_PARAGRAPHS:
        raise _hook_rejected(
            f"description hook 必須是 1–{_HOOK_MAX_PARAGRAPHS} 個短段落"
            f"（目前 {len(paragraphs)} 段）",
            cleaned,
        )
    char_count = len(re.sub(r"\s+", "", cleaned))
    if not _HOOK_MIN_CHARS <= char_count <= _HOOK_MAX_CHARS:
        raise _hook_rejected(
            f"description hook 需約 200–300 字（目前 {char_count} 字；"
            f"允許 {_HOOK_MIN_CHARS}–{_HOOK_MAX_CHARS}）",
            cleaned,
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
    from agents.usopp.publish_timeline import plan_subtitle

    # 逐字稿必須是**成品那一份**。tight SRT 是 ADR-065 製作線的殘留，punch-L04 的
    # 只有 260 秒舊剪輯而成品是 492 秒——照它寫等於替一支不存在的影片寫文案。
    source = plan_subtitle(episode_dir, cut_id)
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
- hook 總長 200–300 個繁體中文字（不含空白），最多四段；每段只推進一件事。
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
