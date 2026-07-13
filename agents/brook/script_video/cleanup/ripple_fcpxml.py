"""Ripple-delete FCPXML — cleanup stage 的 DaVinci timeline 輸出（ADR-050 D3）.

Thin adapter over :mod:`shared.fcpxml`: maps detected :class:`CutPoint`
regions onto a kept-segment V1 timeline. DaVinci applies the actual razor
cuts + ripple deletes on import — the talking head file itself is never
re-encoded (grade latitude invariant, ADR-032 §3a).

Formerly ``agents/brook/script_video/fcpxml_emitter.py`` (ADR-015 Slice 1,
Manifest-based); the Manifest/DSL layer retired with ADR-050 D3.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from agents.brook.script_video.cleanup.cuts import CutPoint
from shared.fcpxml import (
    Asset,
    Clip,
    Timeline,
    build_fcpxml,
    merge_ripple_segments,
    write_fcpxml,
)

_FPS = 30  # frames per second (exact, not 29.97)


def build_kept_segments(
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


def emit_ripple_timeline(
    aroll_video: Path,
    total_duration_sec: float,
    cuts: Sequence[CutPoint],
    episode_id: str,
    output_path: Path,
) -> None:
    """Write a V1-only FCPXML with *cuts* applied as ripple deletes."""
    segments = build_kept_segments(total_duration_sec, cuts)
    timeline_duration = sum(e - s for s, e in segments)

    asset = Asset(
        id="r2",
        path=aroll_video,
        duration_sec=total_duration_sec,
        uid_seed=f"asset-{episode_id}",
        has_audio=True,
    )

    clips = []
    timeline_cursor = 0.0
    for src_start, src_end in segments:
        seg_dur = src_end - src_start
        clips.append(
            Clip(
                asset_id="r2",
                name=aroll_video.stem,
                offset_sec=timeline_cursor,
                duration_sec=seg_dur,
                start_sec=src_start,
            )
        )
        timeline_cursor += seg_dur

    timeline = Timeline(
        name=episode_id,
        event_name=episode_id,
        duration_sec=timeline_duration,
        spine=tuple(clips),
        event_uid_seed=f"event-{episode_id}",
        project_uid_seed=f"project-{episode_id}",
    )

    tree = build_fcpxml(timeline, [asset], version="1.10", fps=_FPS)
    write_fcpxml(tree, output_path)
