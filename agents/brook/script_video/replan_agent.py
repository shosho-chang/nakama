"""LLM tool-call agent that re-plans a single beat (ADR-038 §D3 + §D6).

Renders the storyboard to the LLM using ``[beat:N]`` index prefixes, exposes
the 11 ``BeatEdit`` ops as Anthropic tools, runs a bounded tool loop, and
returns the parsed list of ``BeatEdit`` instances for ``beat_editor.apply_edits``
to consume.

Hard caps (do not regress):

- ``MAX_TOOL_ITERATIONS = 5`` — agent loop terminates after at most 5
  request/response cycles, regardless of stop_reason.
- ``TOTAL_TOKEN_BUDGET = 20000`` — running sum of (input + output) tokens
  across iterations. Checked *before* each tool call so we never start a 6th
  iteration once we've already burned through the budget.
- ``tool_choice = {"type": "any"}`` — every iteration must produce at least
  one tool call (no free-form prose drift).

This module owns the LLM orchestration only. ``beat_editor`` owns the engine.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import TypeAdapter, ValidationError

from agents.brook.script_video.beat_editor import BeatEdit
from shared.llm import ask_with_tools

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 5
TOTAL_TOKEN_BUDGET = 20_000


# ── tool schemas (one per BeatEdit variant; mirrors beat_editor.py) ─────────


def _tool_defs() -> list[dict[str, Any]]:
    """Anthropic tool definitions mirroring the 11 ``BeatEdit`` ops.

    Schemas mirror the Pydantic models exactly so ``TypeAdapter[BeatEdit]``
    validates the round-trip without coercion.
    """
    beat_id_schema = {
        "anyOf": [
            {"type": "integer"},
            {"type": "string", "pattern": r"^\[beat:\d+\]$"},
        ],
        "description": "Beat reference: integer beat_id or '[beat:N]' (D6).",
    }
    return [
        {
            "name": "replace_quote",
            "description": (
                "Replace exact substring on a beat's start_quote or end_quote. "
                "old_quote MUST match the current start_quote or end_quote of "
                "the beat; otherwise the edit is rejected. Use this for text "
                "anchor refinement instead of any char-offset operation."
            ),
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "beat_id": beat_id_schema,
                    "old_quote": {"type": "string"},
                    "new_quote": {"type": "string"},
                },
                "required": ["beat_id", "old_quote", "new_quote"],
            },
        },
        {
            "name": "set_broll",
            "description": "Replace a beat's broll component + params wholesale.",
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "beat_id": beat_id_schema,
                    "component": {"type": "string"},
                    "params": {"type": "object"},
                },
                "required": ["beat_id", "component", "params"],
            },
        },
        {
            "name": "set_layout",
            "description": "Switch a beat's layout (e.g. full_broll, side_overlay_l).",
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "beat_id": beat_id_schema,
                    "layout": {"type": "string"},
                },
                "required": ["beat_id", "layout"],
            },
        },
        {
            "name": "set_transition",
            "description": "Set or clear in/out transitions on a beat's broll spec.",
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "beat_id": beat_id_schema,
                    "in_transition": {"type": ["string", "null"]},
                    "out_transition": {"type": ["string", "null"]},
                },
                "required": ["beat_id"],
            },
        },
        {
            "name": "patch_broll_params",
            "description": (
                "Merge partial_params into existing broll.params (shallow "
                "update). Beat must already have a broll spec."
            ),
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "beat_id": beat_id_schema,
                    "partial_params": {"type": "object"},
                },
                "required": ["beat_id", "partial_params"],
            },
        },
        {
            "name": "set_timing",
            "description": (
                "Manual timing override. Prefer letting realignment via "
                "replace_quote/split_beat refresh timing; only use this when "
                "anchors cannot express the desired bounds."
            ),
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "beat_id": beat_id_schema,
                    "start_seconds": {"type": "number"},
                    "duration_seconds": {"type": "number"},
                },
                "required": ["beat_id", "start_seconds", "duration_seconds"],
            },
        },
        {
            "name": "mark_aroll",
            "description": ("Demote a beat from cutaway to aroll-only (no broll rendered)."),
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"beat_id": beat_id_schema},
                "required": ["beat_id"],
            },
        },
        {
            "name": "split_beat",
            "description": (
                "Split a beat at a quote (semantic anchor, not char offset). "
                "at_quote must occur once inside the beat between start_quote "
                "and end_quote."
            ),
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "beat_id": beat_id_schema,
                    "at_quote": {"type": "string"},
                },
                "required": ["beat_id", "at_quote"],
            },
        },
        {
            "name": "merge_beats",
            "description": "Fuse two adjacent beats into one.",
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "beat_id_a": beat_id_schema,
                    "beat_id_b": beat_id_schema,
                },
                "required": ["beat_id_a", "beat_id_b"],
            },
        },
        {
            "name": "duplicate_broll_from",
            "description": (
                "Copy broll spec (component + params + layout) from source beat to target beat."
            ),
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "target_beat_id": beat_id_schema,
                    "source_beat_id": beat_id_schema,
                },
                "required": ["target_beat_id", "source_beat_id"],
            },
        },
        {
            "name": "restore_previous_render",
            "description": ("Rollback to a previously-rendered mp4 via D2 hash history."),
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "beat_id": beat_id_schema,
                    "run_id": {"type": "string"},
                },
                "required": ["beat_id", "run_id"],
            },
        },
    ]


# ── storyboard rendering ─────────────────────────────────────────────────────


def render_storyboard(storyboard: list[dict[str, Any]]) -> str:
    """Render the storyboard to a Markdown-ish summary for the LLM (D6 syntax).

    Beats are prefixed with ``[beat:N]`` to avoid CJK ``，`` enumerator
    ambiguity (D6). The render is intentionally compact — full beat dicts are
    too noisy; the LLM only needs anchors + broll spec to know what to edit.
    """
    lines: list[str] = []
    for beat in storyboard:
        bid = beat.get("beat_id", "?")
        lines.append(f"[beat:{bid}]")
        lines.append(f"  start_quote: {beat.get('start_quote', '')!r}")
        lines.append(f"  end_quote: {beat.get('end_quote', '')!r}")
        lines.append(f"  layout: {beat.get('layout', '')}")
        lines.append(f"  broll_decision: {beat.get('broll_decision', '')}")
        broll = beat.get("broll")
        if broll:
            lines.append(f"  broll.component: {broll.get('component', '')}")
            params_json = json.dumps(broll.get("params", {}), ensure_ascii=False)
            lines.append(f"  broll.params: {params_json}")
        timing = beat.get("timing") or {}
        if timing:
            lines.append(f"  timing: start={timing.get('start')} duration={timing.get('duration')}")
        lines.append("")
    return "\n".join(lines)


# ── tool-call extraction ─────────────────────────────────────────────────────


_TOOL_NAME_TO_OP = {
    "replace_quote": "replace_quote",
    "set_broll": "set_broll",
    "set_layout": "set_layout",
    "set_transition": "set_transition",
    "patch_broll_params": "patch_broll_params",
    "set_timing": "set_timing",
    "mark_aroll": "mark_aroll",
    "split_beat": "split_beat",
    "merge_beats": "merge_beats",
    "duplicate_broll_from": "duplicate_broll_from",
    "restore_previous_render": "restore_previous_render",
}


def _parse_tool_call(name: str, args: dict[str, Any]) -> BeatEdit:
    """Validate a single Anthropic tool_use block into a typed BeatEdit."""
    op = _TOOL_NAME_TO_OP.get(name)
    if op is None:
        raise ValueError(f"unknown tool name {name!r}; expected one of {sorted(_TOOL_NAME_TO_OP)}")
    payload = {"op": op, **args}
    return TypeAdapter(BeatEdit).validate_python(payload)


def _extract_tool_calls(message: Any) -> list[tuple[str, str, dict[str, Any]]]:
    """Return list of ``(tool_use_id, name, input_dict)`` from a Claude Message."""
    calls: list[tuple[str, str, dict[str, Any]]] = []
    for block in getattr(message, "content", []) or []:
        block_type = getattr(block, "type", None) or (
            block.get("type") if isinstance(block, dict) else None
        )
        if block_type != "tool_use":
            continue
        tool_use_id = getattr(block, "id", None) or block.get("id")  # type: ignore[union-attr]
        name = getattr(block, "name", None) or block.get("name")  # type: ignore[union-attr]
        args = getattr(block, "input", None) or block.get("input")  # type: ignore[union-attr]
        calls.append((tool_use_id, name, args or {}))
    return calls


def _usage_total(message: Any) -> int:
    usage = getattr(message, "usage", None)
    if usage is None:
        return 0
    inp = getattr(usage, "input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    return int(inp) + int(out)


# ── system prompt ────────────────────────────────────────────────────────────


_SYSTEM_PROMPT = """You are the foundry beat editor. The user has flagged a single beat for re-plan.

You operate via TOOLS only — do not output free-form prose. Each iteration must
emit at least one tool call. Once you are satisfied that the beat reflects the
user's note, stop calling tools.

CRITICAL RULES:
- All text edits use replace_quote(beat_id, old_quote, new_quote). Char-offset
  operations are forbidden; LLMs cannot count Mandarin chars reliably.
- Refer to beats as `[beat:N]` (with the brackets). Bare `[N]` is ambiguous in
  Chinese contexts and will be rejected.
- Only emit edits that genuinely change something. Do not echo the existing
  state back as a no-op edit.
- Stay scoped to the single beat the user is re-planning. Touching adjacent
  beats is allowed only via split_beat / merge_beats / duplicate_broll_from.
"""


# ── main entrypoint ──────────────────────────────────────────────────────────


class ReplanResult:
    """Container for the agent run output."""

    def __init__(
        self,
        edits: list[BeatEdit],
        iterations: int,
        tokens_used: int,
        terminated_reason: str,
    ) -> None:
        self.edits = edits
        self.iterations = iterations
        self.tokens_used = tokens_used
        self.terminated_reason = terminated_reason

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"ReplanResult(edits={len(self.edits)}, iterations={self.iterations}, "
            f"tokens={self.tokens_used}, reason={self.terminated_reason!r})"
        )


def run(
    storyboard: list[dict[str, Any]],
    beat_id: int,
    note: str,
    *,
    max_iterations: int = MAX_TOOL_ITERATIONS,
    token_budget: int = TOTAL_TOKEN_BUDGET,
    ask: Any = None,
) -> ReplanResult:
    """Run the tool-call agent against ``beat_id`` with the user's ``note``.

    Args:
        storyboard: full episode storyboard (list of beat dicts).
        beat_id: integer beat_id the user is re-planning.
        note: free-form user note describing what they want changed.
        max_iterations: hard cap on agent loop cycles (default 5).
        token_budget: cumulative (input + output) token cap across iterations
            (default 20k). The loop stops *before* the next tool call once
            the running total has met or exceeded this budget.
        ask: dependency-injected ``ask_with_tools`` callable for tests; defaults
            to :func:`shared.llm.ask_with_tools`.

    Returns:
        ``ReplanResult`` with the parsed edits and run metadata.
    """
    if ask is None:
        ask = ask_with_tools

    storyboard_render = render_storyboard(storyboard)
    user_msg = (
        f"User note for [beat:{beat_id}]:\n{note}\n\nCurrent storyboard:\n{storyboard_render}"
    )
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_msg}]

    tools = _tool_defs()
    edits: list[BeatEdit] = []
    tokens_used = 0
    terminated_reason = "max_iterations"

    for iteration in range(max_iterations):
        if tokens_used >= token_budget:
            terminated_reason = "token_budget_exceeded"
            logger.warning(
                "replan_agent: token budget exhausted (%d >= %d) before iter %d",
                tokens_used,
                token_budget,
                iteration + 1,
            )
            break

        message = ask(
            messages,
            tools,
            system=_SYSTEM_PROMPT,
            tool_choice={"type": "any"},
        )
        tokens_used += _usage_total(message)

        calls = _extract_tool_calls(message)
        if not calls:
            terminated_reason = "no_tool_calls"
            break

        # Append assistant turn (with tool_use blocks) + a synthetic tool_result
        # turn so the next iteration's request is well-formed.
        messages.append({"role": "assistant", "content": message.content})
        tool_results: list[dict[str, Any]] = []
        for tool_use_id, name, args in calls:
            try:
                edit = _parse_tool_call(name, args)
                edits.append(edit)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": "ok",
                    }
                )
            except (ValueError, ValidationError) as exc:
                logger.warning("replan_agent: tool call %s rejected: %s", name, exc)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": f"error: {exc}",
                        "is_error": True,
                    }
                )
        messages.append({"role": "user", "content": tool_results})

        stop_reason = getattr(message, "stop_reason", None)
        if stop_reason == "end_turn":
            terminated_reason = "end_turn"
            break
    else:
        # for-else: loop exhausted without break = hit max_iterations
        terminated_reason = "max_iterations"

    return ReplanResult(
        edits=edits,
        iterations=min(iteration + 1, max_iterations),  # type: ignore[possibly-undefined]
        tokens_used=tokens_used,
        terminated_reason=terminated_reason,
    )
