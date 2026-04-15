---
name: Transcriber 語音轉字幕模組
description: shared/transcriber.py — FunASR + Auphonic + LLM 校正（Pinyin/JSON diff/LifeOS 整合/Opus），已 merge 到 main（PR #9）
type: project
created: 2026-04-14
updated: 2026-04-15
confidence: high
---

## 狀態：已 merge（PR #9，2026-04-15）

### 已完成
- ✅ `shared/auphonic.py` — Auphonic REST API 客戶端（多帳號輪詢 + jingle 裁切）
- ✅ `shared/transcriber.py` — FunASR Paraformer-zh 引擎（取代 openlrc/Whisper）
- ✅ SRT 斷行 ≤20 字 + 標點改空格 + 英文不切斷
- ✅ LLM 校正：Pinyin 輔助 + 三輪校對 prompt + JSON diff + uncertainty flagging + QC 報告
- ✅ LifeOS Project 整合：`project_file` 參數自動提取來賓/主題/術語
- ✅ 預設 Opus（每集 <$0.40）
- ✅ ASR E2E 測試通過（20 min Podcast，GPU 10 秒）
- ✅ 78 個測試全過（60 transcriber + 18 auphonic）

### 待測試
- ⬜ LLM 校正 E2E：`use_llm_correction=True` 搭配真實 podcast 音檔
- ⬜ Auphonic E2E：完整 pipeline 含 normalization + 降噪

### 待進行
- ⬜ CLI 命令 → Skill 化

## 架構

```
音檔 → Auphonic 雲端前處理（normalization + 動態降噪）
     → 裁切免費方案頭尾 6s Jingle（ffmpeg）
     → FunASR Paraformer-zh（本地 GPU ASR + VAD + 時間戳 + Hotword）
     → OpenCC 簡轉繁 → 標點改空格 → 字幕斷行 ≤20 字
     → _correct_with_llm()（Pinyin + 三輪校對 + JSON diff + QC 報告）
     → SRT + .qc.md 輸出
```

### LLM 校正設計
- **Pinyin 輔助**：pypinyin 為 ASR 輸出加拼音，幫助 Opus 辨識同音字
- **三輪校對 prompt**：機械校正 → 語意校正 → 交付檢核（融合修修 ChatGPT 實戰 prompt）
- **JSON diff 輸出**：只回傳有改的行 + uncertain 清單
- **LifeOS 整合**：`project_file` 參數自動提取 guest/topic/Research Dropbox/Script
- **QC 報告**：`.qc.md`（含 risk level: high/medium/low）
- **Fallback**：JSON 解析失敗時 fallback 到 regex

### 成本（1 小時 Podcast）
- Haiku: ~$0.07 / Sonnet: ~$0.20 / **Opus: ~$0.33**
