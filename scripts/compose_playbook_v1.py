"""Compose the §2/§3/§4/§5.2/§7 markdown content of playbook_v1.md.

Reads ``prompts/thumbnail/playbook_data_v1.json`` (cluster output) and
``data/thumbnail_reference_extraction_v1.json`` (raw 140 rows) and produces
``prompts/thumbnail/playbook_v1_body_generated.md`` — a markdown fragment that
gets manually spliced into ``prompts/thumbnail/playbook_v1.md`` placeholders.

Why a fragment rather than full file rewrite:
- The §0/§1/§5.1/§5.3/§5.4/§6/§8/§9 sections are hand-curated theory and don't
  benefit from LLM regeneration on each cluster re-run.
- Keeping LLM-touched content in a separate file makes diff review trivial when
  corpus grows.

Uses streaming for long outputs.

Usage:
    python -m scripts.compose_playbook_v1
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

for _env_candidate in (_REPO_ROOT / ".env", Path("E:/nakama/.env")):
    if _env_candidate.exists():
        load_dotenv(_env_candidate)
        break

from shared.anthropic_client import get_client  # noqa: E402

logger = logging.getLogger(__name__)

_CATALOG_PATH = _REPO_ROOT / "prompts" / "thumbnail" / "playbook_data_v1.json"
_EXTRACTION_PATH = _REPO_ROOT / "data" / "thumbnail_reference_extraction_v1.json"
_OUTPUT_PATH = _REPO_ROOT / "prompts" / "thumbnail" / "playbook_v1_body_generated.md"

_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 32000


def _ask_streaming(*, system: str, user: str) -> str:
    client = get_client()
    parts: list[str] = []
    with client.messages.stream(
        model=_MODEL,
        system=system,
        messages=[{"role": "user", "content": user}],
        max_tokens=_MAX_TOKENS,
    ) as stream:
        for chunk in stream.text_stream:
            parts.append(chunk)
    return "".join(parts)


# ---------------------------------------------------------------------------
# Compose prompt
# ---------------------------------------------------------------------------


_COMPOSE_SYSTEM = """You are composing 修修's Title × Thumbnail Playbook v1 — the body sections (§2 Title Archetypes / §3 Thumbnail Archetypes / §4 Joint Pairings / §5.2 Brand-Fit Matrix / §7 Methodology Caveats).

修修 is a Traditional Chinese (Taiwan / Hong Kong) Health & Wellness / Longevity YouTube creator. The playbook will be loaded by LLM brainstorm calls + read by 修修 for video planning.

## Voice and style

- **繁體中文** for all 修修-facing copy (Chinese adaptation examples, antipattern warnings, when-to-use notes in §5)
- **English** for the structural archetype metadata (frameworks, names) — they must match the JSON catalog verbatim
- Tone: confident, evidence-based, no marketing fluff
- **No emoji** in body; use Markdown tables / headings for structure
- Reference frameworks by their §1 section anchor (e.g. "Loewenstein (§1.1) × Specificity (§1.6)")

## Brand voice constraints (from §5.1, already in playbook)

修修's brand:
1. Evidence-based authority — claims cite research, named experts, or measurable outcomes
2. Warm-but-direct tone (between Attia clinical and Ali calm-confident)
3. No fear-mongering — loss aversion only via factual framing ("研究發現 X 增加 Y 風險")
4. No oversimplification — avoid "輕鬆 / 無痛 / 不用努力" — prefer "簡單 / 微習慣 / 5 分鐘"
5. Specificity over hype — "5g 肌酸 × 6 週" beats "超強補充品"
6. Familiar authority anchors — 台大醫師 / 哈佛研究 / Attia / Huberman / Bryan Johnson

## Brand-fit grading

- **S** = direct copy works (no adaptation needed). Should be RARE in a non-修修 reference set.
- **A** = minor adaptation (translate + light tone tweak). Pattern transfers cleanly.
- **B** = heavy adaptation (preserve click-driver, change voice substantially). Most patterns from this corpus.
- **C** = risky — pattern works but conflicts with 修修's evidence-based voice. Use sparingly with explicit hedges.
- **F** = antipattern — would damage 修修's brand credibility. Do not use.

Be honest about grades. Hormozi-heavy archetypes are usually B or C. Cleo-heavy archetypes vary. Don't grade-inflate; reviewer will notice.

## Output format

Output ONE markdown fragment with these top-level sections in order:

```
## 2. Title Archetypes

(10 archetypes T-A1 through T-A10. For each, use the template below.)

## 3. Thumbnail Archetypes

(10 archetypes T-V1 through T-V10. Same template.)

## 4. Joint Pairings

(8 pairings JP-1 through JP-8. Use the pairing template below.)

## 5.2 Archetype × Brand-Fit Matrix

(Single table with one row per archetype, 20 rows total.)

## 7. Methodology Caveats

(6 entries from catalog, formatted as numbered list with full elaboration.)
```

## Per-archetype template (for §2 and §3)

```markdown
### {ID}. {Name}

- **One-line**: {one_line_summary from catalog}
- **Frameworks**: {primary frameworks with §1 anchors, e.g. "Loewenstein (§1.1) × Numerical anchor (§1.9)"}
- **When to use**: 1-2 繁中 sentences. Be concrete about content type and viewer state.
- **When to avoid**: 1-2 繁中 sentences. What types of content this archetype hurts rather than helps.
- **Frequency in corpus**: {frequency_total} / 140 ({frequency_pct}%)
- **Creator distribution**: list non-zero counts only
- **Canonical example**: "{exact title}" — {creator}
- **Mechanism**: 2-3 sentences explaining WHY this archetype clicks, anchored in named frameworks. Be specific to THIS archetype, not generic theory.
- **Sample examples**: bullet list of 3-5 row IDs with their titles
- **修修 brand fit**: {S/A/B/C/F}
- **Why this grade**: 1 繁中 sentence
- **中文化範例 (2-3 個)**: 真實可發布的繁中標題 — 對應 修修 健康 / 長壽 領域
- **改編要點**: bullet list 繁中 — 跟英文原版具體差在哪
- **避免**: bullet list 繁中 — antipatterns (即使是 A 級也要列至少 1 條 caveat)
```

## Per-pairing template (for §4)

```markdown
### {ID}. {Name}

- **Title archetype**: {T-A id}
- **Thumbnail archetype**: {T-V id}
- **Frequency**: {N} occurrences
- **Why they pair**: 2-3 sentences — what specific psychological/visual interaction makes this combination stronger than either alone
- **Creator distribution**: ...
- **修修 recipe**: 繁中 — a complete title + thumbnail brief 修修 could publish today
  - Title: 「...」
  - Thumbnail: ...
  - Hook 大字: 「...」(3-5 繁中字)
  - 表情 (7-enum): ...
  - 背景: ...
```

## §5.2 matrix table format

```markdown
| ID | Archetype | Grade | One-line rationale | 中文化模板 |
|---|---|---|---|---|
| T-A1 | Numbered Listicle Promise | A | 健康 listicle 直接轉用 | 「N 個 {topic} 的 {power-word} 習慣」 |
| ... | | | | |
```

## §7 caveats format

```markdown
### MC-{n}. {Concern} (from catalog)

{detail paragraph from catalog}

**Implication for 修修**: 1-2 繁中 sentences — what to actually do/avoid in light of this caveat.
```

## CRITICAL

- Output is a markdown FRAGMENT starting with `## 2. Title Archetypes`. No preamble, no front matter, no closing summary.
- All archetype IDs must match catalog verbatim (T-A1...T-A10, T-V1...T-V10, JP-1...JP-8, MC-1...MC-6).
- Chinese adaptation examples must be PUBLISHABLE titles 修修 could actually use today (specific, evidence-based, on-brand).
- Do not invent archetypes beyond the catalog.
- §7 must include "**Implication for 修修**" line for each caveat — this is what makes caveats actionable rather than just hedge."""


def _build_user_message(catalog: dict, sample_extracted_rows: list[dict]) -> str:
    # Subset the extraction to brand_fit + chinese_adaptation_example + key facts
    # so the composer has real examples of 修修-style Chinese titles already produced
    sampled = []
    for row in sample_extracted_rows:
        bf = row.get("shosho_brand_fit", {})
        sampled.append({
            "id": row["id"],
            "creator": row["creator"],
            "title": row["title"],
            "grade": bf.get("grade"),
            "chinese_adaptation_example": bf.get("chinese_adaptation_example"),
            "structure_primary": row.get("title_analysis", {}).get("structure_primary"),
        })
    return (
        "## Catalog (cluster output)\n\n"
        f"```json\n{json.dumps(catalog, ensure_ascii=False, indent=2)}\n```\n\n"
        "## Per-row brand-fit hints (from Phase 2 extraction)\n\n"
        "Use these as raw material for Chinese adaptation examples — many already include 修修-style titles you can refine.\n\n"
        f"```json\n{json.dumps(sampled, ensure_ascii=False)}\n```\n\n"
        "Compose the §2 / §3 / §4 / §5.2 / §7 markdown fragment per the system prompt."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--catalog", type=Path, default=_CATALOG_PATH)
    parser.add_argument("--extraction", type=Path, default=_EXTRACTION_PATH)
    parser.add_argument("--output", type=Path, default=_OUTPUT_PATH)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    catalog_doc = json.loads(args.catalog.read_text(encoding="utf-8"))
    catalog = catalog_doc.get("catalog", catalog_doc)
    extraction = json.loads(args.extraction.read_text(encoding="utf-8"))
    rows = extraction.get("rows", [])

    logger.info("composing playbook body from %d archetypes (%d title + %d thumb), %d rows",
                len(catalog.get("title_archetypes", [])) + len(catalog.get("thumbnail_archetypes", [])),
                len(catalog.get("title_archetypes", [])),
                len(catalog.get("thumbnail_archetypes", [])),
                len(rows))

    user = _build_user_message(catalog, rows)
    raw = _ask_streaming(system=_COMPOSE_SYSTEM, user=user)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(raw, encoding="utf-8")
    logger.info("wrote %s (%d chars)", args.output, len(raw))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
