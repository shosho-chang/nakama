---
name: Transcriber 語音轉字幕模組
description: shared/transcriber.py — FunASR + Auphonic + LLM 校正；多模態仲裁升級中（路線 2：只用 Gemini audio 仲裁 uncertain，不加第二個 ASR）
type: project
created: 2026-04-14
updated: 2026-04-17
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

---

## 多模態仲裁升級（路線 2，2026-04-17 設計凍結）

### 為何走路線 2（放棄雙 ASR）
原本構想：FunASR + Whisper large-v3 並行 → diff → 仲裁。
調研發現 **Whisper large-v3 在純中文遠差於 FunASR Paraformer-zh**（AIShell1 CER 4.72% vs 0.54%），diff 會爆炸且 Whisper 價值稀釋。

**路線 2：** FunASR 仍是主幹 → Opus 第一輪標 uncertain → 對 uncertain 片段用 Gemini 2.5 Pro audio 仲裁（不需要第二個 ASR）。工程複雜度砍半，省掉對齊演算法。

### 升級後架構
```
FunASR 轉寫 → Opus 第一輪校正（標 uncertain）
           → 對 uncertain 片段切 audio clip（±1s padding）
           → Gemini 2.5 Pro audio 仲裁（多模態直接聽）
           → Opus 第二輪整合（吃仲裁結果）
           → SRT + .qc.md
```

### 成本上升（1 小時 Podcast）
- Gemini audio 仲裁 ~20 個片段 × 10 秒 ≈ $0.05–0.20
- 合計 ~$0.40–0.55（相對現況 $0.33，品質大幅提升）

### PR 進度
- ✅ PR-A (#18, 2026-04-17)：`shared/audio_clip.py` ffmpeg 切片（含 tempfile 失敗清理 + capture_output）
- ✅ PR-B (#19, 2026-04-17)：`shared/gemini_client.py` — `ask_gemini_audio()` 支援 Pydantic schema；retryable 只列 `ServerError`（不含 `APIError` 基類，避免 4xx 空重試）；singleton + with_retry + cost tracking；順手修 CI 裝 ffmpeg
- ✅ PR-C (#20, 2026-04-17)：`shared/multimodal_arbiter.py` — per-clip 仲裁；4-way verdict enum；動態 padding（短 2s 長 1s）；前後各 1 行 SRT 文字進 prompt；失敗隔離；code review 找到 `set_current_agent` thread-local bug（worker thread 讀不到主線程設定 → cost tracking 全變 unknown），已修（搬進 `_arbitrate_one`，走 worker 再設）
- ⬜ PR-D：`transcriber.py` `_correct_with_llm()` 改兩輪 + 接仲裁器

### 關鍵技術決策
- **多模態 LLM：** Gemini 2.5 Pro（$1.25/M input audio，32 tokens/秒，10 秒 clip ~$0.0004）
- **不走多模態吃整集：** 1 小時音檔即使技術上行（~11.5 萬 tokens < 2M context），但延遲長、注意力稀釋、成本浪費；僅刀口使用
- **不加第二個 ASR：** 工程成本不值 — Whisper 中文品質差、對齊演算法難寫
- **audio clip 規格：** 16kHz mono WAV（Gemini 內部降到 16kbps，先降省傳輸；mono 省 token）

### 未來可加（暫不做）
- 若 Gemini 對英文/混語片段判不準 → 再加 Whisper 作第三意見（路線 3）
