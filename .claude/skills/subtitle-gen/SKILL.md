---
name: subtitle-gen
description: >
  初始字幕產生：episode 的 normalized.wav → 本機 WhisperX large-v3 →
  subs/raw.srt（cue 級字幕）+ subs/words.json（字級 timestamps）。
  Use when the user says 「產字幕」「跑 ASR」「subtitle-gen」「辨識字幕」,
  or after audio-prep completes in the podcast pipeline. GPU 工作——
  跑之前必讀本文的 GPU 注意事項。
---

# subtitle-gen — 初始字幕產生（GPU）

`scripts/run_subtitle_gen.py`（repo：`E:\nakama`）的互動包裝。

## 前置

- `<episode>/normalized.wav` 必須存在（audio-prep 產出）；沒有 → 先跑 `/audio-prep`
- 參考資料自動收集：`<episode>/refs/` + 訪談準備資料夾（依來賓名配對，見
  subtitle-correct skill 的說明）→ 抽 hotwords 餵 initial prompt

## Cue 品質（shared/cue_builder.py）

斷句從**字級真實時間戳**建構：jieba 詞邊界（詞絕不切半）+ 語音停頓優先斷句
+ 14/22 字軟硬上限，時間戳零內插。不是按字數硬切——修修 2026-07-25 驗收
回饋後重寫（舊版「先請/教老師」型切爛句是紅線）。

## GPU 注意事項（必守）

- **必須用裝了 torch cu128 的 Python**：修修桌機 =
  `C:\Users\Shosho\AppData\Local\Programs\Python\Python310\python.exe`（**不是** `python`/3.14）
- script 起跑會自動 precheck PCIe link gen；若警告「沒鎖 Gen 4」→ **停下來問修修**，
  不要硬跑（Blackwell + Gen 5 有全機黑屏前科，見
  `memory/claude/project_pcie_link_instability_2026_05_01.md`）
- `model.refine()` 類工作（srt_refine.py）仍在禁區，本 skill 不涉及

## 執行

```
C:\Users\Shosho\AppData\Local\Programs\Python\Python310\python.exe ^
  E:\nakama\scripts\run_subtitle_gen.py "<episode 資料夾>" ^
  --host-name "張修修" --show-name "<節目名>"
```

- 60 分鐘音檔約 2–5 分鐘（ASR ~1 分 + align）——背景跑
- 產出：`subs/raw.srt`、`subs/words.json`、`subs/gen_manifest.json`

## 完成後回報

讀 `gen_manifest.json` 回報 cues / words 數與耗時，抽 SRT 開頭 5 個 cue 給使用者掃一眼，然後建議接 `/subtitle-correct`。
