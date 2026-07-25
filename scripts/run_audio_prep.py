"""audio-prep pipeline：episode 資料夾 → normalized.wav + prep_manifest.json。

流程：
1. 在 episode 資料夾找原始音檔（預設 Audio/Live-Mix.wav，大小寫不拘）
2. Auphonic normalization（shared/auphonic.py：多帳號輪替 + 免費方案 Jingle 裁切）
3. 頭尾靜音裁切（shared/audio_trim.py）
4. 產出 <episode>/normalized.wav + prep_manifest.json（下游 skill 靠它偵測進度）

用法：
    python scripts/run_audio_prep.py "G:/footages/20260723 謝伯讓"
    python scripts/run_audio_prep.py <episode> --no-auphonic   # 只做靜音裁切（測試用）
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import find_dotenv, load_dotenv  # noqa: E402

load_dotenv()
# worktree 內執行時 frame-based find 找不到 .env，退回從 cwd 向上找（主 repo）
load_dotenv(find_dotenv(usecwd=True))

from shared.audio_trim import (  # noqa: E402
    DEFAULT_MIN_SILENCE,
    DEFAULT_NOISE_DB,
    detect_edge_silence,
    trim_edge_silence,
)
from shared.auphonic import _get_audio_duration, normalize  # noqa: E402

logger = logging.getLogger("audio_prep")

DEFAULT_AUDIO_CANDIDATES = ("live-mix.wav", "livemix.wav", "live mix.wav")
MANIFEST_NAME = "prep_manifest.json"
OUTPUT_NAME = "normalized.wav"


def find_source_audio(episode_dir: Path) -> Path:
    """在 episode 資料夾找原始音檔：<dir>/Audio/Live-Mix.wav（大小寫不拘）。"""
    audio_dirs = [d for d in episode_dir.iterdir() if d.is_dir() and d.name.lower() == "audio"]
    if not audio_dirs:
        raise FileNotFoundError(f"找不到 Audio 子資料夾: {episode_dir}")
    audio_dir = audio_dirs[0]

    for f in sorted(audio_dir.iterdir()):
        if f.is_file() and f.name.lower() in DEFAULT_AUDIO_CANDIDATES:
            return f

    wavs = sorted(audio_dir.glob("*.wav"))
    listing = "\n".join(f"  - {w.name}" for w in wavs) or "  (無 .wav 檔)"
    raise FileNotFoundError(
        f"{audio_dir} 裡找不到 Live-Mix.wav。現有 wav 檔：\n{listing}\n"
        f"請用 --audio 指定要處理的檔案"
    )


def run_prep(
    episode_dir: Path,
    *,
    audio: Path | None = None,
    use_auphonic: bool = True,
    noise_db: float = DEFAULT_NOISE_DB,
    min_silence: float = DEFAULT_MIN_SILENCE,
    keep_intermediates: bool = False,
) -> Path:
    source = audio if audio else find_source_audio(episode_dir)
    logger.info(f"原始音檔: {source}")
    source_duration = _get_audio_duration(source)

    intermediates: list[Path] = []

    # Stage 1+2: Auphonic（內含免費方案 Jingle 裁切）
    if use_auphonic:
        processed = normalize(source, output_dir=episode_dir)
        # normalize() 可能留下 *_normalized.wav（Jingle 裁切前）中間檔
        untrimmed = episode_dir / f"{source.stem}_normalized.wav"
        if untrimmed.exists() and untrimmed != processed:
            intermediates.append(untrimmed)
        if processed != source:
            intermediates.append(processed)
    else:
        logger.info("跳過 Auphonic（--no-auphonic）")
        processed = source

    # Stage 3: 頭尾靜音裁切
    trim_start, trim_end, dur_before_trim = detect_edge_silence(
        processed, noise_db=noise_db, min_silence=min_silence
    )
    output_path = episode_dir / OUTPUT_NAME
    if trim_start == 0.0 and trim_end >= dur_before_trim:
        logger.info("頭尾無靜音段")
        if processed == source:
            # 不動原始檔：直接複製一份當 normalized.wav
            import shutil

            shutil.copy2(processed, output_path)
        else:
            processed.replace(output_path)
            intermediates = [p for p in intermediates if p != processed]
    else:
        trimmed = trim_edge_silence(
            processed,
            output_path=output_path,
            noise_db=noise_db,
            min_silence=min_silence,
        )
        assert trimmed == output_path

    final_duration = _get_audio_duration(output_path)

    # 清中間檔（1GB 級 wav，留著只吃硬碟）
    if not keep_intermediates:
        for p in intermediates:
            if p.exists() and p != source and p != output_path:
                p.unlink()
                logger.info(f"清除中間檔: {p.name}")

    manifest = {
        "stage": "prep",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "source": str(source),
        "source_duration_sec": round(source_duration, 3),
        "auphonic": use_auphonic,
        "silence_trim": {
            "noise_db": noise_db,
            "min_silence": min_silence,
            "trim_start_sec": round(trim_start, 3),
            "trim_tail_sec": round(max(0.0, dur_before_trim - trim_end), 3),
        },
        "output": OUTPUT_NAME,
        "output_duration_sec": round(final_duration, 3),
    }
    manifest_path = episode_dir / MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(
        f"完成: {output_path}（{final_duration / 60:.1f} min）manifest → {manifest_path.name}"
    )
    return output_path


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="audio-prep：Auphonic normalize + 頭尾靜音裁切")
    parser.add_argument("episode", help="episode 資料夾（含 Audio/ 子資料夾）")
    parser.add_argument("--audio", help="直接指定原始音檔路徑（跳過自動偵測）")
    parser.add_argument("--no-auphonic", action="store_true", help="跳過 Auphonic，只做靜音裁切")
    parser.add_argument("--noise-db", type=float, default=DEFAULT_NOISE_DB, help="靜音判定門檻 dB")
    parser.add_argument(
        "--min-silence", type=float, default=DEFAULT_MIN_SILENCE, help="最短靜音秒數"
    )
    parser.add_argument("--keep-intermediates", action="store_true", help="保留中間檔")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    args = _parse_args(argv)
    episode_dir = Path(args.episode)
    if not episode_dir.is_dir():
        logger.error(f"episode 資料夾不存在: {episode_dir}")
        return 1
    run_prep(
        episode_dir,
        audio=Path(args.audio) if args.audio else None,
        use_auphonic=not args.no_auphonic,
        noise_db=args.noise_db,
        min_silence=args.min_silence,
        keep_intermediates=args.keep_intermediates,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
