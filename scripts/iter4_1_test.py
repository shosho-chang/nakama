"""iter4.1 = iter4 + 細調：抓開頭 backchannel + 不吞短應答。

調整對位 iter4 review 找到的兩個缺：
1. VAD threshold 0.25 → 0.15 — Memo 從 0.000s 起，iter4 從 2.970s 起，VAD 太緊
2. regroup 拆掉 merge_by_gap — iter4 把 backchannel 短應答 merge 進長 cue（4 vs Memo 63）
3. split_by_gap 0.4s → 0.3s — 更敏感的自然停頓切句

輸出到 tests/files/out/whisperx-iter4_1/
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
OUT_DIR = ROOT / "tests" / "files" / "out" / "whisperx-iter4_1"
OUT_DIR.mkdir(parents=True, exist_ok=True)

INITIAL_PROMPT = (
    "數位遊牧、Traveling Village、Hell Yes、花蓮、Paul、本尊、心酸血淚、保羅、安吉、哥大"
)

# iter4.1 regroup：拆掉 mg (merge_by_gap)，sg 從 0.4 改 0.3
#   cm  = clamp_max
#   sp  = split_by_punctuation（中英 . 。 ? ？ ! ！ , ，）
#   sg  = split_by_gap 0.3s（更敏感）
#   sp  = 再次標點切
#   sl  = split_by_length 22 chars
REGROUP = "cm_sp=.* /。/?/？/!/！/,* /，_sg=.3_sp=.* /。/?/？_sl=22"


def main() -> None:
    print(f"torch {torch.__version__}, CUDA {torch.cuda.is_available()}")
    print(f"stable-ts {stable_whisper.__version__}")
    print(f"out: {OUT_DIR}\n")

    print("Loading large-v3 model...")
    t0 = time.time()
    model = stable_whisper.load_model("large-v3", device="cuda")
    print(f"  loaded in {time.time() - t0:.1f}s\n")

    print(f"Transcribing {AUDIO.name} (76 min) — iter4.1 (vad 0.15, no merge_by_gap)...")
    t1 = time.time()
    result = model.transcribe(
        str(AUDIO),
        language="zh",
        initial_prompt=INITIAL_PROMPT,
        vad=True,
        vad_threshold=0.15,  # 0.25 → 0.15，抓更多開頭 backchannel
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

    # Save raw
    json_out = OUT_DIR / "20260415.result.json"
    result.save_as_json(str(json_out))
    print(f"\n[OUT] {json_out}")

    # SRT
    human_srt = OUT_DIR / "20260415.srt"
    result.to_srt_vtt(str(human_srt), segment_level=True, word_level=False)
    verbatim_srt = OUT_DIR / "20260415.verbatim.srt"
    result.to_srt_vtt(str(verbatim_srt), segment_level=False, word_level=True)

    # OpenCC s2twp on SRT files
    print("\nApplying OpenCC s2twp to SRT files...")
    cc = opencc.OpenCC("s2twp")
    for path in (human_srt, verbatim_srt):
        text = path.read_text(encoding="utf-8")
        path.write_text(cc.convert(text), encoding="utf-8")
        print(f"[OUT] {path}")

    print(f"\nDONE in {time.time() - t0:.1f}s total")


if __name__ == "__main__":
    main()
