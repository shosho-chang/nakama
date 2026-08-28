from __future__ import annotations

import json
import subprocess
import sys
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from agents.brook.script_video.long_highlight_orchestrator import (
    LongHighlightOrchestrator,
    SourceInput,
    StagePending,
)
from scripts.run_long_highlight_orchestrator import main as cli_main


class FixtureRunner:
    def __init__(self, responses: dict[tuple[str, str | None], Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str | None]] = []
        self.payloads: list[tuple[str, str | None, dict[str, Any]]] = []
        self.counts: defaultdict[tuple[str, str | None], int] = defaultdict(int)

    def run(
        self,
        stage: str,
        *,
        event_id: str | None,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        key = (stage, event_id)
        self.calls.append(key)
        self.payloads.append((stage, event_id, deepcopy(payload)))
        value = self.responses[key]
        if isinstance(value, list):
            index = self.counts[key]
            self.counts[key] += 1
            value = value[index]
        if isinstance(value, Exception):
            raise value
        return value


def test_script_help_runs_from_repo_root() -> None:
    repo_root = Path(__file__).resolve().parents[3]

    result = subprocess.run(
        [sys.executable, "scripts/run_long_highlight_orchestrator.py", "--help"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "adopt-winner" in result.stdout


def test_script_dry_run_prints_chinese_episode_on_windows_console(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    srt = tmp_path / "master.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:10,000\n測試\n", encoding="utf-8")
    media = tmp_path / "master.mp4"
    media.write_bytes(b"fixture media")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_long_highlight_orchestrator.py",
            "start",
            str(tmp_path / "中文-state.json"),
            "--episode-id",
            "20260805 林之晨",
            "--srt",
            str(srt),
            "--media",
            str(media),
            "--dry-run",
        ],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["episode_id"] == "20260805 林之晨"


@pytest.fixture
def source(tmp_path: Path) -> SourceInput:
    srt = tmp_path / "master.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:10:00,000\nopening\n\n2\n00:10:00,000 --> 00:20:00,000\nending\n",
        encoding="utf-8",
    )
    media = tmp_path / "master.mp4"
    media.write_bytes(b"fixture media")
    return SourceInput("episode-fixture", srt, media, ("topic.md",))


def _candidate(candidate_id: str, start: float, end: float, **extra: Any) -> dict[str, Any]:
    return {"id": candidate_id, "t_start": start, "t_end": end, **extra}


def _sections(*range_indices: int) -> list[dict[str, Any]]:
    return [
        {
            "section_id": f"section-{index + 1:02d}",
            "source_range_index": range_index,
            "summary": "Opening argument" if index == 0 else "Next chapter",
            "transition_before": index > 0,
            "transition_title": None if index == 0 else "下一個完整論點",
            "cut_local_start": float(index * 60),
        }
        for index, range_index in enumerate(range_indices)
    ]


def _responses() -> dict[tuple[str, str | None], Any]:
    return {
        ("mine", "story"): {
            "candidates": [
                _candidate(
                    "story-1",
                    5,
                    515,
                    title="Story",
                    sections=_sections(0, 0),
                    invented="ignored",
                ),
                {"id": "broken", "t_start": "not-a-number", "t_end": 80},
            ],
            "model_notes": "ignored",
        },
        ("mine", "punch"): {
            "candidates": [_candidate("punch-1", 520, 1020, sections=_sections(0))]
        },
        ("mine", "value"): {
            "candidates": [_candidate("value-1", 650, 1150, sections=_sections(0))]
        },
        ("review", "azhe"): {"assessments": [{"candidate_id": "story-1", "score": 8}]},
        ("review", "kevin"): {"assessments": [{"candidate_id": "punch-1", "score": 7}]},
        ("review", "shufen"): {"assessments": []},
        ("review", "renee"): {"notes": "No per-candidate rows, but useful global notes."},
        ("tighten", None): {
            "winner": _candidate("story-1-tight", 7, 507),
            "ref": "tight.json",
        },
        ("director", None): {
            "events": [{"id": "stock-1", "t_start": 10, "t_end": 15}],
            "ref": "director.json",
        },
        ("dp", None): {
            "events": [
                {
                    "id": "stock-1",
                    "status": "ready",
                    "fixed_stock_authority": "fixture:asset-1",
                    "candidates": [{"asset_ref": "fixture.mp4", "playable": True}],
                }
            ],
            "ref": "dp.json",
        },
        ("visual_review", None): {
            "events": [{"id": "stock-1", "status": "pass"}],
            "ref": "visual.json",
        },
        ("resolve_preview", None): {
            "destructive": False,
            "preview_ref": "preview.mp4",
            "duration_sec": 500,
        },
        ("packaging", None): {"ready": True, "ref": "package.json"},
    }


def test_tolerant_semantic_fanout_quarantines_only_bad_candidate(
    tmp_path: Path, source: SourceInput
) -> None:
    runner = FixtureRunner(_responses())
    orchestrator = LongHighlightOrchestrator.create(tmp_path / "state.json", source, runner)

    state = orchestrator.resume()

    assert state["status"] == "needs_review"
    assert [row["id"] for row in state["candidates"]] == ["story-1", "punch-1", "value-1"]
    story = state["candidates"][0]
    assert story["title"] == "Story"
    assert story["hook"] == ""
    assert story["sections"] == _sections(0, 0)
    assert story["selected_duration_sec"] == 510
    assert "invented" not in story
    assert state["quarantine"][0]["id"] == "broken"
    assert any("broken" in warning for warning in state["warnings"])

    reviewer_ids = {event_id for stage, event_id in runner.calls if stage == "review"}
    assert reviewer_ids == {"azhe", "kevin", "shufen", "renee"}
    assert "brand" not in reviewer_ids
    assert state["stages"]["reviews"]["status"] == "approved"
    assert any("renee" in warning for warning in state["warnings"])


def test_mining_quarantines_short_or_sectionless_long_candidates(
    tmp_path: Path, source: SourceInput
) -> None:
    responses = _responses()
    responses[("mine", "story")]["candidates"] = [
        _candidate("story-short", 5, 424, sections=_sections(0))
    ]
    responses[("mine", "value")]["candidates"] = [
        _candidate("value-no-map", 650, 1150, sections=[])
    ]
    runner = FixtureRunner(responses)
    orchestrator = LongHighlightOrchestrator.create(tmp_path / "state.json", source, runner)

    state = orchestrator.resume()

    assert [row["id"] for row in state["candidates"]] == ["punch-1"]
    reasons = {row["id"]: row["reason"] for row in state["quarantine"]}
    assert "8 minutes" in reasons["story-short"]
    assert "section map" in reasons["value-no-map"]


def test_invalid_source_ranges_quarantine_only_that_candidate(
    tmp_path: Path, source: SourceInput
) -> None:
    responses = _responses()
    responses[("mine", "story")]["candidates"] = [
        _candidate(
            "story-overlap",
            5,
            705,
            source_ranges=[
                {"t_start": 5, "t_end": 405},
                {"t_start": 305, "t_end": 705},
            ],
            sections=_sections(0, 1),
        )
    ]
    runner = FixtureRunner(responses)
    orchestrator = LongHighlightOrchestrator.create(tmp_path / "state.json", source, runner)

    state = orchestrator.resume()

    assert [row["id"] for row in state["candidates"]] == ["punch-1", "value-1"]
    assert state["quarantine"][0]["id"] == "story-overlap"
    assert "ordered and non-overlapping" in state["quarantine"][0]["reason"]
    assert runner.calls.count(("mine", "story")) == 1
    assert runner.calls.count(("mine", "punch")) == 1
    assert runner.calls.count(("mine", "value")) == 1


def test_all_quarantined_candidates_require_human_attention_without_redispatch(
    tmp_path: Path, source: SourceInput
) -> None:
    responses = _responses()
    for miner in ("story", "punch", "value"):
        responses[("mine", miner)]["candidates"] = [
            _candidate(f"{miner}-short", 5, 305, sections=_sections(0))
        ]
    runner = FixtureRunner(responses)
    orchestrator = LongHighlightOrchestrator.create(tmp_path / "state.json", source, runner)

    first = orchestrator.resume()
    second = orchestrator.resume()

    assert first["status"] == "needs_review"
    assert first["human"]["attention_required"] is True
    assert first["human"]["reason"] == "all_candidates_quarantined"
    assert second == first
    assert runner.calls.count(("mine", "story")) == 1
    assert runner.calls.count(("mine", "punch")) == 1
    assert runner.calls.count(("mine", "value")) == 1
    assert not any(stage == "review" for stage, _event_id in runner.calls)


def test_multi_range_winner_uses_selected_sum_and_passes_chapter_map_downstream(
    tmp_path: Path, source: SourceInput
) -> None:
    responses = _responses()
    ranges = [
        {"t_start": 5, "t_end": 255},
        {"t_start": 455, "t_end": 705},
    ]
    sections = _sections(0, 1, 1)
    sections[0]["source_t_start"] = 5
    sections[1].pop("cut_local_start")
    sections[1]["source_t_start"] = 480
    sections[2]["cut_local_start"] = 420
    responses[("mine", "story")]["candidates"][0] = _candidate(
        "story-1",
        5,
        705,
        title="Two connected excerpts",
        source_ranges=ranges,
        sections=sections,
    )
    responses[("tighten", None)]["winner"] = _candidate(
        "story-1-tight",
        5,
        705,
        source_ranges=ranges,
    )
    runner = FixtureRunner(responses)
    orchestrator = LongHighlightOrchestrator.create(tmp_path / "state.json", source, runner)
    orchestrator.resume()

    state = orchestrator.approve_winner("story-1")

    assert state["status"] == "approved"
    assert state["winner"]["selected_duration_sec"] == 500
    director_payload = next(
        payload
        for stage, event_id, payload in runner.payloads
        if (stage, event_id) == ("director", None)
    )
    chapter_map = director_payload["chapter_map"]
    assert [row["timestamp_sec"] for row in chapter_map] == [0.0, 275.0, 420.0]
    assert [row["fullscreen_transition"] for row in chapter_map] == [False, True, True]
    assert all(row["youtube_chapter"] is True for row in chapter_map)
    assert chapter_map[1]["transition_title"] == "下一個完整論點"
    assert director_payload["long_highlight_contract"]["fullscreen_transition"] == (
        "chapter_starts_only"
    )
    assert director_payload["long_highlight_contract"]["route"] == (
        "long_highlight_orchestrator_v2"
    )
    assert director_payload["long_highlight_contract"]["validation_profile"] == (
        "semantic_visual_minimal"
    )


def test_unknown_nonfirst_chapter_timestamp_stops_before_director_without_fake_zero(
    tmp_path: Path, source: SourceInput
) -> None:
    responses = _responses()
    sections = _sections(0, 0)
    sections[1].pop("cut_local_start")
    responses[("mine", "story")]["candidates"][0]["sections"] = sections
    runner = FixtureRunner(responses)
    orchestrator = LongHighlightOrchestrator.create(tmp_path / "state.json", source, runner)
    orchestrator.resume()

    first = orchestrator.approve_winner("story-1")
    second = orchestrator.resume()

    assert first["status"] == "needs_review"
    assert first["human"]["attention_required"] is True
    assert first["human"]["reason"] == "chapter_timestamp_unknown"
    assert second == first
    assert runner.calls.count(("tighten", None)) == 1
    assert ("director", None) not in runner.calls
    tighten_payload = next(
        payload
        for stage, event_id, payload in runner.payloads
        if (stage, event_id) == ("tighten", None)
    )
    assert len(tighten_payload["chapter_map"]) == 1
    assert tighten_payload["chapter_map"][0]["timestamp_sec"] == 0.0
    assert tighten_payload["chapter_map"][0]["youtube_chapter"] is True
    assert tighten_payload["chapter_map"][0]["fullscreen_transition"] is False


def test_adopt_winner_rejects_long_bounding_span_with_short_selected_ranges(
    tmp_path: Path, source: SourceInput
) -> None:
    orchestrator = LongHighlightOrchestrator.create(
        tmp_path / "state.json", source, FixtureRunner(_responses())
    )

    state = orchestrator.adopt_winner(
        {
            "id": "legacy-L01",
            "t_start": 5,
            "t_end": 705,
            "source_ranges": [
                {"t_start": 5, "t_end": 205},
                {"t_start": 505, "t_end": 705},
            ],
            "sections": _sections(0, 1),
        }
    )

    assert state["status"] == "failed"
    assert state["hard_blocker"] == "winner_too_short"


def test_adopt_winner_rejects_empty_section_map(tmp_path: Path, source: SourceInput) -> None:
    orchestrator = LongHighlightOrchestrator.create(
        tmp_path / "state.json", source, FixtureRunner(_responses())
    )

    state = orchestrator.adopt_winner(
        {"id": "legacy-L01", "t_start": 5, "t_end": 505, "sections": []}
    )

    assert state["status"] == "failed"
    assert state["hard_blocker"] == "winner_sections_invalid"


def test_section_shape_drift_is_normalized_with_warnings_instead_of_rerun(
    tmp_path: Path, source: SourceInput
) -> None:
    responses = _responses()
    responses[("mine", "story")]["candidates"][0] = _candidate(
        "story-1",
        5,
        505,
        sections=[
            {"label": "Opening"},
            {
                "section_id": "section-01",
                "summary": "A second point",
                "transition_before": "not-a-bool",
            },
        ],
    )
    runner = FixtureRunner(responses)
    orchestrator = LongHighlightOrchestrator.create(tmp_path / "state.json", source, runner)

    state = orchestrator.resume()

    story = next(row for row in state["candidates"] if row["id"] == "story-1")
    assert [row["section_id"] for row in story["sections"]] == [
        "section-01",
        "section-01-2",
    ]
    assert all(row["source_range_index"] == 0 for row in story["sections"])
    assert all(row["transition_before"] is False for row in story["sections"])
    assert any("generated section-01" in warning for warning in state["warnings"])
    assert not any(row["id"] == "story-1" for row in state["quarantine"])


def test_tighten_cannot_shrink_approved_winner_below_eight_minutes(
    tmp_path: Path, source: SourceInput
) -> None:
    responses = _responses()
    responses[("tighten", None)] = {
        "winner": _candidate("story-1-tight", 7, 477),
        "ref": "tight-too-short.json",
    }
    runner = FixtureRunner(responses)
    orchestrator = LongHighlightOrchestrator.create(tmp_path / "state.json", source, runner)
    orchestrator.resume()

    state = orchestrator.approve_winner("story-1")

    assert state["status"] == "failed"
    assert state["hard_blocker"] == "tightened_winner_too_short"
    assert ("director", None) not in runner.calls


def test_malformed_tighten_winner_requires_attention_without_redispatch(
    tmp_path: Path, source: SourceInput
) -> None:
    responses = _responses()
    responses[("tighten", None)] = {
        "winner": _candidate("story-1-tight", "not-a-time", 507),
        "ref": "malformed-tight.json",
    }
    runner = FixtureRunner(responses)
    orchestrator = LongHighlightOrchestrator.create(tmp_path / "state.json", source, runner)
    orchestrator.resume()

    first = orchestrator.approve_winner("story-1")
    second = orchestrator.resume()

    assert first["status"] == "needs_review"
    assert first["human"]["attention_required"] is True
    assert first["human"]["reason"] == "tighten_invalid_winner"
    assert second == first
    assert runner.calls.count(("tighten", None)) == 1
    assert ("director", None) not in runner.calls


def test_tighten_empty_sections_reuses_approved_section_map_with_warning(
    tmp_path: Path, source: SourceInput
) -> None:
    responses = _responses()
    responses[("tighten", None)]["winner"]["sections"] = []
    runner = FixtureRunner(responses)
    orchestrator = LongHighlightOrchestrator.create(tmp_path / "state.json", source, runner)
    mined = orchestrator.resume()
    approved_sections = next(
        row["sections"] for row in mined["candidates"] if row["id"] == "story-1"
    )

    state = orchestrator.approve_winner("story-1")

    assert state["status"] == "approved"
    assert state["winner"]["sections"] == approved_sections
    assert any("reused the approved section map" in warning for warning in state["warnings"])


def test_tighten_reuses_approved_ranges_when_multi_range_bounds_are_unchanged(
    tmp_path: Path, source: SourceInput
) -> None:
    responses = _responses()
    responses[("mine", "story")]["candidates"][0] = _candidate(
        "story-1",
        5,
        705,
        source_ranges=[
            {"t_start": 5, "t_end": 255},
            {"t_start": 455, "t_end": 705},
        ],
        sections=_sections(0, 1),
    )
    responses[("tighten", None)]["winner"] = _candidate("story-1-tight", 5, 705)
    runner = FixtureRunner(responses)
    orchestrator = LongHighlightOrchestrator.create(tmp_path / "state.json", source, runner)
    orchestrator.resume()

    state = orchestrator.approve_winner("story-1")

    assert state["status"] == "approved"
    assert state["winner"]["selected_duration_sec"] == 500
    assert len(state["winner"]["source_ranges"]) == 2
    assert any("reused the approved ranges" in warning for warning in state["warnings"])


def test_tighten_requires_ranges_when_multi_range_boundaries_change(
    tmp_path: Path, source: SourceInput
) -> None:
    responses = _responses()
    responses[("mine", "story")]["candidates"][0] = _candidate(
        "story-1",
        5,
        705,
        source_ranges=[
            {"t_start": 5, "t_end": 255},
            {"t_start": 455, "t_end": 705},
        ],
        sections=_sections(0, 1),
    )
    responses[("tighten", None)]["winner"] = _candidate("story-1-tight", 6, 704)
    runner = FixtureRunner(responses)
    orchestrator = LongHighlightOrchestrator.create(tmp_path / "state.json", source, runner)
    orchestrator.resume()

    state = orchestrator.approve_winner("story-1")

    assert state["status"] == "failed"
    assert state["hard_blocker"] == "tightened_ranges_missing"
    assert ("director", None) not in runner.calls


def test_preview_cannot_be_shorter_than_eight_minutes(tmp_path: Path, source: SourceInput) -> None:
    responses = _responses()
    responses[("resolve_preview", None)]["duration_sec"] = 259.967
    runner = FixtureRunner(responses)
    orchestrator = LongHighlightOrchestrator.create(tmp_path / "state.json", source, runner)
    orchestrator.resume()

    state = orchestrator.approve_winner("story-1")

    assert state["status"] == "failed"
    assert state["hard_blocker"] == "preview_too_short"


def test_preview_without_known_duration_stops_terminal_without_redispatch(
    tmp_path: Path, source: SourceInput
) -> None:
    responses = _responses()
    responses[("resolve_preview", None)].pop("duration_sec")
    runner = FixtureRunner(responses)
    orchestrator = LongHighlightOrchestrator.create(tmp_path / "state.json", source, runner)
    orchestrator.resume()

    first = orchestrator.approve_winner("story-1")
    second = orchestrator.resume()

    assert first["status"] == "failed"
    assert first["hard_blocker"] == "preview_duration_unknown"
    assert second == first
    assert runner.calls.count(("resolve_preview", None)) == 1
    assert any("duration is unknown" in warning for warning in first["warnings"])


def test_human_gate_then_happy_path_exposes_all_readiness_refs(
    tmp_path: Path, source: SourceInput
) -> None:
    runner = FixtureRunner(_responses())
    orchestrator = LongHighlightOrchestrator.create(tmp_path / "state.json", source, runner)
    orchestrator.resume()

    state = orchestrator.approve_winner("story-1", {"title": "Human corrected"})

    assert state["status"] == "approved"
    assert state["winner"]["title"] == "Human corrected"
    assert state["refs"] == {
        "candidates": "state.json#/candidates",
        "reviews": "state.json#/reviews",
        "winner": "state.json#/winner",
        "tighten": "tight.json",
        "director": "director.json",
        "dp": "dp.json",
        "visual": "visual.json",
        "preview": "preview.mp4",
        "packaging": "package.json",
    }
    assert runner.calls.index(("mine", "story")) < runner.calls.index(("review", "azhe"))
    assert runner.calls.index(("review", "renee")) < runner.calls.index(("tighten", None))


def test_failed_visual_event_is_the_only_event_fixed_and_rechecked(
    tmp_path: Path, source: SourceInput
) -> None:
    responses = _responses()
    responses[("director", None)] = {
        "events": [
            {"id": "stock-ok", "t_start": 10, "t_end": 15},
            {"id": "stock-fail", "t_start": 20, "t_end": 25},
        ],
        "ref": "director.json",
    }
    responses[("dp", None)] = {
        "events": [
            {"id": "stock-ok", "status": "ready", "asset": {"playable": True}},
            {"id": "stock-fail", "status": "ready", "asset": {"playable": True}},
        ],
        "ref": "dp.json",
    }
    responses[("visual_review", None)] = {
        "events": [
            {"id": "stock-ok", "status": "pass"},
            {"id": "stock-fail", "status": "failed", "reason": "wrong semantics"},
        ],
        "ref": "visual-v1.json",
    }
    responses[("visual_fix", "stock-fail")] = {
        "event": {"id": "stock-fail", "asset": {"asset_ref": "fixed.mp4", "playable": True}},
        "ref": "fixed.json",
    }
    responses[("visual_review", "stock-fail")] = {
        "id": "stock-fail",
        "status": "pass",
        "ref": "visual-v2.json",
    }
    runner = FixtureRunner(responses)
    orchestrator = LongHighlightOrchestrator.create(tmp_path / "state.json", source, runner)
    orchestrator.resume()

    state = orchestrator.approve_winner("story-1")

    assert state["status"] == "approved"
    assert ("visual_fix", "stock-fail") in runner.calls
    assert ("visual_review", "stock-fail") in runner.calls
    assert ("visual_fix", "stock-ok") not in runner.calls
    assert ("visual_review", "stock-ok") not in runner.calls


def test_visual_fix_updates_only_target_dp_event_before_targeted_review(
    tmp_path: Path, source: SourceInput
) -> None:
    responses = _responses()
    responses[("director", None)] = {
        "events": [
            {"id": "stock-ok", "t_start": 10, "t_end": 15},
            {"id": "stock-fail", "t_start": 20, "t_end": 25},
        ],
        "ref": "director.json",
    }
    responses[("dp", None)] = {
        "events": [
            {
                "id": "stock-ok",
                "implementation_kind": "stock",
                "candidates": [{"asset_ref": "untouched.mp4", "playable": True}],
            },
            {
                "id": "stock-fail",
                "implementation_kind": "stock",
                "candidates": [{"asset_ref": "stale.mp4", "playable": True}],
            },
        ],
        "ref": "dp.json",
    }
    responses[("visual_review", None)] = {
        "events": [
            {"id": "stock-ok", "status": "pass"},
            {"id": "stock-fail", "status": "failed", "reason": "stale asset"},
        ]
    }
    responses[("visual_fix", "stock-fail")] = {
        "event": {
            "event_id": "stock-fail",
            "implementation_kind": "stock",
            "semantic_justification": "Replacement matches the requested action",
            "candidates": [{"asset_ref": "updated.mp4", "playable": True}],
        }
    }
    responses[("visual_review", "stock-fail")] = {
        "id": "stock-fail",
        "status": "pass",
    }
    runner = FixtureRunner(responses)
    orchestrator = LongHighlightOrchestrator.create(tmp_path / "state.json", source, runner)
    orchestrator.resume()

    state = orchestrator.approve_winner("story-1")

    dp_events = state["stages"]["dp"]["events"]
    assert dp_events["stock-ok"] == {
        "status": "approved",
        "data": {
            "id": "stock-ok",
            "candidates": [{"asset_ref": "untouched.mp4", "playable": True}],
            "implementation_kind": "stock",
        },
    }
    assert dp_events["stock-fail"]["data"]["candidates"][0]["asset_ref"] == "updated.mp4"
    targeted_payload = next(
        payload
        for stage, event_id, payload in runner.payloads
        if stage == "visual_review" and event_id == "stock-fail"
    )
    fix_payload = next(
        payload
        for stage, event_id, payload in runner.payloads
        if stage == "visual_fix" and event_id == "stock-fail"
    )
    reviewed_dp = targeted_payload["stages"]["dp"]["events"]
    assert reviewed_dp["stock-fail"]["data"]["candidates"][0]["asset_ref"] == "updated.mp4"
    assert reviewed_dp["stock-ok"] == fix_payload["stages"]["dp"]["events"]["stock-ok"]
    assert reviewed_dp["stock-ok"] == dp_events["stock-ok"]


def test_visual_fix_without_valid_event_remains_queued(tmp_path: Path, source: SourceInput) -> None:
    responses = _responses()
    responses[("visual_review", None)] = {
        "events": [{"id": "stock-1", "status": "failed", "reason": "wrong asset"}]
    }
    responses[("visual_fix", "stock-1")] = {"status": "fixed-but-no-event"}
    runner = FixtureRunner(responses)
    orchestrator = LongHighlightOrchestrator.create(tmp_path / "state.json", source, runner)
    orchestrator.resume()

    state = orchestrator.approve_winner("story-1")

    assert state["retry_queue"] == [{"stage": "visual_fix", "event_id": "stock-1"}]
    assert ("visual_review", "stock-1") not in runner.calls


def test_event_retry_does_not_rerun_the_full_visual_stage(
    tmp_path: Path, source: SourceInput
) -> None:
    responses = _responses()
    responses[("visual_review", None)] = {
        "events": [{"id": "stock-1", "status": "failed", "reason": "wrong semantics"}],
        "ref": "visual-v1.json",
    }
    responses[("visual_fix", "stock-1")] = [
        RuntimeError("fixture failure"),
        {"event": {"id": "stock-1", "asset": {"asset_ref": "fixed.mp4", "playable": True}}},
    ]
    responses[("visual_review", "stock-1")] = {"id": "stock-1", "status": "pass"}
    runner = FixtureRunner(responses)
    orchestrator = LongHighlightOrchestrator.create(tmp_path / "state.json", source, runner)
    orchestrator.resume()

    first = orchestrator.approve_winner("story-1")
    assert first["status"] == "running"
    assert first["retry_queue"] == [{"stage": "visual_fix", "event_id": "stock-1"}]

    final = orchestrator.retry_event("visual_fix", "stock-1")

    assert final["status"] == "approved"
    assert runner.calls.count(("visual_review", None)) == 1
    assert runner.calls.count(("visual_fix", "stock-1")) == 2


def test_resume_recovers_visual_stage_created_without_events(
    tmp_path: Path, source: SourceInput
) -> None:
    # This reproduces a real two-resume exchange: request first, response later.
    responses = _responses()
    responses[("visual_review", None)] = [
        StagePending("waiting for host response"),
        {
            "events": [{"id": "stock-1", "status": "failed", "reason": "wrong semantics"}],
            "ref": "visual-response.json",
        },
    ]
    responses[("visual_fix", "stock-1")] = StagePending("targeted fix not ready")
    runner = FixtureRunner(responses)
    orchestrator = LongHighlightOrchestrator.create(tmp_path / "state.json", source, runner)
    orchestrator.resume()

    first = orchestrator.approve_winner("story-1")
    preserved_winner = first["winner"]
    assert first["stages"]["visual"] == {"status": "pending"}

    recovered = orchestrator.resume()

    assert recovered["winner"] == preserved_winner
    assert recovered["stages"]["visual"]["events"]["stock-1"]["status"] == "failed"
    assert recovered["retry_queue"] == [{"stage": "visual_fix", "event_id": "stock-1"}]
    assert runner.calls.count(("mine", "story")) == 1


def test_adopt_existing_imports_usable_rows_and_leaves_gaps_pending(
    tmp_path: Path, source: SourceInput
) -> None:
    runner = FixtureRunner(_responses())
    orchestrator = LongHighlightOrchestrator.create(tmp_path / "state.json", source, runner)

    state = orchestrator.adopt_existing(
        director={
            "events": [
                {"id": "stock-ready", "t_start": 5, "t_end": 10, "kind": "stock"},
                {"id": "stock-bad", "status": "failed"},
            ],
            "receipt": {"opaque": "ignored"},
            "unknown_top_level": True,
        },
        dp={
            "events": [
                {
                    "id": "stock-ready",
                    "status": "ready",
                    "fixed_stock_authority": "fixture:one",
                    "candidates": [{"asset_ref": "one.mp4", "playable": True}],
                },
                {"id": "stock-bad", "status": "failed"},
            ],
            "receipt": {"opaque": "ignored"},
        },
    )

    assert state["stages"]["director"]["events"]["stock-ready"]["status"] == "approved"
    assert state["stages"]["director"]["events"]["stock-bad"]["status"] == "pending"
    assert state["stages"]["dp"]["events"]["stock-ready"]["status"] == "approved"
    assert state["stages"]["dp"]["events"]["stock-bad"]["status"] == "pending"
    assert len(state["stages"]["dp"]["events"]["stock-ready"]["data"]["candidates"]) == 1
    assert state["retry_queue"] == [
        {"stage": "director", "event_id": "stock-bad"},
        {"stage": "dp", "event_id": "stock-bad"},
    ]
    serialized = json.dumps(state)
    assert "opaque" not in serialized
    assert runner.calls == []


def test_adopt_existing_accepts_real_director_and_dp_alias_shapes(
    tmp_path: Path, source: SourceInput
) -> None:
    orchestrator = LongHighlightOrchestrator.create(
        tmp_path / "state.json", source, FixtureRunner(_responses())
    )

    state = orchestrator.adopt_existing(
        director={
            "events": [
                {
                    "event_id": "stock-real",
                    "t0": 12.5,
                    "t1": 18.0,
                    "kind": "stock",
                    "request": "A classroom demonstration",
                    "identity": {"ignored": True},
                }
            ],
            "timeline_identity": {"ignored": True},
        },
        dp={
            "implementations": [
                {
                    "event_id": "stock-real",
                    "t0": 12.5,
                    "t1": 18.0,
                    "status": "ready",
                    "selections": [
                        {
                            "asset_ref": "stock-real.mp4",
                            "playable": True,
                            "visual_summary": "Teacher helps students at a table",
                        }
                    ],
                    "identity": {"ignored": True},
                }
            ],
            "fulfillment_identity": {"ignored": True},
        },
    )

    director_row = state["stages"]["director"]["events"]["stock-real"]
    dp_row = state["stages"]["dp"]["events"]["stock-real"]
    assert director_row == {
        "status": "approved",
        "data": {
            "id": "stock-real",
            "t_start": 12.5,
            "t_end": 18.0,
            "kind": "stock",
            "request": "A classroom demonstration",
        },
    }
    assert dp_row["status"] == "approved"
    assert dp_row["data"]["id"] == "stock-real"
    assert dp_row["data"]["t_start"] == 12.5
    assert dp_row["data"]["t_end"] == 18.0
    assert dp_row["data"]["selections"][0]["asset_ref"] == "stock-real.mp4"
    assert state["retry_queue"] == []
    assert "identity" not in json.dumps(state)


def test_adopt_existing_preserves_director_and_dp_execution_semantics(
    tmp_path: Path, source: SourceInput
) -> None:
    orchestrator = LongHighlightOrchestrator.create(
        tmp_path / "state.json", source, FixtureRunner(_responses())
    )

    state = orchestrator.adopt_existing(
        director={
            "events": [
                {
                    "event_id": "stock-semantic",
                    "t0": 20,
                    "t1": 28,
                    "category": "stock",
                    "decision": "use_stock",
                    "form": "full_screen",
                    "negative_constraints": ["no modern children"],
                    "on_screen_text": "",
                    "quote": "A grounded transcript quote",
                    "rationale": "Makes the historical example concrete",
                    "search_angles": ["historic classroom", "authoritarian education"],
                    "shots_hint": "wide classroom, archival feeling",
                    "contract": {"ignored": True},
                    "worker": "ignored",
                }
            ]
        },
        dp={
            "implementations": [
                {
                    "event_id": "stock-semantic",
                    "t0": 20,
                    "t1": 28,
                    "implementation_kind": "stock",
                    "mode": "fixed_authority",
                    "semantic_justification": "Matches the specified historic classroom",
                    "target_lane": "video-2",
                    "candidates": [
                        {
                            "asset_ref": "historic-classroom.mp4",
                            "source_url": "https://example.invalid/asset",
                            "media_path": "fixtures/historic-classroom.mp4",
                            "asset_fingerprint": "nested-media-detail-may-remain",
                            "playable": True,
                        }
                    ],
                    "provenance": {"ignored": True},
                    "content_fingerprint": "ignored-outer",
                }
            ]
        },
    )

    director_data = state["stages"]["director"]["events"]["stock-semantic"]["data"]
    dp_data = state["stages"]["dp"]["events"]["stock-semantic"]["data"]
    assert director_data["category"] == "stock"
    assert director_data["decision"] == "use_stock"
    assert director_data["form"] == "full_screen"
    assert director_data["negative_constraints"] == ["no modern children"]
    assert director_data["quote"] == "A grounded transcript quote"
    assert director_data["rationale"] == "Makes the historical example concrete"
    assert director_data["search_angles"] == [
        "historic classroom",
        "authoritarian education",
    ]
    assert director_data["shots_hint"] == "wide classroom, archival feeling"
    assert dp_data["implementation_kind"] == "stock"
    assert dp_data["mode"] == "fixed_authority"
    assert dp_data["semantic_justification"].startswith("Matches")
    assert dp_data["target_lane"] == "video-2"
    assert dp_data["candidates"][0]["source_url"].startswith("https://")
    serialized = json.dumps(state)
    assert "ignored-outer" not in serialized
    assert '"worker"' not in serialized
    assert '"provenance"' not in serialized


def test_adopted_gap_retry_calls_only_that_event(tmp_path: Path, source: SourceInput) -> None:
    responses = _responses()
    responses[("director", "missing-row")] = {
        "event": {"id": "missing-row", "t_start": 40, "t_end": 50, "kind": "stock"}
    }
    runner = FixtureRunner(responses)
    orchestrator = LongHighlightOrchestrator.create(tmp_path / "state.json", source, runner)
    orchestrator.adopt_existing(
        director={
            "events": [
                {"id": "kept-row", "t_start": 5, "t_end": 10},
                {"id": "missing-row", "status": "missing"},
            ]
        }
    )

    state = orchestrator.retry_event("director", "missing-row")

    assert state["stages"]["director"]["status"] == "approved"
    assert runner.calls == [("director", "missing-row")]


def test_adopt_winner_skips_mining_review_and_already_adopted_downstream(
    tmp_path: Path, source: SourceInput
) -> None:
    runner = FixtureRunner(_responses())
    orchestrator = LongHighlightOrchestrator.create(tmp_path / "state.json", source, runner)
    orchestrator.adopt_existing(
        director={"events": [{"id": "stock-1", "t_start": 10, "t_end": 15}]},
        dp={
            "events": [
                {
                    "id": "stock-1",
                    "status": "ready",
                    "fixed_stock_authority": "fixture:one",
                    "candidates": [{"asset_ref": "one.mp4", "playable": True}],
                }
            ]
        },
    )

    adopted = orchestrator.adopt_winner(
        {
            "id": "legacy-L01",
            "title": "Approved title",
            "hook": "Approved hook",
            "rationale": "Approved rationale",
            "t_start": 5,
            "t_end": 505,
            "sections": _sections(0),
            "receipt": {"opaque": "ignored"},
            "unknown": "ignored",
        }
    )

    assert adopted["human"] == {
        "approved": True,
        "candidate_id": "legacy-L01",
        "source": "adopted",
    }
    assert adopted["winner"] == {
        "id": "legacy-L01",
        "t_start": 5.0,
        "t_end": 505.0,
        "title": "Approved title",
        "hook": "Approved hook",
        "rationale": "Approved rationale",
        "sections": _sections(0),
        "selected_duration_sec": 500.0,
    }
    assert "opaque" not in json.dumps(adopted)

    final = orchestrator.resume()

    assert final["status"] == "approved"
    called_stages = [stage for stage, _event_id in runner.calls]
    assert "mine" not in called_stages
    assert "review" not in called_stages
    assert "director" not in called_stages
    assert "dp" not in called_stages
    assert called_stages == ["tighten", "visual_review", "resolve_preview", "packaging"]


def test_adopt_winner_outside_source_range_hard_fails(tmp_path: Path, source: SourceInput) -> None:
    runner = FixtureRunner(_responses())
    orchestrator = LongHighlightOrchestrator.create(tmp_path / "state.json", source, runner)

    state = orchestrator.adopt_winner(
        {"id": "legacy-L01", "t_start": 5, "t_end": 1500, "sections": _sections(0)}
    )

    assert state["status"] == "failed"
    assert state["hard_blocker"] == "winner_out_of_range"
    assert runner.calls == []


def test_adopt_winner_with_tighten_ref_skips_tighten_and_preserves_winner(
    tmp_path: Path, source: SourceInput
) -> None:
    tighten_ref = tmp_path / "existing-tight.srt"
    tighten_ref.write_text(
        "1\n00:00:00,000 --> 00:08:20,000\nexisting approved tight cut\n",
        encoding="utf-8",
    )
    runner = FixtureRunner(_responses())
    orchestrator = LongHighlightOrchestrator.create(tmp_path / "state.json", source, runner)
    winner = {
        "id": "legacy-L02",
        "title": "Already tightened",
        "hook": "Keep this exact content",
        "rationale": "Human approved",
        "t_start": 12,
        "t_end": 512,
        "sections": _sections(0),
    }

    adopted = orchestrator.adopt_winner(winner, tighten_ref=tighten_ref)
    final = orchestrator.resume()

    assert adopted["stages"]["tighten"] == {
        "status": "approved",
        "duration_sec": 500.0,
    }
    assert adopted["refs"]["tighten"] == str(tighten_ref)
    assert final["winner"] == {
        **winner,
        "t_start": 12.0,
        "t_end": 512.0,
        "selected_duration_sec": 500.0,
    }
    assert ("tighten", None) not in runner.calls


def test_adopt_winner_rejects_existing_tight_cut_under_eight_minutes(
    tmp_path: Path, source: SourceInput
) -> None:
    tighten_ref = tmp_path / "too-short-tight.srt"
    tighten_ref.write_text("1\n00:00:00,000 --> 00:04:19,967\ntoo short\n", encoding="utf-8")
    orchestrator = LongHighlightOrchestrator.create(
        tmp_path / "state.json", source, FixtureRunner(_responses())
    )

    state = orchestrator.adopt_winner(
        {
            "id": "legacy-L03",
            "t_start": 12,
            "t_end": 512,
            "sections": _sections(0),
        },
        tighten_ref=tighten_ref,
    )

    assert state["status"] == "failed"
    assert state["hard_blocker"] == "tightened_winner_too_short"
    assert "tighten" not in state["stages"]


def test_adopt_winner_rejects_missing_tighten_ref_without_mutating_state(
    tmp_path: Path, source: SourceInput
) -> None:
    orchestrator = LongHighlightOrchestrator.create(
        tmp_path / "state.json", source, FixtureRunner(_responses())
    )

    with pytest.raises(ValueError, match="tighten ref is not readable"):
        orchestrator.adopt_winner(
            {
                "id": "legacy-L02",
                "t_start": 12,
                "t_end": 512,
                "sections": _sections(0),
            },
            tighten_ref=tmp_path / "missing.srt",
        )

    state = orchestrator.status()
    assert state["winner"] is None
    assert state["human"]["approved"] is False
    assert "tighten" not in state["stages"]


@pytest.mark.parametrize(
    ("mutator", "blocker"),
    [
        (
            lambda rows: rows.__setitem__(
                ("dp", None),
                {"events": [{"id": "stock-1", "asset": {"playable": False}}]},
            ),
            "unplayable_asset",
        ),
        (
            lambda rows: rows.__setitem__(
                ("resolve_preview", None), {"destructive": True, "preview_ref": "preview.mp4"}
            ),
            "destructive_resolve",
        ),
        (
            lambda rows: rows.__setitem__(
                ("resolve_preview", None), {"destructive": False, "preview_ref": ""}
            ),
            "no_preview",
        ),
    ],
)
def test_only_explicit_downstream_safety_conditions_hard_fail(
    tmp_path: Path,
    source: SourceInput,
    mutator: Any,
    blocker: str,
) -> None:
    responses = _responses()
    mutator(responses)
    runner = FixtureRunner(responses)
    orchestrator = LongHighlightOrchestrator.create(tmp_path / "state.json", source, runner)
    orchestrator.resume()

    state = orchestrator.approve_winner("story-1")

    assert state["status"] == "failed"
    assert state["hard_blocker"] == blocker


def test_selected_winner_must_be_inside_source_range(tmp_path: Path, source: SourceInput) -> None:
    responses = _responses()
    runner = FixtureRunner(responses)
    orchestrator = LongHighlightOrchestrator.create(tmp_path / "state.json", source, runner)
    orchestrator.resume()

    state = orchestrator.approve_winner("story-1", {"t_end": 1500})

    assert state["status"] == "failed"
    assert state["hard_blocker"] == "winner_out_of_range"


def test_unreadable_source_is_a_hard_blocker(tmp_path: Path) -> None:
    source = SourceInput("missing", tmp_path / "missing.srt", tmp_path / "missing.mp4")

    state = LongHighlightOrchestrator.create(
        tmp_path / "state.json", source, FixtureRunner({})
    ).status()

    assert state["status"] == "failed"
    assert state["hard_blocker"] == "unreadable_source"


def test_dry_run_is_read_only_and_lists_the_reusable_stage_plan(
    tmp_path: Path, source: SourceInput
) -> None:
    runner = FixtureRunner(_responses())
    orchestrator = LongHighlightOrchestrator.create(tmp_path / "state.json", source, runner)
    before = (tmp_path / "state.json").read_bytes()

    plan = orchestrator.dry_run()

    assert plan["parallel_miners"] == ["story", "punch", "value"]
    assert plan["parallel_reviewers"] == ["azhe", "kevin", "shufen", "renee"]
    assert plan["human_gate"] == "winner_approval"
    assert (tmp_path / "state.json").read_bytes() == before
    assert runner.calls == []


def test_cli_supports_dry_run_status_and_resume_without_live_work(
    tmp_path: Path, source: SourceInput, capsys: pytest.CaptureFixture[str]
) -> None:
    state_path = tmp_path / "state.json"
    exchange_dir = tmp_path / "exchange"

    assert (
        cli_main(
            [
                "start",
                str(state_path),
                "--episode-id",
                source.episode_id,
                "--srt",
                str(source.srt_path),
                "--media",
                str(source.media_path),
                "--dry-run",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["human_gate"] == "winner_approval"

    assert cli_main(["status", str(state_path)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "pending"

    assert cli_main(["resume", str(state_path), "--exchange-dir", str(exchange_dir)]) == 0
    resumed = json.loads(capsys.readouterr().out)
    assert resumed["status"] == "running"
    assert (exchange_dir / "requests" / "mine" / "story.json").is_file()


def test_cli_adopt_winner_marks_human_approved_without_running_stages(
    tmp_path: Path, source: SourceInput, capsys: pytest.CaptureFixture[str]
) -> None:
    state_path = tmp_path / "state.json"
    winner_path = tmp_path / "winner.json"
    winner_path.write_text(
        json.dumps(
            {
                "id": "legacy-L01",
                "t_start": 5,
                "t_end": 505,
                "sections": _sections(0),
                "legacy": True,
            }
        ),
        encoding="utf-8",
    )
    assert (
        cli_main(
            [
                "start",
                str(state_path),
                "--episode-id",
                source.episode_id,
                "--srt",
                str(source.srt_path),
                "--media",
                str(source.media_path),
                "--dry-run",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert cli_main(["adopt-winner", str(state_path), "--winner", str(winner_path)]) == 0
    adopted = json.loads(capsys.readouterr().out)

    assert adopted["status"] == "running"
    assert adopted["human"]["approved"] is True
    assert adopted["stages"] == {}
    assert not (tmp_path / "long-highlight-exchange").exists()


def test_cli_adopt_winner_accepts_existing_tighten_ref(
    tmp_path: Path, source: SourceInput, capsys: pytest.CaptureFixture[str]
) -> None:
    state_path = tmp_path / "state.json"
    winner_path = tmp_path / "winner.json"
    tighten_ref = tmp_path / "tight.srt"
    winner_path.write_text(
        json.dumps(
            {
                "id": "legacy-L02",
                "t_start": 12,
                "t_end": 512,
                "sections": _sections(0),
            }
        ),
        encoding="utf-8",
    )
    tighten_ref.write_text("1\n00:00:00,000 --> 00:08:20,000\nexisting\n", encoding="utf-8")
    cli_main(
        [
            "start",
            str(state_path),
            "--episode-id",
            source.episode_id,
            "--srt",
            str(source.srt_path),
            "--media",
            str(source.media_path),
            "--dry-run",
        ]
    )
    capsys.readouterr()

    assert (
        cli_main(
            [
                "adopt-winner",
                str(state_path),
                "--winner",
                str(winner_path),
                "--tighten-ref",
                str(tighten_ref),
            ]
        )
        == 0
    )
    state = json.loads(capsys.readouterr().out)
    assert state["stages"]["tighten"]["status"] == "approved"
    assert state["refs"]["tighten"] == str(tighten_ref)
