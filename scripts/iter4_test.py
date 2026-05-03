"""iter4 = stable-ts swap PoC.

跑 stable-ts (Whisper large-v3) 在 76 min 訪談檔，產出兩份 SRT：
- 20260415.srt          = segment-level（給人讀）
- 20260415.verbatim.srt = word-level（給 LLM repurpose）

跟 iter3 / Memo 比的 metrics 由 scripts/iter4_compare.py 跑（讀完三份 SRT 出 markdown 報告）。

Algorithm 設計（對位 iter3 缺的點）：
- word-level timestamp 走 cross-attention DTW（真實），不是線性插值
- 切句子靠：標點 + 0.4s gap + 0.2s/3 字 merge + 22 字硬上限
- VAD threshold 0.25（比預設 0.35 鬆，抓開頭 backchannel）
- Anti-hallucination 三件 (PR #274)
- code-switch 不走自寫 ASCII regex（word-level 自然不切 compound）
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
OUT_DIR = ROOT / "tests" / "files" / "out" / "whisperx-iter4"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# 純頓號詞表（PR #274 反 prompt-leak 教訓：不要「主持人 X」label 結構）
INITIAL_PROMPT = (
    "數位遊牧、Traveling Village、Hell Yes、花蓮、Paul、本尊、心酸血淚、保羅、安吉、哥大"
)

# Custom regroup string for Taiwan podcast
#   cm  = clamp_max（限制 segment 字數）
#   sp  = split_by_punctuation（中英 . 。 ? ？ ! ！ , ，）
#   sg  = split_by_gap 0.4s（自然停頓 ≥0.4s 必切）
#   mg  = merge_by_gap 0.2s + max_words=3（短應答可保留獨立）
#   sl  = split_by_length 22 chars（硬上限）
REGROUP = "cm_sp=.* /。/?/？/!/！/,* /，_sg=.4_mg=.2+3_sp=.* /。/?/？_sl=22"


def main() -> None:
    print(f"torch {torch.__version__}, CUDA {torch.cuda.is_available()}")
    print(f"stable-ts {stable_whisper.__version__}\n")

    # 1. Load model (OpenAI Whisper backend, large-v3, GPU)
    print("Loading large-v3 model...")
    t0 = time.time()
    model = stable_whisper.load_model("large-v3", device="cuda")
    print(f"  loaded in {time.time() - t0:.1f}s\n")

    # 2. Transcribe
    print(f"Transcribing {AUDIO.name} (76 min)...")
    t1 = time.time()
    result = model.transcribe(
        str(AUDIO),
        language="zh",
        initial_prompt=INITIAL_PROMPT,
        vad=True,
        vad_threshold=0.25,
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

    # 3. Save raw result first (之後可重 regroup 不重跑 ASR)
    json_out = OUT_DIR / "20260415.result.json"
    result.save_as_json(str(json_out))
    print(f"\n[OUT] {json_out}")

    # 4. Output raw SRT (尚未 OpenCC)
    human_srt = OUT_DIR / "20260415.srt"
    result.to_srt_vtt(str(human_srt), segment_level=True, word_level=False)

    verbatim_srt = OUT_DIR / "20260415.verbatim.srt"
    result.to_srt_vtt(str(verbatim_srt), segment_level=False, word_level=True)

    # 5. Apply OpenCC s2twp on the SRT files (post-process，避免動 stable-ts in-memory state)
    print("\nApplying OpenCC s2twp to SRT files...")
    cc = opencc.OpenCC("s2twp")
    for path in (human_srt, verbatim_srt):
        text = path.read_text(encoding="utf-8")
        path.write_text(cc.convert(text), encoding="utf-8")
        print(f"[OUT] {path}")

    print(f"\nDONE in {time.time() - t0:.1f}s total")


if __name__ == "__main__":
    main()
