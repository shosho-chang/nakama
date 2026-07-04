"""Brook video line pipeline entry — orchestrates cleanup / planning / dispatching / FCPXML emit.

Subcommands:
- cleanup: raw_recording.mp4 → 拍掌 marker 偵測 → out/cleanup.fcpxml
  ripple-delete timeline（選配前置 stage，ADR-050 D3）
- plan: SRT → storyboard.yaml (PR-3); ``--use-hints`` consumes
  ``storyboard_hints.yaml`` if present (ADR-038 §D5)
- hint-beats: raw_recording.mp4 → storyboard_hints.yaml via silencedetect
  (ADR-038 §D5 / PR-C)
- render: storyboard.yaml → b_roll_*.mp4 (PR-4)
- emit: storyboard.yaml + rendered mp4s → episode.fcpxml (PR-4)
- run: plan → render → emit end-to-end
- diff: storyboard A vs storyboard B → Myers-style LCS +/-/= rows (ADR-038 §D7)
- replan-beat: single-beat LLM tool-call re-plan (ADR-038 §D3 + §D6)

Episode dirs require an ``episode.yaml``; every completed stage is stamped
into its ``stages:`` map as provenance (ADR-050 D4).
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# Repo-root anchored — a cwd-relative root would silently write to the wrong
# place when the CLI is invoked from outside the repo (ADR-050 D4 bug fix).
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DATA_ROOT = _REPO_ROOT / "data" / "script_video"


def _episode_dir(episode_id: str) -> Path:
    return _DATA_ROOT / episode_id


def _require_episode(ep_dir: Path) -> dict:
    """Fail loud unless the episode dir has an episode.yaml (ADR-050 D4).

    Returns the parsed episode metadata. The guidance message doubles as the
    directory-creation recipe so a typo'd episode id never silently spawns a
    half-formed directory tree.
    """
    episode_yaml = ep_dir / "episode.yaml"
    if not episode_yaml.exists():
        raise SystemExit(
            f"episode.yaml not found: {episode_yaml}\n"
            f"每個 episode 目錄必須有 episode.yaml（provenance anchor，ADR-050 D4）。建立方式：\n"
            f"  mkdir -p {ep_dir}/out\n"
            f"  printf 'id: {ep_dir.name}\\ntitle: \"<標題>\"\\n' > {episode_yaml}\n"
            f"其餘輸入：raw_recording.mp4（talking head）+ transcript.srt（/transcribe 輸出）"
        )
    return yaml.safe_load(episode_yaml.read_text(encoding="utf-8")) or {}


def _record_stage(ep_dir: Path, stage: str) -> None:
    """Stamp a completed stage into episode.yaml ``stages:`` (ADR-050 D4)."""
    episode_yaml = ep_dir / "episode.yaml"
    meta = yaml.safe_load(episode_yaml.read_text(encoding="utf-8")) or {}
    stages = meta.setdefault("stages", {})
    stages[stage] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    episode_yaml.write_text(
        yaml.dump(meta, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )


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
    from agents.brook.script_video.beat_aligner import AnchorNotFoundError, align_beat
    from agents.brook.script_video.chinese_normalizer import (
        normalize_numbers,
        normalize_punctuation,
    )
    from agents.brook.script_video.planner import plan_episode
    from agents.brook.script_video.srt_flattener import flatten_cues, parse_srt

    ep_dir = _episode_dir(args.episode)
    episode_meta = _require_episode(ep_dir)
    srt_path = ep_dir / "transcript.srt"
    mp4_path = ep_dir / "raw_recording.mp4"
    hints_path = ep_dir / "storyboard_hints.yaml"
    out_path = ep_dir / "storyboard.yaml"

    cues = parse_srt(srt_path.read_text(encoding="utf-8"))
    raw_flat, char_to_time, srt_line_id_to_char_range = flatten_cues(cues)

    _check_srt_mp4_duration(cues, mp4_path)

    # Normalize for LLM consumption (char_to_time positions may shift for numbers,
    # but timestamp granularity is at cue level so the approximation is acceptable)
    normalized = normalize_numbers(normalize_punctuation(raw_flat))

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
            from agents.brook.script_video.schemas.storyboard import Timing

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
    _record_stage(ep_dir, "plan")
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
    from agents.brook.script_video.silence_detection import find_speaking_spans

    ep_dir = _episode_dir(args.episode)
    _require_episode(ep_dir)
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
    _record_stage(ep_dir, "hint-beats")
    return 0


def _cmd_render(args: argparse.Namespace) -> int:
    """storyboard.yaml → individual b_roll_<beat_id>.mp4 in out/."""
    import asyncio

    from agents.brook.script_video.render_dispatcher import run_queue

    ep_dir = _episode_dir(args.episode)
    _require_episode(ep_dir)
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
    _record_stage(ep_dir, "render")
    return 0


def _cmd_emit(args: argparse.Namespace) -> int:
    """storyboard.yaml + rendered mp4s → out/episode.fcpxml."""
    from agents.brook.script_video.fcpxml_emitter import emit

    ep_dir = _episode_dir(args.episode)
    _require_episode(ep_dir)
    storyboard = yaml.safe_load((ep_dir / "storyboard.yaml").read_text(encoding="utf-8"))
    fcpxml_path = emit(storyboard, ep_dir, fcpxml_version=args.fcpxml_version)
    logger.info("FCPXML emitted: %s", fcpxml_path)
    _record_stage(ep_dir, "emit")
    return 0


def _find_script(ep_dir: Path, override: str | None) -> Path | None:
    """逐字稿路徑解析：--script 優先，否則 ep_dir 下 script.md / script.txt."""
    if override:
        return Path(override)
    for name in ("script.md", "script.txt"):
        candidate = ep_dir / name
        if candidate.exists():
            return candidate
    return None


def _cmd_cleanup(args: argparse.Namespace) -> int:
    """raw_recording.mp4 → 擊掌 marker 偵測 → out/cleanup.fcpxml（ADR-050 D3）.

    兩種模式（自動選擇）：

    - **script-anchored（主線）**：episode 目錄有 words.json（WhisperX 字級
      timestamps，`scripts/run_whisperx_words.py` 產出）時啟用。單擊掌 +
      retake 指紋回溯決定刪除範圍；若逐字稿（script.md）也在，另產文字
      全對、時間軸已重映射到乾淨 timeline 的 transcript.srt。
    - **audio-only（legacy fallback）**：無 words.json 時退回 double-clap
      音訊 VAD 模式。

    DaVinci import cleanup.fcpxml 接管實際 razor cut + ripple delete（不另
    出乾淨 mp4 — ADR-015 凍結語意；talking head 原檔不 re-encode 保 grade
    latitude）。
    """
    import wave

    from agents.brook.script_video.cleanup import (
        correct_srt,
        detect_clap_markers,
        detect_script_anchored_cuts,
        detect_single_claps,
        emit_ripple_timeline,
        load_words,
    )

    ep_dir = _episode_dir(args.episode)
    _require_episode(ep_dir)
    raw_mp4 = ep_dir / "raw_recording.mp4"
    wav_path = ep_dir / "aroll-audio.wav"
    out_path = ep_dir / "out" / "cleanup.fcpxml"
    words_path = Path(args.words) if getattr(args, "words", None) else ep_dir / "words.json"
    script_path = _find_script(ep_dir, getattr(args, "script", None))

    if not raw_mp4.exists():
        logger.error("cleanup: raw_recording.mp4 not found at %s", raw_mp4)
        return 1

    # marker 偵測讀 stdlib `wave` — PCM WAV only，先抽（前次殘留可重用）。
    if not wav_path.exists():
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(raw_mp4), "-vn", "-acodec", "pcm_s16le", str(wav_path)],
            check=True,
            capture_output=True,
        )
        logger.info("cleanup: extracted PCM audio to %s", wav_path)

    with wave.open(str(wav_path), "rb") as wf:
        total_sec = wf.getnframes() / wf.getframerate()

    if words_path.exists():
        # ── script-anchored 模式（單擊掌 + 文字回溯）──
        words = load_words(words_path)
        claps = detect_single_claps(wav_path)
        cuts = detect_script_anchored_cuts(words, claps)
        logger.info(
            "cleanup[script-anchored]: %d clap(s) → %d cut(s)（words: %s）",
            len(claps),
            len(cuts),
            words_path.name,
        )
        if script_path is not None:
            srt = correct_srt(
                words,
                script_path.read_text(encoding="utf-8"),
                cuts=cuts,
                total_duration_sec=total_sec,
            )
            srt_path = ep_dir / "transcript.srt"
            srt_path.write_text(srt, encoding="utf-8")
            logger.info(
                "cleanup: corrected SRT（乾淨 timeline 時間軸）→ %s。"
                "注意：若在 DaVinci 手動增刪切點，此 SRT 時間軸即失效，"
                "需對乾淨檔重跑 /transcribe",
                srt_path,
            )
        else:
            logger.warning(
                "cleanup: 找不到逐字稿（script.md / script.txt / --script）— "
                "跳過 corrected SRT，之後走 /transcribe"
            )
    else:
        # ── legacy audio-only 模式（double-clap + VAD）──
        logger.info(
            "cleanup[audio-only legacy]: %s 不存在 — 用 double-clap 音訊模式。"
            "已知限制：失敗 take 前只隔靜音的正確內容會被誤刪",
            words_path.name,
        )
        cuts = detect_clap_markers(wav_path)

    emit_ripple_timeline(raw_mp4, total_sec, cuts, args.episode, out_path)
    logger.info(
        "cleanup: %d ripple-delete cuts → %s (source %.1fs)",
        len(cuts),
        out_path,
        total_sec,
    )
    _record_stage(ep_dir, "cleanup")
    return 0


def _cmd_correct_srt(args: argparse.Namespace) -> int:
    """words.json + 逐字稿 → 文字全對的 SRT（standalone，不做 cut 重映射）.

    給「字幕檔錯字全換成稿上正確寫法」的獨立用途 — 時間軸沿用 words.json
    的 timeline（即產生它的那個音檔）。
    """
    from agents.brook.script_video.cleanup import correct_srt, load_words

    ep_dir = _episode_dir(args.episode)
    _require_episode(ep_dir)
    words_path = Path(args.words) if args.words else ep_dir / "words.json"
    script_path = _find_script(ep_dir, args.script)
    out_path = Path(args.out) if args.out else ep_dir / "transcript.srt"

    if not words_path.exists():
        logger.error("correct-srt: words.json not found at %s", words_path)
        return 1
    if script_path is None:
        logger.error("correct-srt: 找不到逐字稿（script.md / script.txt / --script）")
        return 1

    srt = correct_srt(load_words(words_path), script_path.read_text(encoding="utf-8"))
    out_path.write_text(srt, encoding="utf-8")
    logger.info("correct-srt: %s × %s → %s", words_path.name, script_path.name, out_path)
    return 0


def _cmd_diff(args: argparse.Namespace) -> int:
    """Storyboard A vs Storyboard B → Myers-style LCS diff to stdout (ADR-038 §D7)."""
    from agents.brook.script_video.storyboard_diff import diff_storyboards, format_diff

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
    from agents.brook.script_video import beat_editor, edit_log, replan_agent

    ep_dir = _episode_dir(args.episode)
    _require_episode(ep_dir)
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
    p = argparse.ArgumentParser(
        prog="agents.brook.script_video", description=__doc__.splitlines()[0]
    )
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
    cleanup_sub = sub.add_parser(
        "cleanup",
        help=(
            "擊掌 marker 偵測 → out/cleanup.fcpxml ripple-delete timeline"
            "（ADR-050 D3 選配前置；有 words.json 走 script-anchored 單擊掌模式）"
        ),
    )
    cleanup_sub.add_argument(
        "--words",
        default=None,
        help=(
            "WhisperX 字級 timestamps JSON（預設 <episode>/words.json；"
            "存在即啟用 script-anchored 模式）"
        ),
    )
    cleanup_sub.add_argument(
        "--script",
        default=None,
        help=(
            "完整逐字稿路徑（預設 <episode>/script.md 或 script.txt；"
            "有則另產 corrected transcript.srt）"
        ),
    )
    cleanup_sub.set_defaults(fn=_cmd_cleanup)
    correct_srt_sub = sub.add_parser(
        "correct-srt",
        help="words.json × 逐字稿 → 文字全對的 SRT（standalone，不做 cut 重映射）",
    )
    correct_srt_sub.add_argument("--words", default=None, help="預設 <episode>/words.json")
    correct_srt_sub.add_argument(
        "--script", default=None, help="預設 <episode>/script.md 或 script.txt"
    )
    correct_srt_sub.add_argument("--out", default=None, help="預設 <episode>/transcript.srt")
    correct_srt_sub.set_defaults(fn=_cmd_correct_srt)
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
