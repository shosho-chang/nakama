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
- `--audio <path>` 指定非慣例檔名；`--no-auphonic` 只做靜音裁切（測試用）
- 靜音判定預設 -40dB / 0.8s，可用 `--noise-db` / `--min-silence` 調

## 完成後回報

讀 `prep_manifest.json`，回報：輸出長度、頭尾各裁了幾秒、用了哪個 Auphonic 帳號（log 內有）。裁切秒數異常大（>60s）時提醒使用者抽聽頭尾確認沒切到內容。

## 錯誤處理

- 「未設定任何 AUPHONIC_ACCOUNT_N」→ `.env` 沒被找到；確認從有 `.env` 的目錄執行，或 repo 在 `E:\nakama`
- 所有帳號額度不足 → 回報下次 reset 日期（log 內有），問使用者要等還是 `--no-auphonic` 跳過
- CJK 路徑編碼錯誤已在 pipeline 內處理（utf-8 明示），不應再出現
