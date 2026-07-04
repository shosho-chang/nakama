"""FCPXML emitter — DaVinci Resolve timeline writer (ADR-032 §3, ADR-050 D2).

Thin adapter over :mod:`shared.fcpxml`: this module owns the episode-dir
contract (raw_recording lookup, render_status gating, ADR-038 §D2
content-addressed b-roll resolution); all XML shape and DaVinci quirk
knowledge lives in the shared builder.

V1 track: talking head (raw_recording.mp4), full duration, no transform.
V2 track (lane 1): rendered B-roll mp4 references at each beat's timing.

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

from shared.fcpxml import (
    SUPPORTED_VERSIONS,
    Asset,
    Clip,
    FcpxmlVersion,
    Timeline,
    build_fcpxml,
    write_fcpxml,
)

logger = logging.getLogger(__name__)

FPS = 30  # Phase 1 hardcoded — composition spec assumes 30fps


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


def emit(
    storyboard: list[dict],
    episode_dir: Path,
    fcpxml_version: FcpxmlVersion = "1.10",
) -> Path:
    """Emit episode.fcpxml from storyboard + rendered B-roll mp4s.

    Args:
        storyboard: list of beat dicts (Beat.model_dump()).
        episode_dir: data/script_video/<episode-id>/ — must contain
            raw_recording.mp4 and out/b_roll_<hash>.mp4 per rendered beat.
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

    cutaways: list[tuple[dict, Path, float, str]] = []
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
            from agents.brook.script_video.export_hash import compute_beat_hash

            cached_hash = compute_beat_hash(beat)
        broll_mp4 = out_dir / f"b_roll_{cached_hash}.mp4"
        if not broll_mp4.exists():
            raise ValueError(
                f"cutaway beat {beat['beat_id']} mp4 not found at {broll_mp4} (hash={cached_hash})"
            )
        broll_duration = _mp4_duration(broll_mp4)
        cutaways.append((beat, broll_mp4, broll_duration, cached_hash))

    assets = [
        Asset(
            id="r2",
            path=raw_mp4,
            duration_sec=aroll_duration,
            # Episode-scoped seed — every episode's talking head is named
            # raw_recording.mp4, so a name-derived UID would collide across
            # episodes in the same DaVinci library.
            uid_seed=f"{episode_dir.name}/raw_recording",
            has_audio=True,
        )
    ]
    overlays = []
    for idx, (beat, mp4, dur, cached_hash) in enumerate(cutaways, start=3):
        assets.append(Asset(id=f"r{idx}", path=mp4, duration_sec=dur))
        overlays.append(
            Clip(
                asset_id=f"r{idx}",
                name=f"b_roll_{cached_hash}",
                offset_sec=beat["timing"]["start"],
                duration_sec=dur,
                lane=1,
            )
        )

    timeline = Timeline(
        name=episode_dir.name,
        event_name=f"nakama-{episode_dir.name}",
        duration_sec=aroll_duration,
        spine=(
            Clip(
                asset_id="r2",
                name=raw_mp4.stem,
                offset_sec=0.0,
                duration_sec=aroll_duration,
                children=tuple(overlays),
            ),
        ),
    )

    tree = build_fcpxml(timeline, assets, version=fcpxml_version, fps=FPS)
    write_fcpxml(tree, fcpxml_path)
    logger.info(
        "FCPXML %s written to %s (%d cutaway clips)",
        fcpxml_version,
        fcpxml_path,
        len(cutaways),
    )
    return fcpxml_path
