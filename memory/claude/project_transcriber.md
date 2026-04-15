---
name: Transcriber 語音轉字幕模組
description: shared/transcriber.py — FunASR Paraformer-zh 本地 ASR + Auphonic 雲端前處理，Podcast 轉繁體中文 SRT
type: project
created: 2026-04-14
updated: 2026-04-15
confidence: high
---

## 狀態：feat/transcriber-upgrade branch，待 merge

`shared/transcriber.py` + `shared/auphonic.py`

## 架構（2026-04-15 升級）

```
音檔 → Auphonic 雲端前處理（normalization -16 LUFS + 動態降噪）
     → 裁切免費方案頭尾 6s Jingle（ffmpeg）
     → FunASR Paraformer-zh（本地 GPU ASR + VAD + 時間戳 + 標點 + Hotword）
     → OpenCC 簡轉繁
     → 標點控制（預設不加）
     → _correct_with_llm() (可選, 用 shared/anthropic_client.py)
     → SRT 輸出
```

### 從 Whisper 升級到 FunASR 的原因
- FunASR Paraformer-zh 在乾淨音頻 AISHELL-2 CER 2.3%（Whisper ~4%+）
- 內建 VAD + 時間戳 + 標點 + Hotword（Whisper 需要額外處理）
- 解決 openlrc 鎖 anthropic<0.40 的依賴衝突

### Auphonic 多帳號
- 5 個免費帳號（每帳號 2 hr/月 = 10 hr/月）
- `AUPHONIC_API_KEYS=key1,key2,...` 逗號分隔
- 自動輪詢找有餘額的帳號

## 安裝（GPU 環境）

```bash
pip install funasr torch torchaudio
```

系統需求：ffmpeg + ffprobe（Auphonic jingle 裁切用）

## E2E 測試待執行

需要 GPU 環境 + 實際 Podcast 音檔 + Auphonic API key 驗證。
