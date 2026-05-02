"""FCPXML 1.10 emitter — generate a DaVinci-importable timeline.

Emits a minimal FCPXML 1.10 document containing:
  - V1 track: A-roll clips with CutPoints applied as ripple deletes
  - Resources: asset reference + 1080p 30fps format
  - No V2-V4 B-roll tracks (Slice 2+)

Timing convention:
  All durations use the ``N/30s`` rational format required by FCPXML 1.10
  (30 fps, exact — not 29.97).  DaVinci Resolve interprets this correctly.
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Sequence

from agents.brook.script_video.cuts import CutPoint
from agents.brook.script_video.manifest import Manifest

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def emit(manifest: Manifest, output_path: Path) -> None:
    """Write FCPXML 1.10 to *output_path*."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tree = _build_fcpxml(manifest)
    _write_xml(tree, output_path)


# ---------------------------------------------------------------------------
# Internal builders
# ---------------------------------------------------------------------------

_FPS = 30  # frames per second (exact, not 29.97)


def _dur(seconds: float) -> str:
    """Convert seconds to FCPXML rational duration string (N/30s)."""
    frames = round(seconds * _FPS)
    if frames == 0:
        return "0s"
    return f"{frames}/{_FPS}s"


def _uid(seed: str) -> str:
    """Deterministic 16-char hex UID derived from *seed*."""
    return hashlib.sha256(seed.encode()).hexdigest()[:16].upper()


def _build_segments(
    total_duration_sec: float,
    cuts: Sequence[CutPoint],
) -> list[tuple[float, float]]:
    """Split source timeline into kept segments after ripple deletes.

    Returns a list of (start_sec, end_sec) pairs in source coordinates,
    guaranteed non-overlapping and ordered.
    """
    # Sort and deduplicate cuts
    sorted_cuts = sorted(
        (c for c in cuts if c.type == "ripple-delete"),
        key=lambda c: c.start_sec,
    )

    # Merge overlapping/adjacent cut regions
    merged: list[tuple[float, float]] = []
    for cut in sorted_cuts:
        if merged and cut.start_sec <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], cut.end_sec))
        else:
            merged.append((cut.start_sec, cut.end_sec))

    # Build kept segments
    segments: list[tuple[float, float]] = []
    cursor = 0.0
    for cut_start, cut_end in merged:
        if cut_start > cursor:
            segments.append((cursor, cut_start))
        cursor = cut_end
    if cursor < total_duration_sec:
        segments.append((cursor, total_duration_sec))

    return segments


def _build_fcpxml(manifest: Manifest) -> ET.ElementTree:
    aroll_path = Path(manifest.aroll_video)
    total_src_sec = manifest.total_frames / manifest.fps

    segments = _build_segments(total_src_sec, manifest.cuts)
    timeline_duration = sum(e - s for s, e in segments)

    asset_uid = _uid(f"asset-{manifest.episode_id}")
    event_uid = _uid(f"event-{manifest.episode_id}")
    project_uid = _uid(f"project-{manifest.episode_id}")

    # Root
    root = ET.Element("fcpxml", version="1.10")

    # Resources
    resources = ET.SubElement(root, "resources")
    ET.SubElement(
        resources,
        "format",
        id="r1",
        name="FFVideoFormat1080p30",
        frameDuration=f"1/{_FPS}s",
        width="1920",
        height="1080",
        colorSpace="1-1-1 (Rec. 709)",
    )
    asset = ET.SubElement(
        resources,
        "asset",
        id="r2",
        name=aroll_path.stem,
        uid=asset_uid,
        src=aroll_path.as_uri(),
        duration=_dur(total_src_sec),
        hasVideo="1",
        hasAudio="1",
        videoSources="1",
        audioSources="1",
        audioChannels="2",
        audioRate="48000",
    )
    ET.SubElement(
        asset,
        "media-rep",
        kind="original-media",
        sig=asset_uid,
        src=aroll_path.as_uri(),
    )

    # Library → Event → Project → Sequence → Spine
    library = ET.SubElement(root, "library")
    event = ET.SubElement(library, "event", name=manifest.episode_id, uid=event_uid)
    project = ET.SubElement(
        event,
        "project",
        name=manifest.episode_id,
        uid=project_uid,
    )
    sequence = ET.SubElement(
        project,
        "sequence",
        duration=_dur(timeline_duration),
        format="r1",
        tcStart="0s",
        tcFormat="NDF",
        audioLayout="stereo",
        audioRate="48k",
    )
    spine = ET.SubElement(sequence, "spine")

    # Emit one asset-clip per kept segment.
    # FCPXML 1.10 spec: <asset-clip> for whole-asset references; <clip> is for
    # sub-element compositions. DaVinci tolerates both, but Final Cut Pro rejects
    # <clip ref=...>. Use <asset-clip> for portable interchange.
    timeline_cursor = 0.0
    for src_start, src_end in segments:
        seg_dur = src_end - src_start
        ET.SubElement(
            spine,
            "asset-clip",
            name=aroll_path.stem,
            ref="r2",
            offset=_dur(timeline_cursor),
            duration=_dur(seg_dur),
            start=_dur(src_start),
        )
        timeline_cursor += seg_dur

    return ET.ElementTree(root)


def _write_xml(tree: ET.ElementTree, path: Path) -> None:
    ET.indent(tree.getroot(), space="    ")
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<!DOCTYPE fcpxml>",
    ]
    body = ET.tostring(tree.getroot(), encoding="unicode")
    lines.append(body)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
