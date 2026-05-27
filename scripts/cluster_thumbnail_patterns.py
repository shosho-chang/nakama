"""Cluster extracted thumbnail rows into archetype catalog (Phase 3).

Reads ``data/thumbnail_reference_extraction_v1.json`` and produces:
- ``prompts/thumbnail/playbook_data_v1.json`` — machine-readable archetype catalog
  (title archetypes + thumbnail archetypes + joint pairings + per-creator signatures)

Threshold (per docs/research/2026-05-26-thumbnail-playbook-design.md §1.3):
A pattern is declared an archetype ONLY if it has ≥3 examples across ≥2 creators.
Single-creator patterns are recorded in `per_creator_signatures` instead.

This uses a single LLM call with compressed row representations — no images
needed, just structured JSON. Cost: ~$0.50-1.

Usage:
    python -m scripts.cluster_thumbnail_patterns
    python -m scripts.cluster_thumbnail_patterns --rerun-self-critique  # additional self-critique pass

Output schema: ``prompts/thumbnail/playbook_data_schema_v1.json`` (generated below).
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

for _env_candidate in (_REPO_ROOT / ".env", Path("E:/nakama/.env")):
    if _env_candidate.exists():
        load_dotenv(_env_candidate)
        break

from shared.anthropic_client import ask_claude_multi  # noqa: E402
from shared.anthropic_client import get_client  # noqa: E402


def _ask_claude_streaming(messages, system, model, max_tokens):
    """Streaming variant of ask_claude_multi for long outputs (>8K tokens).

    The Anthropic SDK requires streaming for non-opus operations with large max_tokens
    (estimated wall time >10 min). Used by cluster script where catalog output exceeds
    the non-streaming threshold.
    """
    client = get_client()
    text_parts: list[str] = []
    with client.messages.stream(
        model=model,
        system=system,
        messages=messages,
        max_tokens=max_tokens,
    ) as stream:
        for chunk in stream.text_stream:
            text_parts.append(chunk)
    return "".join(text_parts)

logger = logging.getLogger(__name__)

_EXTRACTION_PATH = _REPO_ROOT / "data" / "thumbnail_reference_extraction_v1.json"
_OUTPUT_PATH = _REPO_ROOT / "prompts" / "thumbnail" / "playbook_data_v1.json"
_MODEL = "claude-sonnet-4-6"
# 16K truncated at per-creator signatures. 64K hits SDK streaming requirement.
# 24K sits under the non-streaming threshold and gives ~50% margin over the truncated baseline.
_MAX_TOKENS = 24000


# ---------------------------------------------------------------------------
# Row compression — strip verbose prose to keep cluster call compact
# ---------------------------------------------------------------------------


def _compress_row(row: dict) -> dict:
    """Reduce a full extraction row to its key structured signals."""
    ta = row.get("title_analysis", {})
    tn = row.get("thumbnail_analysis", {})
    ja = row.get("joint_analysis", {})
    bf = row.get("shosho_brand_fit", {})
    return {
        "id": row["id"],
        "creator": row["creator"],
        "title": row["title"],
        "title": {
            "structure_primary": ta.get("structure_primary"),
            "structure_secondary_tags": ta.get("structure_secondary_tags", []),
            "implied_promise": ta.get("implied_promise"),
            "promise_concreteness_1to5": ta.get("promise_concreteness_1to5"),
            "specificity_markers": ta.get("specificity_markers", []),
            "click_drivers": [d.get("framework") for d in ta.get("click_drivers", [])],
            "hook_emotion": ta.get("hook_emotion"),
        },
        "thumb": {
            "layout_archetype": tn.get("composition", {}).get("layout_archetype"),
            "face_present": tn.get("composition", {}).get("face_present"),
            "facial_expression": tn.get("composition", {}).get("facial_expression_inferred"),
            "text_overlay": tn.get("typography", {}).get("text_overlay_present"),
            "overlay_text": tn.get("typography", {}).get("overlay_text", []),
            "color_dominant": tn.get("color", {}).get("dominant"),
            "background_strategy": tn.get("color", {}).get("background_strategy"),
            "contrast_pattern": tn.get("color", {}).get("contrast_pattern"),
            "negative_space_1to5": tn.get("negative_space_ratio_1to5"),
            "click_drivers": [d.get("framework") for d in tn.get("click_drivers", [])],
        },
        "joint": {
            "relationship": ja.get("title_thumb_relationship"),
            "outcome_clarity_1to5": ja.get("implied_outcome_clarity_1to5"),
            "novelty_balance": ja.get("novelty_vs_familiarity_balance"),
        },
        "brand_fit": {
            "grade": bf.get("grade"),
            "chinese_adaptation_example": bf.get("chinese_adaptation_example"),
        },
    }


# ---------------------------------------------------------------------------
# Pre-cluster statistics (mechanical; no LLM)
# ---------------------------------------------------------------------------


def _pre_cluster_stats(rows: list[dict]) -> dict:
    """Mechanical aggregations the LLM then synthesises archetypes from."""
    by_creator = defaultdict(list)
    structure_counter = Counter()
    layout_counter = Counter()
    title_framework_counter = Counter()
    thumb_framework_counter = Counter()
    relationship_counter = Counter()
    expression_counter = Counter()
    creator_x_structure = defaultdict(Counter)

    for row in rows:
        creator = row["creator"]
        by_creator[creator].append(row["id"])
        ta = row.get("title_analysis", {})
        tn = row.get("thumbnail_analysis", {})
        ja = row.get("joint_analysis", {})
        structure_counter[ta.get("structure_primary")] += 1
        creator_x_structure[creator][ta.get("structure_primary")] += 1
        layout_counter[tn.get("composition", {}).get("layout_archetype")] += 1
        expression_counter[tn.get("composition", {}).get("facial_expression_inferred")] += 1
        for d in ta.get("click_drivers", []):
            title_framework_counter[d.get("framework")] += 1
        for d in tn.get("click_drivers", []):
            thumb_framework_counter[d.get("framework")] += 1
        relationship_counter[ja.get("title_thumb_relationship")] += 1

    return {
        "total_rows": len(rows),
        "per_creator_count": {c: len(ids) for c, ids in by_creator.items()},
        "structure_primary_distribution": dict(structure_counter.most_common()),
        "layout_archetype_distribution": dict(layout_counter.most_common()),
        "facial_expression_distribution": dict(expression_counter.most_common()),
        "title_framework_distribution": dict(title_framework_counter.most_common()),
        "thumbnail_framework_distribution": dict(thumb_framework_counter.most_common()),
        "title_thumb_relationship_distribution": dict(relationship_counter.most_common()),
        "creator_x_structure": {c: dict(d) for c, d in creator_x_structure.items()},
    }


# ---------------------------------------------------------------------------
# LLM cluster prompt
# ---------------------------------------------------------------------------


_CLUSTER_SYSTEM_PROMPT = """You are synthesising a Title × Thumbnail Playbook for 修修, a Traditional Chinese Health & Wellness / Longevity YouTube creator.

You receive: (a) compressed extraction rows for 140 high-CTR thumbnails from 4 creators (Ali Abdaal, Alex Hormozi, Cleo Abram, Jeff Su), and (b) mechanical pre-cluster statistics.

## Your job

Produce ONE JSON object with this top-level structure:

```json
{
  "title_archetypes": [...],          // ≥8 archetypes, each ≥3 examples + ≥2 creators
  "thumbnail_archetypes": [...],      // ≥8 archetypes, each ≥3 examples + ≥2 creators
  "joint_pairings": [...],            // recurring title-arch × thumb-arch combinations
  "per_creator_signatures": [...],    // each creator's dominant archetype set + creator-specific quirks
  "universal_patterns": [...],        // patterns found in ALL 4 creators
  "methodology_caveats": [...]        // self-critique: what might be over-attributed
}
```

## Archetype object schema

```json
{
  "id": "T-A1",                        // T-A{n} for title, T-V{n} for thumbnail (V=visual)
  "name": "Numbered Listicle Promise",
  "one_line_summary": "Title leads with finite small number (3-10) of items.",
  "click_drivers_primary": ["Loewenstein information gap", "Numerical anchor"],
  "click_drivers_secondary": ["Cognitive ease"],
  "frameworks": ["Loewenstein", "Cialdini-commitment"],
  "when_to_use": "Method-style content where outcomes can be enumerated; viewer wants completeness signal.",
  "when_to_avoid": "Narrative or single-thesis content; over-promising specific counts you can't deliver.",
  "examples": [
    {"id": "ali_abdaal_001", "creator": "Ali Abdaal", "title": "5 Easy Ways to Become More Self-Disciplined"},
    {"id": "jeff_su_001", "creator": "Jeff Su", "title": "10 INCREDIBLE things Google Sheets can do Right Now!"}
  ],
  "creator_distribution": {"Ali Abdaal": 12, "Jeff Su": 9, "Alex Hormozi": 2, "Cleo Abram": 0},
  "frequency_total": 23,
  "frequency_pct": 16.4,
  "canonical_example_id": "ali_abdaal_001",
  "mechanism_writeup": "2-3 sentences explaining WHY this archetype works psychologically (anchored in frameworks). Be specific — not 'leverages curiosity', but exactly which curiosity-gap mechanism fires."
}
```

For `thumbnail_archetypes`, replace `click_drivers_primary` references with thumbnail-relevant frameworks (Pattern interrupt, Face emotion contagion, Numerical anchor, etc.) and include layout/colour/typography signature notes.

## Joint pairings schema

```json
{
  "id": "JP-1",
  "name": "Listicle Number + Face + Promise Overlay",
  "title_archetype_id": "T-A1",
  "thumb_archetype_id": "T-V3",
  "frequency": 17,
  "creator_distribution": {...},
  "why_they_pair": "..."
}
```

## Per-creator signatures schema

```json
{
  "creator": "Alex Hormozi",
  "dominant_archetypes": ["T-A4", "T-V7"],
  "signature_quirks_not_in_general_catalog": [
    "Brutally-honest aggressive frame (English-only; not in general catalog because no other creator uses)",
    "Number-led income claims ($100M / 4000+ sales)"
  ],
  "shosho_relevance": "Low — aggressive frame conflicts with evidence-based health voice. Specific numerical-anchor lesson is portable."
}
```

## Threshold rules (strict)

- An archetype must have **≥3 examples across ≥2 creators**. Single-creator patterns with high frequency go in `per_creator_signatures.signature_quirks_not_in_general_catalog`, NOT in `title_archetypes` / `thumbnail_archetypes`.
- If an archetype has exactly 3 examples and ≥2 creators, mark `confidence: "low"` (add this field).
- ≥6 examples: `confidence: "medium"`.
- ≥10 examples: `confidence: "high"`.

## Methodology caveats — required honest self-critique

`methodology_caveats` MUST contain ≥3 entries identifying possible over-attribution:
- Patterns that might be English-only YouTube convention (don't translate to zh-Hant)
- Click-driver attributions that are post-hoc rationalisation
- Archetypes that mix two distinct patterns (could be split in v2)
- Creator-specific design quirks promoted to "archetype" too eagerly

## Output

Output ONLY the JSON object. No preamble. First char `{`, last char `}`."""


def _build_cluster_user_message(rows: list[dict], stats: dict) -> str:
    compressed = [_compress_row(r) for r in rows]
    return (
        "## Pre-cluster mechanical statistics\n\n"
        f"```json\n{json.dumps(stats, ensure_ascii=False, indent=2)}\n```\n\n"
        "## Compressed extraction rows\n\n"
        f"```json\n{json.dumps(compressed, ensure_ascii=False)}\n```\n\n"
        "Produce the playbook_data_v1 JSON object per the schema in the system prompt."
    )


# ---------------------------------------------------------------------------
# Self-critique pass
# ---------------------------------------------------------------------------


_SELF_CRITIQUE_PROMPT = """You wrote this playbook archetype catalog. Read it critically as if you were a sceptical Codex / Gemini reviewer.

For each section (title_archetypes, thumbnail_archetypes, joint_pairings, per_creator_signatures, universal_patterns):

1. List archetypes that might be **promoted too eagerly** (exactly 3 examples, all sharing a single secondary trait that may not be the actual click-driver).
2. List archetypes whose **mechanism writeup is post-hoc** (assumes the framework AFTER seeing the example; can't predict).
3. List archetypes whose **English-language convention** likely doesn't translate to zh-Hant audiences (e.g. all-caps emphasis, parenthetical credibility, dollar-sign income claims).
4. List joint_pairings whose **frequency** might just be that both archetypes are independently popular, not that they synergise.

Append findings to the existing `methodology_caveats` array (don't replace; append). Output the FULL updated JSON object."""


def _run_self_critique(catalog: dict) -> dict:
    user = (
        "## Existing playbook catalog (draft)\n\n"
        f"```json\n{json.dumps(catalog, ensure_ascii=False, indent=2)}\n```"
    )
    raw = _ask_claude_streaming(
        messages=[{"role": "user", "content": user}],
        system=_SELF_CRITIQUE_PROMPT,
        model=_MODEL,
        max_tokens=_MAX_TOKENS,
    )
    return _parse_llm_json(raw)


def _parse_llm_json(text: str) -> dict:
    stripped = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, re.DOTALL)
    if fence:
        stripped = fence.group(1)
    return json.loads(stripped)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _run(extraction_path: Path, output_path: Path, run_self_critique: bool) -> int:
    data = json.loads(extraction_path.read_text(encoding="utf-8"))
    rows = data.get("rows", [])
    if len(rows) < 20:
        logger.error(
            "extraction has only %d rows — needs ≥20 for meaningful clustering (target ≥100)",
            len(rows),
        )
        return 1

    stats = _pre_cluster_stats(rows)
    logger.info("pre-cluster stats: %d rows across %d creators", stats["total_rows"], len(stats["per_creator_count"]))
    logger.info("structure_primary distribution: %s", stats["structure_primary_distribution"])

    user_message = _build_cluster_user_message(rows, stats)
    logger.info("dispatching cluster LLM call (~%d chars)", len(user_message))

    raw = _ask_claude_streaming(
        messages=[{"role": "user", "content": user_message}],
        system=_CLUSTER_SYSTEM_PROMPT,
        model=_MODEL,
        max_tokens=_MAX_TOKENS,
    )
    # Dump raw for forensic inspection in case of parse failure
    _raw_dump = output_path.with_suffix(".raw.txt")
    _raw_dump.parent.mkdir(parents=True, exist_ok=True)
    _raw_dump.write_text(raw, encoding="utf-8")
    logger.info("raw LLM response dumped to %s (%d chars)", _raw_dump, len(raw))
    try:
        catalog = _parse_llm_json(raw)
    except json.JSONDecodeError as exc:
        logger.error("JSON parse failed: %s. First 500 chars of response: %r", exc, raw[:500])
        logger.error("Last 500 chars: %r", raw[-500:])
        raise
    logger.info(
        "cluster output: %d title archetypes, %d thumbnail archetypes, %d joint pairings, %d caveats",
        len(catalog.get("title_archetypes", [])),
        len(catalog.get("thumbnail_archetypes", [])),
        len(catalog.get("joint_pairings", [])),
        len(catalog.get("methodology_caveats", [])),
    )

    if run_self_critique:
        logger.info("running self-critique pass...")
        catalog = _run_self_critique(catalog)
        logger.info(
            "after self-critique: %d methodology caveats",
            len(catalog.get("methodology_caveats", [])),
        )

    # Wrap with metadata
    output = {
        "schema_version": "v1",
        "source_extraction": str(extraction_path.relative_to(_REPO_ROOT)),
        "row_count": len(rows),
        "creator_count": len(stats["per_creator_count"]),
        "stats": stats,
        "catalog": catalog,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("wrote %s", output_path)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--extraction", type=Path, default=_EXTRACTION_PATH)
    parser.add_argument("--output", type=Path, default=_OUTPUT_PATH)
    parser.add_argument("--no-self-critique", action="store_true", help="Skip second LLM critique pass.")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    return _run(
        extraction_path=args.extraction,
        output_path=args.output,
        run_self_critique=not args.no_self_critique,
    )


if __name__ == "__main__":
    raise SystemExit(main())
