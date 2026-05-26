"""Smoke eval — does Sonnet 4.6 vision actually pick up 修修's taste?

ADR-033 D4.a panel gate (P1). Pre-PR4 merge requirement: run this against
ONE known past project, manually inspect the 3 generated ideas, decide
whether to ship or fall back to a hand-written style rubric.

Workflow:

1. Make sure ``Attachments/cutouts/reference/youtube/{mine,peers}/`` is
   populated with 5+ images each (panel P10: capped at 30 total).
2. Pick a known past project (one 修修 already published with a thumbnail
   he liked).
3. ``python -m scripts.eval_thumbnail_reference_taste 肌酸的妙用 [--brief BRIEF_FILE]``
4. Read the 3 ideas. Do they read as plausible variants in 修修's voice?
   - YES → ship PR4-A as is.
   - NO  → generate ``prompts/thumbnail/style_rubric.md`` (a one-page rubric
     captured from the reference set by vision LLM + 修修 review) and have
     the brainstorm endpoint attach it alongside the references.

The script does NOT write to vault frontmatter — it only calls the LLM and
prints. Safe to run repeatedly.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from shared.anthropic_client import ask_claude_multi  # noqa: E402
from shared.config import get_vault_path  # noqa: E402
from shared.llm_router import get_model  # noqa: E402
from shared.project_indexer import ProjectIndexer, normalize_slug  # noqa: E402
from shared.thumbnail_idea import parse_ideas_batch  # noqa: E402
from thousand_sunny.routers.bridge_project_thumbnails import (  # noqa: E402
    _brainstorm_user_message,
    _load_brainstorm_prompt,
    _reference_images_for,
)

logger = logging.getLogger(__name__)


async def _run(project_slug: str) -> int:
    vault = get_vault_path()
    indexer = ProjectIndexer(vault_root=vault)
    slug = normalize_slug(project_slug)

    entry = indexer.get(slug)
    if entry.content_type != "youtube":
        print(
            f"smoke eval supports content_type=youtube; project is {entry.content_type}",
            file=sys.stderr,
        )
        return 2

    raw_fm = entry.raw_frontmatter if isinstance(entry.raw_frontmatter, dict) else {}
    references = _reference_images_for("youtube")
    if len(references) < 3:
        print(
            f"WARN: only {len(references)} reference images found at "
            f"{vault / 'Attachments' / 'cutouts' / 'reference' / 'youtube'}. "
            "Recommend ≥5 mine + ≥10 peers before relying on the inferred taste.",
            file=sys.stderr,
        )

    user_parts = _brainstorm_user_message(
        title_candidates=list(entry.title_candidates),
        one_sentence=str(raw_fm.get("one_sentence") or ""),
        search_topic=str(raw_fm.get("search_topic") or ""),
        reference_images=references,
    )
    system_prompt = _load_brainstorm_prompt()
    messages = [{"role": "user", "content": user_parts}]
    model = get_model(agent="bridge", task="thumbnail_brainstorm")

    print(
        f"=== smoke eval: project={slug} references={len(references)} model={model} ===",
        file=sys.stderr,
    )
    response_text = ask_claude_multi(
        messages,
        system=system_prompt,
        model=model,
        max_tokens=2048,
    )

    print("\n--- raw LLM response ---", file=sys.stderr)
    print(response_text)
    print("\n--- parsed ideas (structured) ---", file=sys.stderr)
    try:
        ideas = parse_ideas_batch(response_text)
    except Exception as exc:  # noqa: BLE001
        print(f"PARSE FAILED: {exc}", file=sys.stderr)
        print(
            "→ ship PR4-A blocked. Either tune the prompt, or fall back to "
            "style_rubric.md (per ADR-033 D4.a).",
            file=sys.stderr,
        )
        return 3

    for i, idea in enumerate(ideas, start=1):
        print(f"\nIdea {i}: {idea}")

    print(
        "\n--- now manual inspection ---\n"
        "  1. Read the 3 ideas. Do they sound like 修修?\n"
        "  2. Are they actually distinct on ≥2 axes (panel P1)?\n"
        "  3. Did the model pick reasonable emotions from the closed enum?\n"
        "\n  If YES → ship PR4-A.\n"
        "  If NO  → write prompts/thumbnail/style_rubric.md and update the\n"
        "          brainstorm endpoint to attach it.",
        file=sys.stderr,
    )
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("project_slug", help="title of a known past project (Projects/{slug}.md)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    return asyncio.run(_run(args.project_slug))


if __name__ == "__main__":
    raise SystemExit(main())
