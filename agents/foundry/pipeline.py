"""foundry pipeline entry — orchestrates planning / dispatching / FCPXML emit.

Subcommands (Phase 1 surface):
- plan: SRT → storyboard.yaml (PR-3)
- render: storyboard.yaml → b_roll_*.mp4 (PR-4)
- emit: storyboard.yaml + rendered mp4s → episode.fcpxml (PR-4)
- run: plan → render → emit end-to-end
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_DATA_ROOT = Path("data/script_video")


def _episode_dir(episode_id: str) -> Path:
    return _DATA_ROOT / episode_id


def _get_mp4_duration(mp4_path: Path) -> float:
    """Return mp4 duration in seconds via ffprobe; 0.0 if unavailable."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_streams",
                str(mp4_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        data = json.loads(result.stdout)
        for stream in data.get("streams", []):
            if "duration" in stream:
                return float(stream["duration"])
    except Exception as exc:
        logger.debug("ffprobe failed for %s: %s", mp4_path, exc)
    return 0.0


def _check_srt_mp4_duration(srt_cues: list, mp4_path: Path) -> None:
    """Warn if SRT last timestamp and mp4 duration differ by more than 1s."""
    if not mp4_path.exists():
        logger.warning(
            "raw_recording.mp4 not found at %s — skipping SRT/mp4 duration check", mp4_path
        )
        return
    srt_duration = max((c.end for c in srt_cues), default=0.0)
    mp4_duration = _get_mp4_duration(mp4_path)
    delta = abs(srt_duration - mp4_duration)
    if delta > 1.0:
        logger.warning(
            "SRT duration %.2fs vs mp4 duration %.2fs — delta %.2fs exceeds 1s threshold; "
            "review SRT alignment (ADR-032 §Risks)",
            srt_duration,
            mp4_duration,
            delta,
        )


def _cmd_plan(args: argparse.Namespace) -> int:
    """SRT → storyboard.yaml via LLM planner + beat aligner."""
    from agents.foundry.beat_aligner import AnchorNotFoundError, align_beat
    from agents.foundry.chinese_normalizer import normalize_numbers, normalize_punctuation
    from agents.foundry.planner import plan_episode
    from agents.foundry.srt_flattener import flatten_cues, parse_srt

    ep_dir = _episode_dir(args.episode)
    srt_path = ep_dir / "transcript.srt"
    episode_yaml_path = ep_dir / "episode.yaml"
    mp4_path = ep_dir / "raw_recording.mp4"
    out_path = ep_dir / "storyboard.yaml"

    cues = parse_srt(srt_path.read_text(encoding="utf-8"))
    raw_flat, char_to_time, srt_line_id_to_char_range = flatten_cues(cues)

    _check_srt_mp4_duration(cues, mp4_path)

    # Normalize for LLM consumption (char_to_time positions may shift for numbers,
    # but timestamp granularity is at cue level so the approximation is acceptable)
    normalized = normalize_numbers(normalize_punctuation(raw_flat))

    episode_meta: dict = {}
    if episode_yaml_path.exists():
        episode_meta = yaml.safe_load(episode_yaml_path.read_text(encoding="utf-8")) or {}

    beats = plan_episode(normalized, episode_meta)

    # Align each beat; log failures but continue (operator reviews storyboard)
    for beat in beats:
        try:
            result = align_beat(
                beat.model_dump(),
                normalized,
                char_to_time,
                srt_line_id_to_char_range,
            )
            from agents.foundry.schemas.storyboard import Timing

            beat.timing = Timing(**result["timing"])
            beat.srt_line_ids = result["srt_line_ids"]
        except AnchorNotFoundError as exc:
            logger.warning("beat %d alignment failed: %s", beat.beat_id, exc)

    storyboard = [b.model_dump() for b in beats]
    out_path.write_text(
        yaml.dump(storyboard, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    logger.info("storyboard written to %s (%d beats)", out_path, len(beats))
    return 0


def _cmd_render(args: argparse.Namespace) -> int:
    raise NotImplementedError("PR-4 — render_dispatcher + hyperframes_worker")


def _cmd_emit(args: argparse.Namespace) -> int:
    raise NotImplementedError("PR-4 — fcpxml_emitter")


def _cmd_run(args: argparse.Namespace) -> int:
    raise NotImplementedError("PR-3/PR-4 — end-to-end orchestration")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agents.foundry", description=__doc__.splitlines()[0])
    p.add_argument("--episode", required=True, help="episode id under data/script_video/")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan").set_defaults(fn=_cmd_plan)
    sub.add_parser("render").set_defaults(fn=_cmd_render)
    sub.add_parser("emit").set_defaults(fn=_cmd_emit)
    sub.add_parser("run").set_defaults(fn=_cmd_run)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
