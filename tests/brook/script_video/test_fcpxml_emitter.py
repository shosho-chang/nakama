"""Tests for agents.brook.script_video.fcpxml_emitter.

Verifies:
- Emitted file is well-formed XML
- Root element is <fcpxml version="1.10">
- Required structural elements are present
- Spine clip count matches expected segments after ripple-delete
- Total timeline duration equals source duration minus deleted regions
"""

from __future__ import annotations

import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from agents.brook.script_video.cuts import CutPoint
from agents.brook.script_video.fcpxml_emitter import _build_segments, _dur, emit
from agents.brook.script_video.manifest import ARollFullScene, Manifest, ManifestCutPoint

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manifest(
    total_sec: float = 60.0,
    cuts: list[CutPoint] | None = None,
    *,
    tmp_path: Path,
) -> Manifest:
    aroll_video = tmp_path / "ep001" / "aroll-video.mp4"
    aroll_audio = tmp_path / "ep001" / "aroll-audio.mp3"
    aroll_video.parent.mkdir(parents=True, exist_ok=True)
    aroll_video.touch()
    aroll_audio.touch()

    fps = 30
    total_frames = int(total_sec * fps)
    scene = ARollFullScene(
        id="scene-001",
        start_frame=0,
        duration_frames=total_frames,
        aroll_start_sec=0.0,
    )
    manifest_cuts = [ManifestCutPoint.from_cut_point(c) for c in (cuts or [])]
    return Manifest(
        episode_id="ep001",
        fps=fps,
        total_frames=total_frames,
        scenes=[scene],
        aroll_audio=str(aroll_audio),
        aroll_video=str(aroll_video),
        cuts=manifest_cuts,
    )


def _parse_fcpxml(path: Path) -> ET.Element:
    tree = ET.parse(path)
    return tree.getroot()


# ---------------------------------------------------------------------------
# Well-formedness and structure
# ---------------------------------------------------------------------------


def test_emit_creates_file(tmp_path: Path) -> None:
    manifest = _make_manifest(tmp_path=tmp_path)
    out = tmp_path / "ep001" / "out" / "episode.fcpxml"
    emit(manifest, out)
    assert out.exists()


def test_root_is_fcpxml_110(tmp_path: Path) -> None:
    manifest = _make_manifest(tmp_path=tmp_path)
    out = tmp_path / "ep001" / "out" / "episode.fcpxml"
    emit(manifest, out)
    root = _parse_fcpxml(out)
    assert root.tag == "fcpxml"
    assert root.get("version") == "1.10"


def test_required_structure_present(tmp_path: Path) -> None:
    manifest = _make_manifest(tmp_path=tmp_path)
    out = tmp_path / "ep001" / "out" / "episode.fcpxml"
    emit(manifest, out)
    root = _parse_fcpxml(out)

    assert root.find("resources") is not None
    assert root.find("resources/format") is not None
    assert root.find("resources/asset") is not None
    assert root.find("library") is not None
    assert root.find("library/event") is not None
    assert root.find("library/event/project") is not None
    assert root.find("library/event/project/sequence") is not None
    assert root.find("library/event/project/sequence/spine") is not None


def test_xml_declaration_present(tmp_path: Path) -> None:
    manifest = _make_manifest(tmp_path=tmp_path)
    out = tmp_path / "ep001" / "out" / "episode.fcpxml"
    emit(manifest, out)
    text = out.read_text(encoding="utf-8")
    assert '<?xml version="1.0"' in text
    assert "<!DOCTYPE fcpxml>" in text


# ---------------------------------------------------------------------------
# No cuts → single clip spanning full source
# ---------------------------------------------------------------------------


def test_no_cuts_emits_one_clip(tmp_path: Path) -> None:
    manifest = _make_manifest(total_sec=60.0, cuts=[], tmp_path=tmp_path)
    out = tmp_path / "ep001" / "out" / "episode.fcpxml"
    emit(manifest, out)
    root = _parse_fcpxml(out)
    clips = root.findall("library/event/project/sequence/spine/clip")
    assert len(clips) == 1


def test_no_cuts_clip_offset_is_zero(tmp_path: Path) -> None:
    manifest = _make_manifest(total_sec=30.0, cuts=[], tmp_path=tmp_path)
    out = tmp_path / "ep001" / "out" / "episode.fcpxml"
    emit(manifest, out)
    root = _parse_fcpxml(out)
    clip = root.find("library/event/project/sequence/spine/clip")
    assert clip is not None
    assert clip.get("offset") == "0s"
    assert clip.get("start") == "0s"


# ---------------------------------------------------------------------------
# With cuts → multiple clips
# ---------------------------------------------------------------------------


def test_one_cut_emits_two_clips(tmp_path: Path) -> None:
    cuts = [
        CutPoint(
            type="ripple-delete", start_sec=10.0, end_sec=20.0, reason="marker", confidence=0.9
        )
    ]
    manifest = _make_manifest(total_sec=60.0, cuts=cuts, tmp_path=tmp_path)
    out = tmp_path / "ep001" / "out" / "episode.fcpxml"
    emit(manifest, out)
    root = _parse_fcpxml(out)
    clips = root.findall("library/event/project/sequence/spine/clip")
    assert len(clips) == 2


def test_two_cuts_emit_three_clips(tmp_path: Path) -> None:
    cuts = [
        CutPoint(
            type="ripple-delete", start_sec=5.0, end_sec=10.0, reason="marker", confidence=0.9
        ),
        CutPoint(
            type="ripple-delete", start_sec=20.0, end_sec=25.0, reason="marker", confidence=0.9
        ),
    ]
    manifest = _make_manifest(total_sec=60.0, cuts=cuts, tmp_path=tmp_path)
    out = tmp_path / "ep001" / "out" / "episode.fcpxml"
    emit(manifest, out)
    root = _parse_fcpxml(out)
    clips = root.findall("library/event/project/sequence/spine/clip")
    assert len(clips) == 3


def test_sequence_duration_equals_kept_duration(tmp_path: Path) -> None:
    """Timeline duration should equal source duration minus all deleted regions."""
    cuts = [
        CutPoint(
            type="ripple-delete", start_sec=10.0, end_sec=20.0, reason="marker", confidence=0.9
        ),
    ]
    total_sec = 60.0
    expected_dur_frames = int((60.0 - 10.0) * 30)  # 50 s × 30 fps = 1500 frames
    manifest = _make_manifest(total_sec=total_sec, cuts=cuts, tmp_path=tmp_path)
    out = tmp_path / "ep001" / "out" / "episode.fcpxml"
    emit(manifest, out)
    root = _parse_fcpxml(out)
    seq = root.find("library/event/project/sequence")
    assert seq is not None
    duration_str = seq.get("duration", "")
    # Duration should be "1500/30s"
    assert duration_str == f"{expected_dur_frames}/30s", (
        f"Sequence duration={duration_str!r}, expected {expected_dur_frames}/30s"
    )


# ---------------------------------------------------------------------------
# _build_segments unit tests
# ---------------------------------------------------------------------------


def test_build_segments_no_cuts() -> None:
    segs = _build_segments(60.0, [])
    assert segs == [(0.0, 60.0)]


def test_build_segments_middle_cut() -> None:
    cuts = [
        CutPoint(
            type="ripple-delete", start_sec=10.0, end_sec=20.0, reason="marker", confidence=0.9
        )
    ]
    segs = _build_segments(60.0, cuts)
    assert segs == [(0.0, 10.0), (20.0, 60.0)]


def test_build_segments_overlapping_cuts_merged() -> None:
    cuts = [
        CutPoint(
            type="ripple-delete", start_sec=5.0, end_sec=15.0, reason="marker", confidence=0.9
        ),
        CutPoint(
            type="ripple-delete", start_sec=12.0, end_sec=20.0, reason="marker", confidence=0.9
        ),
    ]
    segs = _build_segments(60.0, cuts)
    # Should merge into one cut [5.0, 20.0]
    assert segs == [(0.0, 5.0), (20.0, 60.0)]


def test_build_segments_razor_cuts_ignored() -> None:
    """Only ripple-delete CutPoints affect the timeline; razor cuts are no-ops."""
    cuts = [
        CutPoint(type="razor", start_sec=10.0, end_sec=20.0, reason="marker", confidence=0.9),
    ]
    segs = _build_segments(60.0, cuts)
    assert segs == [(0.0, 60.0)]


# ---------------------------------------------------------------------------
# _dur helper
# ---------------------------------------------------------------------------


def test_dur_zero() -> None:
    assert _dur(0.0) == "0s"


def test_dur_one_second() -> None:
    assert _dur(1.0) == "30/30s"


def test_dur_fractional() -> None:
    # 0.5 s = 15 frames at 30fps
    assert _dur(0.5) == "15/30s"


# ---------------------------------------------------------------------------
# xmllint validation (skipped if not available)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("xmllint") is None, reason="xmllint not installed")
def test_xmllint_wellformed(tmp_path: Path) -> None:
    import subprocess

    manifest = _make_manifest(tmp_path=tmp_path)
    out = tmp_path / "ep001" / "out" / "episode.fcpxml"
    emit(manifest, out)
    result = subprocess.run(
        ["xmllint", "--noout", str(out)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"xmllint errors:\n{result.stderr}"
