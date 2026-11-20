"""audio-prep pipeline：episode 資料夾 → normalized.wav + prep_manifest.json。

流程：
1. 在 episode 資料夾找原始音檔（預設 Audio/Live-Mix.wav，大小寫不拘）
2. Auphonic normalization（shared/auphonic.py：多帳號輪替 + 免費方案 Jingle 裁切）
3. 頭尾靜音裁切（shared/audio_trim.py）
4. 產出 <episode>/normalized.wav + prep_manifest.json（下游 skill 靠它偵測進度）

用法：
    python scripts/run_audio_prep.py "G:/footages/20260723 謝伯讓"
    python scripts/run_audio_prep.py <episode> --no-auphonic   # 只做靜音裁切（測試用）
    # 手動下載的 Auphonic 輸出
    python scripts/run_audio_prep.py <episode> --pre-processed <已處理檔>
    # 社群直播（Zoom m4a）：不送 Auphonic，本機解成 PCM WAV 並寫 V2 handoff
    python scripts/run_audio_prep.py <episode> --audio <zoom.m4a> --local-decode
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

from agents.brook.podcast_subtitles.adapters.normalized_handoff import (  # noqa: E402
    NormalizedAudioHandoffManifestV1,
    wav_duration_ms,
)
from agents.brook.podcast_subtitles.hashing import (  # noqa: E402
    canonical_json_bytes,
    measure_regular_file,
)
from shared.audio_trim import (  # noqa: E402
    DEFAULT_MIN_SILENCE,
    DEFAULT_NOISE_DB,
    detect_edge_silence,
    trim_edge_silence,
)
from shared.auphonic import (  # noqa: E402
    _align_trim,
    _env_float,
    _get_audio_duration,
    normalize,
)

logger = logging.getLogger("audio_prep")

DEFAULT_AUDIO_CANDIDATES = ("live-mix.wav", "livemix.wav", "live mix.wav")
MANIFEST_NAME = "prep_manifest.json"
OUTPUT_NAME = "normalized.wav"
V2_HANDOFF_NAME = "normalized-handoff.v1.json"


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


def decode_to_pcm_wav(source: Path, output_path: Path) -> Path:
    """用本機 ffmpeg 把任何音檔（例如 Zoom 的 m4a）解成 24-bit PCM WAV。

    不重新取樣、不改聲道數、不加任何濾鏡——時間軸與原始錄影一致，
    只是把容器／編碼換成 Memo 與 V2 handoff 讀得了的 PCM WAV。
    """
    import subprocess

    tmp = output_path.with_name(output_path.stem + ".decoding.wav")
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-map",
        "0:a:0",
        "-vn",
        "-c:a",
        "pcm_s24le",
        str(tmp),
    ]
    try:
        subprocess.run(cmd, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        if tmp.exists():
            tmp.unlink()
        raise RuntimeError(f"ffmpeg 解碼失敗：{source}（{exc}）") from exc
    tmp.replace(output_path)
    return output_path


def run_prep(
    episode_dir: Path,
    *,
    audio: Path | None = None,
    use_auphonic: bool = True,
    pre_processed: Path | None = None,
    trim_silence: bool = False,
    noise_db: float = DEFAULT_NOISE_DB,
    min_silence: float = DEFAULT_MIN_SILENCE,
    keep_intermediates: bool = False,
    local_decode: bool = False,
) -> Path:
    if local_decode and (pre_processed is not None or trim_silence):
        raise ValueError("--local-decode 不能和 --pre-processed／--trim-silence 一起用")
    source = audio if audio else find_source_audio(episode_dir)
    logger.info(f"原始音檔: {source}")
    source_duration = _get_audio_duration(source)

    intermediates: list[Path] = []

    if local_decode:
        # 社群直播（2026-11-20 修修選項 2）：不送 Auphonic（額度留給 podcast、會員聲音不出本機），
        # 本機解成 PCM WAV。這是 production 輸入，所以照樣寫 V2 handoff。
        output_path = decode_to_pcm_wav(source, episode_dir / OUTPUT_NAME)
        logger.info("本機解碼（--local-decode）：未經 Auphonic normalization，時間軸與原始錄影一致")
        return _write_outputs(
            episode_dir,
            output_path=output_path,
            source=source,
            source_duration=source_duration,
            auphonic=False,
            pre_processed=None,
            silence_trim=None,
            handoff=True,
            extra={"local_decode": True},
        )

    # Stage 1+2: Auphonic（內含免費方案 Jingle 裁切）
    if pre_processed is not None:
        # 已在 Auphonic 網站處理好、手動下載的輸出：不重傳，只做 Jingle 對齊裁切。
        # 對齊基準是 raw source，輸出時間軸因此還原成原始錄影（字幕要對回 DaVinci）。
        if not pre_processed.is_file():
            raise FileNotFoundError(f"--pre-processed 檔案不存在: {pre_processed}")
        logger.info(f"已處理音檔: {pre_processed}（跳過 Auphonic 上傳）")
        processed = _align_trim(pre_processed, source, _env_float("AUPHONIC_JINGLE_SECONDS", 6.0))
        if processed != pre_processed:
            intermediates.append(processed)
    elif use_auphonic:
        processed = normalize(source, output_dir=episode_dir)
        # normalize() 可能留下 *_normalized.wav（Jingle 裁切前）中間檔
        untrimmed = episode_dir / f"{source.stem}_normalized.wav"
        if untrimmed.exists() and untrimmed != processed:
            intermediates.append(untrimmed)
        if processed != source:
            intermediates.append(processed)
    else:
        logger.warning(
            "跳過 Auphonic（--no-auphonic）：輸出將是 raw 的直接複製，"
            "**未經任何 normalization**——僅供測試或 Auphonic 不可用時的暫行方案"
        )
        processed = source

    # Stage 3（opt-in）: 頭尾靜音裁切。
    # 預設**不裁**——normalized.wav 時間軸必須與原始錄影完全一致，
    # 字幕才能對回 DaVinci 的原始影片（修修 2026-07-25 裁決）。
    # 只有純音訊用途（podcast 上架等不需對影片）才用 --trim-silence。
    output_path = episode_dir / OUTPUT_NAME
    trim_start, trim_end, dur_before_trim = 0.0, 0.0, 0.0
    if trim_silence:
        trim_start, trim_end, dur_before_trim = detect_edge_silence(
            processed, noise_db=noise_db, min_silence=min_silence
        )
    if not trim_silence or (trim_start == 0.0 and trim_end >= dur_before_trim):
        if not trim_silence:
            logger.info("靜音裁切關閉（預設）— 時間軸與原始錄影一致")
        else:
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

    # 清中間檔（1GB 級 wav，留著只吃硬碟）
    if not keep_intermediates:
        for p in intermediates:
            if p.exists() and p != source and p != output_path:
                p.unlink()
                logger.info(f"清除中間檔: {p.name}")

    return _write_outputs(
        episode_dir,
        output_path=output_path,
        source=source,
        source_duration=source_duration,
        auphonic=True if pre_processed is not None else use_auphonic,
        pre_processed=pre_processed,
        silence_trim=(
            {
                "noise_db": noise_db,
                "min_silence": min_silence,
                "trim_start_sec": round(trim_start, 3),
                "trim_tail_sec": round(max(0.0, dur_before_trim - trim_end), 3),
            }
            if trim_silence
            else None  # 預設不裁：時間軸與原始錄影一致
        ),
        handoff=pre_processed is not None or use_auphonic,
    )


def _write_outputs(
    episode_dir: Path,
    *,
    output_path: Path,
    source: Path,
    source_duration: float,
    auphonic: bool,
    pre_processed: Path | None,
    silence_trim: dict | None,
    handoff: bool,
    extra: dict | None = None,
) -> Path:
    """寫 prep_manifest.json；handoff=True 時另寫 Subtitle V2 normalized handoff。"""
    final_duration = _get_audio_duration(output_path)
    completed_at = datetime.now(timezone.utc)
    manifest = {
        "stage": "prep",
        "completed_at": completed_at.isoformat(),
        "source": str(source),
        "source_duration_sec": round(source_duration, 3),
        "auphonic": auphonic,
        "pre_processed": str(pre_processed) if pre_processed is not None else None,
        "silence_trim": silence_trim,
        "output": OUTPUT_NAME,
        "output_duration_sec": round(final_duration, 3),
        **(extra or {}),
    }
    manifest_path = episode_dir / MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if handoff:
        normalized_hash, normalized_size = measure_regular_file(output_path)
        handoff_manifest = NormalizedAudioHandoffManifestV1(
            normalized_audio_sha256=normalized_hash,
            normalized_audio_size_bytes=normalized_size,
            normalized_audio_duration_ms=wav_duration_ms(output_path),
            accepted_at=completed_at,
        )
        handoff_path = episode_dir / V2_HANDOFF_NAME
        temporary_handoff = handoff_path.with_suffix(".json.tmp")
        temporary_handoff.write_bytes(canonical_json_bytes(handoff_manifest))
        temporary_handoff.replace(handoff_path)
    else:
        logger.warning(
            "未產生 Subtitle V2 normalized handoff：--no-auphonic raw copy 不是 production "
            "normalized audio"
        )
    logger.info(
        f"完成: {output_path}（{final_duration / 60:.1f} min）manifest → {manifest_path.name}"
    )
    return output_path


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="audio-prep：Auphonic normalize + 頭尾靜音裁切")
    parser.add_argument("episode", help="episode 資料夾（含 Audio/ 子資料夾）")
    parser.add_argument("--audio", help="直接指定原始音檔路徑（跳過自動偵測）")
    parser.add_argument("--no-auphonic", action="store_true", help="跳過 Auphonic")
    parser.add_argument(
        "--pre-processed",
        help="已在 Auphonic 網站處理好、手動下載的音檔：不重傳，只對齊原始檔裁掉 Jingle",
    )
    parser.add_argument(
        "--trim-silence",
        action="store_true",
        help="裁頭尾靜音（預設關——會讓時間軸偏離原始錄影，僅純音訊用途使用）",
    )
    parser.add_argument("--noise-db", type=float, default=DEFAULT_NOISE_DB, help="靜音判定門檻 dB")
    parser.add_argument(
        "--min-silence", type=float, default=DEFAULT_MIN_SILENCE, help="最短靜音秒數"
    )
    parser.add_argument("--keep-intermediates", action="store_true", help="保留中間檔")
    parser.add_argument(
        "--local-decode",
        action="store_true",
        help="不送 Auphonic：本機 ffmpeg 解成 PCM WAV 並寫 V2 handoff（社群直播用；podcast 不用）",
    )
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
        pre_processed=Path(args.pre_processed) if args.pre_processed else None,
        trim_silence=args.trim_silence,
        noise_db=args.noise_db,
        min_silence=args.min_silence,
        keep_intermediates=args.keep_intermediates,
        local_decode=args.local_decode,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
