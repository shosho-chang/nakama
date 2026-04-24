---
name: Transcriber 語音轉字幕模組
description: shared/transcriber.py — FunASR + Auphonic + LLM 校正 + 多模態仲裁；2026-04-24 修掉長音檔 ~10% 線性漂移 bug（char_idx 當 timestamp 索引）
type: project
created: 2026-04-14
updated: 2026-04-24
confidence: high

originSessionId: c2ace3b3-f24c-4428-9d8b-ddd315f7d92e
---

## 2026-04-24 重大 Bug 修復：長音檔時間戳線性漂移

### 症狀
EP107 81 分鐘音檔走 transcribe() 不含 LLM 校正，產出 SRT 時間戳在中後段**線性漂移 ~10%**：
- 60s → 偏 ~0s（頭段勉強對）
- 600s → 偏 +66s
- 1200s → 偏 +121s
- 2400s → 偏 +262s
- 末段（>4800s）**clamp 到 audio end time**，多個 cue 擠在同一時間

### 根因
`_funasr_to_srt()` char-level 分支（`is_char_level = len(timestamps) > len(sentences) * 2`）假設 text 的 `char_idx` = `timestamps[char_idx]`。但 **FunASR 的 timestamp 陣列只覆蓋可發音字，不含標點/空白/全形括號**。81 分鐘音檔：text 27047 字、timestamps 24461 個（差 ~10% = 標點佔比）。char_idx 每遇標點就多算一格，漂移線性累積。

### 診斷方法
1. **取樣比對**：9 個時間點切 15 秒片段獨立跑 FunASR（短片段 ASR 無漂移，當 ground truth），跟 hand SRT + 長音檔 A-run SRT 三欄對照
2. **驗證 raw timestamp 準度**：直接找 "同居" 在 `item['text']` 的 char position，用 `ts[non_punct_count_before]` 算時間，比對 hand SRT — 若 raw 差只 ±10s 代表 bug 在下游映射
3. **切掉 batch 變因**：`batch_size_s=6000`（不切塊）vs `=300` — 結果相同，排除 batch 拼接 bug

### 修法
- 新 helper `_funasr_char_to_ts_idx(text, n_ts)`：建 char position → ts idx 的映射
  1. 找出所有「可發音字」位置（用 `_FUNASR_NO_TS_CHAR` 排除標點/空白/全形括號）
  2. 把第 k 個可發音字線性映射到 `ts[k * n_ts / n_content]`（scale 處理 FunASR 對英文 tokenization）
  3. 標點位置繼承前一個可發音字的 ts 索引
- `_funasr_to_srt` char-level 分支：
  - 改用 `text.find(sentence, search_from)` 定位（不再靠 `char_idx += len(sentence)`，因為 `_split_sentences` 會 `.strip()`）
  - 查新 helper 拿 `ts_start` / `ts_end`
- **`shared/srt_align.py:run_asr_segments()` 有同款 bug，同一套 helper 修掉**

### 驗證
- 新 `tests/test_transcriber.py::test_funasr_to_srt_char_level_no_drift_with_punctuation`：30 個合成句 × 15 字（14 可發音 + 1 句號）配 420 timestamp。舊 bug → 末 cue clamp 到 41.9s；修後 → 40.6s ±1s。實測 stash 掉修正 → test 紅燈命中預期訊息 "末 cue 應在 40.6s 附近，得 41.899s"
- EP107 全檔 9 取樣點：修後所有 anchor 偏移 ±2s 內（同居 0.8s / 爸爸可以抱 0.6s / 輔導老師 1.8s）

### 影響範圍（歷史檔）
**所有** 20+ 分鐘長音檔走 transcribe() 產出的 SRT 都有這個漂移 bug。修修有需要可能要重跑歷史轉寫。短音檔（<10 分鐘）漂移 <10s 可能被 Gemini 仲裁/人工校對吸收所以沒發現。

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

### 未來方向
**計畫將 transcriber 獨立出來開源給其他使用者。** 這讓**降低仲裁成本從內部優化升級為產品級需求** — 現行 $1.5/hr 太貴，一般使用者無法承擔。優先順序：
1. ✅ **限 thinking_budget** — PR #24 完成（預設 512，成本降 5-10x）
2. **降級 gemini-2.5-flash**（1 小時工程 + shadow test）— 週一實測後決定
3. **本地方案**（Qwen2.5-Omni / Gemma 4 E4B 等，見 `project_local_multimodal_audio_models.md`）

### PR #24（2026-04-18，merged 419db3d）— thinking_budget + cost tracking 修
- `ask_gemini_audio` 加 `thinking_budget: int | None = 512` 參數，arbiter 顯式傳 512
- `_record_usage` bug 修：之前只算 `candidates_token_count`，漏算 `thoughts_token_count`，cost tracking 顯示的成本僅真實值的 1/5
- 實測預估 1hr Angie：$1.5 → ~$0.3-0.5

### PR #25（2026-04-18，merged 6209c45）— 拒答訊號偵測
- 新 verdict `refused`（加在 `ArbitrationVerdict` 側，`_GeminiResponse` 不動，由 arbiter 偵測後覆蓋）
- `_is_refusal(reasoning)` 偵測拒答字樣：`無關 / 無法判斷 / 無法辨識 / 無法識別 / 沒有相關 / 沒有對應 / 不相關`
- 偵測到 → 強制 verdict=refused、conf=0、final_text=ASR 原文、進 `.qc.md`
- transcriber `_apply_arbitration_verdicts` 把 refused 當 uncertain 處理（pop corrections + 進 QC）
- **code-review 抓到真 bug（分 88）同 branch 修掉**：原實作同時掃 `reasoning` + `final_text`，但 final_text 是轉寫的語音內容，來賓若自然講「這件事無關緊要」會被誤判為拒答、清掉修正。最終版只掃 reasoning（meta-commentary 只出現在 reasoning 欄位）

### PR #26（2026-04-18，merged 1ff1f3f）— `run_transcribe.py` argparse
- 加 CLI flags：`--project-file` / `--output-dir` / `--no-auphonic` / `--no-arbitration` / `--no-llm-correction`
- 開源友善：使用者不需改 source code 就能關 Auphonic / 仲裁

### 待進行（QC 改進清單）
- ✅ **CLI 命令 → Skill 化**：`f:/nakama/.claude/skills/transcribe/` 完成（2026-04-18），SKILL.md + 6 references，週一 Angie 實測作為首次 eval
- ⬜ 本地模型替代 Gemini 2.5 Pro 仲裁（候選見 `project_local_multimodal_audio_models.md`）
- ⬜ **SRT 段落硬拆問題**：FunASR VAD 只看聲學靜默切段，會把「可以」「時間」這種詞拆到相鄰兩段。建議在 LLM 校正 prompt 加「若相鄰段在詞語/短語中間被截斷請合併」+ 後處理 re-segment 步驟
- ✅ **Gemini 仲裁拒答信號**：PR #25 完成
- ⬜ **同音字優先清單**：等 1hr Angie 實測看哪些高頻再補
- ⬜ **[開源] `_REFUSAL_PATTERNS` 可擴充化**：目前 module constant，他人場景要擴充需改 source；應改為 arg + 預設清單（見 `feedback_open_source_ready.md`）

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

### 成本（**實測修正，原估低估 10x**）
**實測：20 分鐘 Angie podcast / 13 個仲裁片段 ≈ USD 0.5**（2026-04-17）。推回 1 小時 podcast ≈ USD 1.5。

**原估為何錯：** 只算 audio input token（$1.25/M × 10s × 32 tok/s ≈ $0.0004/clip），漏算 **thinking mode output token**。Gemini 2.5 Pro output $10/M（含 thinking），dynamic thinking 會吃滿 `max_output_tokens`（PR #22 bug #4 為此從 1024 調到 8192）。

**實測每 clip 成本拆解（推估）：**
- Audio input 320 tok × $1.25/M ≈ $0.0004
- Text input（prompt + SRT context + schema）~1-2K tok × $1.25/M ≈ $0.002
- **Output（thinking + JSON）~3-5K tok × $10/M ≈ $0.03-0.05** ← 主成本
- 合計 ~$0.04/clip，13 clips ≈ $0.5 ✅

**降本選項（依工程量排序）：**
1. **限 thinking budget** — Gemini 2.5 Pro 支援 `thinking_config.thinking_budget`，設低（e.g. 512）直接砍主成本；工程 0.5 小時
2. **降級 gemini-2.5-flash** — audio 也支援，價格低 ~10x；工程 1 小時（重跑 shadow test 驗準確率）
3. **本地 Qwen2.5-Omni-7B-GPTQ-Int4** — 見 `project_local_multimodal_audio_models.md`；工程 1-2 天，風險見該檔

### PR 進度
- ✅ PR-A (#18, 2026-04-17)：`shared/audio_clip.py` ffmpeg 切片（含 tempfile 失敗清理 + capture_output）
- ✅ PR-B (#19, 2026-04-17)：`shared/gemini_client.py` — `ask_gemini_audio()` 支援 Pydantic schema；retryable 只列 `ServerError`（不含 `APIError` 基類，避免 4xx 空重試）；singleton + with_retry + cost tracking；順手修 CI 裝 ffmpeg
- ✅ PR-C (#20, 2026-04-17)：`shared/multimodal_arbiter.py` — per-clip 仲裁；4-way verdict enum；動態 padding（短 2s 長 1s）；前後各 1 行 SRT 文字進 prompt；失敗隔離；code review 找到 `set_current_agent` thread-local bug（worker thread 讀不到主線程設定 → cost tracking 全變 unknown），已修（搬進 `_arbitrate_one`，走 worker 再設）
- ✅ PR-D (#21, 2026-04-17)：`transcriber.py` `_correct_with_llm()` 兩輪 pipeline — Pass 1 Opus 標 uncertain → `arbitrate_uncertain()` → 自動應用 verdicts（策略 A，無第二輪 Opus）；`_write_qc_report` 支援新格式（verdict/信心/Gemini 理由）；整批仲裁失敗退回舊流程；code review 找到 `accept_suggestion` 分支沒寫 `corrections[line]` 的 bug（Opus Pass 1 按 prompt 不放 uncertain 進 corrections，仲裁採納後永遠套不上去），已修並補測試

### 關鍵技術決策
- **多模態 LLM：** Gemini 2.5 Pro（input $1.25/M、output $10/M；thinking 模式下 output 是主成本，估 audio 預算時**必須**連 thinking output 一起估）
- **不走多模態吃整集：** 1 小時音檔即使技術上行（~11.5 萬 tokens < 2M context），但延遲長、注意力稀釋、成本浪費；僅刀口使用
- **不加第二個 ASR：** 工程成本不值 — Whisper 中文品質差、對齊演算法難寫
- **audio clip 規格：** 16kHz mono WAV（Gemini 內部降到 16kbps，先降省傳輸；mono 省 token）

### 未來可加（暫不做）
- 若 Gemini 對英文/混語片段判不準 → 再加 Whisper 作第三意見（路線 3）
