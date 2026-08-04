---
name: audio-prep
description: >
  Podcast/影片錄音的音檔前處理：episode 資料夾 → Auphonic normalization
  （多帳號輪替 + 免費方案 Jingle 裁切）→ 頭尾靜音裁切 → normalized.wav +
  prep_manifest.json。Use when the user says 「音檔前處理」「normalize 音檔」
  「跑 audio-prep」「把 <episode> 的音檔準備好」, or points at a footage
  episode folder (e.g. G:\footages\20260723 謝伯讓) asking to start the
  subtitle pipeline. 這是字幕產線的第一段；後續接 subtitle-gen。
---

# audio-prep — 音檔前處理

`scripts/run_audio_prep.py`（repo：`E:\nakama`）的互動包裝。

## Episode 資料夾慣例

```
<episode>/               例：G:\footages\20260723 謝伯讓
├── Audio/Live-Mix.wav   ← 原始音檔（大小寫不拘；找不到時列出現有 wav 請使用者選）
├── Video/…              （本 skill 不碰）
├── normalized.wav       ← 本 skill 產出
└── prep_manifest.json   ← 本 skill 產出（下游偵測進度用）
```

## 執行

```
python E:\nakama\scripts\run_audio_prep.py "<episode 資料夾>"
```

- 63 分鐘音檔全程約 10–25 分鐘（上傳 1GB 級 wav + Auphonic 雲端處理）——**放背景跑**，完成後回報
- **時間軸鐵則：normalized.wav 時間軸必須與原始錄影完全一致**（字幕要對回
  DaVinci 的原始影片）。Auphonic 免費方案外加的頭尾 Jingle 各 6 秒會自動裁掉
  （裁掉才「還原」原始時間軸）；**頭尾靜音預設不裁**——僅純音訊用途
  （podcast 上架、不對影片）才用 `--trim-silence` 明確開啟（修修 2026-07-25 裁決）
- `--audio <path>` 指定非慣例檔名；`--no-auphonic` 跳過 Auphonic（額度不足時的
  fallback：直接複製 raw 當 normalized.wav，時間軸不變）

## 已在 Auphonic 網站處理好的音檔（`--pre-processed`）

修修自己上傳 Auphonic、手動下載回來的檔（檔名如 `<date>-processed.wav`）：

```
python E:\nakama\scripts\run_audio_prep.py "<episode>" --pre-processed "<episode>\<date>-processed.wav"
```

不重傳（省額度、省 20 分鐘上傳），只把免費方案的頭尾 Jingle 裁掉——走
`_align_trim` 與 **raw 交叉相關對齊**，輸出時間軸還原成原始錄影。判斷是否
需要這條路徑：processed 檔比 raw 長 ~12s（頭尾各 6s）即是。

驗收：`ffprobe` 出來的 normalized.wav 長度必須與 raw **完全一致**（例：安吉集
4588.158s = 4588.158s）；log 會印對齊偏移與相關峰值（peak <0.5 會退回固定
秒數裁切，此時要人工確認頭尾）。

## 完成後回報

讀 `prep_manifest.json`，回報：輸出長度、有無 Auphonic、用了哪個帳號（log 內有）。若開了 `--trim-silence`，回報裁切秒數並**明確警告時間軸已偏移原始錄影**。

## 錯誤處理

- 「未設定任何 AUPHONIC_ACCOUNT_N」→ `.env` 沒被找到；確認從有 `.env` 的目錄執行，或 repo 在 `E:\nakama`
- 所有帳號額度不足 → 回報下次 reset 日期（log 內有），問使用者要等還是 `--no-auphonic` 跳過
- CJK 路徑編碼錯誤已在 pipeline 內處理（utf-8 明示），不應再出現
