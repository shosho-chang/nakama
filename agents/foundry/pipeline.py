"""foundry pipeline entry — orchestrates planning / dispatching / FCPXML emit.

Subcommands:
- plan: SRT → storyboard.yaml (PR-3); ``--use-hints`` consumes
  ``storyboard_hints.yaml`` if present (ADR-038 §D5)
- hint-beats: raw_recording.mp4 → storyboard_hints.yaml via silencedetect
  (ADR-038 §D5 / PR-C)
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
    hints_path = ep_dir / "storyboard_hints.yaml"
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

    # ADR-038 §D5: opt-in silence hints. Default behavior unchanged when the
    # flag is absent or the file does not exist.
    hints: dict | None = None
    if getattr(args, "use_hints", False):
        if hints_path.exists():
            hints = yaml.safe_load(hints_path.read_text(encoding="utf-8")) or None
            logger.info("planner consuming silence hints from %s", hints_path)
        else:
            logger.warning(
                "--use-hints set but %s not found; planner will run without hints",
                hints_path,
            )

    beats = plan_episode(normalized, episode_meta, hints=hints)

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


def _cmd_hint_beats(args: argparse.Namespace) -> int:
    """raw_recording.mp4 → storyboard_hints.yaml via ffmpeg silencedetect.

    ADR-038 §D5 / PR-C. Output payload:

        speaking_spans:
          - [start_s, end_s]
          - ...
        params:
          noise_db: -30.0
          min_silence_s: 0.7

    Consumed by `plan --use-hints` as an advisory prior on beat boundaries.
    """
    from agents.foundry.silence_detection import find_speaking_spans

    ep_dir = _episode_dir(args.episode)
    mp4_path = ep_dir / "raw_recording.mp4"
    out_path = ep_dir / "storyboard_hints.yaml"

    if not mp4_path.exists():
        logger.error("hint-beats: raw_recording.mp4 not found at %s", mp4_path)
        return 1

    spans = find_speaking_spans(
        mp4_path,
        noise_db=args.noise_db,
        min_silence_s=args.min_silence_s,
    )

    payload = {
        "speaking_spans": [[round(s, 3), round(e, 3)] for s, e in spans],
        "params": {
            "noise_db": args.noise_db,
            "min_silence_s": args.min_silence_s,
        },
    }
    out_path.write_text(
        yaml.dump(payload, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    logger.info(
        "hint-beats wrote %d speaking spans to %s (noise=%sdB, d=%ss)",
        len(spans),
        out_path,
        args.noise_db,
        args.min_silence_s,
    )
    return 0


def _cmd_render(args: argparse.Namespace) -> int:
    """storyboard.yaml → individual b_roll_<beat_id>.mp4 in out/."""
    import asyncio

    from agents.foundry.render_dispatcher import run_queue

    ep_dir = _episode_dir(args.episode)
    storyboard_path = ep_dir / "storyboard.yaml"
    out_dir = ep_dir / "out"

    storyboard = yaml.safe_load(storyboard_path.read_text(encoding="utf-8"))
    if not isinstance(storyboard, list):
        raise ValueError(f"{storyboard_path}: expected list of beats, got {type(storyboard)}")

    cutaways = [b for b in storyboard if b.get("broll_decision") == "cutaway"]
    logger.info("rendering %d cutaway beats (concurrency=1)", len(cutaways))

    paths = asyncio.run(run_queue(cutaways, out_dir, concurrency=args.concurrency))

    # Update storyboard with render_status=done for rendered beats
    rendered_ids = {b["beat_id"] for b in cutaways}
    for beat in storyboard:
        if beat["beat_id"] in rendered_ids:
            beat.setdefault("status", {})["render_status"] = "done"
    storyboard_path.write_text(
        yaml.dump(storyboard, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    logger.info("rendered %d mp4s; storyboard.yaml updated", len(paths))
    return 0


def _cmd_emit(args: argparse.Namespace) -> int:
    """storyboard.yaml + rendered mp4s → out/episode.fcpxml."""
    from agents.foundry.fcpxml_emitter import emit

    ep_dir = _episode_dir(args.episode)
    storyboard = yaml.safe_load((ep_dir / "storyboard.yaml").read_text(encoding="utf-8"))
    fcpxml_path = emit(storyboard, ep_dir, fcpxml_version=args.fcpxml_version)
    logger.info("FCPXML emitted: %s", fcpxml_path)
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    rc = _cmd_plan(args)
    if rc != 0:
        return rc
    rc = _cmd_render(args)
    if rc != 0:
        return rc
    return _cmd_emit(args)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agents.foundry", description=__doc__.splitlines()[0])
    p.add_argument("--episode", required=True, help="episode id under data/script_video/")
    sub = p.add_subparsers(dest="cmd", required=True)
    plan_sub = sub.add_parser("plan")
    plan_sub.add_argument(
        "--use-hints",
        action="store_true",
        help=(
            "Consume storyboard_hints.yaml (from `hint-beats`) as an advisory "
            "prior on beat boundaries. Default opt-out — when flag absent or "
            "file missing, planner behavior is unchanged."
        ),
    )
    plan_sub.set_defaults(fn=_cmd_plan)
    hint_sub = sub.add_parser(
        "hint-beats",
        help="Detect speaking spans via silencedetect → storyboard_hints.yaml",
    )
    hint_sub.add_argument("--noise-db", type=float, default=-30.0, dest="noise_db")
    hint_sub.add_argument("--min-silence-s", type=float, default=0.7, dest="min_silence_s")
    hint_sub.set_defaults(fn=_cmd_hint_beats)
    render_sub = sub.add_parser("render")
    render_sub.add_argument("--concurrency", type=int, default=1)
    render_sub.set_defaults(fn=_cmd_render)
    emit_sub = sub.add_parser("emit")
    emit_sub.add_argument("--fcpxml-version", default="1.10", choices=["1.10", "1.11", "1.9"])
    emit_sub.set_defaults(fn=_cmd_emit)
    run_sub = sub.add_parser("run")
    run_sub.add_argument("--concurrency", type=int, default=1)
    run_sub.add_argument("--fcpxml-version", default="1.10", choices=["1.10", "1.11", "1.9"])
    run_sub.set_defaults(fn=_cmd_run)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
