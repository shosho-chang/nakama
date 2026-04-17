---
name: Transcriber 語音轉字幕模組
description: shared/transcriber.py — FunASR + Auphonic + LLM 校正 + 多模態仲裁（路線 2，E2E 實測通過，PR #23 全 pipeline 強制無標點輸出）
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

### E2E 實測（PR #22，2026-04-17）
20 分鐘 Angie podcast 音檔跑通，找出並修復 9 個 bug：
1. Opus model ID `claude-opus-4-20250918` 過期 → `claude-opus-4-7`
2. Claude 4.7+ 廢 `temperature` → `ask_claude` / `ask_claude_multi` 改 optional conditional-send
3. Gemini SDK 傳 Pydantic class 要用 `response_schema`，不是 `response_json_schema`（mock test 沒抓到真實契約）
4. Gemini 2.5 Pro dynamic thinking 吃爆預設 `max_output_tokens=1024` → 調 8192，空回應加 finish_reason 診斷
5. Auphonic `filtermethod` enum 改版：`voice_autoeq` → `autoeq`（API 只接受 `hipfilter/autoeq/bwe`）
6. `_env_str` 不剝 inline `#` 註解，`.env.example` 空值行的註解被當值讀進 bitrate 欄位
7. Auphonic download URL 格式是 `/download/audio-result/{uuid}/`，不是 `/production/{uuid}/download/`；API 回傳有 `download_url` 欄位直接用
8. `test_auphonic.py` mock payload 還用舊欄位（ending + output_basename）不對齊 `download_url` 新契約
9. `test_auphonic.py` 沒 delenv 掉開發機 .env 的 ACCOUNT_3-5，autouse fixture 補上

### 運行成本與時間（20 min 音檔，實測）
- Auphonic 上傳 344MB WAV：~19 分（受上行頻寬限制）
- Auphonic 處理：~46 秒（Auphonic 伺服器側超快）
- FunASR GPU ASR：~11 秒
- Opus 校正 Pass 1：~75 秒
- Gemini 仲裁 10-13 片段（3 workers）：~70 秒
- 總計含 Auphonic ~23 分；不含 Auphonic ~3 分

### PR #23（2026-04-17，merged 4396d44）— 強制無標點輸出
**Why：** 標點會誤導下游 LLM 語氣判斷（QC 案例「成功是過遊牧」/「成功試過游牧」— Gemini 仲裁說「音檔與候選文字無關」就維持原文，標點干擾了判斷）。

**改動：**
- 移除 `use_punctuation` 參數，最終 SRT 永遠無標點（句中→空格、句尾→刪除）
- 新增 Pass 2 過濾：LLM 校正之後再跑一次 `_process_srt_line`，清掉 Opus/Gemini 加回的標點
- `_correct_with_llm` prompt 加「輸出不要標點，半形空格分隔」
- **保留 `punc_model="ct-punc-c"`**（注意 rationale）：實際作用是給 `_funasr_to_srt` 句尾標點做**字級時間戳對齊**（看 `_split_sentences` L631 「先用句尾標點拆分」），不是給 LLM 校正參考（Pass 1 在 LLM 之前已去標點，LLM 永遠看不到）
- Code review 找到 mock 契約問題（9 字配 2 timestamp），改用 FunASR `sentence_info` 真實形式

### 待進行（QC 改進清單）
- ⬜ CLI 命令 → Skill 化
- ⬜ 本地模型替代 Gemini 2.5 Pro 仲裁（候選見 `project_local_multimodal_audio_models.md`）
- ⬜ **SRT 段落硬拆問題**：FunASR VAD 只看聲學靜默切段，會把「可以」「時間」這種詞拆到相鄰兩段。建議在 LLM 校正 prompt 加「若相鄰段在詞語/短語中間被截斷請合併」+ 後處理 re-segment 步驟
- ⬜ **Gemini 仲裁拒答信號**：Gemini 回「音檔與候選文字無關」這類拒答應 downgrade 為低信心而非採信原文（現在會直接保留 ASR 原文）
- ⬜ **同音字優先清單**：是/試/式、地/的、做/作 等中文 ASR 高頻同音字可在 prompt 顯式列出

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
- ✅ PR-D (#21, 2026-04-17)：`transcriber.py` `_correct_with_llm()` 兩輪 pipeline — Pass 1 Opus 標 uncertain → `arbitrate_uncertain()` → 自動應用 verdicts（策略 A，無第二輪 Opus）；`_write_qc_report` 支援新格式（verdict/信心/Gemini 理由）；整批仲裁失敗退回舊流程；code review 找到 `accept_suggestion` 分支沒寫 `corrections[line]` 的 bug（Opus Pass 1 按 prompt 不放 uncertain 進 corrections，仲裁採納後永遠套不上去），已修並補測試

### 關鍵技術決策
- **多模態 LLM：** Gemini 2.5 Pro（$1.25/M input audio，32 tokens/秒，10 秒 clip ~$0.0004）
- **不走多模態吃整集：** 1 小時音檔即使技術上行（~11.5 萬 tokens < 2M context），但延遲長、注意力稀釋、成本浪費；僅刀口使用
- **不加第二個 ASR：** 工程成本不值 — Whisper 中文品質差、對齊演算法難寫
- **audio clip 規格：** 16kHz mono WAV（Gemini 內部降到 16kbps，先降省傳輸；mono 省 token）

### 未來可加（暫不做）
- 若 Gemini 對英文/混語片段判不準 → 再加 Whisper 作第三意見（路線 3）
