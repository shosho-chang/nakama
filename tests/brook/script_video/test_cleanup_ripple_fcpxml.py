"""Tests for agents.brook.script_video.cleanup.ripple_fcpxml (ADR-050 D3).

Formerly test_fcpxml_emitter.py (Manifest-based, ADR-015 Slice 1). Verifies:
- Emitted file is well-formed XML with FCPXML 1.10 root
- Required structural elements are present
- Spine clip count matches expected segments after ripple-delete
- Total timeline duration equals source duration minus deleted regions
- Referential integrity + asset-clip (not clip) interchange rule
"""

from __future__ import annotations

import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from agents.brook.script_video.cleanup.cuts import CutPoint
from agents.brook.script_video.cleanup.ripple_fcpxml import (
    build_kept_segments,
    emit_ripple_timeline,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _emit(
    tmp_path: Path,
    total_sec: float = 60.0,
    cuts: list[CutPoint] | None = None,
) -> Path:
    aroll_video = tmp_path / "ep001" / "aroll-video.mp4"
    aroll_video.parent.mkdir(parents=True, exist_ok=True)
    aroll_video.touch()
    out = tmp_path / "ep001" / "out" / "episode.fcpxml"
    emit_ripple_timeline(aroll_video, total_sec, cuts or [], "ep001", out)
    return out


def _ripple(start: float, end: float) -> CutPoint:
    return CutPoint(
        type="ripple-delete", start_sec=start, end_sec=end, reason="marker", confidence=0.9
    )


def _parse(path: Path) -> ET.Element:
    return ET.parse(path).getroot()


# ---------------------------------------------------------------------------
# Well-formedness and structure
# ---------------------------------------------------------------------------


def test_emit_creates_file(tmp_path: Path) -> None:
    assert _emit(tmp_path).exists()


def test_root_is_fcpxml_110(tmp_path: Path) -> None:
    root = _parse(_emit(tmp_path))
    assert root.tag == "fcpxml"
    assert root.get("version") == "1.10"


def test_required_structure_present(tmp_path: Path) -> None:
    root = _parse(_emit(tmp_path))
    assert root.find("resources") is not None
    assert root.find("resources/format") is not None
    assert root.find("resources/asset") is not None
    assert root.find("library/event/project/sequence/spine") is not None


def test_xml_declaration_present(tmp_path: Path) -> None:
    text = _emit(tmp_path).read_text(encoding="utf-8")
    assert '<?xml version="1.0"' in text
    assert "<!DOCTYPE fcpxml>" in text


# ---------------------------------------------------------------------------
# No cuts → single clip spanning full source
# ---------------------------------------------------------------------------


def test_no_cuts_emits_one_clip(tmp_path: Path) -> None:
    root = _parse(_emit(tmp_path, total_sec=60.0))
    clips = root.findall("library/event/project/sequence/spine/asset-clip")
    assert len(clips) == 1


def test_no_cuts_clip_offset_is_zero(tmp_path: Path) -> None:
    root = _parse(_emit(tmp_path, total_sec=30.0))
    clip = root.find("library/event/project/sequence/spine/asset-clip")
    assert clip.get("offset") == "0s"
    assert clip.get("start") == "0s"


# ---------------------------------------------------------------------------
# With cuts → multiple clips
# ---------------------------------------------------------------------------


def test_one_cut_emits_two_clips(tmp_path: Path) -> None:
    root = _parse(_emit(tmp_path, cuts=[_ripple(10.0, 20.0)]))
    clips = root.findall("library/event/project/sequence/spine/asset-clip")
    assert len(clips) == 2


def test_two_cuts_emit_three_clips(tmp_path: Path) -> None:
    root = _parse(_emit(tmp_path, cuts=[_ripple(5.0, 10.0), _ripple(20.0, 25.0)]))
    clips = root.findall("library/event/project/sequence/spine/asset-clip")
    assert len(clips) == 3


def test_sequence_duration_equals_kept_duration(tmp_path: Path) -> None:
    """Timeline duration should equal source duration minus all deleted regions."""
    root = _parse(_emit(tmp_path, total_sec=60.0, cuts=[_ripple(10.0, 20.0)]))
    seq = root.find("library/event/project/sequence")
    assert seq.get("duration") == "1500/30s"  # (60-10)s × 30fps


# ---------------------------------------------------------------------------
# build_kept_segments unit tests
# ---------------------------------------------------------------------------


def test_build_kept_segments_no_cuts() -> None:
    assert build_kept_segments(60.0, []) == [(0.0, 60.0)]


def test_build_kept_segments_middle_cut() -> None:
    assert build_kept_segments(60.0, [_ripple(10.0, 20.0)]) == [(0.0, 10.0), (20.0, 60.0)]


def test_build_kept_segments_overlapping_cuts_merged() -> None:
    segs = build_kept_segments(60.0, [_ripple(5.0, 15.0), _ripple(12.0, 20.0)])
    assert segs == [(0.0, 5.0), (20.0, 60.0)]


def test_build_kept_segments_razor_cuts_ignored() -> None:
    """Only ripple-delete CutPoints affect the timeline; razor cuts are no-ops."""
    razor = CutPoint(type="razor", start_sec=10.0, end_sec=20.0, reason="marker", confidence=0.9)
    assert build_kept_segments(60.0, [razor]) == [(0.0, 60.0)]


# ---------------------------------------------------------------------------
# Interchange rules (shared builder contract, re-asserted at adapter level)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("xmllint") is None, reason="xmllint not installed")
def test_xmllint_wellformed(tmp_path: Path) -> None:
    import subprocess

    out = _emit(tmp_path)
    result = subprocess.run(["xmllint", "--noout", str(out)], capture_output=True, text=True)
    assert result.returncode == 0, f"xmllint errors:\n{result.stderr}"


def test_fcpxml_lxml_strict_parse(tmp_path: Path) -> None:
    from lxml import etree as lxml_etree

    out = _emit(tmp_path, cuts=[_ripple(10.0, 20.0)])
    parser = lxml_etree.XMLParser(recover=False, no_network=True)
    lxml_etree.parse(str(out), parser)  # raises on any structural error


def test_fcpxml_referential_integrity(tmp_path: Path) -> None:
    root = _parse(_emit(tmp_path))
    declared = {el.get("id") for el in root.findall("resources/*") if el.get("id")}
    refs = {el.get("ref") for el in root.iter() if el.get("ref")}
    seq = root.find("library/event/project/sequence")
    if seq.get("format"):
        refs.add(seq.get("format"))
    assert refs <= declared, f"refs {refs - declared} have no matching <resources> entry"


def test_fcpxml_uses_asset_clip_not_clip(tmp_path: Path) -> None:
    root = _parse(_emit(tmp_path))
    spine = root.find("library/event/project/sequence/spine")
    for child in spine:
        if child.tag == "clip" and child.get("ref"):
            pytest.fail(f"<clip ref={child.get('ref')!r}> found — use <asset-clip> for asset refs")
