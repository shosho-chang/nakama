"""Load Title × Thumbnail Playbook archetype index for brainstorm prompt injection.

ADR-033 D4 — replaces vision-few-shot reference-image attachment with a distilled
text archetype catalog (~1.1K tokens vs ~15K tokens for 30 images).

Reads:
- ``prompts/thumbnail/playbook_data_v1.json`` (cluster output — 10+10 archetypes + 8 pairings)
- ``prompts/thumbnail/playbook_v1.md`` §5.2 table → archetype_id → brand-fit grade map

Provides:
- :func:`load_playbook_index` — parsed playbook in structured form
- :func:`format_playbook_index_for_prompt` — compact text for LLM injection
- :func:`valid_archetype_ids` — set of valid {T-AN, T-VN, JP-N} for parser validation
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PLAYBOOK_DATA_PATH = _REPO_ROOT / "prompts" / "thumbnail" / "playbook_data_v1.json"
_PLAYBOOK_MD_PATH = _REPO_ROOT / "prompts" / "thumbnail" / "playbook_v1.md"


@dataclass(frozen=True)
class PlaybookArchetype:
    """One title or thumbnail archetype from the catalog + brand-fit grade."""

    id: str  # e.g. "T-A1", "T-V3"
    name: str
    one_line: str
    when_to_use: str
    when_to_avoid: str
    brand_fit_grade: str  # S / A / B / C / D / F  (D from v1.1 T-V6 downgrade)
    click_drivers_primary: tuple[str, ...] = ()
    click_drivers_secondary: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()
    frequency_total: int = 0
    confidence: str = ""
    mechanism_writeup: str = ""


@dataclass(frozen=True)
class PlaybookPairing:
    """A common joint pairing (title-arch × thumb-arch)."""

    id: str  # e.g. "JP-3"
    name: str
    title_archetype_id: str
    thumb_archetype_id: str
    frequency: int


@dataclass(frozen=True)
class PlaybookIndex:
    title_archetypes: tuple[PlaybookArchetype, ...]
    thumbnail_archetypes: tuple[PlaybookArchetype, ...]
    joint_pairings: tuple[PlaybookPairing, ...]
    source_extraction: str = ""
    row_count: int = 0


# §5.2 markdown table row format:
#   | T-A1 | Numbered Listicle Promise | A | rationale | template |
#   | T-V6 | ... | **D / Avoid** | ...
_MATRIX_ROW_RE = re.compile(
    r"^\|\s*(T-[AV]\d+)\s*\|\s*[^|]+\|\s*(\*{0,2}[A-Za-z]+(?:\s*/\s*Avoid)?\*{0,2})\s*\|",
    re.MULTILINE,
)

TITLE_ARCHETYPE_EMOTION_MAP: dict[str, str] = {
    "T-A1": "好奇 / 可控感",
    "T-A2": "可操作 / 效率",
    "T-A3": "驚訝 / 報復快感",
    "T-A4": "共鳴 / 同理",
    "T-A5": "FOMO / 偷窺欲",
    "T-A6": "認知 gap / 好奇",
    "T-A7": "時間焦慮 / 效率",
    "T-A8": "信任 / 安全",
    "T-A9": "緊迫 / FOMO",
    "T-A10": "損失厭惡 / 恐懼",
}

_SHOSHO_TITLE_ADAPTATION_MAP: dict[str, str] = {
    "T-A1": "用「5 個」「8 個」這種小而完整的數字；健康長壽題材避免超過 10 項，否則容易顯得空泛。",
    "T-A2": "用「如何在 X 內做到 Y」加具體錨點；不要承諾過快健康效果。",
    "T-A3": "用「你以為的 X 其實是錯的」或「停止 X，改做 Y」；前提是影片真的有 evidence backing。",
    "T-A4": "用第一人稱或給家人的建議，例如「如果是給我爸的建議，我會說...」。",
    "T-A5": "健康題材避免陰謀論語氣；改成「我訪問 X 位研究者後發現的事」會更安全。",
    "T-A6": "問題必須是真正有資訊缺口，例如「空腹有氧到底有沒有用？」；不要問答案太 obvious 的空題。",
    "T-A7": "適合複雜題目的壓縮導覽，例如「X 分鐘看懂 Y」；時間承諾要對得起內容深度。",
    "T-A8": "用研究、醫師背景、年份或權威人物建立可信度；適合爭議或反直覺健康主題。",
    "T-A9": "只在真的有新研究、新年份、新工具時加年份；evergreen 題目不要硬加。",
    "T-A10": "健康題材少量使用 loss frame；把恐嚇改成可驗證的忽略成本或延後成本。",
}


def _parse_grade(raw: str) -> str:
    """`**D / Avoid**` → `D`; `A` → `A`."""
    return raw.strip().strip("*").split("/")[0].strip().upper()


def _clean_prompt_text(text: str) -> str:
    """Normalize extraction artifacts before they reach an LLM prompt."""

    return re.sub(r"\s+", " ", (text or "").replace("\ufffd", "-")).strip()


def _load_grade_map() -> dict[str, str]:
    """Parse playbook_v1.md §5.2 matrix table → {archetype_id: grade}."""
    if not _PLAYBOOK_MD_PATH.exists():
        return {}
    md = _PLAYBOOK_MD_PATH.read_text(encoding="utf-8")
    grades: dict[str, str] = {}
    for m in _MATRIX_ROW_RE.finditer(md):
        grades[m.group(1)] = _parse_grade(m.group(2))
    return grades


@lru_cache(maxsize=1)
def load_playbook_index() -> PlaybookIndex:
    """Read catalog + brand-fit grades. Cached for hot brainstorm path.

    To pick up edits to the playbook files during dev, call
    ``load_playbook_index.cache_clear()``.
    """
    data = json.loads(_PLAYBOOK_DATA_PATH.read_text(encoding="utf-8"))
    catalog = data.get("catalog", data)
    grades = _load_grade_map()

    def _archetype(a: dict) -> PlaybookArchetype:
        examples: list[str] = []
        for ex in a.get("examples", [])[:6]:
            if not isinstance(ex, dict):
                continue
            title = _clean_prompt_text(str(ex.get("title") or ex.get("overlay_text") or ""))
            creator = _clean_prompt_text(str(ex.get("creator") or ""))
            if title and creator:
                examples.append(f"{title} ({creator})")
            elif title:
                examples.append(title)
        return PlaybookArchetype(
            id=a["id"],
            name=_clean_prompt_text(a.get("name", "")),
            one_line=_clean_prompt_text(a.get("one_line_summary", "")),
            when_to_use=_clean_prompt_text(a.get("when_to_use", "")),
            when_to_avoid=_clean_prompt_text(a.get("when_to_avoid", "")),
            brand_fit_grade=grades.get(a["id"], "?"),
            click_drivers_primary=tuple(
                _clean_prompt_text(str(v)) for v in a.get("click_drivers_primary", [])
            ),
            click_drivers_secondary=tuple(
                _clean_prompt_text(str(v)) for v in a.get("click_drivers_secondary", [])
            ),
            examples=tuple(examples),
            frequency_total=int(a.get("frequency_total") or 0),
            confidence=_clean_prompt_text(a.get("confidence", "")),
            mechanism_writeup=_clean_prompt_text(a.get("mechanism_writeup", "")),
        )

    def _pairing(p: dict) -> PlaybookPairing:
        return PlaybookPairing(
            id=p["id"],
            name=p.get("name", ""),
            title_archetype_id=p.get("title_archetype_id", ""),
            thumb_archetype_id=p.get("thumb_archetype_id", ""),
            frequency=int(p.get("frequency", 0)),
        )

    return PlaybookIndex(
        title_archetypes=tuple(_archetype(a) for a in catalog.get("title_archetypes", [])),
        thumbnail_archetypes=tuple(_archetype(a) for a in catalog.get("thumbnail_archetypes", [])),
        joint_pairings=tuple(_pairing(p) for p in catalog.get("joint_pairings", [])),
        source_extraction=str(data.get("source_extraction") or ""),
        row_count=int(data.get("row_count") or 0),
    )


def format_playbook_index_for_prompt(index: PlaybookIndex | None = None) -> str:
    """Compact ~1-1.5K-token markdown text for brainstorm user-message injection.

    Output shape:
        ## Playbook archetype index (pick by ID)
        ### Title archetypes ...
        ### Thumbnail archetypes ...
        ### Common joint pairings ...
        ### Brand-fit grade meaning ...
        ### Required output tag format ...
    """
    if index is None:
        index = load_playbook_index()

    parts: list[str] = [
        "## Playbook archetype index (pick by ID; full text in `prompts/thumbnail/playbook_v1.md`)",
        "",
        "Use these distilled archetype catalogs as your style anchor — NOT vision few-shot images. "
        "Each archetype is graded for 修修's Health & Wellness / Longevity brand fit.",
        "",
        "### Title archetypes",
        "",
    ]
    for a in index.title_archetypes:
        parts.append(f"- **{a.id}** [{a.brand_fit_grade}] {a.name} — {a.one_line}")

    parts.append("")
    parts.append("### Thumbnail archetypes")
    parts.append("")
    for a in index.thumbnail_archetypes:
        parts.append(f"- **{a.id}** [{a.brand_fit_grade}] {a.name} — {a.one_line}")

    parts.append("")
    parts.append("### Common joint pairings (high-frequency title × thumb combos)")
    parts.append("")
    for p in index.joint_pairings:
        parts.append(
            f"- **{p.id}** {p.name}"
            f" ({p.title_archetype_id} × {p.thumb_archetype_id}, n={p.frequency})"
        )

    parts.append("")
    parts.append("### Brand-fit grade meaning")
    parts.append("- **S** = direct copy works for 修修 brand voice")
    parts.append("- **A** = minor adaptation (translate + light tweak)")
    parts.append("- **B** = heavy adaptation (preserve click-driver, change voice substantially)")
    parts.append(
        "- **C** = risky — pattern works but conflicts with evidence-based health voice;"
        " use sparingly with explicit hedges"
    )
    parts.append(
        "- **D** = avoid — regulatory or brand-credibility risk"
        " (e.g. T-V6 Question Overlay under Taiwan Health Food Control Act)"
    )
    parts.append("- **F** = antipattern — would damage brand credibility")

    parts.append("")
    parts.append("### REQUIRED — tag each idea with archetype IDs")
    parts.append(
        "Each idea block MUST start with an `archetype: [T-AN, T-VN, JP-N]` line"
        " BEFORE the 5-line content."
        " Pick exactly 1 title archetype (T-AN) + 1 thumbnail archetype (T-VN)."
        " Optionally add 1 joint pairing ID (JP-N) if a strong empirical match"
        " exists in the catalog."
        " Prefer S/A-grade archetypes for 修修. B-grade OK with explicit brand"
        " adaptation. C-grade only with hedge. D/F-grade DO NOT USE."
    )

    return "\n".join(parts)


def format_title_pool_system_prompt(index: PlaybookIndex | None = None) -> str:
    """Build the title-pool brainstorm system prompt from the playbook index.

    This keeps the divergent title brainstorm aligned with
    ``playbook_data_v1.json`` instead of duplicating the archetype catalog in a
    static markdown prompt.
    """

    if index is None:
        index = load_playbook_index()

    title_archetypes = index.title_archetypes
    archetype_ids = [a.id for a in title_archetypes]
    archetype_id_text = ", ".join(archetype_ids)

    parts: list[str] = [
        "# YouTube Title Brainstorm - Dynamic Playbook Prompt",
        "",
        "You are 修修's video title brainstorming partner. 修修 is a Health & "
        "Wellness / Longevity content creator targeting Traditional Chinese "
        "readers in Taiwan and Hong Kong, roughly 30-50 years old, "
        "science-curious, and health-conscious.",
        "",
        "You produce a divergent pool of title candidates spanning the title "
        "archetypes below. This is not the final 3-candidate output; the user "
        "will review broadly and converge through iteration.",
        "",
        "## Source of truth",
        "",
        f"- playbook_data: prompts/thumbnail/playbook_data_v1.json",
        f"- source_extraction: {index.source_extraction or '(unknown)'}",
        f"- source_rows: {index.row_count or '(unknown)'}",
        "- The archetype names, mechanisms, examples, and frequency counts below "
        "come from the playbook loader, not from a copied prompt catalog.",
        "",
        "## Output schema - strict",
        "",
        "Output titles grouped by archetype ID, exactly this shape:",
        "",
        "```",
    ]
    for a in title_archetypes:
        parts.extend([a.id, "- title 1", "- title 2", ""])
    parts.extend(
        [
            "```",
            "",
            "Rules:",
            f"- Use exactly these section headers, one per line: {archetype_id_text}.",
            "- Under each header, output 2-3 bullet points. Each line starts with `- `.",
            "- Each bullet is one title. No numbering, no commentary, no quotes.",
            "- Output every archetype even if the topic feels weak for one; generate the best two you can.",
            "- No preamble and no closing remarks.",
            "",
            "## Title constraints",
            "",
            "- Traditional Chinese only.",
            "- 80 characters or fewer per title.",
            "- No emoji.",
            "- Prefer Chinese fullwidth punctuation in title copy.",
            "- No clickbait the body cannot deliver; 修修's audience trusts content density.",
            "- Use corpus examples as structural inspiration, not literal templates.",
            "",
            "## Title archetype catalog",
            "",
        ]
    )

    for a in title_archetypes:
        primary = ", ".join(a.click_drivers_primary) or "(not specified)"
        secondary = ", ".join(a.click_drivers_secondary) or "(not specified)"
        examples = " / ".join(a.examples[:3]) or "(no examples)"
        emotion = TITLE_ARCHETYPE_EMOTION_MAP.get(a.id, "")
        adaptation = _SHOSHO_TITLE_ADAPTATION_MAP.get(
            a.id,
            "Preserve the click-driver, but translate the promise into 修修's evidence-based health voice.",
        )
        parts.extend(
            [
                f"### {a.id} - {a.name}",
                f"Brand fit: {a.brand_fit_grade}; corpus frequency: {a.frequency_total}; confidence: {a.confidence or '?'}",
                f"One-line: {a.one_line}",
                f"Primary click drivers: {primary}",
                f"Secondary click drivers: {secondary}",
                f"Emotion: {emotion or '(not specified)'}",
                f"When to use: {a.when_to_use}",
                f"When to avoid: {a.when_to_avoid}",
                f"Corpus examples: {examples}",
                f"Mechanism: {a.mechanism_writeup}",
                f"修修 adaptation: {adaptation}",
                "",
            ]
        )

    parts.extend(
        [
            "## Iteration mode",
            "",
            "If the user message includes `## Anchor titles`, treat those titles as signal "
            "of which structural, emotional, and specificity patterns are working for this "
            "topic and audience.",
            "",
            "- Generate fresh titles; do not re-emit anchors verbatim.",
            "- Lean new titles toward the patterns visible in anchors.",
            "- Still cover every archetype listed above.",
            "",
            "## Internal reasoning (do not output)",
            "",
            "For each archetype, inspect search_topic, one_sentence, and hook_text. "
            "Choose the specificity anchor, draft 2-3 distinct titles, then check "
            "deliverability, Traditional Chinese wording, and length. Output only "
            "the grouped schema.",
        ]
    )
    return "\n".join(parts)


@lru_cache(maxsize=1)
def valid_archetype_ids() -> frozenset[str]:
    """Set of all valid archetype IDs (T-AN ∪ T-VN ∪ JP-N) for parser validation."""
    idx = load_playbook_index()
    ids = set()
    for a in idx.title_archetypes:
        ids.add(a.id)
    for a in idx.thumbnail_archetypes:
        ids.add(a.id)
    for p in idx.joint_pairings:
        ids.add(p.id)
    return frozenset(ids)
