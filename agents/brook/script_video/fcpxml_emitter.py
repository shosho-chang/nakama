"""FCPXML emitter — ripple-delete A-roll timeline (ADR-015 Slice 1, ADR-050 D2).

Thin adapter over :mod:`shared.fcpxml`: this module owns the Manifest/CutPoint
domain mapping (which cut types ripple, per-episode UID seeds); XML shape and
DaVinci quirk knowledge live in the shared builder.

Emits a document containing:
  - V1 track: A-roll clips with CutPoints applied as ripple deletes
  - Resources: asset reference + 1080p 30fps format
  - No V2-V4 B-roll tracks (overlay shape is the storyboard pipeline's job)
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from agents.brook.script_video.cuts import CutPoint
from agents.brook.script_video.manifest import Manifest
from shared.fcpxml import (
    Asset,
    Clip,
    Timeline,
    build_fcpxml,
    merge_ripple_segments,
    rational_duration,
    write_fcpxml,
)

_FPS = 30  # frames per second (exact, not 29.97)


def _dur(seconds: float) -> str:
    """Convert seconds to FCPXML rational duration string (N/30s)."""
    return rational_duration(seconds, _FPS)


def _build_segments(
    total_duration_sec: float,
    cuts: Sequence[CutPoint],
) -> list[tuple[float, float]]:
    """Split source timeline into kept segments after ripple deletes.

    Only ``ripple-delete`` CutPoints affect the timeline; razor cuts are
    no-ops here (DaVinci applies them as blade-only edits).
    """
    return merge_ripple_segments(
        total_duration_sec,
        ((c.start_sec, c.end_sec) for c in cuts if c.type == "ripple-delete"),
    )


def emit(manifest: Manifest, output_path: Path) -> None:
    """Write FCPXML 1.10 to *output_path*."""
    aroll_path = Path(manifest.aroll_video)
    total_src_sec = manifest.total_frames / manifest.fps

    segments = _build_segments(total_src_sec, manifest.cuts)
    timeline_duration = sum(e - s for s, e in segments)

    asset = Asset(
        id="r2",
        path=aroll_path,
        duration_sec=total_src_sec,
        uid_seed=f"asset-{manifest.episode_id}",
        has_audio=True,
    )

    clips = []
    timeline_cursor = 0.0
    for src_start, src_end in segments:
        seg_dur = src_end - src_start
        clips.append(
            Clip(
                asset_id="r2",
                name=aroll_path.stem,
                offset_sec=timeline_cursor,
                duration_sec=seg_dur,
                start_sec=src_start,
            )
        )
        timeline_cursor += seg_dur

    timeline = Timeline(
        name=manifest.episode_id,
        event_name=manifest.episode_id,
        duration_sec=timeline_duration,
        spine=tuple(clips),
        event_uid_seed=f"event-{manifest.episode_id}",
        project_uid_seed=f"project-{manifest.episode_id}",
    )

    tree = build_fcpxml(timeline, [asset], version="1.10", fps=_FPS)
    write_fcpxml(tree, output_path)
