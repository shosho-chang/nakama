"""LLM creative planner — normalized transcript → beats with layout + B-roll proposals.

Loads (at runtime):
- docs/design-system.md (brand context)
- agents/foundry/STYLE.md (editorial rubric)
- agents/foundry/guardrails.yaml (allow/deny lists)
- agents/foundry/examples/ (cold-start: NOT loaded in Phase 1; gate len(examples) >= 5)

LLM contract: every beat must include start_quote / end_quote that are
exact substring copies of the supplied normalized transcript. Paraphrasing
or rewriting characters is forbidden — beat_aligner will hard-fail on miss.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml

from agents.foundry.schemas.storyboard import Beat
from shared.anthropic_client import get_client

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PROMPT_TEMPLATE = Path(__file__).parent / "prompts" / "broll_planner.md"
_DESIGN_SYSTEM = _REPO_ROOT / "docs" / "design-system.md"
_STYLE = Path(__file__).parent / "STYLE.md"
_GUARDRAILS = Path(__file__).parent / "guardrails.yaml"
_EXAMPLES_INDEX = Path(__file__).parent / "examples" / "_index.yaml"

_MODEL = "claude-opus-4-7"
_MAX_TOKENS = 4000  # ~25 beats x ~80 tokens = 2000 output; 4000 for safety


def _load_examples() -> list[dict]:
    index = yaml.safe_load(_EXAMPLES_INDEX.read_text(encoding="utf-8"))
    entries = index.get("examples", []) if index else []
    if len(entries) < 5:
        return []
    result: list[dict] = []
    for entry in entries:
        path = Path(__file__).parent / "examples" / entry["file"]
        if path.exists():
            result.append(yaml.safe_load(path.read_text(encoding="utf-8")))
    return result


def _build_prompt(flat_text: str, episode_meta: dict) -> str:
    design_system = (
        _DESIGN_SYSTEM.read_text(encoding="utf-8") if _DESIGN_SYSTEM.exists() else "(not found)"
    )
    style = _STYLE.read_text(encoding="utf-8") if _STYLE.exists() else ""
    guardrails = _GUARDRAILS.read_text(encoding="utf-8") if _GUARDRAILS.exists() else ""
    examples = _load_examples()

    examples_block = ""
    if examples:
        examples_block = "## Few-shot examples\n\n```yaml\n"
        examples_block += yaml.dump(examples, allow_unicode=True, default_flow_style=False)
        examples_block += "```\n"

    template = _PROMPT_TEMPLATE.read_text(encoding="utf-8")
    return template.format(
        design_system=design_system,
        style=style,
        guardrails=guardrails,
        examples_block=examples_block,
        episode_meta=yaml.dump(episode_meta, allow_unicode=True, default_flow_style=False),
        transcript=flat_text,
    )


def _extract_beats(response_text: str) -> list[dict]:
    """Parse YAML beat array from LLM response (handles ```yaml ... ``` fencing)."""
    m = re.search(r"```(?:yaml)?\s*\n(.*?)\n```", response_text, re.DOTALL)
    content = m.group(1) if m else response_text
    parsed = yaml.safe_load(content)
    if not isinstance(parsed, list):
        raise ValueError(f"expected YAML list of beats, got {type(parsed).__name__}")
    return parsed


def plan_episode(flat_text: str, episode_meta: dict) -> list[Beat]:
    """Call Claude Opus to produce storyboard beats for the episode.

    Returns a list of Beat objects with timing=None (filled later by beat_aligner).

    Raises:
        ValueError: if LLM response cannot be parsed as a valid beat list after retries.
    """
    client = get_client()
    prompt = _build_prompt(flat_text, episode_meta)

    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            response = client.messages.create(
                model=_MODEL,
                max_tokens=_MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text
            beats_data = _extract_beats(raw)
            beats = [Beat.model_validate(b) for b in beats_data]
            logger.info("planner produced %d beats (attempt %d)", len(beats), attempt + 1)
            return beats
        except Exception as exc:
            last_exc = exc
            logger.warning("planner attempt %d/3 failed: %s", attempt + 1, exc)

    raise ValueError(f"plan_episode failed after 3 attempts: {last_exc}") from last_exc
