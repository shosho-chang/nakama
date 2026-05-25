"""DaVinci import fixture sanity tests.

Cheap automated checks the fixture has not silently drifted away from the
shape DaVinci actually accepts. The blocking acceptance is the manual import
smoke test documented in `fixtures/davinci_import/README.md` — these tests
just keep the file well-formed between manual runs.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "davinci_import"
FCPXML = FIXTURE_DIR / "minimal.fcpxml"
BLACK_MP4 = FIXTURE_DIR / "black10s.mp4"
BIGSTAT_MP4 = FIXTURE_DIR / "bigstat3s.mp4"


def test_fcpxml_is_well_formed_xml() -> None:
    tree = ET.parse(FCPXML)
    root = tree.getroot()
    assert root.tag == "fcpxml"
    assert root.get("version") == "1.10"


def test_fcpxml_declares_two_assets_referencing_companion_mp4s() -> None:
    tree = ET.parse(FCPXML)
    assets = tree.findall(".//resources/asset")
    assert len(assets) == 2
    media_srcs = {a.find("media-rep").get("src") for a in assets}
    assert media_srcs == {"black10s.mp4", "bigstat3s.mp4"}


def test_fcpxml_broll_is_lane_1_offset_4s() -> None:
    tree = ET.parse(FCPXML)
    nested = tree.find(".//spine/asset-clip/asset-clip")
    assert nested is not None, "expected nested B-roll asset-clip under A-roll"
    assert nested.get("lane") == "1"
    assert nested.get("offset") == "120/30s"
    assert nested.get("duration") == "90/30s"


@pytest.mark.parametrize("mp4", [BLACK_MP4, BIGSTAT_MP4])
def test_companion_mp4s_are_committed_and_nonempty(mp4: Path) -> None:
    assert mp4.exists(), f"missing fixture mp4: {mp4.name}"
    assert mp4.stat().st_size > 1000, f"fixture mp4 implausibly small: {mp4.name}"


@pytest.mark.skipif(
    not __import__("shutil").which("ffprobe"),
    reason="ffprobe not on PATH",
)
def test_companion_mp4s_match_fcpxml_timing() -> None:
    """Probe mp4 durations and check they line up with the fcpxml asset durations."""
    def duration(mp4: Path) -> float:
        out = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(mp4),
            ],
            text=True,
        )
        return float(out.strip())

    assert abs(duration(BLACK_MP4) - 10.0) < 0.1
    assert abs(duration(BIGSTAT_MP4) - 3.0) < 0.1
