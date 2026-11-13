"""Per-image Title + Thumbnail feature extraction (ADR-033 D4 / playbook v1 Phase 1-2).

Reads reference thumbnail images from ``E:/Thumbnail-example/{creator}/*.jpg`` (or
override via ``--corpus-root``), sends each to Sonnet 4.6 vision with a structured
extraction prompt, and writes one JSON row per image to
``data/thumbnail_reference_extraction_v1.json``.

Idempotent: skips rows whose ``id`` is already present in the output file.
Batch-safe: per-image errors are logged but do not abort the run.

Usage:

    python -m scripts.extract_thumbnail_features                 # all creators
    python -m scripts.extract_thumbnail_features --creator "Ali Abdaal"
    python -m scripts.extract_thumbnail_features --limit 5       # smoke test
    python -m scripts.extract_thumbnail_features --dry-run       # build prompts, don't call LLM

Output schema: data/thumbnail_reference_extraction_schema_v1.json (v1).
Design notes: docs/research/2026-05-26-thumbnail-playbook-design.md.
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

# Load .env: prefer worktree-local, fall back to sibling nakama repo (worktrees
# share credentials with main repo per CLAUDE.md). Order is intentional — local
# overrides win if 修修 ever pins a worktree-specific token.
for _env_candidate in (_REPO_ROOT / ".env", Path("E:/nakama/.env")):
    if _env_candidate.exists():
        load_dotenv(_env_candidate)
        break

from shared.anthropic_client import ask_claude_multi  # noqa: E402

logger = logging.getLogger(__name__)

_DEFAULT_CORPUS_ROOT = Path("E:/Thumbnail-example")
_DEFAULT_OUTPUT_PATH = _REPO_ROOT / "data" / "thumbnail_reference_extraction_v1.json"
_SCHEMA_PATH = _REPO_ROOT / "data" / "thumbnail_reference_extraction_schema_v1.json"
_EMOTIONS_YML_PATH = _REPO_ROOT / "prompts" / "thumbnail" / "emotions.yml"

_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 4096


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


_SCHEMA_INLINE = json.loads(
    (_REPO_ROOT / "data" / "thumbnail_reference_extraction_schema_v1.json").read_text(
        encoding="utf-8"
    )
)


_FEW_SHOT_EXAMPLE = {
    "schema_version": "v1",
    "id": "ali_abdaal_001",
    "creator": "Ali Abdaal",
    "route": "youtube",
    "image_path": "E:/Thumbnail-example/Ali Abdaal/5 Easy Ways to Become More Self-Disciplined.jpg",
    "title": "5 Easy Ways to Become More Self-Disciplined",
    "title_analysis": {
        "language": "en",
        "word_count": 9,
        "char_count": 43,
        "structure_primary": "numbered-listicle",
        "structure_secondary_tags": [
            "effort-reduction-promise",
            "identity-aspiration",
            "self-improvement-domain",
        ],
        "implied_promise": "5 low-effort techniques that transform you into a more disciplined person.",
        "promise_concreteness_1to5": 3,
        "specificity_markers": ["5"],
        "click_drivers": [
            {
                "framework": "Loewenstein information gap",
                "mechanism": "Number 5 sets a finite list — viewer can't predict the 5 items so curiosity-gap opens.",
            },
            {
                "framework": "Cognitive ease",
                "mechanism": "'Easy' explicitly lowers perceived investment cost — friction-reduction heuristic.",
            },
            {
                "framework": "Identity-based hook",
                "mechanism": "'Become More Self-Disciplined' targets aspirational identity rather than transactional outcome.",
            },
        ],
        "hook_emotion": "aspirational-anticipation",
    },
    "thumbnail_analysis": {
        "composition": {
            "layout_archetype": "subject-with-overlay-metaphor",
            "face_present": True,
            "face_crop": "medium",
            "facial_expression_inferred": "serious",
            "facial_expression_confidence": "medium",
            "props": ["headphones", "notebook", "tablet", "desk"],
        },
        "typography": {
            "text_overlay_present": True,
            "overlay_text": ["Discipline"],
            "text_word_count": 1,
            "text_relative_size": "medium",
            "font_style": "rounded sans-serif medium-weight",
            "highlight_pattern": "white-pill-with-green-toggle",
        },
        "color": {
            "dominant": "#FFFFFF",
            "secondary": "#34C759",
            "background_strategy": "studio-clean-productivity-scene",
            "contrast_pattern": "high-luminance",
            "pattern_interrupt_potential": "high",
        },
        "visual_hierarchy": [
            "green toggle widget",
            "Discipline pill text",
            "subject face + headphones",
            "desk objects",
        ],
        "negative_space_ratio_1to5": 3,
        "second_look_reward": "The toggle is in the ON position — turning the abstract concept into a switchable state.",
        "click_drivers": [
            {
                "framework": "Pattern interrupt",
                "mechanism": "iOS UI widget overlaid on real-world scene is visually incongruous — brain flags inconsistency for second look.",
            },
            {
                "framework": "Cognitive ease",
                "mechanism": "Toggle metaphor compresses 'become disciplined' into one-tap action — radical friction-reduction.",
            },
        ],
    },
    "joint_analysis": {
        "title_thumb_relationship": "redundant-reinforcing",
        "click_driver_synthesis": "Effort-reduction × concrete-listicle × identity-aspiration unified through toggle metaphor — title promises easy ways, thumbnail visualises 'easy' as a flippable switch.",
        "implied_outcome_clarity_1to5": 4,
        "novelty_vs_familiarity_balance": "novel-frame-familiar-topic",
    },
    "shosho_brand_fit": {
        "grade": "A",
        "rationale": "Effort-reduction × identity-aspiration combo translates cleanly to health/longevity (e.g. '5 個輕鬆習慣讓你睡得更好'). Toggle metaphor is concept-portable.",
        "chinese_adaptation_example": "5 個簡單習慣 讓你的睡眠品質開啟",
        "adaptation_notes": [
            "中文 listicle 開頭加「個 / 種」量詞會更自然",
            "「Easy」對應「簡單 / 輕鬆 / 微」皆可，但健康主題避免「無痛」過度承諾",
            "可以替換 toggle 為心率圖 / 血糖儀指示燈等健康相關 UI 元件",
        ],
        "antipattern_warnings": [
            "別直譯「Become More Self-Disciplined」為「變得更自律」— 中文語境下顯得說教；改成「養成 / 開啟 / 進入 X 狀態」更柔"
        ],
    },
    "self_critique": {
        "potential_overfit_risks": [
            "Toggle metaphor is heavily Ali Abdaal-coded; may read as creator quirk rather than transferable archetype.",
            "'Easy' framing in health content carries trust risk — evidence-based 修修 audience may distrust oversimplification.",
        ],
        "uncertain_categorizations": [
            "Facial expression between 'serious' and 'thoughtful' — picked serious because eyes are direct rather than reflective."
        ],
        "needs_review": False,
    },
}


_SYSTEM_PROMPT = f"""You are a YouTube thumbnail + title analyst building a structured pattern-extraction corpus.

The user is 修修, a Traditional Chinese (Taiwan / Hong Kong) Health & Wellness / Longevity content creator. The corpus is 140 high-CTR thumbnails from 4 creators (Ali Abdaal, Alex Hormozi, Cleo Abram, Jeff Su) — feeding a Title × Thumbnail Playbook for future brainstorm calls.

## Your job

For ONE thumbnail (image attached) + ONE title (provided), output a single JSON object matching the v1 schema below. Every field listed as `required` in the schema MUST be present. Every field with an enum MUST use one of the enum values verbatim. Ad-hoc field names or invented enum values are rejected.

## Schema (v1, authoritative)

```json
{json.dumps(_SCHEMA_INLINE, ensure_ascii=False, indent=2)}
```

## Few-shot example (fully-conformant row)

```json
{json.dumps(_FEW_SHOT_EXAMPLE, ensure_ascii=False, indent=2)}
```

## CRITICAL — DO NOT CROSS ENUM SPACES

`title_analysis.structure_primary` and `click_drivers[].framework` are TWO DIFFERENT ENUM SPACES. Some values LOOK similar but they live in different fields:

- `structure_primary` ∈ {{numbered-listicle, how-to, question-curiosity-gap, contrarian-reversal, authority-research, cost-risk-reframe, time-age-constraint, counter-intuitive-specific, story-confession, comparison-vs, exclusive-secret, year-anchor, duration-promise}}. THESE ARE TITLE STRUCTURE LABELS.
- `framework` ∈ {{Loewenstein information gap, MrBeast PVP, Cialdini authority, Cialdini social-proof, Cialdini scarcity, Cialdini commitment-consistency, Cialdini reciprocity, Cialdini liking, Identity-based hook, Loss aversion, Specificity bias, Pattern interrupt, Face emotion contagion, Numerical anchor, Familiarity scaffolding, Mere-exposure, Cognitive ease, Status signaling, Insider knowledge frame}}. THESE ARE COGNITIVE FRAMEWORKS.

DO NOT put `"year-anchor"` in `framework` — it's a structure label. DO NOT put `"identity-based hook"` in `structure_primary` — it's a framework. If you're unsure where a value belongs, check which list it appears in.

## Critical reminders

1. `click_drivers[].framework` MUST be one of the framework enum values listed above. NO invented names like "CURIOSITY_GAP" or "EFFORT_REDUCTION". NO structure labels.
2. `facial_expression_inferred` MUST use the 7 emotion keys from `prompts/thumbnail/emotions.yml`: excited / thoughtful / surprised / explaining / serious / laughing / pointing — or "n/a" if no face.
3. `title_analysis.structure_primary` MUST be one of the schema enum values.
4. `thumbnail_analysis.color.contrast_pattern` MUST be one of the 6 enum values.
5. `joint_analysis.title_thumb_relationship` MUST be one of the 6 enum values.
6. `shosho_brand_fit.grade` MUST be one of S / A / B / C / F.
7. `shosho_brand_fit.chinese_adaptation_example` must be a real Traditional Chinese title 修修 could actually publish (or "" if grade=F).
8. `self_critique.potential_overfit_risks` should list ≥1 honest caveats. Examples: "creator-specific quirk", "English-only convention", "post-hoc rationalization".

## Mechanism specificity

For each `click_drivers[].mechanism`, explain HOW that framework manifests in THIS exact title/thumbnail — not generic theory. Bad: "leverages curiosity". Good: "the number 5 commits the LLM to a finite enumeration the viewer can't predict — Loewenstein information gap activates because viewer wants to close the list".

## Output

Output ONLY the JSON object. No preamble, no markdown fence, no commentary. The first character must be `{{` and last must be `}}`."""


def _build_user_message(image_path: Path, title: str, row_id: str, creator: str) -> list[dict]:
    """Build the user message: image + title metadata + JSON schema reminder."""
    suffix = image_path.suffix.lower().lstrip(".")
    media_type = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(
        suffix, "image/jpeg"
    )
    payload = base64.standard_b64encode(image_path.read_bytes()).decode("ascii")
    return [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": payload,
            },
        },
        {
            "type": "text",
            "text": (
                f"id: {row_id}\n"
                f"creator: {creator}\n"
                f"title: {title}\n"
                f"route: youtube\n"
                f"image_path: {image_path}\n\n"
                "Output the v1 JSON object per schema."
            ),
        },
    ]


# ---------------------------------------------------------------------------
# Corpus iteration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ImageJob:
    creator: str
    title: str
    image_path: Path
    row_id: str


_CREATOR_SLUG_OVERRIDES = {
    "Alex Hormozi": "alex_hormozi",
    "Ali Abdaal": "ali_abdaal",
    "Cleo Abram": "cleo_abram",
    "Jeff Su": "jeff_su",
}


def _slug_for_creator(creator: str) -> str:
    if creator in _CREATOR_SLUG_OVERRIDES:
        return _CREATOR_SLUG_OVERRIDES[creator]
    return re.sub(r"[^a-z0-9]+", "_", creator.lower()).strip("_")


def _iter_jobs(corpus_root: Path, creator_filter: str | None) -> Iterable[_ImageJob]:
    """Walk the corpus root and yield extraction jobs."""
    if not corpus_root.exists():
        raise FileNotFoundError(f"corpus root not found: {corpus_root}")
    for creator_dir in sorted(corpus_root.iterdir()):
        if not creator_dir.is_dir():
            continue
        if creator_filter and creator_dir.name != creator_filter:
            continue
        creator = creator_dir.name
        slug = _slug_for_creator(creator)
        images = sorted(
            p
            for p in creator_dir.iterdir()
            if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png"}
        )
        for idx, image_path in enumerate(images, start=1):
            title = image_path.stem
            row_id = f"{slug}_{idx:03d}"
            yield _ImageJob(creator=creator, title=title, image_path=image_path, row_id=row_id)


# ---------------------------------------------------------------------------
# Output handling
# ---------------------------------------------------------------------------


def _load_existing(output_path: Path) -> dict[str, dict]:
    """Load previously extracted rows keyed by id (idempotency)."""
    if not output_path.exists():
        return {}
    try:
        data = json.loads(output_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.warning("existing output is invalid JSON, ignoring: %s", exc)
        return {}
    if isinstance(data, list):
        return {row["id"]: row for row in data if isinstance(row, dict) and "id" in row}
    if isinstance(data, dict) and "rows" in data:
        return {row["id"]: row for row in data["rows"] if isinstance(row, dict) and "id" in row}
    return {}


def _write_output(output_path: Path, rows_by_id: dict[str, dict]) -> None:
    """Persist rows sorted by id for deterministic diffs."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows_by_id.values(), key=lambda r: r.get("id", ""))
    payload = {
        "schema_version": "v1",
        "schema_path": "data/thumbnail_reference_extraction_schema_v1.json",
        "rows": rows,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _parse_llm_json(text: str) -> dict:
    """Strip optional markdown fence and parse JSON."""
    stripped = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, re.DOTALL)
    if fence:
        stripped = fence.group(1)
    return json.loads(stripped)


# Lightweight inline schema validator — avoid pulling in jsonschema dep for a
# one-off analysis script. Only checks the enum constraints + top-level required
# fields that LLM most plausibly drifts on.
_REQUIRED_TOP = {
    "schema_version",
    "id",
    "creator",
    "route",
    "image_path",
    "title",
    "title_analysis",
    "thumbnail_analysis",
    "joint_analysis",
    "shosho_brand_fit",
    "self_critique",
}
_EMOTION_ENUM = {
    "excited",
    "thoughtful",
    "surprised",
    "explaining",
    "serious",
    "laughing",
    "pointing",
    "n/a",
}
_GRADE_ENUM = {"S", "A", "B", "C", "F"}
_CONTRAST_ENUM = {
    "high-saturation",
    "high-luminance",
    "monochrome-mute",
    "duotone",
    "split-warm-cool",
    "low-contrast-cinematic",
}
_RELATIONSHIP_ENUM = {
    "redundant-reinforcing",
    "complementary-gap",
    "thumb-amplifies-title",
    "title-grounds-visual-mystery",
    "contradictory-tension",
    "minimal-decoupled",
}
_STRUCTURE_ENUM = {
    "numbered-listicle",
    "how-to",
    "question-curiosity-gap",
    "contrarian-reversal",
    "authority-research",
    "cost-risk-reframe",
    "time-age-constraint",
    "counter-intuitive-specific",
    "story-confession",
    "comparison-vs",
    "exclusive-secret",
    "year-anchor",
    "duration-promise",
}
_FRAMEWORK_ENUM = {
    "Loewenstein information gap",
    "MrBeast PVP",
    "Cialdini authority",
    "Cialdini social-proof",
    "Cialdini scarcity",
    "Cialdini commitment-consistency",
    "Cialdini reciprocity",
    "Cialdini liking",
    "Identity-based hook",
    "Loss aversion",
    "Specificity bias",
    "Pattern interrupt",
    "Face emotion contagion",
    "Numerical anchor",
    "Familiarity scaffolding",
    "Mere-exposure",
    "Cognitive ease",
    "Status signaling",
    "Insider knowledge frame",
}


def _validate_row(row: dict) -> list[str]:
    """Return list of violation messages; empty list means pass."""
    errs: list[str] = []
    missing = _REQUIRED_TOP - set(row.keys())
    if missing:
        errs.append(f"missing top-level fields: {sorted(missing)}")
    grade = row.get("shosho_brand_fit", {}).get("grade")
    if grade not in _GRADE_ENUM:
        errs.append(f"invalid grade: {grade!r}")
    structure = row.get("title_analysis", {}).get("structure_primary")
    if structure not in _STRUCTURE_ENUM:
        errs.append(f"invalid structure_primary: {structure!r}")
    contrast = row.get("thumbnail_analysis", {}).get("color", {}).get("contrast_pattern")
    if contrast not in _CONTRAST_ENUM:
        errs.append(f"invalid contrast_pattern: {contrast!r}")
    relationship = row.get("joint_analysis", {}).get("title_thumb_relationship")
    if relationship not in _RELATIONSHIP_ENUM:
        errs.append(f"invalid title_thumb_relationship: {relationship!r}")
    expression = (
        row.get("thumbnail_analysis", {}).get("composition", {}).get("facial_expression_inferred")
    )
    if expression not in _EMOTION_ENUM:
        errs.append(f"invalid facial_expression_inferred: {expression!r}")
    for path, drivers in (
        ("title_analysis", row.get("title_analysis", {}).get("click_drivers", [])),
        ("thumbnail_analysis", row.get("thumbnail_analysis", {}).get("click_drivers", [])),
    ):
        for i, d in enumerate(drivers):
            fw = d.get("framework") if isinstance(d, dict) else None
            if fw not in _FRAMEWORK_ENUM:
                errs.append(f"{path}.click_drivers[{i}].framework invalid: {fw!r}")
    return errs


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _run(
    corpus_root: Path,
    output_path: Path,
    creator_filter: str | None,
    limit: int | None,
    dry_run: bool,
) -> int:
    jobs = list(_iter_jobs(corpus_root, creator_filter))
    if limit is not None:
        jobs = jobs[:limit]
    logger.info("planned %d extraction jobs (creator_filter=%s)", len(jobs), creator_filter)

    existing = _load_existing(output_path)
    logger.info("existing rows in output: %d", len(existing))

    done = 0
    failed = 0
    for job in jobs:
        if job.row_id in existing:
            logger.info("skip %s (already extracted)", job.row_id)
            continue
        user_parts = _build_user_message(job.image_path, job.title, job.row_id, job.creator)
        if dry_run:
            sample_text = next((p["text"] for p in user_parts if p.get("type") == "text"), "")
            logger.info("[dry-run] %s — would send: %s", job.row_id, sample_text[:120])
            continue
        try:
            raw = ask_claude_multi(
                messages=[{"role": "user", "content": user_parts}],
                system=_SYSTEM_PROMPT,
                model=_MODEL,
                max_tokens=_MAX_TOKENS,
            )
            row = _parse_llm_json(raw)
        except Exception as exc:
            logger.error("FAIL %s: %s", job.row_id, exc)
            failed += 1
            continue
        if row.get("id") != job.row_id:
            logger.warning(
                "LLM returned id=%r, expected %r — overriding to expected",
                row.get("id"),
                job.row_id,
            )
            row["id"] = job.row_id
        violations = _validate_row(row)
        if violations:
            logger.error("VALIDATE FAIL %s: %s", job.row_id, "; ".join(violations))
            failed += 1
            continue
        existing[job.row_id] = row
        _write_output(output_path, existing)  # persist after each row (resumability)
        done += 1
        logger.info("OK   %s (%d/%d this run)", job.row_id, done, len(jobs))

    logger.info(
        "=== done: %d extracted, %d failed, %d skipped existing ===",
        done,
        failed,
        len(jobs) - done - failed,
    )
    return 0 if failed == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--corpus-root", type=Path, default=_DEFAULT_CORPUS_ROOT)
    parser.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT_PATH)
    parser.add_argument(
        "--creator", type=str, default=None, help="Filter to one creator (folder name)."
    )
    parser.add_argument("--limit", type=int, default=None, help="Max jobs this run.")
    parser.add_argument("--dry-run", action="store_true", help="Build prompts, skip LLM calls.")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    return _run(
        corpus_root=args.corpus_root,
        output_path=args.output,
        creator_filter=args.creator,
        limit=args.limit,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())
