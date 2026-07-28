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


@dataclass(frozen=True)
class PlaybookPairing:
    """A common joint pairing (title-arch × thumb-arch)."""

    id: str  # e.g. "JP-3"
    name: str
    title_archetype_id: str
    thumb_archetype_id: str
    frequency: int
    why_they_pair: str


@dataclass(frozen=True)
class PlaybookIndex:
    title_archetypes: tuple[PlaybookArchetype, ...]
    thumbnail_archetypes: tuple[PlaybookArchetype, ...]
    joint_pairings: tuple[PlaybookPairing, ...]


# §5.2 markdown table row format:
#   | T-A1 | Numbered Listicle Promise | A | rationale | template |
#   | T-V6 | ... | **D / Avoid** | ...
_MATRIX_ROW_RE = re.compile(
    r"^\|\s*(T-[AV]\d+)\s*\|\s*[^|]+\|\s*(\*{0,2}[A-Za-z]+(?:\s*/\s*Avoid)?\*{0,2})\s*\|",
    re.MULTILINE,
)


def _parse_grade(raw: str) -> str:
    """`**D / Avoid**` → `D`; `A` → `A`."""
    return raw.strip().strip("*").split("/")[0].strip().upper()


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
        return PlaybookArchetype(
            id=a["id"],
            name=a.get("name", ""),
            one_line=a.get("one_line_summary", ""),
            when_to_use=a.get("when_to_use", ""),
            when_to_avoid=a.get("when_to_avoid", ""),
            brand_fit_grade=grades.get(a["id"], "?"),
        )

    def _pairing(p: dict) -> PlaybookPairing:
        return PlaybookPairing(
            id=p["id"],
            name=p.get("name", ""),
            title_archetype_id=p.get("title_archetype_id", ""),
            thumb_archetype_id=p.get("thumb_archetype_id", ""),
            frequency=int(p.get("frequency", 0)),
            why_they_pair=p.get("why_they_pair", ""),
        )

    return PlaybookIndex(
        title_archetypes=tuple(_archetype(a) for a in catalog.get("title_archetypes", [])),
        thumbnail_archetypes=tuple(_archetype(a) for a in catalog.get("thumbnail_archetypes", [])),
        joint_pairings=tuple(_pairing(p) for p in catalog.get("joint_pairings", [])),
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
        line = (
            f"- **{p.id}** {p.name}"
            f" ({p.title_archetype_id} × {p.thumb_archetype_id}, n={p.frequency})"
        )
        if p.why_they_pair:
            why = p.why_they_pair
            if len(why) > 140:
                why = why[:140].rstrip() + "…"
            line += f" — {why}"
        parts.append(line)

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
