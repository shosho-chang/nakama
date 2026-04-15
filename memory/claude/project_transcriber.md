---
name: Transcriber 語音轉字幕模組
description: shared/transcriber.py — FunASR Paraformer-zh 本地 ASR + Auphonic 雲端前處理 + LLM 校正（Pinyin + JSON diff + QC 報告），PR #9 待 merge
type: project
created: 2026-04-14
updated: 2026-04-15
confidence: high
---

## 狀態：PR #9 open（feat/transcriber-upgrade，5 commits）

### 已完成
- ✅ `shared/auphonic.py` — Auphonic REST API 客戶端（多帳號輪詢 + jingle 裁切）
- ✅ `shared/transcriber.py` — FunASR Paraformer-zh 引擎（取代 openlrc/Whisper）
- ✅ SRT 斷行 ≤20 字 + 標點改空格 + 英文不切斷
- ✅ LLM 校正升級：Pinyin 輔助 + 三輪校對 prompt + JSON diff + uncertainty flagging
- ✅ LifeOS Project 整合：自動從 Obsidian Podcast Project 提取來賓/主題/術語
- ✅ _write_qc_report()：不確定修正寫成 .qc.md
- ✅ 預設 Opus（每集 <$0.40）
- ✅ ASR E2E 測試通過（20 min Podcast，GPU 10 秒）
- ✅ 78 個測試全過（60 transcriber + 18 auphonic）

### 待測試（merge 前或後）
- ⬜ LLM 校正 E2E：`use_llm_correction=True` 搭配真實 podcast 音檔
- ⬜ Auphonic E2E：完整 pipeline 含 normalization + 降噪
- ⬜ 辨識度評估：Pinyin 輔助對同音字修正效果

### 待進行（merge 後）
- ⬜ CLI 命令 → Skill 化

## 架構

```
音檔 → Auphonic 雲端前處理（normalization + 動態降噪）
     → 裁切免費方案頭尾 6s Jingle（ffmpeg）
     → FunASR Paraformer-zh（本地 GPU ASR + VAD + 時間戳 + Hotword）
     → OpenCC 簡轉繁 → 標點改空格 → 字幕斷行 ≤20 字
     → _correct_with_llm()（Pinyin 輔助 + 三輪校對 + JSON diff）
     → SRT + .qc.md 輸出
```

### LLM 校正設計（2026-04-15 升級）
- **Pinyin 輔助**：pypinyin 為 ASR 輸出加拼音，幫助 Opus 辨識同音字
- **三輪校對 prompt**：機械校正 → 語意校正 → 交付檢核（融合修修 ChatGPT 實戰 prompt）
- **JSON diff 輸出**：只回傳有改的行 + uncertain 清單，減少 over-correction
- **LifeOS 整合**：`project_file` 參數自動提取 guest/topic/Research Dropbox/Script
- **QC 報告**：不確定修正寫成 `.qc.md`（含 risk level: high/medium/low）
- **Fallback**：JSON 解析失敗時 fallback 到 regex `[N] text` 格式

### 成本估算（1 小時 Podcast，JSON diff 模式）
- Haiku: ~$0.07 / Sonnet: ~$0.20 / **Opus: ~$0.33**

### GPU 環境
- torch 2.11.0+cu128，RTX 5070 Ti 16GB
- `pip install funasr torch torchaudio --index-url https://download.pytorch.org/whl/cu128`
