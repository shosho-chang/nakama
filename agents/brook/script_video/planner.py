"""LLM creative planner — normalized transcript → beats with layout + B-roll proposals.

Loads (at runtime):
- docs/design-system.md (brand context)
- agents/brook/script_video/STYLE.md (editorial rubric)
- agents/brook/script_video/guardrails.yaml (allow/deny lists)
- agents/brook/script_video/examples/ (cold-start: NOT loaded in Phase 1; gate len(examples) >= 5)

LLM contract: every beat must include start_quote / end_quote that are
exact substring copies of the supplied normalized transcript. Paraphrasing
or rewriting characters is forbidden — beat_aligner will hard-fail on miss.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml
from pydantic import ValidationError

from agents.brook.script_video.schemas.storyboard import Beat
from shared.llm import ask

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PROMPT_TEMPLATE = Path(__file__).parent / "prompts" / "broll_planner.md"
_DESIGN_SYSTEM = _REPO_ROOT / "docs" / "design-system.md"
_STYLE = Path(__file__).parent / "STYLE.md"
_GUARDRAILS = Path(__file__).parent / "guardrails.yaml"
_EXAMPLES_INDEX = Path(__file__).parent / "examples" / "_index.yaml"

_MODEL = "claude-opus-4-7"
_MAX_TOKENS = 16000  # ~80 tokens/beat; 18min+ episodes exceed the old 4000 cap and truncate mid-YAML


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


def _format_silence_hints(hints: dict | None) -> str:
    """Render storyboard_hints.yaml content into a `<silence_hints>` prompt block.

    Opt-out by design: returns empty string when ``hints`` is falsy or has no
    usable ``speaking_spans`` key, so the prompt is byte-identical to the
    pre-PR-C output when no hints file is present.
    """
    if not hints:
        return ""
    spans = hints.get("speaking_spans") or []
    if not spans:
        return ""
    lines = [
        "## Silence-detected speaking spans (prior, advisory)",
        "",
        "The following `(start_s, end_s)` ranges were detected as speaking spans by",
        "`ffmpeg silencedetect` on the raw recording. Treat them as a soft prior for",
        "beat boundaries — prefer ending a beat near a silence boundary when content",
        "allows. They are advisory; do not violate the transcript anchor contract to",
        "satisfy them.",
        "",
        "```yaml",
        "speaking_spans:",
    ]
    for span in spans:
        # Accept either [start, end] list or {start, end} dict shapes.
        if isinstance(span, dict):
            start = span.get("start")
            end = span.get("end")
        else:
            try:
                start, end = span
            except (TypeError, ValueError):
                continue
        if start is None or end is None:
            continue
        lines.append(f"  - [{float(start):.3f}, {float(end):.3f}]")
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def _build_prompt(
    flat_text: str,
    episode_meta: dict,
    hints: dict | None = None,
) -> str:
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

    silence_hints_block = _format_silence_hints(hints)

    template = _PROMPT_TEMPLATE.read_text(encoding="utf-8")
    rendered = template.format(
        design_system=design_system,
        style=style,
        guardrails=guardrails,
        examples_block=examples_block,
        episode_meta=yaml.dump(episode_meta, allow_unicode=True, default_flow_style=False),
        transcript=flat_text,
    )
    # Default opt-out: when no hints provided, the prompt is byte-identical to
    # pre-PR-C output. Hints append after the transcript section.
    if silence_hints_block:
        rendered = rendered.rstrip() + "\n\n" + silence_hints_block
    return rendered


def _extract_beats(response_text: str) -> list[dict]:
    """Parse YAML beat array from LLM response (handles ```yaml ... ``` fencing)."""
    m = re.search(r"```(?:yaml)?\s*\n(.*?)\n```", response_text, re.DOTALL)
    if m:
        content = m.group(1)
    elif response_text.lstrip().startswith("```"):
        # Opening fence with no closing fence — the response was cut off at
        # max_tokens. Parsing the tail would silently drop beats, so fail loud.
        raise ValueError(
            "LLM response has an unterminated ``` fence — output was truncated "
            f"at max_tokens={_MAX_TOKENS}; raise _MAX_TOKENS for longer episodes"
        )
    else:
        content = response_text
    parsed = yaml.safe_load(content)
    if not isinstance(parsed, list):
        raise ValueError(f"expected YAML list of beats, got {type(parsed).__name__}")
    return parsed


def plan_episode(
    flat_text: str,
    episode_meta: dict,
    hints: dict | None = None,
) -> list[Beat]:
    """Call Claude Opus to produce storyboard beats for the episode.

    Args:
        flat_text: normalized transcript (output of ``chinese_normalizer``).
        episode_meta: contents of ``episode.yaml`` (or empty dict).
        hints: optional ``storyboard_hints.yaml`` payload (ADR-038 §D5). When
            provided and non-empty, a `<silence_hints>` advisory block is
            appended to the planner prompt. Absent / falsy → prompt is
            byte-identical to pre-PR-C output (default opt-out).

    Returns:
        List of Beat objects with timing=None (filled later by beat_aligner).

    Raises:
        ValueError: if LLM response cannot be parsed as a valid beat list after retries.
            API/auth 錯誤不走這層 retry，直接 propagate（facade 內已做 transient retry）。
    """
    prompt = _build_prompt(flat_text, episode_meta, hints=hints)

    # Route via shared.llm.ask facade（不直呼 SDK client）— retry / auth
    # dispatch / cost tracking 的 wiring 都在 facade 後面（2026-07-03 架構
    # 審計）。這層 3-attempt loop 只管「回應 parse 得出合法 beat list」；
    # transient API 錯誤由 facade 內 with_retry 處理，這裡不重複 catch —
    # 否則兩層 retry 相乘會把持續性 API 故障放大成數十次呼叫。
    last_exc: Exception | None = None
    for attempt in range(3):
        raw = ask(
            prompt,
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
        )
        try:
            beats_data = _extract_beats(raw)
            beats = [Beat.model_validate(b) for b in beats_data]
            logger.info("planner produced %d beats (attempt %d)", len(beats), attempt + 1)
            return beats
        except (ValueError, yaml.YAMLError, ValidationError) as exc:
            last_exc = exc
            logger.warning("planner attempt %d/3 failed: %s", attempt + 1, exc)

    raise ValueError(f"plan_episode failed after 3 attempts: {last_exc}") from last_exc
