"""iter4.2 = iter4.1 + disable VAD：解開頭 0~3s backchannel 漏抓問題。

iter4.1 的開頭 cue 是 2.970s，前面「可以齁 / 可以齁 / 好」（Memo 0~3.94s）整段被
Silero VAD 當靜音砍掉。iter4.2 完全 disable VAD，純靠 Whisper 內建判斷。

對位差異 vs iter4.1：
- vad=False（拿掉 Silero VAD 過濾）
- suppress_silence=True 保留（Whisper 內建 silence 處理）
- 其餘參數同 iter4.1（regroup / VAD-free decoder hyperparams 不變）

輸出到 tests/files/out/whisperx-iter4_2/
"""

# ruff: noqa: E402  # sys.stdout.reconfigure must run before non-stdlib imports (Windows UTF-8)

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

import opencc
import stable_whisper
import torch

ROOT = Path(__file__).resolve().parent.parent
AUDIO = ROOT / "tests" / "files" / "20260415.wav"
OUT_DIR = ROOT / "tests" / "files" / "out" / "whisperx-iter4_2"
OUT_DIR.mkdir(parents=True, exist_ok=True)

INITIAL_PROMPT = (
    "數位遊牧、Traveling Village、Hell Yes、花蓮、Paul、本尊、心酸血淚、保羅、安吉、哥大"
)

# 同 iter4.1：拆 mg、sg=0.3
REGROUP = "cm_sp=.* /。/?/？/!/！/,* /，_sg=.3_sp=.* /。/?/？_sl=22"


def main() -> None:
    print(f"torch {torch.__version__}, CUDA {torch.cuda.is_available()}")
    print(f"stable-ts {stable_whisper.__version__}")
    print(f"out: {OUT_DIR}\n")

    print("Loading large-v3 model...")
    t0 = time.time()
    model = stable_whisper.load_model("large-v3", device="cuda")
    print(f"  loaded in {time.time() - t0:.1f}s\n")

    print(f"Transcribing {AUDIO.name} (76 min) — iter4.2 (no VAD, suppress_silence only)...")
    t1 = time.time()
    result = model.transcribe(
        str(AUDIO),
        language="zh",
        initial_prompt=INITIAL_PROMPT,
        vad=False,
        suppress_silence=True,
        word_timestamps=True,
        condition_on_previous_text=False,
        compression_ratio_threshold=2.4,
        no_speech_threshold=0.6,
        regroup=REGROUP,
        verbose=False,
    )
    elapsed = time.time() - t1
    print(f"  transcribed in {elapsed:.1f}s = {elapsed / 60:.2f} min")
    print(
        f"  segments: {len(result.segments)}, words: {sum(len(s.words) for s in result.segments)}"
    )

    json_out = OUT_DIR / "20260415.result.json"
    result.save_as_json(str(json_out))
    print(f"\n[OUT] {json_out}")

    human_srt = OUT_DIR / "20260415.srt"
    result.to_srt_vtt(str(human_srt), segment_level=True, word_level=False)
    verbatim_srt = OUT_DIR / "20260415.verbatim.srt"
    result.to_srt_vtt(str(verbatim_srt), segment_level=False, word_level=True)

    print("\nApplying OpenCC s2twp to SRT files...")
    cc = opencc.OpenCC("s2twp")
    for path in (human_srt, verbatim_srt):
        text = path.read_text(encoding="utf-8")
        path.write_text(cc.convert(text), encoding="utf-8")
        print(f"[OUT] {path}")

    print(f"\nDONE in {time.time() - t0:.1f}s total")


if __name__ == "__main__":
    main()
