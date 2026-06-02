"""foundry pipeline entry — orchestrates planning / dispatching / FCPXML emit.

Subcommands:
- plan: SRT → storyboard.yaml (PR-3); ``--use-hints`` consumes
  ``storyboard_hints.yaml`` if present (ADR-038 §D5)
- hint-beats: raw_recording.mp4 → storyboard_hints.yaml via silencedetect
  (ADR-038 §D5 / PR-C)
- render: storyboard.yaml → b_roll_*.mp4 (PR-4)
- emit: storyboard.yaml + rendered mp4s → episode.fcpxml (PR-4)
- run: plan → render → emit end-to-end
- diff: storyboard A vs storyboard B → Myers-style LCS +/-/= rows (ADR-038 §D7)
- replan-beat: single-beat LLM tool-call re-plan (ADR-038 §D3 + §D6)
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
    use_cache = not getattr(args, "no_cache", False)
    logger.info(
        "rendering %d cutaway beats (concurrency=%d, cache=%s)",
        len(cutaways),
        args.concurrency,
        "on" if use_cache else "off",
    )

    results = asyncio.run(
        run_queue(cutaways, out_dir, concurrency=args.concurrency, use_cache=use_cache)
    )

    # Map beat_id → (hash, was_cache_hit) so we update storyboard in place
    by_beat_id: dict[int, tuple[str, bool]] = {}
    cache_hits = 0
    for beat, (_mp4, cached_hash, was_hit) in zip(cutaways, results, strict=True):
        by_beat_id[beat["beat_id"]] = (cached_hash, was_hit)
        if was_hit:
            cache_hits += 1

    for beat in storyboard:
        if beat["beat_id"] in by_beat_id:
            cached_hash, _was_hit = by_beat_id[beat["beat_id"]]
            status = beat.setdefault("status", {})
            status["render_status"] = "done"
            status["cached_hash"] = cached_hash
    storyboard_path.write_text(
        yaml.dump(storyboard, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    logger.info(
        "rendered %d mp4s (%d cache hits); storyboard.yaml updated",
        len(results),
        cache_hits,
    )
    return 0


def _cmd_emit(args: argparse.Namespace) -> int:
    """storyboard.yaml + rendered mp4s → out/episode.fcpxml."""
    from agents.foundry.fcpxml_emitter import emit

    ep_dir = _episode_dir(args.episode)
    storyboard = yaml.safe_load((ep_dir / "storyboard.yaml").read_text(encoding="utf-8"))
    fcpxml_path = emit(storyboard, ep_dir, fcpxml_version=args.fcpxml_version)
    logger.info("FCPXML emitted: %s", fcpxml_path)
    return 0


def _cmd_diff(args: argparse.Namespace) -> int:
    """Storyboard A vs Storyboard B → Myers-style LCS diff to stdout (ADR-038 §D7)."""
    from agents.foundry.storyboard_diff import diff_storyboards, format_diff

    a_path = Path(args.a)
    b_path = Path(args.b)
    before = yaml.safe_load(a_path.read_text(encoding="utf-8")) or []
    after = yaml.safe_load(b_path.read_text(encoding="utf-8")) or []
    if not isinstance(before, list) or not isinstance(after, list):
        raise ValueError("diff inputs must be storyboard lists (got non-list YAML)")
    rows = diff_storyboards(before, after)
    print(format_diff(rows))
    return 0


def _cmd_replan_beat(args: argparse.Namespace) -> int:
    """LLM tool-call re-plan of a single beat (ADR-038 §D3 + §D6).

    Loads ``data/script_video/<episode>/storyboard.yaml``, runs the bounded
    tool-call agent against ``--beat`` with ``--note``, applies the returned
    BeatEdit list via the pure engine, and writes the result back. Also
    records the edit in the episode's edit_log (action=``replan``) with both
    LCS diff (§D7) and typed ``edit_ops`` (§D3).
    """
    from agents.foundry import beat_editor, edit_log, replan_agent

    ep_dir = _episode_dir(args.episode)
    storyboard_path = ep_dir / "storyboard.yaml"
    storyboard = yaml.safe_load(storyboard_path.read_text(encoding="utf-8")) or []
    if not isinstance(storyboard, list):
        raise ValueError(f"{storyboard_path}: expected list of beats")

    before_beat = next((b for b in storyboard if b.get("beat_id") == args.beat), None)
    if before_beat is None:
        logger.error("replan-beat: beat %d not found in %s", args.beat, storyboard_path)
        return 1
    before_payload = {
        "layout": before_beat.get("layout"),
        "broll": before_beat.get("broll"),
    }
    storyboard_before = [dict(b) for b in storyboard]

    result = replan_agent.run(storyboard, args.beat, args.note)
    logger.info(
        "replan_agent: %d edits, %d iterations, %d tokens, reason=%s",
        len(result.edits),
        result.iterations,
        result.tokens_used,
        result.terminated_reason,
    )
    if result.edits:
        storyboard = beat_editor.apply_edits(storyboard, result.edits)
        storyboard_path.write_text(
            yaml.dump(storyboard, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )

    after_beat = next((b for b in storyboard if b.get("beat_id") == args.beat), before_beat)
    edit_log.append_entry(
        episode_id=args.episode,
        beat_id=args.beat,
        action="replan",
        before=before_payload,
        after={"layout": after_beat.get("layout"), "broll": after_beat.get("broll")},
        user_note=args.note or None,
        storyboard_before=storyboard_before,
        storyboard_after=[dict(b) for b in storyboard],
        edit_ops=[e.model_dump() for e in result.edits],
    )
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
    render_sub.add_argument(
        "--no-cache",
        action="store_true",
        help="Force re-render even if content-addressed mp4 already exists (ADR-038 §D2)",
    )
    render_sub.set_defaults(fn=_cmd_render)
    emit_sub = sub.add_parser("emit")
    emit_sub.add_argument("--fcpxml-version", default="1.10", choices=["1.10", "1.11", "1.9"])
    emit_sub.set_defaults(fn=_cmd_emit)
    run_sub = sub.add_parser("run")
    run_sub.add_argument("--concurrency", type=int, default=1)
    run_sub.add_argument("--fcpxml-version", default="1.10", choices=["1.10", "1.11", "1.9"])
    run_sub.add_argument(
        "--no-cache",
        action="store_true",
        help="Force re-render even if content-addressed mp4 already exists (ADR-038 §D2)",
    )
    run_sub.set_defaults(fn=_cmd_run)
    diff_sub = sub.add_parser(
        "diff", help="LCS diff between two storyboard.yaml files (ADR-038 §D7)"
    )
    diff_sub.add_argument("a", help="storyboard A (before) yaml path")
    diff_sub.add_argument("b", help="storyboard B (after) yaml path")
    diff_sub.set_defaults(fn=_cmd_diff)
    replan_sub = sub.add_parser(
        "replan-beat",
        help="LLM tool-call re-plan of a single beat (ADR-038 §D3 + §D6)",
    )
    replan_sub.add_argument("beat", type=int, help="beat_id to re-plan")
    replan_sub.add_argument("--note", default="", help="user note describing the desired change")
    replan_sub.set_defaults(fn=_cmd_replan_beat)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
