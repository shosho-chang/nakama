"""FCPXML 1.10 emitter — DaVinci Resolve timeline writer (ADR-032 §3).

V1 track: talking head (raw_recording.mp4), full duration, no transform.
V2 track: rendered B-roll mp4 references at each beat's timing.
A1 track: talking head audio.

Phase 1 layouts (full_aroll / full_broll) do not require adjust-transform.
side_overlay_* / pip_corner_br require transform, which requires a verified
DaVinci import fixture (ADR-032 §3b warning) — deferred to Phase 1.5.

`fcpxml_version` parameter falls back to 1.11 or 1.9 if 1.10 import fails in
the user's DaVinci Resolve version (ADR-032 §Risks).
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Literal
from xml.dom import minidom
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

FPS = 30  # Phase 1 hardcoded — composition spec assumes 30fps
SUPPORTED_VERSIONS = ("1.10", "1.11", "1.9")
FcpxmlVersion = Literal["1.10", "1.11", "1.9"]


def _frames(seconds: float) -> str:
    """Convert seconds to FCPXML rational frame fraction at 30fps."""
    frames = round(seconds * FPS)
    return f"{frames}/{FPS}s"


def _mp4_duration(path: Path) -> float:
    """Return mp4 duration in seconds via ffprobe; raises on failure."""
    result = subprocess.run(
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
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def _asset_uid(name: str) -> str:
    return f"ASSET-{name.upper().replace(' ', '-').replace('.', '-')}"


def emit(
    storyboard: list[dict],
    episode_dir: Path,
    fcpxml_version: FcpxmlVersion = "1.10",
) -> Path:
    """Emit episode.fcpxml from storyboard + rendered B-roll mp4s.

    Args:
        storyboard: list of beat dicts (Beat.model_dump()).
        episode_dir: data/script_video/<episode-id>/ — must contain
            raw_recording.mp4 and out/b_roll_<beat_id>.mp4 per rendered beat.
        fcpxml_version: 1.10 default; 1.11 / 1.9 fallback if DaVinci rejects.

    Returns Path to episode_dir / "out" / "episode.fcpxml".
    Raises FileNotFoundError if raw_recording.mp4 missing.
    Raises ValueError if a cutaway beat has no rendered mp4 or missing timing.
    """
    if fcpxml_version not in SUPPORTED_VERSIONS:
        raise ValueError(
            f"fcpxml_version must be one of {SUPPORTED_VERSIONS}, got {fcpxml_version!r}"
        )

    raw_mp4 = episode_dir / "raw_recording.mp4"
    if not raw_mp4.exists():
        raise FileNotFoundError(f"raw_recording.mp4 not found in {episode_dir}")

    out_dir = episode_dir / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    fcpxml_path = out_dir / "episode.fcpxml"

    aroll_duration = _mp4_duration(raw_mp4)

    cutaways: list[tuple[dict, Path, float]] = []
    for beat in storyboard:
        if beat.get("broll_decision") != "cutaway":
            continue
        if (beat.get("status") or {}).get("render_status") != "done":
            logger.warning(
                "beat %s render_status != done; skipping in FCPXML emit",
                beat.get("beat_id"),
            )
            continue
        timing = beat.get("timing")
        if not timing:
            raise ValueError(f"cutaway beat {beat.get('beat_id')} missing timing")
        # ADR-038 §D2: rendered mp4 is content-addressed by cached_hash.
        # Dispatcher writes cached_hash to status before/after render; if it
        # is missing (legacy storyboard or out-of-band edit), recompute it
        # so emit() stays self-contained.
        status = beat.get("status") or {}
        cached_hash = status.get("cached_hash")
        if not cached_hash:
            from agents.foundry.export_hash import compute_beat_hash

            cached_hash = compute_beat_hash(beat)
        broll_mp4 = out_dir / f"b_roll_{cached_hash}.mp4"
        if not broll_mp4.exists():
            raise ValueError(
                f"cutaway beat {beat['beat_id']} mp4 not found at {broll_mp4} (hash={cached_hash})"
            )
        broll_duration = _mp4_duration(broll_mp4)
        cutaways.append((beat, broll_mp4, broll_duration, cached_hash))

    fcpxml = ET.Element("fcpxml", {"version": fcpxml_version})
    resources = ET.SubElement(fcpxml, "resources")
    ET.SubElement(
        resources,
        "format",
        {
            "id": "r1",
            "name": "FFVideoFormat1080p30",
            "frameDuration": f"1/{FPS}s",
            "width": "1920",
            "height": "1080",
            "colorSpace": "1-1-1 (Rec. 709)",
        },
    )

    aroll_asset = ET.SubElement(
        resources,
        "asset",
        {
            "id": "r2",
            "name": raw_mp4.stem,
            "uid": _asset_uid(raw_mp4.stem),
            "start": "0s",
            "duration": _frames(aroll_duration),
            "hasVideo": "1",
            "hasAudio": "1",
            "videoSources": "1",
            "audioSources": "1",
            "audioChannels": "2",
            "audioRate": "48000",
            "format": "r1",
        },
    )
    ET.SubElement(
        aroll_asset,
        "media-rep",
        # DaVinci Resolve requires absolute file:// URIs in media-rep src;
        # relative paths are silently rejected at import time.
        {"kind": "original-media", "src": raw_mp4.resolve().as_uri()},
    )

    for idx, (beat, mp4, dur, _hash) in enumerate(cutaways, start=3):
        asset = ET.SubElement(
            resources,
            "asset",
            {
                "id": f"r{idx}",
                "name": mp4.stem,
                "uid": _asset_uid(mp4.stem),
                "start": "0s",
                "duration": _frames(dur),
                "hasVideo": "1",
                "videoSources": "1",
                "format": "r1",
            },
        )
        ET.SubElement(
            asset,
            "media-rep",
            {"kind": "original-media", "src": mp4.resolve().as_uri()},
        )

    library = ET.SubElement(fcpxml, "library")
    event = ET.SubElement(library, "event", {"name": f"nakama-{episode_dir.name}"})
    project = ET.SubElement(event, "project", {"name": episode_dir.name})
    sequence = ET.SubElement(
        project,
        "sequence",
        {
            "format": "r1",
            "duration": _frames(aroll_duration),
            "tcStart": "0s",
            "tcFormat": "NDF",
            "audioLayout": "stereo",
            "audioRate": "48k",
        },
    )
    spine = ET.SubElement(sequence, "spine")
    aroll_clip = ET.SubElement(
        spine,
        "asset-clip",
        {
            "name": raw_mp4.stem,
            "ref": "r2",
            "offset": "0s",
            "start": "0s",
            "duration": _frames(aroll_duration),
        },
    )

    for idx, (beat, _mp4, dur, cached_hash) in enumerate(cutaways, start=3):
        ET.SubElement(
            aroll_clip,
            "asset-clip",
            {
                "name": f"b_roll_{cached_hash}",
                "ref": f"r{idx}",
                "lane": "1",
                "offset": _frames(beat["timing"]["start"]),
                "start": "0s",
                "duration": _frames(dur),
            },
        )

    pretty = minidom.parseString(ET.tostring(fcpxml, encoding="unicode")).toprettyxml(indent="  ")
    # minidom adds <?xml ...?> with double quotes — replace with FCPXML conventional doctype
    lines = pretty.splitlines()
    body = "\n".join(line for line in lines[1:] if line.strip())
    final = f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n{body}\n'
    fcpxml_path.write_text(final, encoding="utf-8")
    logger.info(
        "FCPXML %s written to %s (%d cutaway clips)",
        fcpxml_version,
        fcpxml_path,
        len(cutaways),
    )
    return fcpxml_path
