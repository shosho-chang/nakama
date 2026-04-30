"""分析 SRT 找出 sub-cue 拆分問題。

跑完 WhisperX → SRT 後掃這四類 issue：
1. 雙字詞被切（cue N 結尾單字 + cue N+1 開頭單字 拼回是常見詞）
2. 空白 cue（時間長但文字空）
3. 硬拆 ≤MAX 字（剛好 20 字 = force_break 觸發、未在語意邊界）
4. 異常 gap（前後 cue 時間差大，可能漏辨識）

用法：python scripts/analyze_srt_cuts.py <srt_path>
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

MAX = 20  # 對齊 _MAX_SUBTITLE_CHARS

# 常見雙字 / 三字詞，被切到 cue 邊界要 flag
COMMON_BIGRAMS = {
    # 連接詞
    "然後", "因為", "所以", "但是", "或者", "可是", "如果", "雖然", "不過",
    "其實", "就是", "只是", "也是", "還是", "或是", "以及", "而且", "並且",
    # 副詞
    "可能", "應該", "也許", "大概", "可以", "需要", "必須", "已經", "正在",
    "剛剛", "馬上", "立刻", "突然", "終於", "永遠", "一直", "經常", "偶爾",
    # 代詞
    "我們", "你們", "他們", "自己", "大家", "別人", "什麼", "怎麼", "為什麼",
    # 量詞 / 時間
    "幾個", "一些", "很多", "一點", "今天", "昨天", "明天", "今年", "去年",
    "禮拜", "星期",
    # 動詞 / 形容詞常見
    "覺得", "知道", "認為", "發現", "看到", "聽到", "感覺", "希望", "決定",
    "重要", "有趣", "困難", "容易", "簡單", "複雜",
    # 介係詞 / 助詞
    "對於", "關於", "至於", "由於", "對方",
    # podcast / 訪談常見
    "其中", "目前", "之前", "之後", "以前", "以後", "後來", "本來", "原來",
    "結果", "另外", "尤其", "特別",
}

# 三字詞
COMMON_TRIGRAMS = {
    "為什麼", "怎麼樣", "這樣子", "那時候", "這時候", "這個人", "那個人",
    "另外的", "事實上", "基本上", "其實是",
}


def parse_srt(path: Path) -> list[tuple[int, str, str, str]]:
    """回傳 (seq, ts_line, text, raw_block)。"""
    blocks = []
    raw = path.read_text(encoding="utf-8")
    for chunk in raw.strip().split("\n\n"):
        lines = chunk.splitlines()
        if len(lines) < 2:
            continue
        seq = int(lines[0].strip())
        ts_line = lines[1].strip()
        text = "\n".join(lines[2:]).strip() if len(lines) > 2 else ""
        blocks.append((seq, ts_line, text, chunk))
    return blocks


def parse_ts(ts_line: str) -> tuple[float, float]:
    m = re.match(r"(\d+):(\d+):(\d+),(\d+) --> (\d+):(\d+):(\d+),(\d+)", ts_line)
    h1, m1, s1, ms1, h2, m2, s2, ms2 = map(int, m.groups())
    start = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000
    end = h2 * 3600 + m2 * 60 + s2 + ms2 / 1000
    return start, end


def main():
    srt_path = Path(sys.argv[1])
    blocks = parse_srt(srt_path)
    print(f"Total cues: {len(blocks)}")
    print(f"Path: {srt_path}\n")

    # 1. 雙字 / 三字詞被切（cue N 結尾 + cue N+1 開頭）
    print("=" * 60)
    print("[1] 詞被切到 cue 邊界")
    print("=" * 60)
    boundary_cuts = []
    for i in range(len(blocks) - 1):
        seq_a, _, text_a, _ = blocks[i]
        seq_b, _, text_b, _ = blocks[i + 1]
        if not text_a or not text_b:
            continue
        # 雙字
        bigram = text_a[-1] + text_b[0]
        if bigram in COMMON_BIGRAMS:
            boundary_cuts.append((seq_a, seq_b, bigram, text_a, text_b))
        # 三字（取 a 結尾 1 + b 開頭 2 / a 結尾 2 + b 開頭 1）
        if len(text_a) >= 1 and len(text_b) >= 2:
            tri = text_a[-1] + text_b[:2]
            if tri in COMMON_TRIGRAMS:
                boundary_cuts.append((seq_a, seq_b, tri, text_a, text_b))
        if len(text_a) >= 2 and len(text_b) >= 1:
            tri = text_a[-2:] + text_b[0]
            if tri in COMMON_TRIGRAMS:
                boundary_cuts.append((seq_a, seq_b, tri, text_a, text_b))
    print(f"找到 {len(boundary_cuts)} 處詞被切：")
    for seq_a, seq_b, word, text_a, text_b in boundary_cuts[:25]:
        print(f"  cue {seq_a}/{seq_b}「{word}」: ...{text_a[-6:]} | {text_b[:6]}...")
    if len(boundary_cuts) > 25:
        print(f"  ... 還有 {len(boundary_cuts) - 25} 處")

    # 2. 空白 / 過短 cue
    print()
    print("=" * 60)
    print("[2] 空白 cue（時間長但文字空 / 極短）")
    print("=" * 60)
    empty_cues = []
    for seq, ts_line, text, _ in blocks:
        start, end = parse_ts(ts_line)
        dur = end - start
        if not text and dur > 0.5:
            empty_cues.append((seq, dur, "<空>"))
        elif text and dur > 5.0 and len(text) < 3:
            empty_cues.append((seq, dur, text))
    print(f"找到 {len(empty_cues)} 處：")
    for seq, dur, text in empty_cues[:15]:
        print(f"  cue {seq} ({dur:.1f}s): {text!r}")

    # 3. 剛好 20 字硬拆（force_break）
    print()
    print("=" * 60)
    print("[3] 硬拆 20 字（_MAX_SUBTITLE_CHARS force_break）")
    print("=" * 60)
    forced = [(seq, text) for seq, _, text, _ in blocks if len(text) == MAX]
    print(f"找到 {len(forced)} 處 cue 文字長度剛好 = {MAX}（高機率是 force_break 觸發）")
    for seq, text in forced[:15]:
        print(f"  cue {seq}: {text}")
    if len(forced) > 15:
        print(f"  ... 還有 {len(forced) - 15} 處")

    # 4. 異常 gap（cue N 結尾到 cue N+1 開頭差距 > 5s）
    print()
    print("=" * 60)
    print("[4] 異常 gap（cue 間靜音 > 5s）")
    print("=" * 60)
    gaps = []
    for i in range(len(blocks) - 1):
        seq_a, ts_a, _, _ = blocks[i]
        seq_b, ts_b, _, _ = blocks[i + 1]
        _, end_a = parse_ts(ts_a)
        start_b, _ = parse_ts(ts_b)
        gap = start_b - end_a
        if gap > 5.0:
            gaps.append((seq_a, seq_b, end_a, start_b, gap))
    print(f"找到 {len(gaps)} 處 gap > 5s：")
    for seq_a, seq_b, end_a, start_b, gap in gaps[:10]:
        print(f"  cue {seq_a}→{seq_b} 從 {end_a:.1f}s → {start_b:.1f}s ({gap:.1f}s gap)")

    # 5. 統計：cue 長度分布
    print()
    print("=" * 60)
    print("[5] Cue 文字長度分布")
    print("=" * 60)
    lens = [len(text) for _, _, text, _ in blocks if text]
    if lens:
        buckets = {f"{i}-{i + 4}": 0 for i in range(0, MAX + 5, 5)}
        for ln in lens:
            key = f"{(ln // 5) * 5}-{(ln // 5) * 5 + 4}"
            if key in buckets:
                buckets[key] += 1
        for k, v in buckets.items():
            bar = "█" * (v * 50 // max(buckets.values()))
            print(f"  {k}: {v:4d} {bar}")
        print(f"  最長: {max(lens)}，最短: {min(lens)}，平均: {sum(lens) / len(lens):.1f}")


if __name__ == "__main__":
    main()
