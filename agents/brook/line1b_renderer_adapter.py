"""Adapter: typed Line1bStage1Result → Line-1-shape Stage1Result for legacy renderers.

ADR-027 §Decision 5 mandates that the three existing channel renderers
(``BlogRenderer`` / ``FBRenderer`` / ``IGRenderer``) consume
``Line1bStage1Result.brief`` as their shared input when running in Line 1b
mode.

The legacy renderers (Line 1) read untyped ``stage1.data`` keys such as
``title_candidates``, ``meta_description``, ``episode_type``, ``hooks``,
``origin``, ``turning_point``, ``rebirth``, ``present_action``,
``ending_direction``, ``quotes``. Rewriting them to read the typed 1b shape
would either fork the renderers (rejected: violates ADR-014 "extend, don't
fork") or break the Line 1 path.

Instead this module **adapts** a typed ``Line1bStage1Result`` into a Line-1
shaped dict so the existing renderers can run unchanged. The adapter is a
pure data transform — no LLM call. The trade-off:

- Pros: Zero changes to BlogRenderer / FBRenderer / IGRenderer. Line 1 path
  untouched.
- Cons: 1b's typed ``brief`` (a single canonical narrative) must be packed
  into Line-1's 8-segment skeleton. We treat ``brief`` as the union source
  for hooks / origin / etc., and let the renderer's Stage-2 LLM call extract
  what it needs. This works because Stage-2 prompts already say "use these
  fields as material" — they tolerate the 1b brief shape gracefully.

Future cleanup: if 1b becomes the dominant flow, the renderers can grow
explicit ``mode="line1b"`` branches that read the typed shape directly.
That is **out of scope for PR-5b** (which ships modules only, does not
activate Line 1b in production).
"""

from __future__ import annotations

from agents.brook.repurpose_engine import Stage1Result
from shared.schemas.line1b import Line1bStage1Result


def to_legacy_stage1(
    typed: Line1bStage1Result,
    *,
    episode_type: str = "narrative_journey",
) -> Stage1Result:
    """Pack a typed Line1bStage1Result into a Line-1-shape Stage1Result.

    Args:
        typed: The validated 1b Stage 1 output.
        episode_type: Line-1 ``episode_type`` enum value. Defaults to
            ``narrative_journey`` because interview repurpose maps there
            (see ``ig_renderer.EPISODE_TYPE_CARD_COUNT``). Caller may
            override for atypical interviews (e.g. ``framework`` for an
            interview that surfaces a clear N-step method).

    Returns:
        Stage1Result with ``data`` shaped for legacy renderers AND a nested
        ``line1b`` key carrying the full typed dump for renderers that want
        to consume the brief directly (future explicit-1b branches).
    """
    legacy_quotes = [
        {
            "text": q.original_text or q.text,
            "timestamp": q.timestamp,
            "speaker": q.speaker,
        }
        for q in typed.quotes
    ]

    # Best-effort hooks: take first 3 narrative segments' opening lines.
    # This is intentionally crude — the Stage-2 renderer rewrites hooks
    # from raw material anyway; the legacy field is just a non-empty seed.
    hooks_seed = [seg.text[:120] for seg in typed.narrative_segments[:3]]
    while len(hooks_seed) < 3:
        hooks_seed.append(typed.brief[:120])

    # Map the typed brief into the legacy 8-segment scaffold. All segments
    # receive the SAME brief text — the Stage-2 renderer's LLM call splits
    # it. Avoids us doing LLM-level structuring twice (which is exactly
    # what 2b architecture rejects, see ADR-027 §Decision 5).
    legacy_data = {
        "hooks": hooks_seed,
        "identity_sketch": typed.brief,
        "origin": typed.brief,
        "turning_point": typed.brief,
        "rebirth": typed.brief,
        "present_action": typed.brief,
        "ending_direction": typed.brief,
        "quotes": legacy_quotes,
        "title_candidates": list(typed.titles),
        "meta_description": (
            typed.brief[:180] if len(typed.brief) >= 80 else (typed.brief + " " * 80)[:180]
        ),
        "episode_type": episode_type,
        # Full typed payload for future explicit-1b renderer branches.
        "line1b": typed.model_dump(),
    }

    return Stage1Result(
        data=legacy_data,
        source_repr=f"<line1b adapted, brief {len(typed.brief)} chars>",
    )
