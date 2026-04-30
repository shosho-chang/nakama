"""端到端跑一次 Transcriber pipeline（Auphonic + WhisperX + LLM 校正 + Gemini 仲裁）。

用法：
    # 最小
    python scripts/run_transcribe.py <audio_path>

    # 指定輸出 + LifeOS Project file（建議：人名/術語命中率差很多）
    python scripts/run_transcribe.py <audio_path> \
        --output-dir <dir> \
        --project-file "E:/Shosho LifeOS/Projects/Angie.md"

    # 跳過 Auphonic（省上傳時間；犧牲 ASR 品質）
    python scripts/run_transcribe.py <audio_path> --no-auphonic

    # 只跑 ASR + Opus，不做 Gemini 仲裁（省 Gemini 成本）
    python scripts/run_transcribe.py <audio_path> --no-arbitration
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from shared.transcriber import transcribe  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transcriber pipeline 端到端執行",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("audio_path", type=Path, help="音檔路徑（WAV / MP3 / M4A）")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="輸出目錄（預設：<audio_path>/out）",
    )
    parser.add_argument(
        "--project-file",
        type=Path,
        default=None,
        help="LifeOS Podcast Project .md 檔（抽 hotwords + LLM 校正 context）",
    )
    parser.add_argument(
        "--no-auphonic",
        action="store_true",
        help="跳過 Auphonic normalization（省上傳時間）",
    )
    parser.add_argument(
        "--no-arbitration",
        action="store_true",
        help="關閉 Gemini 2.5 Pro 多模態仲裁（省仲裁成本）",
    )
    parser.add_argument(
        "--no-llm-correction",
        action="store_true",
        help="完全跳過 Opus 校正（純 ASR 輸出）",
    )
    parser.add_argument(
        "--diarize",
        action="store_true",
        help=(
            "額外輸出 {stem}.diar.srt（含 [SPEAKER_XX] prefix，給 repurpose 用）；"
            "純 SRT 仍照常輸出。需 HUGGINGFACE_TOKEN env + pyannote EULA accept。"
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    audio_path: Path = args.audio_path
    output_dir: Path = args.output_dir or audio_path.parent / "out"

    print(f"音檔: {audio_path}")
    print(f"輸出: {output_dir}")
    if args.project_file:
        print(f"Project: {args.project_file}")
    pipeline_parts = []
    if not args.no_auphonic:
        pipeline_parts.append("Auphonic normalization")
    pipeline_parts.append("WhisperX (large-v3)")
    if args.diarize:
        pipeline_parts.append("pyannote diarize → .diar.srt")
    if not args.no_llm_correction:
        pipeline_parts.append("Opus 校正")
        if not args.no_arbitration:
            pipeline_parts.append("Gemini 2.5 Pro 仲裁")
    print(f"Pipeline: {' + '.join(pipeline_parts)}")
    print("-" * 60)

    started = time.time()
    srt_path = transcribe(
        audio_path=audio_path,
        output_dir=output_dir,
        project_file=args.project_file,
        normalize_audio=not args.no_auphonic,
        use_llm_correction=not args.no_llm_correction,
        use_multimodal_arbitration=not args.no_arbitration,
        use_diarization=args.diarize,
    )
    elapsed = time.time() - started

    print("-" * 60)
    print(f"完成！耗時 {elapsed / 60:.1f} 分鐘")
    print(f"SRT: {srt_path}")
    diar_path = srt_path.with_suffix(".diar.srt")
    if diar_path.exists():
        print(f"Diar SRT: {diar_path}")
    qc_path = srt_path.with_suffix(".qc.md")
    if qc_path.exists():
        print(f"QC:  {qc_path}")


if __name__ == "__main__":
    main()
