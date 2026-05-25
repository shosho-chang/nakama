"""Integration test for foundry plan pipeline (PR-3).

No real LLM calls — Anthropic client is mocked throughout.
Manual smoke test: `python -m agents.foundry --episode <real-episode> plan`
with a real ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

SYNTHETIC_SRT = """\
1
00:00:00,000 --> 00:00:05,000
今天我們要聊一個研究。

2
00:00:05,500 --> 00:00:12,000
研究追蹤了11,000名受試者長達20年。

3
00:00:12,500 --> 00:00:18,000
結果發現每天走路的人。

4
00:00:18,500 --> 00:00:24,000
心臟病風險降低了35%。

5
00:00:24,500 --> 00:00:30,000
這個數字很驚人。
"""

# After normalize_punctuation + normalize_numbers:
# "11,000" → "11000"
# "35%" stays (not a pure CN numeral)
_NORMALIZED_START = "今天我們要聊一個研究"
_NORMALIZED_STAT_START = "研究追蹤了11000名受試者長達20年"

_CANNED_BEATS = [
    {
        "beat_id": 1,
        "start_quote": "今天我們要聊一個研究",
        "end_quote": "今天我們要聊一個研究",
        "broll_decision": "none",
        "layout": "full_aroll",
        "broll": None,
        "status": {
            "text_approved": False,
            "render_status": "pending",
            "visual_approved": False,
        },
        "user_notes": [],
    },
    {
        "beat_id": 2,
        "start_quote": "研究追蹤了11000名受試者長達20年",
        "end_quote": "研究追蹤了11000名受試者長達20年",
        "broll_decision": "cutaway",
        "layout": "full_broll",
        "broll": {
            "render_target": "hyperframes",
            "component": "bigstat",
            "params": {"label": "研究追蹤", "value": "11000", "unit": "名受試者"},
            "transitions": {"in_transition": "fade", "out_transition": "fade"},
        },
        "status": {
            "text_approved": False,
            "render_status": "pending",
            "visual_approved": False,
        },
        "user_notes": [],
    },
]


def _make_mock_client(beats: list[dict] | None = None) -> MagicMock:
    """Return a mock Anthropic client whose messages.create returns canned beats."""
    if beats is None:
        beats = _CANNED_BEATS
    mock_client = MagicMock()
    mock_response = MagicMock()
    yaml_text = yaml.dump(beats, allow_unicode=True)
    mock_response.content = [MagicMock(text=f"```yaml\n{yaml_text}\n```")]
    mock_client.messages.create.return_value = mock_response
    return mock_client


def _write_episode_fixture(ep_dir: Path) -> None:
    ep_dir.mkdir(parents=True)
    (ep_dir / "transcript.srt").write_text(SYNTHETIC_SRT, encoding="utf-8")
    (ep_dir / "episode.yaml").write_text(
        "title: Test Episode\ntarget_duration: 30\n", encoding="utf-8"
    )


def test_plan_produces_valid_storyboard(tmp_path, monkeypatch):
    """End-to-end: mock SRT + mock LLM → storyboard.yaml validates against schema."""
    from agents.foundry.pipeline import _cmd_plan
    from agents.foundry.schemas.storyboard import Beat

    ep_dir = tmp_path / "data" / "script_video" / "test-001"
    _write_episode_fixture(ep_dir)

    monkeypatch.chdir(tmp_path)

    with patch("agents.foundry.planner.get_client", return_value=_make_mock_client()):
        result = _cmd_plan(argparse.Namespace(episode="test-001"))

    assert result == 0

    storyboard_path = ep_dir / "storyboard.yaml"
    assert storyboard_path.exists(), "storyboard.yaml was not written"

    raw = yaml.safe_load(storyboard_path.read_text(encoding="utf-8"))
    assert isinstance(raw, list)
    assert len(raw) == 2

    # Every entry must validate against the Beat schema
    beats = [Beat.model_validate(b) for b in raw]
    assert beats[0].beat_id == 1
    assert beats[0].broll_decision == "none"
    assert beats[1].beat_id == 2
    assert beats[1].broll_decision == "cutaway"
    assert beats[1].broll is not None
    assert beats[1].broll.component == "bigstat"


def test_storyboard_beat_anchors_aligned(tmp_path, monkeypatch):
    """Beats with exact-copy anchors get timing filled in by align_beat."""
    from agents.foundry.pipeline import _cmd_plan
    from agents.foundry.schemas.storyboard import Beat

    ep_dir = tmp_path / "data" / "script_video" / "test-002"
    _write_episode_fixture(ep_dir)
    monkeypatch.chdir(tmp_path)

    with patch("agents.foundry.planner.get_client", return_value=_make_mock_client()):
        _cmd_plan(argparse.Namespace(episode="test-002"))

    raw = yaml.safe_load((ep_dir / "storyboard.yaml").read_text(encoding="utf-8"))
    beat1 = Beat.model_validate(raw[0])
    # Cue 1 starts at 0.0s — beat 1 anchors into cue 1 text
    assert beat1.timing is not None
    assert beat1.timing.start == pytest.approx(0.0)


def test_duration_check_warns_on_missing_mp4(tmp_path, caplog, monkeypatch):
    """Warning emitted when raw_recording.mp4 is absent."""
    from agents.foundry.pipeline import _cmd_plan

    ep_dir = tmp_path / "data" / "script_video" / "test-003"
    _write_episode_fixture(ep_dir)
    monkeypatch.chdir(tmp_path)

    with caplog.at_level(logging.WARNING, logger="agents.foundry.pipeline"):
        with patch("agents.foundry.planner.get_client", return_value=_make_mock_client()):
            _cmd_plan(argparse.Namespace(episode="test-003"))

    assert any("raw_recording.mp4" in r.message for r in caplog.records)


def test_duration_check_warns_on_large_delta(tmp_path, caplog, monkeypatch):
    """Warning emitted when SRT and mp4 durations differ by more than 1s."""
    from agents.foundry.pipeline import _cmd_plan

    ep_dir = tmp_path / "data" / "script_video" / "test-004"
    _write_episode_fixture(ep_dir)
    # Create a fake mp4 placeholder so the exists() check passes
    (ep_dir / "raw_recording.mp4").write_bytes(b"")
    monkeypatch.chdir(tmp_path)

    # SRT ends at 30.0s; mock mp4 duration as 45.0s → delta = 15s > 1s
    with patch("agents.foundry.pipeline._get_mp4_duration", return_value=45.0):
        with caplog.at_level(logging.WARNING, logger="agents.foundry.pipeline"):
            with patch("agents.foundry.planner.get_client", return_value=_make_mock_client()):
                _cmd_plan(argparse.Namespace(episode="test-004"))

    assert any("delta" in r.message for r in caplog.records)
