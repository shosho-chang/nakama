"""Tests for ``agents.foundry.beat_editor`` (ADR-038 §D3 + §D6).

Covers the 11 ops with at least one happy-path each plus the
``replace_quote`` anchor-not-found error. Also exercises the ``[beat:N]``
syntax acceptance and the bare-``[N]`` rejection (D6).
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import TypeAdapter

from agents.foundry.beat_aligner import AnchorNotFoundError
from agents.foundry.beat_editor import (
    BeatEdit,
    DuplicateBrollFrom,
    MarkAroll,
    MergeBeats,
    PatchBrollParams,
    ReplaceQuote,
    RestorePreviousRender,
    SetBroll,
    SetLayout,
    SetTiming,
    SetTransition,
    SplitBeat,
    _resolve_beat_id,
    apply_edits,
)

# ── fixtures ─────────────────────────────────────────────────────────────────


def _beat(beat_id: int, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "beat_id": beat_id,
        "start_quote": f"start-{beat_id}",
        "end_quote": f"end-{beat_id}",
        "broll_decision": "cutaway",
        "layout": "full_broll",
        "broll": {
            "render_target": "hyperframes",
            "component": "bigstat",
            "params": {"value": "42%"},
            "transitions": {"in_transition": None, "out_transition": None},
        },
        "status": {
            "text_approved": True,
            "render_status": "done",
            "visual_approved": True,
            "cached_hash": "abc123",
        },
        "user_notes": [],
        "timing": {"start": 1.0, "duration": 2.0},
        "srt_line_ids": [10],
    }
    base.update(over)
    return base


def _sb(*beats: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(b) for b in beats]


# ── BeatId parsing (D6) ──────────────────────────────────────────────────────


def test_beat_ref_int_passes_through():
    assert _resolve_beat_id(7) == 7


def test_beat_ref_bracket_form_accepted():
    assert _resolve_beat_id("[beat:7]") == 7


def test_beat_ref_bare_n_rejected():
    with pytest.raises(ValueError, match="bare"):
        _resolve_beat_id("[7]")


def test_beat_ref_malformed_string_rejected():
    with pytest.raises(ValueError):
        _resolve_beat_id("beat:7")


# ── op 1: replace_quote ──────────────────────────────────────────────────────


def test_replace_quote_replaces_start_quote_and_resets_approval():
    sb = _sb(_beat(1, start_quote="老的開頭", end_quote="老的結尾"))
    edits = [ReplaceQuote(beat_id=1, old_quote="老的開頭", new_quote="新的開頭")]
    out = apply_edits(sb, edits)
    assert out[0]["start_quote"] == "新的開頭"
    assert out[0]["end_quote"] == "老的結尾"
    # approval reset
    assert out[0]["status"]["text_approved"] is False
    assert out[0]["status"]["visual_approved"] is False
    # input is not mutated
    assert sb[0]["start_quote"] == "老的開頭"


def test_replace_quote_replaces_end_quote():
    sb = _sb(_beat(1, start_quote="head", end_quote="tail"))
    out = apply_edits(sb, [ReplaceQuote(beat_id=1, old_quote="tail", new_quote="ending")])
    assert out[0]["end_quote"] == "ending"


def test_replace_quote_raises_when_old_quote_does_not_match():
    sb = _sb(_beat(1, start_quote="head", end_quote="tail"))
    with pytest.raises(AnchorNotFoundError):
        apply_edits(sb, [ReplaceQuote(beat_id=1, old_quote="middle", new_quote="x")])


def test_replace_quote_accepts_bracket_beat_ref():
    sb = _sb(_beat(1, start_quote="a", end_quote="b"))
    edits = [ReplaceQuote(beat_id="[beat:1]", old_quote="a", new_quote="aa")]
    out = apply_edits(sb, edits)
    assert out[0]["start_quote"] == "aa"


# ── op 2: set_broll ──────────────────────────────────────────────────────────


def test_set_broll_swaps_component_and_params():
    sb = _sb(_beat(1))
    out = apply_edits(sb, [SetBroll(beat_id=1, component="lower_third", params={"text": "hi"})])
    assert out[0]["broll"]["component"] == "lower_third"
    assert out[0]["broll"]["params"] == {"text": "hi"}
    assert out[0]["status"]["text_approved"] is False


def test_set_broll_promotes_aroll_beat_to_cutaway():
    sb = _sb(_beat(1, broll_decision="none", broll=None))
    out = apply_edits(sb, [SetBroll(beat_id=1, component="bigstat", params={"value": "9"})])
    assert out[0]["broll_decision"] == "cutaway"
    assert out[0]["broll"]["component"] == "bigstat"


# ── op 3: set_layout ─────────────────────────────────────────────────────────


def test_set_layout_changes_layout_and_clears_approval():
    sb = _sb(_beat(1, layout="full_broll"))
    out = apply_edits(sb, [SetLayout(beat_id=1, layout="side_overlay_l")])
    assert out[0]["layout"] == "side_overlay_l"
    assert out[0]["status"]["text_approved"] is False


# ── op 4: set_transition ─────────────────────────────────────────────────────


def test_set_transition_sets_both_directions():
    sb = _sb(_beat(1))
    out = apply_edits(
        sb,
        [SetTransition(beat_id=1, in_transition="fade", out_transition="cut")],
    )
    assert out[0]["broll"]["transitions"] == {
        "in_transition": "fade",
        "out_transition": "cut",
    }


# ── op 5: patch_broll_params ─────────────────────────────────────────────────


def test_patch_broll_params_merges_into_existing():
    sb = _sb(_beat(1))
    out = apply_edits(sb, [PatchBrollParams(beat_id=1, partial_params={"colour": "red"})])
    # Existing "value": "42%" preserved, new "colour" added.
    assert out[0]["broll"]["params"] == {"value": "42%", "colour": "red"}


def test_patch_broll_params_without_broll_raises():
    sb = _sb(_beat(1, broll=None))
    with pytest.raises(ValueError, match="no broll spec"):
        apply_edits(sb, [PatchBrollParams(beat_id=1, partial_params={"x": 1})])


# ── op 6: set_timing ─────────────────────────────────────────────────────────


def test_set_timing_overrides_timing():
    sb = _sb(_beat(1))
    out = apply_edits(sb, [SetTiming(beat_id=1, start_seconds=10.5, duration_seconds=4.25)])
    assert out[0]["timing"] == {"start": 10.5, "duration": 4.25}


# ── op 7: mark_aroll ─────────────────────────────────────────────────────────


def test_mark_aroll_clears_broll_decision_and_spec():
    sb = _sb(_beat(1))
    out = apply_edits(sb, [MarkAroll(beat_id=1)])
    assert out[0]["broll_decision"] == "none"
    assert out[0]["broll"] is None
    assert out[0]["status"]["text_approved"] is False


# ── op 8: split_beat ─────────────────────────────────────────────────────────


def test_split_beat_inserts_new_beat_after_original():
    sb = _sb(_beat(1, start_quote="A", end_quote="C"), _beat(2))
    out = apply_edits(sb, [SplitBeat(beat_id=1, at_quote="B")])
    assert len(out) == 3
    assert out[0]["beat_id"] == 1
    assert out[0]["start_quote"] == "A"
    assert out[0]["end_quote"] == "B"
    # New beat gets a fresh int id (max + 1).
    new = out[1]
    assert new["start_quote"] == "B"
    assert new["end_quote"] == "C"
    assert new["beat_id"] == 3  # max was 2
    assert new["status"]["text_approved"] is False
    # The originally-second beat moved to index 2.
    assert out[2]["beat_id"] == 2


# ── op 9: merge_beats ────────────────────────────────────────────────────────


def test_merge_beats_fuses_adjacent_beats():
    sb = _sb(
        _beat(1, start_quote="A", end_quote="B", srt_line_ids=[1, 2]),
        _beat(2, start_quote="B", end_quote="C", srt_line_ids=[3, 4]),
    )
    out = apply_edits(sb, [MergeBeats(beat_id_a=1, beat_id_b=2)])
    assert len(out) == 1
    assert out[0]["beat_id"] == 1
    assert out[0]["start_quote"] == "A"
    assert out[0]["end_quote"] == "C"
    assert out[0]["srt_line_ids"] == [1, 2, 3, 4]


def test_merge_beats_non_adjacent_rejected():
    sb = _sb(_beat(1), _beat(2), _beat(3))
    with pytest.raises(ValueError, match="adjacent"):
        apply_edits(sb, [MergeBeats(beat_id_a=1, beat_id_b=3)])


# ── op 10: duplicate_broll_from ──────────────────────────────────────────────


def test_duplicate_broll_from_copies_component_params_and_layout():
    sb = _sb(
        _beat(
            1,
            layout="side_overlay_l",
            broll={
                "render_target": "hyperframes",
                "component": "kpi_card",
                "params": {"value": "99"},
                "transitions": {"in_transition": None, "out_transition": None},
            },
        ),
        _beat(2, broll_decision="none", broll=None, layout="full_broll"),
    )
    out = apply_edits(
        sb,
        [DuplicateBrollFrom(target_beat_id=2, source_beat_id=1)],
    )
    assert out[1]["broll_decision"] == "cutaway"
    assert out[1]["broll"]["component"] == "kpi_card"
    assert out[1]["broll"]["params"] == {"value": "99"}
    assert out[1]["layout"] == "side_overlay_l"


# ── op 11: restore_previous_render ───────────────────────────────────────────


def test_restore_previous_render_sets_cached_hash_and_marks_done():
    sb = _sb(_beat(1))
    sb[0]["status"]["cached_hash"] = "stale_hash"
    sb[0]["status"]["render_status"] = "failed"
    out = apply_edits(sb, [RestorePreviousRender(beat_id=1, run_id="run_xyz_001")])
    assert out[0]["status"]["cached_hash"] == "run_xyz_001"
    assert out[0]["status"]["render_status"] == "done"
    assert any("restore_previous_render" in n["note"] for n in out[0]["user_notes"])


# ── unknown beat_id ──────────────────────────────────────────────────────────


def test_unknown_beat_id_raises_keyerror():
    sb = _sb(_beat(1))
    with pytest.raises(KeyError):
        apply_edits(sb, [SetLayout(beat_id=999, layout="x")])


# ── alignment context (replace_quote + flat_text) ────────────────────────────


def test_replace_quote_with_flat_text_realigns_timing():
    sb = _sb(_beat(1, start_quote="老開頭", end_quote="老結尾"))
    flat_text = "新開頭中段老結尾"
    char_to_time = {i: float(i) * 0.1 for i in range(len(flat_text))}
    out = apply_edits(
        sb,
        [ReplaceQuote(beat_id=1, old_quote="老開頭", new_quote="新開頭")],
        flat_text=flat_text,
        char_to_time=char_to_time,
    )
    # Timing recomputed from new start_quote position (idx 0).
    assert out[0]["timing"]["start"] == pytest.approx(0.0)
    assert out[0]["timing"]["duration"] > 0


# ── pydantic discriminator round-trip ────────────────────────────────────────


def test_typeadapter_validates_dict_payload():
    adapter = TypeAdapter(BeatEdit)
    edit = adapter.validate_python({"op": "set_layout", "beat_id": 3, "layout": "full_broll"})
    assert isinstance(edit, SetLayout)
    assert edit.beat_id == 3
