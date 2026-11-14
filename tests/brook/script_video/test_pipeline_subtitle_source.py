"""影片線 plan／emit 的字幕來源：讀新字幕程式（ADR-063 Memo Dual-Audit Release V1）的 release。

plan 讀 ``subtitle-release/memo-dual-audit-v1/STAGE5-HANDOFF.json`` 指到的 release.srt，
把用了哪一份 release 記進 ``storyboard.subtitle-source.json``；emit 只接受綁著「現在這份」
release 的 storyboard。沒有 release 就中止，不會退回讀 ``transcript.srt``。
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from agents.brook.script_video import pipeline
from agents.brook.script_video.subtitle_handoff import VerifiedProjectionHandoffError
from tests.brook.script_video.test_planner_integration import SYNTHETIC_SRT, _canned_llm_text
from tests.brook.script_video.test_verified_projection_handoff import (
    _memo_dual_audit_release_fixture,
)

_RELEASE_SRT = Path("subtitle-release/memo-dual-audit-v1/release.srt")


def _write_episode(ep_dir: Path, *, with_release: bool = True) -> None:
    ep_dir.mkdir(parents=True)
    (ep_dir / "episode.yaml").write_text(
        f'id: {ep_dir.name}\ntitle: "subtitle source"\n', encoding="utf-8"
    )
    # 舊式的裸 transcript.srt 永遠不能被當成字幕來源。
    (ep_dir / "transcript.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n不該被讀到的字幕\n", encoding="utf-8"
    )
    if with_release:
        _memo_dual_audit_release_fixture(ep_dir, srt_text=SYNTHETIC_SRT)


def _plan(ep_dir: Path, **kwargs) -> int:
    return pipeline._cmd_plan(argparse.Namespace(episode=ep_dir.name, **kwargs))


def _emit(ep_dir: Path) -> int:
    return pipeline._cmd_emit(argparse.Namespace(episode=ep_dir.name, fcpxml_version="1.10"))


@pytest.fixture
def episode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(pipeline, "_DATA_ROOT", tmp_path)
    return tmp_path / "video-ep-001"


def test_plan_stops_before_planner_without_subtitle_release(
    episode: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_episode(episode, with_release=False)
    planner = Mock(side_effect=AssertionError("planner must not run without a subtitle release"))
    monkeypatch.setattr("agents.brook.script_video.planner.plan_episode", planner)

    with pytest.raises(SystemExit, match="podcast_subtitle_release.py finalize"):
        _plan(episode)

    planner.assert_not_called()
    assert not (episode / "storyboard.yaml").exists()
    assert not (episode / pipeline.SUBTITLE_SOURCE_RECORD).exists()


def test_plan_reads_release_srt_and_records_its_identity(episode: Path, mock_llm_response) -> None:
    _write_episode(episode)
    mock_llm_response(_canned_llm_text())

    assert _plan(episode) == 0

    assert (episode / "storyboard.yaml").exists()
    record = json.loads((episode / pipeline.SUBTITLE_SOURCE_RECORD).read_text(encoding="utf-8"))
    binding = record["subtitle"]
    assert binding["subtitle_mode"] == "memo-dual-audit-v1"
    assert binding["episode_id"] == episode.name
    assert (
        binding["subtitle_srt_sha256"]
        == hashlib.sha256((episode / _RELEASE_SRT).read_bytes()).hexdigest()
    )
    # 只記內容雜湊，不記本機絕對路徑。
    assert not any(str(episode) in str(value) for value in binding.values())


def test_plan_rejects_release_srt_tampered_after_finalize(
    episode: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_episode(episode)
    (episode / _RELEASE_SRT).write_text(SYNTHETIC_SRT.replace("驚人", "普通"), encoding="utf-8")
    planner = Mock(side_effect=AssertionError("planner must not run on a tampered release"))
    monkeypatch.setattr("agents.brook.script_video.planner.plan_episode", planner)

    with pytest.raises(SystemExit, match="讀不到可用的字幕 release"):
        _plan(episode)

    planner.assert_not_called()


def test_plan_then_emit_uses_same_release(
    episode: Path, monkeypatch: pytest.MonkeyPatch, mock_llm_response
) -> None:
    _write_episode(episode)
    mock_llm_response(_canned_llm_text())
    assert _plan(episode) == 0
    emitter = Mock(return_value=episode / "out" / "episode.fcpxml")
    monkeypatch.setattr("agents.brook.script_video.fcpxml_emitter.emit", emitter)

    assert _emit(episode) == 0

    emitter.assert_called_once()


def test_emit_rejects_storyboard_without_subtitle_source_record(
    episode: Path, monkeypatch: pytest.MonkeyPatch, mock_llm_response
) -> None:
    _write_episode(episode)
    mock_llm_response(_canned_llm_text())
    assert _plan(episode) == 0
    (episode / pipeline.SUBTITLE_SOURCE_RECORD).unlink()
    emitter = Mock(side_effect=AssertionError("emit must not run on an unbound storyboard"))
    monkeypatch.setattr("agents.brook.script_video.fcpxml_emitter.emit", emitter)

    with pytest.raises(VerifiedProjectionHandoffError, match="重跑 plan"):
        _emit(episode)

    emitter.assert_not_called()


def test_emit_rejects_storyboard_planned_on_a_different_release(
    episode: Path, monkeypatch: pytest.MonkeyPatch, mock_llm_response
) -> None:
    _write_episode(episode)
    mock_llm_response(_canned_llm_text())
    assert _plan(episode) == 0
    record_path = episode / pipeline.SUBTITLE_SOURCE_RECORD
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["subtitle"]["subtitle_srt_sha256"] = "0" * 64
    record_path.write_text(json.dumps(record), encoding="utf-8")
    emitter = Mock(side_effect=AssertionError("emit must not run on a stale storyboard"))
    monkeypatch.setattr("agents.brook.script_video.fcpxml_emitter.emit", emitter)

    with pytest.raises(VerifiedProjectionHandoffError, match="換過了"):
        _emit(episode)

    emitter.assert_not_called()


def test_emit_stops_without_subtitle_release(
    episode: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_episode(episode, with_release=False)
    (episode / "storyboard.yaml").write_text("[]\n", encoding="utf-8")
    emitter = Mock(side_effect=AssertionError("emit must not run without a subtitle release"))
    monkeypatch.setattr("agents.brook.script_video.fcpxml_emitter.emit", emitter)

    with pytest.raises(SystemExit, match="讀不到可用的字幕 release"):
        _emit(episode)

    emitter.assert_not_called()


def test_explicit_handoff_path_inside_episode_is_used(episode: Path, mock_llm_response) -> None:
    episode.mkdir(parents=True)
    (episode / "episode.yaml").write_text('title: "custom handoff"\n', encoding="utf-8")
    handoff = _memo_dual_audit_release_fixture(
        episode, srt_text=SYNTHETIC_SRT, relative_root="subtitle-release/rerun-2"
    )
    mock_llm_response(_canned_llm_text())

    assert _plan(episode, subtitle_release_handoff=str(handoff)) == 0
    assert (episode / pipeline.SUBTITLE_SOURCE_RECORD).exists()


def test_missing_episode_yaml_still_fails_first(episode: Path) -> None:
    with pytest.raises(SystemExit, match="episode.yaml not found"):
        _plan(episode)


@pytest.mark.parametrize("cmd", ["plan", "emit", "run"])
def test_cli_accepts_subtitle_release_handoff(cmd: str) -> None:
    args = pipeline._build_parser().parse_args(
        ["--episode", "ep", cmd, "--subtitle-release-handoff", "subtitle-release/x/H.json"]
    )
    assert args.subtitle_release_handoff == "subtitle-release/x/H.json"
