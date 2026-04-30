# ADR-013: Transcribe 引擎重新選型 — FunASR 退場 / WhisperX 接手

**Date:** 2026-04-30
**Status:** Accepted（含 2026-04-30 Amendment：speaker diarization 移出 scope）
**Supersedes:** Transcribe pipeline 中 FunASR 引擎選擇部分（ADR-001 line ~ 與 PR #9 commit message 對應的選型理由）

---

## 2026-04-30 Amendment — speaker diarization 移出 scope

修修當天確認**不需要 speaker diarization**（Line 1 訪談 cleanup → narrative 用人耳/編輯處理，不靠演算法分軌）。

下述項目自 D1 / Consequences scope 撤除：

- pyannote diarization、word-level alignment、`whisperx.assign_word_speakers` 全鏈路
- `pyannote.audio` 依賴
- `HUGGINGFACE_TOKEN` env + 對應 runbook
- SRT 內 `[SPEAKER_00]` label
- CLI `--no-diarization` flag

引擎本體（WhisperX = Whisper Large V3 + faster-whisper backend）落地不變；本 ADR 後續段落原文保留作歷史脈絡，**實際 ship 內容以本 amendment 為準**。

---

## Context

### 原 ASR 引擎選擇（2026-04-15 / PR #9）

`shared/transcriber.py` 採用 **FunASR Paraformer-zh** 作為 ASR 引擎，rationale 引用學術 benchmark：

> Whisper Large V3 在純中文遠差於 FunASR Paraformer-zh（AIShell1 CER 4.72% vs 0.54%）

論證來自 [ModelScope FunASR README](https://github.com/modelscope/FunASR) + AIShell1 paper benchmark。當時未做真實工況對照，直接採用。

### 真實工況的反證（2026-04-30 benchmark）

[2026-04-30-funasr-vs-whisper-results.md](../research/2026-04-30-funasr-vs-whisper-results.md) — 用修修自己 podcast 的 **76 分鐘真實訪談錄音**（Auphonic-normalized），對照：

- **A：本 repo FunASR pipeline**（`--no-auphonic --no-llm-correction` 純引擎輸出）
- **B：MemoAI（Whisper Large V2 desktop app）**

逐桶 228 段對照後，修修主觀判定 **MemoAI 全勝**：

1. **Code-switching 全敗** — `Traveling Village` → `potravelling village`、`Hell yes` → `hell yes`、`跟Paul` → `跟友`。Paraformer-zh 訓練語料純中文無英文 vocabulary，引擎本質決定，演算法層救不回
2. **同音字選錯** — `數位遊牧` → `蘇味遊牧`、`心酸` → `辛酸`。LM 缺 podcast domain knowledge
3. **重複字 bug** — `本本尊`、`花蓮花蓮`、`有一個有一個`。VAD 邊界 artifact
4. **VAD 不貼對話節奏** — 訪談「對 / 對 / 對 / 好」對答被合併成「對對對 好」，失去 turn-taking 結構

### 失敗歸因

[今日對話](../../memory/claude/) 與修修共同檢視結論：**95% 是引擎模型問題、5% 是 nakama 演算法問題**（`OpenCC("s2t")` 應為 `s2twp`，1 行修可解 `着 / 羣` 簡繁混用）。即使把演算法寫到極致 + 跑滿 LLM 校正 + Gemini 仲裁，**英文 code-switch 失敗模式無法靠下游補全**（LLM 看不到音訊只能猜拼音串、Gemini 仲裁需 Pass 1 標 uncertain 但 FunASR 給的拼音串看起來「完整」不會被 flag）。

### Paper benchmark 為何誤導

AIShell1 = 朗讀普通話 + 零 code-switch + 零台灣腔 + 純中文 vocabulary。**完全不對應**台灣 podcast 訪談工況（中英 code-switch 為常態 + 台灣腔 + 對話節奏 + 業務領域詞彙）。原 ADR 用此 benchmark 做選型 = [feedback_design_rationale_trace](../../memory/claude/feedback_design_rationale_trace.md) 的典型誤判（憑直覺/論文選型不 trace 真實 pipeline）。

## Decision

**Transcribe pipeline ASR 引擎從 FunASR Paraformer-zh 換為 Whisper Large V3 系列**，第一輪 ship 採 **WhisperX**（faster-whisper backend + pyannote diarization），保留 Qwen3-ASR-1.7B 為後備。

詳細選型分析：[2026-04-30-asr-engine-prior-art.md](../research/2026-04-30-asr-engine-prior-art.md)。

### 7 條 sub-decision

#### D1 — 引擎主路徑：WhisperX（Whisper Large V3 + pyannote）

理由：

- **MemoAI 用 Whisper V2 已贏 FunASR**；V3 ≥ V2，加 nakama LLM 校正 + Gemini 仲裁 = 結構必贏 MemoAI
- **Windows GPU 已 explicitly supported**（CUDA 12.8，與修修 RTX 5070 Ti 16GB 開發機一致）
- **Diarization 內建 pyannote** → 順便解 Line 1 訪談 speaker 分離（[project_three_content_lines.md](../../memory/claude/project_three_content_lines.md) 最緊急 line 的關鍵設計題）
- 生態最成熟（21K stars、近 4 年維護、衛星 GUI / standalone 多）
- BSD-2-Clause license

#### D2 — 引擎後備：Qwen3-ASR-1.7B（only if WhisperX 仍輸 MemoAI）

理由：

- Mandarin WER（WenetSpeech net/meeting）4.97 / 5.88 vs Whisper V3 9.86 / 19.11 — **約 2× 領先**
- AISHELL-2-test 2.71 vs Whisper V3 5.06
- Apache-2.0
- 風險：Windows GPU 部署不確定 + diarization 自接 pyannote = 兩個 unknown，先別兩個一起吃

觸發切換 Qwen3-ASR：「ship WhisperX 後仍輸 MemoAI」。如果贏了 MemoAI 但修修追求極限，可 shadow run 看 delta。

#### D3 — LLM 校正 + 多模態仲裁層 unchanged

`_correct_with_llm` + `multimodal_arbiter` 全套 (~11 個 function) 為 engine-agnostic，**不動**。Opus pass 1 + Gemini 仲裁繼續發揮對 Mandarin 同音字 / domain 名詞的補強作用。

#### D4 — Auphonic 前處理 unchanged

雲端 normalization + 動態降噪保留，[與 2026-04-30 修修明確拍板](../../memory/claude/project_three_content_lines.md)的決定一致。

#### D5 — Hotwords API 改寫成 `initial_prompt`

FunASR `hotword=" ".join(hotwords)` → Whisper 走 `initial_prompt`（自然語句）。`_extract_hotwords()` retain，但組裝邏輯改：從空白分隔字串 → 自然中文 prompt（如「來賓姓名：張安吉。主題：數位遊牧。專名：Traveling Village, Paul, Hell yes」）。

#### D6 — SRT 格式轉換 (~5 個 function) 退場

`_funasr_to_srt` / `_funasr_char_to_ts_idx` / `_split_sentences` / `_force_break` / `_split_by_pattern` — 部分退場（WhisperX 自己出 word-level segments + timestamps），部分可重用（如果想對 WhisperX 句子重新斷行 ≤20 字）。

退場時保留：4-24 fix 的 `_funasr_char_to_ts_idx` 教訓進 [project_transcriber.md](../../memory/claude/project_transcriber.md) 補一條 historical 紀錄。

#### D7 — 後處理 + QC + LLM 校正 + Audio clip + 仲裁 unchanged

`_to_traditional`（順便 `s2t → s2twp` 1 行修）/ `_remove_punctuation` / `_process_srt_line` / `_write_qc_report` / `_extract_srt_texts` / `_replace_srt_texts` / `extract_clip` / `arbitrate_uncertain` 全套 unchanged。

## Consequences

### 立即影響

1. **`shared/transcriber.py` 大改** — `_get_asr_model` + `model.generate` call site 重寫；SRT 格式轉換約 ~5 個 function 退場
2. **新模組 `shared/diarization.py`** — pyannote pipeline 薄 wrapper
3. **新依賴 `whisperx` + `pyannote.audio`** — `requirements.txt` + `pyproject.toml` 同步
4. **新 env `HUGGINGFACE_TOKEN`** — `.env.example` 加範例 + runbook（[setup-huggingface-pyannote.md](../runbooks/setup-huggingface-pyannote.md)）
5. **SRT 格式變更** — 加 `[SPEAKER_00] 文字 / [SPEAKER_01] 文字` speaker label（向下相容：no-speaker 模式仍可 fallback）
6. **CLI 旗標新增** `--no-diarization`（給不需要 speaker 分離的 case，如 monologue）
7. **既有測試大改** — `test_funasr_to_srt_*` 系列退場、`test_whisperx_to_srt_*` 系列新增；LLM 校正 / 仲裁 / 後處理測試應全綠

### 對既有 ADR 的影響

- **ADR-001 line 38 範圍外** — 此 ADR 是 transcribe 引擎選型不是 agent 角色重分配
- **ADR-007 (Franky scope)** — 不受影響（不同 agent）
- **無 ADR 直接 supersede** — 原 FunASR 選型 rationale 沒成 ADR、只在 PR #9 commit message + memory，本 ADR 為首次形式化引擎選型決策

### 對 Line 1（Podcast → 訪談多 channel）的影響

- **Speaker diarization 內建解設計題** — 原本 grill 必問「主持人/來賓怎麼分？」現在 WhisperX 直接給
- **訪談 cleanup → narrative** 仍是下游待設計問題（不在 transcribe 範圍）
- 預估 wall-clock：1hr 訪談 ≈ 3-5 分鐘（WhisperX V3 + diar）vs FunASR 1.3 分鐘 — 慢但仍 real-time-fast，可接受

### 工程量估

```
Day 1：runbook + ADR-013（本文件）+ 修修 HF 帳號設定
Day 2：requirements / pyproject 加 whisperx + pyannote.audio
       _get_asr_model / model.generate / SRT 格式轉換重寫
       diarization.py 薄 wrapper
       hotword API 改 initial_prompt
       s2t → s2twp 順手修
Day 3：unit tests 重綠（FunASR 系列退場、WhisperX 系列新增）
       同 20260415.wav 重跑 benchmark vs MemoAI
       目標：每個維度都贏；輸了則 D2（Qwen3-ASR）路徑啟動
Day 4（buffer）：bug fix + 結論進 memory + 退場 FunASR 依賴
```

### 退場風險

| 風險 | 機率 | mitigation |
|---|---|---|
| WhisperX Windows CUDA 跑不起來 | 低（官方 explicit support）| 改 WSL2 或 Docker |
| pyannote diarization quality 在中文訪談差 | 中 | 先 ship 不帶 diar 版本、diar 改 follow-up |
| Whisper V3 中英 code-switch 仍不夠好 | 低（MemoAI V2 已贏，V3 ≥ V2）| 切換 D2（Qwen3-ASR） |
| Hotword `initial_prompt` 效果不如預期 | 中 | LLM 校正 Pass 1 補位 |
| 既有歷史 SRT artifacts 格式變化（加 speaker label）破壞下游消費者 | 低（目前無下游消費者，Line 1 還沒蓋）| 加 `--no-diarization` flag 保留舊格式 |

### 不變項

- LLM 校正（Opus pass 1）+ Gemini 多模態仲裁完整保留
- Auphonic 前處理保留
- QC 報告格式保留
- CLI 主介面保留（`scripts/run_transcribe.py` argparse 不變、新增 `--no-diarization` flag）
- Skill `.claude/skills/transcribe/SKILL.md` 觸發詞不變

## Open Questions（不阻擋落地）

- **Q1**: WhisperX 對「不切英文單字」邏輯（既有 `_force_break`）如何適配？
  - WhisperX 給 word-level segments，可能不需要再做 force_break；但若要 ≤20 字硬上限可保留
- **Q2**: Speaker label 在 SRT 內的格式長相 — `[SPEAKER_00] 文字` vs `<v Speaker_00>文字</v>` (WebVTT) ?
  - 採 simple bracket prefix 即可；正式 WebVTT 留未來
- **Q3**: pyannote 對「主持人 vs 來賓」分軌 vs「N 個來賓」泛化是否一樣好？
  - 待 D3 day 重跑 benchmark 後實證
- **Q4**: Qwen3-ASR shadow run 何時做？
  - 觸發：WhisperX ship 後若主觀仍輸 MemoAI、或修修追求 Mandarin 極限品質

## 援引

- [2026-04-30-funasr-vs-whisper-results.md](../research/2026-04-30-funasr-vs-whisper-results.md) — 今日 benchmark 對照
- [2026-04-30-asr-engine-prior-art.md](../research/2026-04-30-asr-engine-prior-art.md) — 候選 landscape + Top 2 選型
- [2026-04-30-funasr-vs-whisper-benchmark.md](../plans/2026-04-30-funasr-vs-whisper-benchmark.md) — benchmark plan
- [setup-huggingface-pyannote.md](../runbooks/setup-huggingface-pyannote.md) — 修修 HF + pyannote 設定 runbook
- [project_transcriber.md](../../memory/claude/project_transcriber.md) — transcriber 模組歷史 + 4-24 漂移 bug 教訓
- [project_three_content_lines.md](../../memory/claude/project_three_content_lines.md) — Line 1 訪談需求（speaker diar 來源）
- [feedback_design_rationale_trace.md](../../memory/claude/feedback_design_rationale_trace.md) — paper benchmark 不能取代真實工況的教訓
