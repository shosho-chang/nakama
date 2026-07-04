"""Pure-functional beat editor — apply LLM-emitted edit ops to a storyboard.

ADR-038 §D3 + §D6. This module is IO-free. The LLM-call orchestration lives
in ``agents/brook/script_video/replan_agent.py``; the engine here only knows how to take
a typed ``BeatEdit`` list and produce a new storyboard.

Design highlights (do not regress):

- **Quote-based semantic anchors only**. ``replace_quote(beat_id, old_quote,
  new_quote)`` is the canonical text edit; ``shift_anchor(direction,
  char_count)`` is explicitly forbidden per ADR-038 §D3 panel review — char
  counting "guaranteed to fail" in Mandarin (full/half-width punctuation, NFC
  vs NFD, LLMs cannot count CJK chars reliably).

- **``[beat:N]`` syntax**. ``BeatEdit.beat_id`` accepts both the canonical
  ``int`` and the ``"[beat:N]"`` string form. Bare ``[N]`` is rejected because
  CJK ``，`` enumerator makes ``[1, 2]`` vs ``[12]`` ambiguous (D6).

- **Preserve approval flags**. ``status.text_approved`` /
  ``status.visual_approved`` on non-edited beats are untouched. On edited
  beats, ``text_approved`` is cleared (the operator has not yet seen the new
  text); ``visual_approved`` is also cleared because broll/layout may have
  changed. ``render_status`` auto-resets via the D2 ``cached_hash`` mismatch
  on the next render — we do NOT manually clear it here.

- **Re-alignment**. After every edit that mutates ``start_quote`` or
  ``end_quote`` (currently ``replace_quote`` + ``split_beat``), we re-run
  ``beat_aligner.align_beat`` on the affected beats to refresh timing +
  srt_line_ids.

- **11 ops** in the discriminated union:
  1. ``replace_quote(beat_id, old_quote, new_quote)``
  2. ``set_broll(beat_id, component, params)``
  3. ``set_layout(beat_id, layout)``
  4. ``set_transition(beat_id, in_transition, out_transition)``
  5. ``patch_broll_params(beat_id, partial_params)``
  6. ``set_timing(beat_id, start_seconds, duration_seconds)``
  7. ``mark_aroll(beat_id)``
  8. ``split_beat(beat_id, at_quote)``
  9. ``merge_beats(beat_id_a, beat_id_b)``
  10. ``duplicate_broll_from(target_beat_id, source_beat_id)``
  11. ``restore_previous_render(beat_id, run_id)``
"""

from __future__ import annotations

import copy
import re
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field

from agents.brook.script_video.beat_aligner import AnchorNotFoundError, align_beat

# ── BeatId parsing ───────────────────────────────────────────────────────────

BeatIdRef = Union[int, str]
"""Accepted forms for a beat reference in a tool call. ``int`` is the canonical
UUID-equivalent (storyboard schema uses ``int``). ``str`` must be ``"[beat:N]"``
(D6 syntax). Bare ``"[N]"`` is rejected."""

_BEAT_REF_RE = re.compile(r"^\[beat:(\d+)\]$")


def _resolve_beat_id(ref: BeatIdRef) -> int:
    """Coerce a BeatIdRef to its int beat_id, accepting both forms.

    Raises ValueError on malformed ``[beat:N]`` or bare ``[N]``.
    """
    if isinstance(ref, int):
        return ref
    if isinstance(ref, str):
        m = _BEAT_REF_RE.match(ref.strip())
        if m is None:
            raise ValueError(
                f"beat_id must be int or '[beat:N]' (got {ref!r}); "
                f"bare '[N]' is rejected per ADR-038 §D6 (CJK comma ambiguity)"
            )
        return int(m.group(1))
    raise TypeError(f"beat_id must be int or str, got {type(ref).__name__}")


# ── BeatEdit discriminated union ─────────────────────────────────────────────


class _BaseEdit(BaseModel):
    model_config = {"extra": "forbid"}


class ReplaceQuote(_BaseEdit):
    op: Literal["replace_quote"] = "replace_quote"
    beat_id: BeatIdRef
    old_quote: str
    new_quote: str


class SetBroll(_BaseEdit):
    op: Literal["set_broll"] = "set_broll"
    beat_id: BeatIdRef
    component: str
    params: dict[str, Any]


class SetLayout(_BaseEdit):
    op: Literal["set_layout"] = "set_layout"
    beat_id: BeatIdRef
    layout: str


class SetTransition(_BaseEdit):
    op: Literal["set_transition"] = "set_transition"
    beat_id: BeatIdRef
    in_transition: str | None = None
    out_transition: str | None = None


class PatchBrollParams(_BaseEdit):
    op: Literal["patch_broll_params"] = "patch_broll_params"
    beat_id: BeatIdRef
    partial_params: dict[str, Any]


class SetTiming(_BaseEdit):
    op: Literal["set_timing"] = "set_timing"
    beat_id: BeatIdRef
    start_seconds: float
    duration_seconds: float


class MarkAroll(_BaseEdit):
    op: Literal["mark_aroll"] = "mark_aroll"
    beat_id: BeatIdRef


class SplitBeat(_BaseEdit):
    op: Literal["split_beat"] = "split_beat"
    beat_id: BeatIdRef
    at_quote: str


class MergeBeats(_BaseEdit):
    op: Literal["merge_beats"] = "merge_beats"
    beat_id_a: BeatIdRef
    beat_id_b: BeatIdRef


class DuplicateBrollFrom(_BaseEdit):
    op: Literal["duplicate_broll_from"] = "duplicate_broll_from"
    target_beat_id: BeatIdRef
    source_beat_id: BeatIdRef


class RestorePreviousRender(_BaseEdit):
    op: Literal["restore_previous_render"] = "restore_previous_render"
    beat_id: BeatIdRef
    run_id: str


BeatEdit = Annotated[
    Union[
        ReplaceQuote,
        SetBroll,
        SetLayout,
        SetTransition,
        PatchBrollParams,
        SetTiming,
        MarkAroll,
        SplitBeat,
        MergeBeats,
        DuplicateBrollFrom,
        RestorePreviousRender,
    ],
    Field(discriminator="op"),
]


# ── helpers ──────────────────────────────────────────────────────────────────


def _find_beat_index(storyboard: list[dict[str, Any]], beat_id: int) -> int:
    for i, beat in enumerate(storyboard):
        if beat.get("beat_id") == beat_id:
            return i
    raise KeyError(f"beat_id {beat_id} not found in storyboard")


def _clear_text_and_visual_approval(beat: dict[str, Any]) -> None:
    """Edited beats lose approval; render_status is reset elsewhere via hash."""
    status = beat.setdefault("status", {})
    status["text_approved"] = False
    status["visual_approved"] = False


def _realign(
    beat: dict[str, Any],
    flat_text: str | None,
    char_to_time: dict[int, float] | None,
    srt_line_id_to_char_range: dict[int, tuple[int, int]] | None,
) -> None:
    """Re-run align_beat on a beat if alignment context was supplied.

    No-op when ``flat_text`` is None (e.g., headless tests that don't care
    about timing). AnchorNotFoundError is propagated — callers (replan_agent
    or CLI) decide how to recover.
    """
    if flat_text is None or char_to_time is None:
        return
    result = align_beat(beat, flat_text, char_to_time, srt_line_id_to_char_range)
    beat["timing"] = result["timing"]
    beat["srt_line_ids"] = result["srt_line_ids"]


def _next_beat_id(storyboard: list[dict[str, Any]]) -> int:
    """Generate a fresh int beat_id (max + 1) for splits."""
    existing = [b.get("beat_id") for b in storyboard if isinstance(b.get("beat_id"), int)]
    return (max(existing) + 1) if existing else 1


# ── per-op handlers (pure functions on storyboard list) ──────────────────────


def _apply_replace_quote(
    storyboard: list[dict[str, Any]],
    edit: ReplaceQuote,
    align_ctx: dict[str, Any],
) -> None:
    bid = _resolve_beat_id(edit.beat_id)
    idx = _find_beat_index(storyboard, bid)
    beat = storyboard[idx]
    matched = False
    if beat.get("start_quote") == edit.old_quote:
        beat["start_quote"] = edit.new_quote
        matched = True
    if beat.get("end_quote") == edit.old_quote:
        beat["end_quote"] = edit.new_quote
        matched = True
    if not matched:
        raise AnchorNotFoundError(beat, edit.old_quote)
    _clear_text_and_visual_approval(beat)
    _realign(beat, **align_ctx)


def _apply_set_broll(
    storyboard: list[dict[str, Any]],
    edit: SetBroll,
    align_ctx: dict[str, Any],
) -> None:
    bid = _resolve_beat_id(edit.beat_id)
    beat = storyboard[_find_beat_index(storyboard, bid)]
    if beat.get("broll") is None:
        beat["broll"] = {
            "render_target": "hyperframes",
            "component": edit.component,
            "params": dict(edit.params),
            "transitions": {"in_transition": None, "out_transition": None},
        }
    else:
        beat["broll"]["component"] = edit.component
        beat["broll"]["params"] = dict(edit.params)
    if beat.get("broll_decision") == "none":
        beat["broll_decision"] = "cutaway"
    _clear_text_and_visual_approval(beat)


def _apply_set_layout(
    storyboard: list[dict[str, Any]],
    edit: SetLayout,
    align_ctx: dict[str, Any],
) -> None:
    bid = _resolve_beat_id(edit.beat_id)
    beat = storyboard[_find_beat_index(storyboard, bid)]
    beat["layout"] = edit.layout
    _clear_text_and_visual_approval(beat)


def _apply_set_transition(
    storyboard: list[dict[str, Any]],
    edit: SetTransition,
    align_ctx: dict[str, Any],
) -> None:
    bid = _resolve_beat_id(edit.beat_id)
    beat = storyboard[_find_beat_index(storyboard, bid)]
    if beat.get("broll") is None:
        beat["broll"] = {
            "render_target": "hyperframes",
            "component": "",
            "params": {},
            "transitions": {"in_transition": None, "out_transition": None},
        }
    beat["broll"].setdefault("transitions", {})
    beat["broll"]["transitions"]["in_transition"] = edit.in_transition
    beat["broll"]["transitions"]["out_transition"] = edit.out_transition
    _clear_text_and_visual_approval(beat)


def _apply_patch_broll_params(
    storyboard: list[dict[str, Any]],
    edit: PatchBrollParams,
    align_ctx: dict[str, Any],
) -> None:
    bid = _resolve_beat_id(edit.beat_id)
    beat = storyboard[_find_beat_index(storyboard, bid)]
    broll = beat.get("broll")
    if broll is None:
        raise ValueError(
            f"patch_broll_params on beat {bid}: beat has no broll spec; use set_broll first"
        )
    params = broll.setdefault("params", {})
    params.update(edit.partial_params)
    _clear_text_and_visual_approval(beat)


def _apply_set_timing(
    storyboard: list[dict[str, Any]],
    edit: SetTiming,
    align_ctx: dict[str, Any],
) -> None:
    bid = _resolve_beat_id(edit.beat_id)
    beat = storyboard[_find_beat_index(storyboard, bid)]
    beat["timing"] = {"start": edit.start_seconds, "duration": edit.duration_seconds}
    _clear_text_and_visual_approval(beat)


def _apply_mark_aroll(
    storyboard: list[dict[str, Any]],
    edit: MarkAroll,
    align_ctx: dict[str, Any],
) -> None:
    bid = _resolve_beat_id(edit.beat_id)
    beat = storyboard[_find_beat_index(storyboard, bid)]
    # storyboard schema literal is {"none", "cutaway"} today. ADR-038 §D3
    # surfaces "aroll_only" as a target value; we write it as "none" (no broll
    # rendered) and additionally drop the broll spec to keep the schema valid.
    beat["broll_decision"] = "none"
    beat["broll"] = None
    _clear_text_and_visual_approval(beat)


def _apply_split_beat(
    storyboard: list[dict[str, Any]],
    edit: SplitBeat,
    align_ctx: dict[str, Any],
) -> None:
    bid = _resolve_beat_id(edit.beat_id)
    idx = _find_beat_index(storyboard, bid)
    beat = storyboard[idx]
    end_quote_original = beat.get("end_quote", "")
    # The split point is a quote that currently sits inside the beat between
    # start_quote and end_quote. The first half keeps start_quote and gets
    # end_quote = at_quote; the second half starts at at_quote and keeps the
    # original end_quote. We don't try to verify the at_quote sits inside the
    # flat_text here — realign() will raise AnchorNotFoundError if it doesn't.
    new_beat = copy.deepcopy(beat)
    new_beat["beat_id"] = _next_beat_id(storyboard)
    beat["end_quote"] = edit.at_quote
    new_beat["start_quote"] = edit.at_quote
    new_beat["end_quote"] = end_quote_original
    # Reset approval + render artefacts on the new beat too.
    new_beat["status"] = {
        "text_approved": False,
        "render_status": "pending",
        "visual_approved": False,
        "cached_hash": None,
    }
    new_beat["user_notes"] = []
    storyboard.insert(idx + 1, new_beat)
    _clear_text_and_visual_approval(beat)
    _realign(beat, **align_ctx)
    _realign(new_beat, **align_ctx)


def _apply_merge_beats(
    storyboard: list[dict[str, Any]],
    edit: MergeBeats,
    align_ctx: dict[str, Any],
) -> None:
    a_id = _resolve_beat_id(edit.beat_id_a)
    b_id = _resolve_beat_id(edit.beat_id_b)
    a_idx = _find_beat_index(storyboard, a_id)
    b_idx = _find_beat_index(storyboard, b_id)
    if abs(a_idx - b_idx) != 1:
        raise ValueError(
            f"merge_beats requires adjacent beats; beat {a_id} (idx {a_idx}) "
            f"and beat {b_id} (idx {b_idx}) are not adjacent"
        )
    first_idx, second_idx = sorted((a_idx, b_idx))
    first = storyboard[first_idx]
    second = storyboard[second_idx]
    first["end_quote"] = second.get("end_quote", first.get("end_quote", ""))
    # Concatenate srt_line_ids (best-effort; realign will replace if context).
    a_ids = first.get("srt_line_ids") or []
    b_ids = second.get("srt_line_ids") or []
    first["srt_line_ids"] = sorted(set(a_ids) | set(b_ids))
    # Concatenate user_notes; broll/layout from the first beat wins.
    first["user_notes"] = (first.get("user_notes") or []) + (second.get("user_notes") or [])
    storyboard.pop(second_idx)
    _clear_text_and_visual_approval(first)
    _realign(first, **align_ctx)


def _apply_duplicate_broll_from(
    storyboard: list[dict[str, Any]],
    edit: DuplicateBrollFrom,
    align_ctx: dict[str, Any],
) -> None:
    tgt_id = _resolve_beat_id(edit.target_beat_id)
    src_id = _resolve_beat_id(edit.source_beat_id)
    src = storyboard[_find_beat_index(storyboard, src_id)]
    tgt = storyboard[_find_beat_index(storyboard, tgt_id)]
    src_broll = src.get("broll")
    if src_broll is None:
        raise ValueError(f"duplicate_broll_from: source beat {src_id} has no broll to copy")
    tgt["broll"] = copy.deepcopy(src_broll)
    tgt["layout"] = src.get("layout", tgt.get("layout"))
    if tgt.get("broll_decision") == "none":
        tgt["broll_decision"] = "cutaway"
    _clear_text_and_visual_approval(tgt)


def _apply_restore_previous_render(
    storyboard: list[dict[str, Any]],
    edit: RestorePreviousRender,
    align_ctx: dict[str, Any],
) -> None:
    bid = _resolve_beat_id(edit.beat_id)
    beat = storyboard[_find_beat_index(storyboard, bid)]
    # D2 hash history lookup is out of scope for this PR (no on-disk history
    # store landed yet); we record the requested run_id in user_notes so the
    # operator can manually re-cache. cached_hash is set to the run_id which
    # makes fcpxml_emitter resolve out/b_roll_<run_id>.mp4 if it exists.
    beat.setdefault("status", {})["cached_hash"] = edit.run_id
    beat["status"]["render_status"] = "done"
    beat.setdefault("user_notes", []).append(
        {
            "timestamp": "",
            "note": f"restore_previous_render(run_id={edit.run_id!r})",
        }
    )


_OP_HANDLERS = {
    "replace_quote": _apply_replace_quote,
    "set_broll": _apply_set_broll,
    "set_layout": _apply_set_layout,
    "set_transition": _apply_set_transition,
    "patch_broll_params": _apply_patch_broll_params,
    "set_timing": _apply_set_timing,
    "mark_aroll": _apply_mark_aroll,
    "split_beat": _apply_split_beat,
    "merge_beats": _apply_merge_beats,
    "duplicate_broll_from": _apply_duplicate_broll_from,
    "restore_previous_render": _apply_restore_previous_render,
}


# ── public entrypoint ────────────────────────────────────────────────────────


def apply_edits(
    storyboard: list[dict[str, Any]],
    edits: list[BeatEdit],
    *,
    flat_text: str | None = None,
    char_to_time: dict[int, float] | None = None,
    srt_line_id_to_char_range: dict[int, tuple[int, int]] | None = None,
) -> list[dict[str, Any]]:
    """Apply an ordered list of typed edits to a storyboard. Returns a new list.

    The input ``storyboard`` is not mutated; we deep-copy first. This makes
    the function safe to call from a tool-loop where the caller wants to
    inspect the diff between iterations.

    Args:
        storyboard: list of beat dicts (storyboard.yaml shape).
        edits: list of ``BeatEdit`` instances (already-validated discriminated
            union members). Pass raw dicts only after running them through
            ``pydantic`` adapter externally.
        flat_text / char_to_time / srt_line_id_to_char_range: optional
            alignment context. When provided, ``replace_quote`` / ``split_beat``
            / ``merge_beats`` re-run ``align_beat`` on affected beats. When
            absent, timing/srt_line_ids are left as-is.

    Returns:
        A new storyboard list with the edits applied.

    Raises:
        KeyError: beat_id referenced does not exist.
        AnchorNotFoundError: ``replace_quote.old_quote`` did not match
            ``start_quote`` or ``end_quote`` (or post-edit realign failed).
        ValueError: structural violation (merge non-adjacent, patch params
            on a beat without broll, malformed ``[beat:N]`` ref).
    """
    new_storyboard = copy.deepcopy(storyboard)
    align_ctx = {
        "flat_text": flat_text,
        "char_to_time": char_to_time,
        "srt_line_id_to_char_range": srt_line_id_to_char_range,
    }
    for edit in edits:
        handler = _OP_HANDLERS[edit.op]
        handler(new_storyboard, edit, align_ctx)
    return new_storyboard
