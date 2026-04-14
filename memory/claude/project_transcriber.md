---
name: Transcriber 語音轉字幕模組
description: shared/transcriber.py 已 merge — 本地 ASR + 自建 LLM 校正，Podcast 轉繁體中文 SRT
type: project
created: 2026-04-14
updated: 2026-04-14
confidence: high
---

## 狀態：已 merge（PR #7，2026-04-14）

`shared/transcriber.py` — 獨立模組，可被任何 agent 調用。

## 架構

```
音檔 → openlrc/faster-whisper (本地 GPU, skip_trans=True)
     → OpenCC 簡轉繁
     → 標點控制（預設不加）
     → _correct_with_llm() (可選, 用 shared/anthropic_client.py)
     → SRT 輸出
```

## 安裝（GPU 環境）

```bash
pip install openlrc --no-deps && pip install faster-whisper ctranslate2
```

openlrc 在 optional-deps，因為它鎖 `anthropic<0.40` 與 Nakama 主依賴衝突。

## E2E 測試待執行

需要 GPU 環境 + 實際 Podcast 音檔驗證。
