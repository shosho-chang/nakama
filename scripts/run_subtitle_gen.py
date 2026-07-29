"""subtitle-gen pipeline：normalized.wav → subs/raw.srt + subs/words.json。

單次 GPU pass：WhisperX ASR（segments → SRT）+ align（字級 timestamps →
words.json）。words.json 供下游對稿校正與 video line（script_align）重用。

- refs/ 資料夾內的 .md/.txt 會自動抽 hotwords 餵 initial prompt（LM 偏置）
- 起跑前做 GPU precheck（PCIe link gen；桌機必須鎖 Gen 4，見
  memory/claude/project_pcie_link_instability_2026_05_01.md）
- 必須用裝了 torch cu128 的 Python 執行（修修桌機 = system Python 3.10）

用法：
    python scripts/run_subtitle_gen.py "G:/footages/20260723 謝伯讓"
    python scripts/run_subtitle_gen.py <episode> --audio <path>  # 指定音檔
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import find_dotenv, load_dotenv  # noqa: E402

load_dotenv()
load_dotenv(find_dotenv(usecwd=True))

logger = logging.getLogger("subtitle_gen")

SUBS_DIR = "subs"
SRT_NAME = "raw.srt"
WORDS_NAME = "words.json"
ALIGNED_NAME = "aligned_segments.json"
MANIFEST_NAME = "gen_manifest.json"
REFS_DIR = "refs"


def _speaker_fn(episode_dir: Path):
    """episode 的 Audio/ 分軌 mic → 詞級說話者判定函數（找不到兩軌回 None）。"""
    from shared.speaker_assign import assign_word_speakers, detect_mic_tracks, load_envelopes

    audio_dir = next(
        (d for d in episode_dir.iterdir() if d.is_dir() and d.name.lower() == "audio"), None
    )
    if audio_dir is None:
        return None
    mics = detect_mic_tracks(audio_dir)
    if len(mics) < 2:
        logger.info("分軌 mic 不足兩軌，跳過說話者斷句")
        return None
    logger.info(f"說話者斷句啟用: {[p.name for p in mics]}")
    reference = episode_dir / "normalized.wav"
    envelopes = load_envelopes(mics, reference=reference if reference.exists() else None)
    return lambda words: assign_word_speakers(words, envelopes)


def _build_srt_from_aligned(aligned_segments: list[dict], episode_dir: Path | None = None) -> str:
    """aligned segments → cue（jieba 詞邊界 + 停頓優先 + 說話者斷句，零內插）
    → house style 後處理。"""
    from shared.cue_builder import aligned_segments_to_srt
    from shared.transcriber import _process_srt_line

    speaker_fn = _speaker_fn(episode_dir) if episode_dir else None
    srt_content = aligned_segments_to_srt(aligned_segments, speaker_fn=speaker_fn)
    return "\n".join(_process_srt_line(line) for line in srt_content.splitlines())


def run_recue(episode_dir: Path) -> Path:
    """從已存的 aligned_segments.json 重建 raw.srt（cue 參數迭代用，零 GPU）。"""
    aligned_path = episode_dir / SUBS_DIR / ALIGNED_NAME
    if not aligned_path.exists():
        raise FileNotFoundError(f"找不到 {aligned_path}（需先跑過一次完整 subtitle-gen）")
    aligned = json.loads(aligned_path.read_text(encoding="utf-8"))
    srt_content = _build_srt_from_aligned(aligned, episode_dir)
    srt_path = episode_dir / SUBS_DIR / SRT_NAME
    srt_path.write_text(srt_content, encoding="utf-8")
    logger.info(f"recue 完成: {srt_content.count(' --> ')} cues → {srt_path}")
    return srt_path


def gpu_precheck() -> None:
    """PCIe link gen 檢查：桌機 BIOS 必須鎖 Gen 4（Blackwell + Gen 5 會 hard hang）。"""
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=pcie.link.gen.max,pcie.link.gen.gpumax",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )
        parts = [p.strip() for p in (result.stdout or "").strip().split(",")]
        link_max = int(parts[0])
        logger.info(f"GPU precheck: PCIe link gen max = {link_max}（gpumax {parts[1]}）")
        if link_max >= 5:
            logger.warning(
                "PCIe link 沒鎖 Gen 4！Blackwell + Gen 5 有 hard hang 前科"
                "（WHEA 17 / 全機黑屏），建議先進 BIOS 鎖 Gen 4 再跑長音檔"
            )
    except Exception as e:  # nvidia-smi 不在（非 GPU 機）→ 讓 whisperx 自己報錯
        logger.warning(f"GPU precheck 跳過（{e}）")


def collect_ref_files(episode_dir: Path) -> list[Path]:
    from shared.subtitle_correct import discover_ref_files

    return discover_ref_files(episode_dir)


def run_gen(
    episode_dir: Path,
    *,
    audio: Path | None = None,
    model_id: str = "large-v3",
    language: str = "zh",
    host_name: str = "",
    show_name: str = "",
) -> tuple[Path, Path]:
    from shared.transcriber import (
        _build_initial_prompt,
        _extract_hotwords,
        _get_align_model,
        _get_asr_model,
    )

    audio_path = audio if audio else episode_dir / "normalized.wav"
    if not audio_path.exists():
        raise FileNotFoundError(f"找不到音檔 {audio_path}（先跑 audio-prep，或用 --audio 指定）")

    refs = collect_ref_files(episode_dir)
    hotwords = _extract_hotwords(refs) if refs else []
    initial_prompt = _build_initial_prompt(hotwords, None, host_name, show_name)
    if refs:
        logger.info(f"refs: {[p.name for p in refs]} → hotwords {len(hotwords)} 個")

    gpu_precheck()

    import whisperx

    started = time.time()
    model = _get_asr_model(model_id, initial_prompt=initial_prompt)
    audio_data = whisperx.load_audio(str(audio_path))
    logger.info(f"開始 ASR: {audio_path.name}")
    transcription = model.transcribe(audio_data, batch_size=16, language=language)
    segs = transcription.get("segments") or []
    if not segs:
        raise RuntimeError("WhisperX 辨識結果為空")
    detected_lang = transcription.get("language", language)
    logger.info(f"ASR 完成: {len(segs)} segments（語言 {detected_lang}）")

    # 字級 timestamps（cue 建構與下游對稿/video line 共用）
    align_model, align_metadata = _get_align_model(detected_lang)
    aligned = whisperx.align(
        segs, align_model, align_metadata, audio_data, "cuda", return_char_alignments=False
    )

    # aligned segments 落地：cue 參數調整可用 --recue 重建，免重跑 GPU
    subs_dir = episode_dir / SUBS_DIR
    subs_dir.mkdir(exist_ok=True)
    aligned_slim = [
        {
            "words": [
                {
                    "word": (w.get("word") or "").strip(),
                    "start": w.get("start"),
                    "end": w.get("end"),
                }
                for w in seg.get("words", [])
            ]
        }
        for seg in aligned["segments"]
    ]
    (subs_dir / ALIGNED_NAME).write_text(
        json.dumps(aligned_slim, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    srt_content = _build_srt_from_aligned(aligned_slim, episode_dir)
    words: list[dict] = []
    skipped = 0
    for seg in aligned["segments"]:
        for w in seg.get("words", []):
            if w.get("start") is None or w.get("end") is None:
                skipped += 1
                continue
            words.append(
                {
                    "word": (w.get("word") or "").strip(),
                    "start": round(float(w["start"]), 3),
                    "end": round(float(w["end"]), 3),
                }
            )
    if skipped:
        logger.warning(f"{skipped} 個 token 缺 timestamp（align 失敗），已略過")

    srt_path = subs_dir / SRT_NAME
    srt_path.write_text(srt_content, encoding="utf-8")
    words_path = subs_dir / WORDS_NAME
    words_path.write_text(
        json.dumps(
            {"audio": str(audio_path), "model": model_id, "words": words},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )

    cue_count = srt_content.count(" --> ")
    manifest = {
        "stage": "gen",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "audio": str(audio_path),
        "model": model_id,
        "language": detected_lang,
        "segments": len(segs),
        "cues": cue_count,
        "words": len(words),
        "hotwords": hotwords,
        "elapsed_min": round((time.time() - started) / 60, 1),
    }
    (subs_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(
        f"完成: {cue_count} cues → {srt_path}；{len(words)} words → {words_path}"
        f"（耗時 {manifest['elapsed_min']} 分鐘）"
    )
    return srt_path, words_path


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="subtitle-gen：WhisperX SRT + 字級 timestamps")
    parser.add_argument("episode", help="episode 資料夾（含 normalized.wav）")
    parser.add_argument("--audio", help="直接指定音檔（跳過 normalized.wav 慣例）")
    parser.add_argument("--model", default="large-v3")
    parser.add_argument("--language", default="zh")
    parser.add_argument("--host-name", default="", help="主持人名（hotword 偏置用）")
    parser.add_argument("--show-name", default="", help="節目名（hotword 偏置用）")
    parser.add_argument(
        "--recue",
        action="store_true",
        help="從既有 aligned_segments.json 重建 raw.srt（cue 參數迭代，零 GPU）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    args = _parse_args(argv)
    episode_dir = Path(args.episode)
    if not episode_dir.is_dir():
        logger.error(f"episode 資料夾不存在: {episode_dir}")
        return 1
    if args.recue:
        run_recue(episode_dir)
        return 0
    run_gen(
        episode_dir,
        audio=Path(args.audio) if args.audio else None,
        model_id=args.model,
        language=args.language,
        host_name=args.host_name,
        show_name=args.show_name,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
