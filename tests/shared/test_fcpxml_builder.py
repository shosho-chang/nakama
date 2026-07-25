"""Golden tests for shared.fcpxml — the single FCPXML builder seam (ADR-050 D2).

Every DaVinci quirk the two retired sibling emitters learned via import-smoke
rounds is asserted here so a regression in the shared layer fails loudly:
absolute file:// media-rep, <asset-clip> (never <clip ref=...>), rational
N/30s durations with "0s" zero, deterministic UIDs, version fallback ladder.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from shared.fcpxml import (
    SUPPORTED_VERSIONS,
    Asset,
    Clip,
    Timeline,
    build_fcpxml,
    deterministic_uid,
    merge_ripple_segments,
    rational_duration,
    write_fcpxml,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _asset(tmp_path: Path, name: str = "aroll.mp4", **kw) -> Asset:
    p = tmp_path / name
    p.touch()
    return Asset(id=kw.pop("id", "r2"), path=p, duration_sec=kw.pop("duration_sec", 60.0), **kw)


def _simple_timeline(asset_id: str = "r2", duration: float = 60.0) -> Timeline:
    return Timeline(
        name="ep001",
        duration_sec=duration,
        spine=(Clip(asset_id=asset_id, offset_sec=0.0, duration_sec=duration),),
    )


# ---------------------------------------------------------------------------
# Deterministic helpers
# ---------------------------------------------------------------------------


def test_rational_duration_zero_is_bare_0s() -> None:
    assert rational_duration(0.0) == "0s"


def test_rational_duration_frames() -> None:
    assert rational_duration(1.0) == "30/30s"
    assert rational_duration(0.5) == "15/30s"
    assert rational_duration(4.0) == "120/30s"


def test_deterministic_uid_stable_and_hex16() -> None:
    a = deterministic_uid("asset-ep001")
    assert a == deterministic_uid("asset-ep001")
    assert len(a) == 16
    assert a == a.upper()
    assert a != deterministic_uid("asset-ep002")


def test_merge_ripple_segments_no_cuts() -> None:
    assert merge_ripple_segments(60.0, []) == [(0.0, 60.0)]


def test_merge_ripple_segments_middle_cut() -> None:
    assert merge_ripple_segments(60.0, [(10.0, 20.0)]) == [(0.0, 10.0), (20.0, 60.0)]


def test_merge_ripple_segments_overlaps_merged() -> None:
    assert merge_ripple_segments(60.0, [(5.0, 15.0), (12.0, 20.0)]) == [
        (0.0, 5.0),
        (20.0, 60.0),
    ]


def test_merge_ripple_segments_drops_reversed_region() -> None:
    """A reversed (end < start) cut must not produce overlapping kept segments."""
    assert merge_ripple_segments(60.0, [(20.0, 10.0)]) == [(0.0, 60.0)]


def test_merge_ripple_segments_clamps_out_of_range() -> None:
    assert merge_ripple_segments(60.0, [(-5.0, 10.0), (55.0, 90.0)]) == [(10.0, 55.0)]


def test_rational_duration_rejects_negative() -> None:
    with pytest.raises(ValueError, match="must be >= 0"):
        rational_duration(-1.0)


# ---------------------------------------------------------------------------
# Version ladder
# ---------------------------------------------------------------------------


def test_build_rejects_unsupported_version(tmp_path: Path) -> None:
    asset = _asset(tmp_path)
    with pytest.raises(ValueError, match="fcpxml_version must"):
        build_fcpxml(_simple_timeline(), [asset], version="1.8")  # type: ignore[arg-type]


def test_build_supports_fallback_ladder(tmp_path: Path) -> None:
    asset = _asset(tmp_path)
    for ver in SUPPORTED_VERSIONS:
        root = build_fcpxml(_simple_timeline(), [asset], version=ver).getroot()
        assert root.get("version") == ver


# ---------------------------------------------------------------------------
# DaVinci quirk assertions
# ---------------------------------------------------------------------------


def test_media_rep_src_is_absolute_file_uri(tmp_path: Path) -> None:
    """DaVinci silently rejects relative media-rep src at import time."""
    asset = _asset(tmp_path)
    root = build_fcpxml(_simple_timeline(), [asset]).getroot()
    srcs = [mr.get("src") for mr in root.findall(".//asset/media-rep")]
    assert srcs, "media-rep missing"
    for src in srcs:
        assert src.startswith("file:///"), f"not absolute file:// URI: {src}"


def test_spine_uses_asset_clip_never_clip(tmp_path: Path) -> None:
    """Final Cut Pro rejects <clip ref=...>; interchange requires <asset-clip>."""
    asset = _asset(tmp_path)
    root = build_fcpxml(_simple_timeline(), [asset]).getroot()
    spine = root.find(".//spine")
    assert spine is not None
    assert [c.tag for c in spine] == ["asset-clip"]


def test_clip_referencing_undeclared_asset_fails_loud(tmp_path: Path) -> None:
    asset = _asset(tmp_path)
    with pytest.raises(ValueError, match="undeclared asset id"):
        build_fcpxml(_simple_timeline(asset_id="r99"), [asset])


def test_duplicate_asset_ids_fail_loud(tmp_path: Path) -> None:
    a = _asset(tmp_path, "a.mp4", id="r2")
    b = _asset(tmp_path, "b.mp4", id="r2")
    with pytest.raises(ValueError, match="duplicate asset ids"):
        build_fcpxml(_simple_timeline(), [a, b])


def test_referential_integrity(tmp_path: Path) -> None:
    """Every ref/format attribute resolves to a declared resource id."""
    aroll = _asset(tmp_path, "aroll.mp4", id="r2", has_audio=True)
    broll = _asset(tmp_path, "broll.mp4", id="r3")
    timeline = Timeline(
        name="ep001",
        duration_sec=60.0,
        spine=(
            Clip(
                asset_id="r2",
                offset_sec=0.0,
                duration_sec=60.0,
                children=(Clip(asset_id="r3", offset_sec=4.0, duration_sec=3.0, lane=1),),
            ),
        ),
    )
    root = build_fcpxml(timeline, [aroll, broll]).getroot()
    declared = {el.get("id") for el in root.findall("resources/*")}
    refs = {el.get("ref") for el in root.iter() if el.get("ref")}
    seq = root.find(".//sequence")
    refs.add(seq.get("format"))
    assert refs <= declared, f"dangling refs: {refs - declared}"


def test_nested_overlay_clip_shape(tmp_path: Path) -> None:
    """B-roll overlays nest inside the primary asset-clip at lane=1."""
    aroll = _asset(tmp_path, "aroll.mp4", id="r2", has_audio=True)
    broll = _asset(tmp_path, "broll.mp4", id="r3", duration_sec=3.0)
    timeline = Timeline(
        name="ep001",
        duration_sec=60.0,
        spine=(
            Clip(
                asset_id="r2",
                offset_sec=0.0,
                duration_sec=60.0,
                children=(Clip(asset_id="r3", offset_sec=4.0, duration_sec=3.0, lane=1),),
            ),
        ),
    )
    root = build_fcpxml(timeline, [aroll, broll]).getroot()
    nested = root.find(".//spine/asset-clip/asset-clip")
    assert nested is not None
    assert nested.get("lane") == "1"
    assert nested.get("offset") == "120/30s"
    assert nested.get("duration") == "90/30s"


def test_asset_uid_deterministic_across_builds(tmp_path: Path) -> None:
    """Re-emitting the same episode keeps clip identity across DaVinci re-imports."""
    asset = _asset(tmp_path, uid_seed="asset-ep001")
    uid1 = build_fcpxml(_simple_timeline(), [asset]).getroot().find(".//asset").get("uid")
    uid2 = build_fcpxml(_simple_timeline(), [asset]).getroot().find(".//asset").get("uid")
    assert uid1 == uid2 == deterministic_uid("asset-ep001")


def test_event_project_uids_emitted_when_seeded(tmp_path: Path) -> None:
    asset = _asset(tmp_path)
    timeline = Timeline(
        name="ep001",
        duration_sec=60.0,
        spine=(Clip(asset_id="r2", offset_sec=0.0, duration_sec=60.0),),
        event_uid_seed="event-ep001",
        project_uid_seed="project-ep001",
    )
    root = build_fcpxml(timeline, [asset]).getroot()
    assert root.find(".//event").get("uid") == deterministic_uid("event-ep001")
    assert root.find(".//project").get("uid") == deterministic_uid("project-ep001")


def test_sequence_attrs_frozen(tmp_path: Path) -> None:
    asset = _asset(tmp_path)
    seq = build_fcpxml(_simple_timeline(), [asset]).getroot().find(".//sequence")
    assert seq.get("format") == "r1"
    assert seq.get("tcStart") == "0s"
    assert seq.get("tcFormat") == "NDF"
    assert seq.get("audioLayout") == "stereo"
    assert seq.get("audioRate") == "48k"
    assert seq.get("duration") == "1800/30s"


def test_audio_attrs_follow_has_audio_flag(tmp_path: Path) -> None:
    aroll = _asset(tmp_path, "aroll.mp4", id="r2", has_audio=True)
    broll = _asset(tmp_path, "broll.mp4", id="r3")
    root = build_fcpxml(_simple_timeline(), [aroll, broll]).getroot()
    by_id = {a.get("id"): a for a in root.findall(".//resources/asset")}
    assert by_id["r2"].get("hasAudio") == "1"
    assert by_id["r2"].get("audioRate") == "48000"
    assert by_id["r3"].get("hasAudio") is None


# ---------------------------------------------------------------------------
# Writer header convention
# ---------------------------------------------------------------------------


def test_write_fcpxml_header_convention(tmp_path: Path) -> None:
    asset = _asset(tmp_path)
    tree = build_fcpxml(_simple_timeline(), [asset])
    out = tmp_path / "out" / "episode.fcpxml"
    write_fcpxml(tree, out)
    text = out.read_text(encoding="utf-8")
    assert text.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>')
    # Round-trips through a strict parser
    assert ET.parse(out).getroot().tag == "fcpxml"
