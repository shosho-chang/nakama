"""WhisperX 字級 timestamps → words.json（script-anchored mistake removal 輸入）。

GPU side — 修修本機執行；下游 cleanup / correct-srt 純 Python 吃這份 JSON。

用法：
    # 最小（輸出 <audio_dir>/words.json）
    python scripts/run_whisperx_words.py data/script_video/<ep>/aroll-audio.wav

    # 指定輸出路徑
    python scripts/run_whisperx_words.py <audio> --output data/script_video/<ep>/words.json

輸出格式：
    {"audio": "<path>", "model": "large-v3",
     "words": [{"word": "今", "start": 0.52, "end": 0.61}, ...]}

注意：本 script 跑在 **raw** 音訊上（cleanup 之前），時間戳對應 raw
timeline；cleanup 會把 cut 區域內的字丟棄並重映射到乾淨 timeline。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from shared.transcriber import _get_align_model, _get_asr_model  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="WhisperX 字級 timestamps → words.json")
    parser.add_argument("audio_path", type=Path, help="音檔路徑（WAV / MP3 / M4A / MP4）")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="輸出 JSON 路徑（預設：<audio_dir>/words.json）",
    )
    parser.add_argument("--model", default="large-v3", help="WhisperX 模型（預設 large-v3）")
    parser.add_argument("--language", default="zh")
    args = parser.parse_args()

    audio_path: Path = args.audio_path
    out_path: Path = args.output or audio_path.parent / "words.json"

    import whisperx

    started = time.time()
    print(f"音檔: {audio_path}")
    model = _get_asr_model(args.model)
    audio = whisperx.load_audio(str(audio_path))
    transcription = model.transcribe(audio, batch_size=16, language=args.language)
    segs = transcription.get("segments") or []
    if not segs:
        raise RuntimeError("WhisperX 辨識結果為空")
    detected_lang = transcription.get("language", args.language)
    print(f"ASR 完成：{len(segs)} segments（語言 {detected_lang}）")

    align_model, align_metadata = _get_align_model(detected_lang)
    aligned = whisperx.align(
        segs,
        align_model,
        align_metadata,
        audio,
        "cuda",
        return_char_alignments=False,
    )

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
        print(f"警告：{skipped} 個 token 缺 timestamp（align 失敗），已略過")

    payload = {"audio": str(audio_path), "model": args.model, "words": words}
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"完成！{len(words)} words → {out_path}（耗時 {(time.time() - started) / 60:.1f} 分鐘）")


if __name__ == "__main__":
    main()
