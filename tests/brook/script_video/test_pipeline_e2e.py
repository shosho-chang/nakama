"""End-to-end smoke test for the Script-Driven Video pipeline.

Catches integration-only regressions that the per-stage unit tests miss:
- Stage 0 ffmpeg codec ↔ Stage 1 ``_load_wav`` (PR #320 mp3-vs-wav bug)
- Stage 2 ``parse.js`` CLI invocation contract (PR #320 missing-CLI-main bug)
- ``manifest.total_frames`` anchored to source-time seconds (PR #320 placeholder bug)
- ``dist/src/parser/parse.js`` build path (PR #320 tsconfig rootDir bug)

Skips gracefully when ffmpeg / node / built ``video/dist`` are unavailable so
local dev without full bring-up still runs the rest of the suite.
"""

from __future__ import annotations

import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from agents.brook.script_video import pipeline

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FIXTURE_WAV = _REPO_ROOT / "tests" / "fixtures" / "script_video" / "clap_marker_audio.wav"
_VIDEO_DIR = _REPO_ROOT / "video"
_PARSER_DIST = _VIDEO_DIR / "dist" / "src" / "parser" / "parse.js"


@pytest.fixture
def smoke_episode(tmp_path, monkeypatch):
    """Create a minimal episode dir with raw_recording.mp4 + script.md."""
    if not _FIXTURE_WAV.exists():
        pytest.skip(f"Missing fixture WAV: {_FIXTURE_WAV}")
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available")
    if shutil.which("node") is None:
        pytest.skip("node not available")
    if not _PARSER_DIST.exists():
        pytest.skip("video/ subproject not built — run `cd video && npm install && npm run build`")

    episode_id = "e2e-smoke"
    data_root = tmp_path / "script_video"
    episode_dir = data_root / episode_id
    episode_dir.mkdir(parents=True)

    raw_mp4 = episode_dir / "raw_recording.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(_FIXTURE_WAV),
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x240:r=30",
            "-shortest",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(raw_mp4),
        ],
        check=True,
        capture_output=True,
    )

    (episode_dir / "script.md").write_text("# E2E\n\n[aroll-full]\n測試錄音。\n", encoding="utf-8")

    monkeypatch.setattr(pipeline, "_DATA_ROOT", data_root)
    return episode_id


def test_pipeline_e2e_emits_fcpxml_with_clips(smoke_episode):
    """Full pipeline produces a well-formed FCPXML with kept-segment clips."""
    result = pipeline.run(smoke_episode)

    assert result.fcpxml_path.exists(), "FCPXML not emitted"
    assert result.srt_path.exists(), "SRT not emitted"
    assert len(result.cuts) >= 1, "fixture should yield at least 1 clap marker"

    tree = ET.parse(str(result.fcpxml_path))
    root = tree.getroot()
    assert root.tag == "fcpxml"
    assert root.attrib.get("version") == "1.10"

    spine = root.find(".//spine")
    assert spine is not None, "spine element missing"
    clips = spine.findall("asset-clip")
    assert len(clips) >= 1, "spine should contain at least one kept-segment clip"


def test_pipeline_e2e_total_frames_matches_source_audio(smoke_episode):
    """manifest.total_frames must reflect source-media duration, not parser placeholder.

    Regression for PR #320: parser's word-count placeholder (~30 frames)
    leaked into fcpxml_emitter, collapsing 8s timelines to 0.5s.
    """
    import json
    import wave

    pipeline.run(smoke_episode)

    episode_dir = pipeline._DATA_ROOT / smoke_episode  # type: ignore[attr-defined]
    with wave.open(str(episode_dir / "aroll-audio.wav"), "rb") as wf:
        actual_sec = wf.getnframes() / wf.getframerate()
    expected_frames = round(actual_sec * 30)

    with (episode_dir / "manifest.json").open() as f:
        data = json.load(f)
    assert abs(data["total_frames"] - expected_frames) <= 1, (
        f"total_frames={data['total_frames']} but source audio is {actual_sec:.2f}s "
        f"({expected_frames} frames @ 30fps) — parser placeholder leaked through"
    )


def test_pipeline_e2e_xmllint_validates(smoke_episode):
    """FCPXML passes xmllint --noout (well-formed XML)."""
    if shutil.which("xmllint") is None:
        pytest.skip("xmllint not available")

    result = pipeline.run(smoke_episode)
    proc = subprocess.run(
        ["xmllint", "--noout", str(result.fcpxml_path)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"xmllint failed: {proc.stderr}"
