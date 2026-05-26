"""fcpxml_emitter tests — emit storyboard → FCPXML, validate against
hand-authored minimal.fcpxml fixture shape.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from agents.foundry.fcpxml_emitter import emit

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "davinci_import"
BLACK_MP4 = FIXTURE_DIR / "black10s.mp4"
BIGSTAT_MP4 = FIXTURE_DIR / "bigstat3s.mp4"


def _build_episode(tmp_path: Path) -> Path:
    """Build a minimal episode dir mirroring the DaVinci import fixture."""
    ep = tmp_path / "test-episode-001"
    out = ep / "out"
    out.mkdir(parents=True)
    shutil.copy(BLACK_MP4, ep / "raw_recording.mp4")
    shutil.copy(BIGSTAT_MP4, out / "b_roll_42.mp4")
    return ep


def _storyboard_one_cutaway() -> list[dict]:
    return [
        {
            "beat_id": 42,
            "start_quote": "x",
            "end_quote": "y",
            "broll_decision": "cutaway",
            "layout": "full_broll",
            "timing": {"start": 4.0, "duration": 3.0},
            "broll": {
                "render_target": "hyperframes",
                "component": "bigstat",
                "params": {},
                "transitions": {},
            },
            "status": {
                "text_approved": True,
                "render_status": "done",
                "visual_approved": False,
            },
            "user_notes": [],
        }
    ]


@pytest.mark.skipif(not shutil.which("ffprobe"), reason="ffprobe required")
def test_emit_writes_episode_fcpxml(tmp_path):
    ep = _build_episode(tmp_path)
    out_path = emit(_storyboard_one_cutaway(), ep)

    assert out_path == ep / "out" / "episode.fcpxml"
    assert out_path.read_text(encoding="utf-8").startswith(
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>'
    )


@pytest.mark.skipif(not shutil.which("ffprobe"), reason="ffprobe required")
def test_emit_matches_minimal_fixture_structure(tmp_path):
    """Emitted FCPXML has the same critical shape as hand-authored fixture."""
    ep = _build_episode(tmp_path)
    out_path = emit(_storyboard_one_cutaway(), ep)
    emitted = ET.parse(out_path).getroot()
    fixture = ET.parse(FIXTURE_DIR / "minimal.fcpxml").getroot()

    assert emitted.get("version") == fixture.get("version") == "1.10"
    # Both have format r1 + 2 assets
    assert len(emitted.findall(".//resources/format")) == 1
    assert len(emitted.findall(".//resources/asset")) == 2
    # Spine has nested asset-clip at lane="1"
    nested = emitted.find(".//spine/asset-clip/asset-clip")
    fix_nested = fixture.find(".//spine/asset-clip/asset-clip")
    assert nested is not None
    assert nested.get("lane") == fix_nested.get("lane") == "1"
    assert nested.get("offset") == fix_nested.get("offset") == "120/30s"
    assert nested.get("duration") == fix_nested.get("duration") == "90/30s"


@pytest.mark.skipif(not shutil.which("ffprobe"), reason="ffprobe required")
def test_emit_writes_absolute_file_uri_in_media_rep(tmp_path):
    """DaVinci rejects relative media-rep src — must be absolute file:// URI."""
    ep = _build_episode(tmp_path)
    out_path = emit(_storyboard_one_cutaway(), ep)
    root = ET.parse(out_path).getroot()
    srcs = [mr.get("src") for mr in root.findall(".//resources/asset/media-rep")]
    assert len(srcs) == 2
    for src in srcs:
        assert src.startswith("file:///"), f"media-rep src not absolute file:// URI: {src}"


@pytest.mark.skipif(not shutil.which("ffprobe"), reason="ffprobe required")
def test_emit_skips_beats_without_render_status_done(tmp_path):
    ep = _build_episode(tmp_path)
    storyboard = _storyboard_one_cutaway()
    storyboard[0]["status"]["render_status"] = "pending"
    out_path = emit(storyboard, ep)
    nested = ET.parse(out_path).getroot().find(".//spine/asset-clip/asset-clip")
    assert nested is None, "pending beat should not appear in spine"


@pytest.mark.skipif(not shutil.which("ffprobe"), reason="ffprobe required")
def test_emit_raises_when_raw_recording_missing(tmp_path):
    ep = tmp_path / "ep"
    ep.mkdir()
    with pytest.raises(FileNotFoundError, match="raw_recording.mp4"):
        emit(_storyboard_one_cutaway(), ep)


@pytest.mark.skipif(not shutil.which("ffprobe"), reason="ffprobe required")
def test_emit_raises_for_cutaway_without_rendered_mp4(tmp_path):
    ep = tmp_path / "ep"
    ep.mkdir()
    shutil.copy(BLACK_MP4, ep / "raw_recording.mp4")
    (ep / "out").mkdir()
    with pytest.raises(ValueError, match="mp4 not found"):
        emit(_storyboard_one_cutaway(), ep)


@pytest.mark.skipif(not shutil.which("ffprobe"), reason="ffprobe required")
def test_emit_supports_fcpxml_version_fallbacks(tmp_path):
    ep = _build_episode(tmp_path)
    for ver in ("1.10", "1.11", "1.9"):
        out = emit(_storyboard_one_cutaway(), ep, fcpxml_version=ver)
        assert ET.parse(out).getroot().get("version") == ver


def test_emit_rejects_unsupported_fcpxml_version(tmp_path):
    ep = tmp_path / "ep"
    ep.mkdir()
    with pytest.raises(ValueError, match="fcpxml_version must"):
        emit([], ep, fcpxml_version="1.8")  # type: ignore[arg-type]
