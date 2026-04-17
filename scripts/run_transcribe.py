"""端到端跑一次 Transcriber pipeline（Auphonic + FunASR + LLM 校正 + Gemini 仲裁）。

用法：
    python scripts/run_transcribe.py <audio_path> [output_dir]
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from shared.transcriber import transcribe  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        print("用法: python scripts/run_transcribe.py <audio_path> [output_dir]")
        sys.exit(1)

    audio_path = Path(sys.argv[1])
    output_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else audio_path.parent / "out"

    print(f"音檔: {audio_path}")
    print(f"輸出: {output_dir}")
    print("Pipeline: Auphonic normalization + FunASR + Opus 校正 + Gemini 2.5 Pro 仲裁")
    print("-" * 60)

    started = time.time()
    srt_path = transcribe(
        audio_path=audio_path,
        output_dir=output_dir,
        normalize_audio=True,
        use_llm_correction=True,
        use_multimodal_arbitration=True,
    )
    elapsed = time.time() - started

    print("-" * 60)
    print(f"完成！耗時 {elapsed / 60:.1f} 分鐘")
    print(f"SRT: {srt_path}")
    qc_path = srt_path.with_suffix(".qc.md")
    if qc_path.exists():
        print(f"QC:  {qc_path}")


if __name__ == "__main__":
    main()
