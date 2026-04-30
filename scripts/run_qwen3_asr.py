"""Qwen3-ASR-1.7B 端到端跑一次 ASR + 輸出 SRT。

對標 ADR-013 D2 路徑（替代 WhisperX）；用同一段 76 min 訪談對比。

用法：
    python scripts/run_qwen3_asr.py <audio_path> --output-dir <dir>

依賴：
    pip install qwen-asr  # 已完成 2026-04-30
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

import torch  # noqa: E402
from qwen_asr import Qwen3ASRModel  # noqa: E402


def _fmt_srt_ts(seconds: float) -> str:
    td = timedelta(seconds=max(seconds, 0))
    total = int(td.total_seconds())
    ms = int((td.total_seconds() - total) * 1000)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Qwen3-ASR-1.7B 端到端 ASR")
    p.add_argument("audio_path", type=Path, help="音檔路徑")
    p.add_argument("--output-dir", type=Path, default=None, help="輸出目錄")
    p.add_argument(
        "--language", type=str, default=None,
        help="語言 hint（None = auto LID，中英 code-switch 也可 None）",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    audio_path: Path = args.audio_path
    output_dir: Path = args.output_dir or audio_path.parent / "out"
    output_dir.mkdir(parents=True, exist_ok=True)
    out_srt = output_dir / f"{audio_path.stem}.srt"

    print(f"音檔: {audio_path}")
    print(f"輸出: {out_srt}")
    print(f"GPU: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU'}")
    print("-" * 60)

    started = time.time()

    print("[1/3] 載入 Qwen3-ASR-1.7B + ForcedAligner-0.6B …")
    model = Qwen3ASRModel.from_pretrained(
        "Qwen/Qwen3-ASR-1.7B",
        dtype=torch.bfloat16,
        device_map="cuda:0",
        forced_aligner="Qwen/Qwen3-ForcedAligner-0.6B",
        forced_aligner_kwargs=dict(dtype=torch.bfloat16, device_map="cuda:0"),
        max_inference_batch_size=8,
        max_new_tokens=4096,
    )
    print(f"    模型載入完成（耗時 {time.time() - started:.1f}s）")

    print("[2/3] 開始 ASR + alignment …")
    asr_started = time.time()
    results = model.transcribe(
        audio=str(audio_path),
        language=args.language,
        return_time_stamps=True,
    )
    print(f"    ASR 完成（耗時 {time.time() - asr_started:.1f}s）")

    r = results[0]
    print(f"    Detected language: {getattr(r, 'language', 'unknown')}")
    n_segs = len(getattr(r, "time_stamps", []) or [])
    print(f"    Segments: {n_segs}")

    print("[3/3] 寫入 SRT …")
    with out_srt.open("w", encoding="utf-8") as f:
        for i, seg in enumerate(r.time_stamps, start=1):
            f.write(f"{i}\n")
            f.write(f"{_fmt_srt_ts(seg.start_time)} --> {_fmt_srt_ts(seg.end_time)}\n")
            f.write(f"{seg.text}\n\n")

    elapsed = time.time() - started
    print("-" * 60)
    print(f"完成！總耗時 {elapsed / 60:.1f} 分鐘")
    print(f"SRT: {out_srt}")


if __name__ == "__main__":
    main()
