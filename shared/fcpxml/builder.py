"""Single FCPXML builder — every DaVinci quirk lives here (ADR-050 D2).

Consolidates the two sibling emitters that used to drift independently
(``agents/brook/script_video/cleanup/ripple_fcpxml.py`` ripple-delete shape
and ``agents/brook/script_video/fcpxml_emitter.py`` B-roll overlay shape,
both now thin adapters). Callers construct
a :class:`Timeline` of :class:`Clip` referencing :class:`Asset` entries and
hand them to :func:`build_fcpxml` / :func:`write_fcpxml`.

DaVinci / FCPXML interchange rules enforced here (each cost a real
import-smoke round to learn — do not relax without re-running the
DaVinci import fixture):

- ``media-rep src`` must be an **absolute** ``file://`` URI; DaVinci
  silently drops relative paths at import time.
- Whole-asset references use ``<asset-clip>``; Final Cut Pro rejects
  ``<clip ref=...>`` even though DaVinci tolerates it.
- Durations/offsets are rational ``N/<fps>s`` strings (exact fps, not
  29.97); zero is written as ``0s``.
- UIDs are deterministic (sha256 of a caller-supplied seed) so re-emitting
  the same episode keeps clip identity across DaVinci re-imports.
- Version fallback ladder 1.10 → 1.11 → 1.9 for DaVinci versions that
  reject the default (ADR-032 §Risks).
"""

from __future__ import annotations

import dataclasses
import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable, Literal, Sequence

FcpxmlVersion = Literal["1.10", "1.11", "1.9"]
SUPPORTED_VERSIONS: tuple[FcpxmlVersion, ...] = ("1.10", "1.11", "1.9")

DEFAULT_FPS = 30  # composition spec assumes exact 30fps (ADR-032 Phase 1)


# ---------------------------------------------------------------------------
# Deterministic helpers
# ---------------------------------------------------------------------------


def rational_duration(seconds: float, fps: int = DEFAULT_FPS) -> str:
    """Seconds → FCPXML rational duration string (``N/<fps>s``; zero → ``0s``)."""
    if seconds < 0:
        raise ValueError(f"duration/offset must be >= 0, got {seconds}")
    frames = round(seconds * fps)
    if frames == 0:
        return "0s"
    return f"{frames}/{fps}s"


def deterministic_uid(seed: str) -> str:
    """Stable 16-char hex UID from *seed* — keeps clip identity across re-emits."""
    return hashlib.sha256(seed.encode()).hexdigest()[:16].upper()


def merge_ripple_segments(
    total_duration_sec: float,
    delete_regions: Iterable[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Split a source timeline into kept segments after ripple deletes.

    *delete_regions* are (start_sec, end_sec) pairs in source coordinates;
    overlapping/adjacent regions are merged. Reversed, zero-length, or fully
    out-of-range regions are dropped; partially out-of-range regions are
    clamped to ``[0, total_duration_sec]`` — a malformed cut must never
    produce overlapping or negative-start kept segments. Returns ordered,
    non-overlapping kept segments.
    """
    cleaned: list[tuple[float, float]] = []
    for start, end in delete_regions:
        start = max(0.0, start)
        end = min(end, total_duration_sec)
        if end <= start:
            continue
        cleaned.append((start, end))

    merged: list[tuple[float, float]] = []
    for start, end in sorted(cleaned):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    segments: list[tuple[float, float]] = []
    cursor = 0.0
    for cut_start, cut_end in merged:
        if cut_start > cursor:
            segments.append((cursor, cut_start))
        cursor = cut_end
    if cursor < total_duration_sec:
        segments.append((cursor, total_duration_sec))
    return segments


# ---------------------------------------------------------------------------
# Timeline model
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Asset:
    """One media file in <resources>. ``uid_seed`` defaults to the file name."""

    id: str
    path: Path
    duration_sec: float
    name: str | None = None
    uid_seed: str | None = None
    has_video: bool = True
    has_audio: bool = False
    format_ref: str | None = "r1"

    @property
    def display_name(self) -> str:
        return self.name if self.name is not None else self.path.stem

    @property
    def uid(self) -> str:
        return deterministic_uid(self.uid_seed or self.display_name)


@dataclasses.dataclass(frozen=True)
class Clip:
    """One <asset-clip> in the spine (or nested overlay when ``lane`` is set)."""

    asset_id: str
    offset_sec: float
    duration_sec: float
    start_sec: float = 0.0
    name: str | None = None
    lane: int | None = None
    children: tuple["Clip", ...] = ()


@dataclasses.dataclass(frozen=True)
class Timeline:
    """One library/event/project/sequence tree."""

    name: str
    duration_sec: float
    spine: tuple[Clip, ...]
    event_name: str | None = None
    event_uid_seed: str | None = None
    project_uid_seed: str | None = None


# ---------------------------------------------------------------------------
# Document builder
# ---------------------------------------------------------------------------


def build_fcpxml(
    timeline: Timeline,
    assets: Sequence[Asset],
    *,
    version: FcpxmlVersion = "1.10",
    fps: int = DEFAULT_FPS,
) -> ET.ElementTree:
    """Build the FCPXML document tree. Raises ValueError on unsupported version."""
    if version not in SUPPORTED_VERSIONS:
        raise ValueError(f"fcpxml_version must be one of {SUPPORTED_VERSIONS}, got {version!r}")

    asset_ids = {a.id for a in assets}
    if len(asset_ids) != len(assets):
        dupes = [a.id for a in assets if [b.id for b in assets].count(a.id) > 1]
        raise ValueError(f"duplicate asset ids: {sorted(set(dupes))}")

    root = ET.Element("fcpxml", {"version": version})
    resources = ET.SubElement(root, "resources")
    ET.SubElement(
        resources,
        "format",
        {
            "id": "r1",
            "name": f"FFVideoFormat1080p{fps}",
            "frameDuration": f"1/{fps}s",
            "width": "1920",
            "height": "1080",
            "colorSpace": "1-1-1 (Rec. 709)",
        },
    )

    for asset in assets:
        attrs: dict[str, str] = {
            "id": asset.id,
            "name": asset.display_name,
            "uid": asset.uid,
            "start": "0s",
            "duration": rational_duration(asset.duration_sec, fps),
            "src": asset.path.resolve().as_uri(),
        }
        if asset.has_video:
            attrs["hasVideo"] = "1"
            attrs["videoSources"] = "1"
        if asset.has_audio:
            attrs["hasAudio"] = "1"
            attrs["audioSources"] = "1"
            attrs["audioChannels"] = "2"
            attrs["audioRate"] = "48000"
        if asset.format_ref:
            attrs["format"] = asset.format_ref
        asset_el = ET.SubElement(resources, "asset", attrs)
        ET.SubElement(
            asset_el,
            "media-rep",
            # DaVinci requires absolute file:// URIs here; relative paths are
            # silently rejected at import time.
            {
                "kind": "original-media",
                "sig": asset.uid,
                "src": asset.path.resolve().as_uri(),
            },
        )

    library = ET.SubElement(root, "library")
    event_attrs = {"name": timeline.event_name or timeline.name}
    if timeline.event_uid_seed:
        event_attrs["uid"] = deterministic_uid(timeline.event_uid_seed)
    event = ET.SubElement(library, "event", event_attrs)
    project_attrs = {"name": timeline.name}
    if timeline.project_uid_seed:
        project_attrs["uid"] = deterministic_uid(timeline.project_uid_seed)
    project = ET.SubElement(event, "project", project_attrs)
    sequence = ET.SubElement(
        project,
        "sequence",
        {
            "format": "r1",
            "duration": rational_duration(timeline.duration_sec, fps),
            "tcStart": "0s",
            "tcFormat": "NDF",
            "audioLayout": "stereo",
            "audioRate": "48k",
        },
    )
    spine = ET.SubElement(sequence, "spine")
    for clip in timeline.spine:
        _emit_clip(spine, clip, asset_ids, fps)

    return ET.ElementTree(root)


def _emit_clip(parent: ET.Element, clip: Clip, asset_ids: set[str], fps: int) -> None:
    if clip.asset_id not in asset_ids:
        raise ValueError(f"clip references undeclared asset id {clip.asset_id!r}")
    attrs = {
        "name": clip.name or clip.asset_id,
        "ref": clip.asset_id,
        "offset": rational_duration(clip.offset_sec, fps),
        "duration": rational_duration(clip.duration_sec, fps),
        "start": rational_duration(clip.start_sec, fps),
    }
    if clip.lane is not None:
        attrs["lane"] = str(clip.lane)
    el = ET.SubElement(parent, "asset-clip", attrs)
    for child in clip.children:
        _emit_clip(el, child, asset_ids, fps)


def write_fcpxml(tree: ET.ElementTree, path: Path) -> None:
    """Serialize with the FCPXML header convention both NLE smokes verified."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(tree.getroot(), space="    ")
    body = ET.tostring(tree.getroot(), encoding="unicode")
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n' + body + "\n",
        encoding="utf-8",
    )
