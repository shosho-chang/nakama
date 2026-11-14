"""影片線 plan／emit 的字幕來源接點——Subtitle V2 移除後暫時一律中止。

之後重做影片線、接上新的字幕來源時，這個檔要跟著改寫（或刪掉）。
"""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import Mock

import pytest

from agents.brook.script_video import pipeline


def _write_episode(ep_dir: Path) -> None:
    ep_dir.mkdir(parents=True)
    (ep_dir / "episode.yaml").write_text(
        f'id: {ep_dir.name}\ntitle: "pending subtitle source"\n', encoding="utf-8"
    )
    # 就算有一份 transcript.srt 也不能被當成字幕來源——那是重做時 owner 要決定的事。
    (ep_dir / "transcript.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n字幕\n", encoding="utf-8"
    )


def test_plan_stops_before_planner_until_subtitle_source_is_rebuilt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ep_dir = tmp_path / "episode-pending-plan"
    _write_episode(ep_dir)
    planner = Mock(side_effect=AssertionError("planner must not run without a subtitle source"))
    monkeypatch.setattr(pipeline, "_DATA_ROOT", tmp_path)
    monkeypatch.setattr("agents.brook.script_video.planner.plan_episode", planner)

    with pytest.raises(SystemExit, match="字幕來源待重做"):
        pipeline._cmd_plan(argparse.Namespace(episode=ep_dir.name))

    planner.assert_not_called()
    assert not (ep_dir / "storyboard.yaml").exists()


def test_emit_stops_before_emitter_until_subtitle_source_is_rebuilt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ep_dir = tmp_path / "episode-pending-emit"
    _write_episode(ep_dir)
    (ep_dir / "storyboard.yaml").write_text("[]\n", encoding="utf-8")
    emitter = Mock(side_effect=AssertionError("emit must not run without a subtitle source"))
    monkeypatch.setattr(pipeline, "_DATA_ROOT", tmp_path)
    monkeypatch.setattr("agents.brook.script_video.fcpxml_emitter.emit", emitter)

    with pytest.raises(SystemExit, match="Subtitle V2.*已於 2026-11 移除"):
        pipeline._cmd_emit(argparse.Namespace(episode=ep_dir.name, fcpxml_version="1.10"))

    emitter.assert_not_called()


def test_missing_episode_yaml_still_fails_first(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pipeline, "_DATA_ROOT", tmp_path)

    with pytest.raises(SystemExit, match="episode.yaml not found"):
        pipeline._cmd_plan(argparse.Namespace(episode="no-such-episode"))
