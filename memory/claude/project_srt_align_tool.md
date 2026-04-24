---
name: SRT 字幕時間對齊工具
description: shared/srt_align.py + scripts/align_srt.py — shift/scale/auto/retime 四模式，用來對齊跑掉時間戳的 SRT；2026-04-24 搭配 FunASR bug 修復實戰驗證
type: project
created: 2026-04-24
confidence: high
originSessionId: b3e311d8-3e4a-427d-a7e2-147b0d4bab90
---
## 背景
2026-04-24 幫修修處理 EP107 音檔（G:/Footages/EP107_TEST.mp3 + .srt）字幕時間對不上的問題而做。原以為是字幕漂移，查到底發現是 FunASR 長音檔時間戳 bug（見 `project_transcriber.md`）；hand SRT 其實對，只差 ~1s。

## 模組位置
- [shared/srt_align.py](../../shared/srt_align.py) — 核心函式
- [scripts/align_srt.py](../../scripts/align_srt.py) — CLI

## 四種模式

| 模式 | 用法 | 適用情境 |
|---|---|---|
| `--shift <秒>` | `align_srt.py in.srt --shift 1.0` | 偏移量已知，固定位移 |
| `--scale <a> --shift <b>` | `t_new = a*t_old + b` | 已知線性漂移參數 |
| `--auto --audio <mp3>` | 跑 FunASR 比對文字 + 最小平方擬合 `(a, b)` | 全局線性偏移 |
| `--retime --audio <mp3>` | 逐 cue 從 ASR 轉移時間戳 + 鄰居內插 | 非線性抖動、局部偏差 |

`--retime` 最強：搭配修好的 FunASR，EP107 上 81.3% cue 直接採用 ASR 時間，median +1.15s，其餘用前後鄰居線性內插。

## 關鍵實作細節

### 文字比對評分（`_best_match`）
- `SequenceMatcher.ratio()` 在 hand cue（短）vs ASR segment（長）情境失準 — hand 是 ASR 的子字串，ratio 被分母拉低
- 解法：`max(SequenceMatcher.ratio, substring_coverage)`，其中 `substring_coverage = LCS(short, long) / len(short)`
- 正規化必須 strip HTML 標籤（`<b>...</b>`）、標點、空白、簡繁差（用 OpenCC s2t 把 ASR 輸出轉繁體再比）

### 兩 pass 匹配（`match_cues_to_asr`）
1. Pass 1：大視窗（±2×window）抓高信心（ratio≥0.85）錨點，估中位偏移
2. Pass 2：以中位偏移為中心的小視窗收所有可用匹配

### Retime 演算法（`retime_cues_from_asr`）
- 每個 hand cue 在 ±window_s 內找最像 ASR segment，ratio≥threshold 則採 ASR start/end
- 沒匹配到的 cue → 前後最近 matched cue 做線性內插（依原 start 比例）
- 序列頭尾未匹配區用最近 matched cue 的 offset 平移

## 使用範例（實測有效）
```bash
# EP107 dry-run 看 offset 分布
python scripts/align_srt.py "G:/Footages/EP107_TEST.srt" --audio "G:/Footages/EP107_TEST.mp3" --retime --dry-run

# 實際產檔
python scripts/align_srt.py "G:/Footages/EP107_TEST.srt" --audio "G:/Footages/EP107_TEST.mp3" --retime --ratio-threshold 0.7 --window 5 --output "G:/Footages/EP107_TEST.retimed.srt"
```

## 踩過的坑
1. **ASR 簡體 vs SRT 繁體**：`run_asr_segments` 原本沒做 s2t，char-level 比對被字形差拉爆（median ratio 0.22）；加 `_to_traditional` 後拉回 0.90+
2. **hand cue 短於 ASR segment**：純 `SequenceMatcher.ratio()` 不夠，必須加 substring coverage
3. **第一次 auto 擬合被 FunASR 自己的時間戳 bug 誤導**：slope 算出 1.115 看起來「線性漂移」，實際是 ASR ground truth 壞了。先確定 ASR 準才能用 auto／retime — 否則結果是把 hand SRT 往錯誤方向推
