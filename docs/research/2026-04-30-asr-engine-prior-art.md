# 2026-04-30 — ASR Engine Prior-Art Research

換掉 FunASR Paraformer-zh 的引擎選型調研。今日 benchmark（[2026-04-30-funasr-vs-whisper-results.md](2026-04-30-funasr-vs-whisper-results.md)）證實 FunASR 在台灣 podcast 訪談（中英 code-switch + 對話節奏）上輸 MemoAI（Whisper Large V2）。本研究找出能贏 MemoAI 的引擎。

## 0. 結論先行（給跳讀者）

**Top 2 上場試水：**

1. **WhisperX**（faster-whisper Whisper Large V3 + pyannote diarization）— 安全牌、Windows GPU 已驗證、有內建 speaker 分離
2. **Qwen3-ASR-1.7B**（Alibaba, 2026-01）— 上限牌、Mandarin WER 2x 領先 Whisper V3、Apache-2.0、Windows 可行性需自己驗

**淘汰：** Parakeet-TDT-V3（無中文）/ Distil-Whisper（英文 only）/ Apple MLX·WhisperKit（Mac only）/ Cohere（proprietary cloud）/ MCP servers（多為 OpenAI cloud wrapper、不是 local GPU 路徑）

**保留觀望：** SenseVoice（FunASR 同隊、本日實測對 Paraformer 失敗的不信任延伸）/ MiMo-V2.5-ASR（README 缺 benchmark）/ Parakeet-CTC-0.6B-zh-tw（90hr 訓練量小 + light code-switch + Linux only）

## 1. 範圍 + 評選維度

| 維度 | 為何重要 | 權重 |
|---|---|---|
| Mandarin WER（真實口語不是朗讀稿） | 我們本業 | 30% |
| 中英 code-switch（不需 language tag） | FunASR 致命傷 | 25% |
| Speaker diarization | Line 1 訪談主持人/來賓分軌 | 15% |
| Windows GPU 可行性（RTX 5070 Ti 16GB CUDA 12.8） | 修修開發機 | 15% |
| License（commercial OK） | 未來開源/商用 | 5% |
| Wall-clock per 1hr | 實用上限 | 5% |
| 生態成熟度（持續維護） | 長期維護成本 | 5% |

## 2. 局部檢查（Step 0）

**Local codebase**：repo 內有 8 個 file 提到 whisper，全部是 docs/memory/skill 提及，**零實作 code**。FunASR 唯一在用的 ASR 引擎在 `shared/transcriber.py:775-793` `_get_asr_model()` + `:861` `model.generate()`。

**已裝 skills (~/.claude/skills/)**：`transcribe`（FunASR-based，本次要換引擎），無其他 ASR skill。

**已裝 plugins**：6 個（skill-creator / claude-md-management / pyright-lsp / code-review / playwright / firecrawl），無 ASR-related。

**結論**：repo 沒抄作業空間，必走 external library。

## 3. 候選 landscape（依 channel 5+6 結果）

### Tier 1：Top 2 推薦

#### A. WhisperX

| 項目 | 數據 |
|---|---|
| Stars / 維護 | 21.6K, 最後 commit 2026-04-30（活）|
| 最新 release | v3.8.5（2026-04-01）|
| License | BSD-2-Clause |
| Backend | faster-whisper（CTranslate2）+ pyannote-audio |
| 模型 | Whisper Large V2/V3、base、medium |
| **Windows GPU** | **✅ CUDA 12.8 explicitly supported** |
| **Diarization** | **✅ pyannote 內建**（需 HF token + EULA accept） |
| Word-level timestamps | ✅ wav2vec2 forced alignment |
| Speed claim | 70× realtime on Large V2 |
| Chinese WER | 繼承 Whisper V3：WenetSpeech net 9.86 / meeting 19.11 |
| Code-switch | 繼承 Whisper V3 — 我們已驗證 V2 在 code-switch 全勝 FunASR；V3 ≥ V2 |
| 已知 limitation | 數字、特殊字元的 alignment 可能無 timing |

**為什麼是安全牌**：MemoAI 已經贏我們、它用的是 Whisper V2；我們上 V3 + LLM 校正 + Gemini 仲裁 = 結構上必贏。同時內建 speaker diarization 解 Line 1 訪談需求。

#### B. Qwen3-ASR-1.7B

| 項目 | 數據 |
|---|---|
| Stars / 維護 | 阿里官方，活躍 |
| 最新 release | 2026-01-29 |
| License | Apache-2.0 |
| Backend | HF transformers + FlashAttention 2 |
| 模型 | Qwen3-ASR-1.7B / 0.6B + Qwen3-ForcedAligner-0.6B |
| **Windows GPU** | ⚠️ Linux 為主、Windows 沒明說（可能 WSL2 / 直接 pip） |
| **Diarization** | ❌ 無，需自己接 pyannote |
| Timestamps | 透過 ForcedAligner 副模型 |
| Chinese WER（WenetSpeech net/meeting） | **4.97 / 5.88**（vs Whisper V3 9.86 / 19.11）|
| Chinese WER（AISHELL-2-test） | **2.71**（vs Whisper V3 5.06）|
| Code-switch | 自稱 mid-conversation language switching；30+ 語言 |
| 風險 | Windows 部署不確定、需自己組 diarization layer |

**為什麼是上限牌**：Mandarin benchmark 上 ~2× 領先 Whisper V3。如果 Windows 部署順、diarization 自接 pyannote 行得通，這套天花板比 WhisperX 高。

### Tier 2：保留觀望（不在第一輪試）

#### C. MiMo-V2.5-ASR (Xiaomi)

- Apache-2.0、CUDA >= 12.0、**Linux only**
- 自稱 seamless Chinese-English code-switch + multi-speaker + dialects (Wu / Cantonese / Hokkien / Sichuanese)
- **README 缺具體 benchmark 數字**，blog 連結要另外查
- 無 Taiwanese Mandarin 顯式宣告（標準 Mandarin 應該包含）
- 風險：自我宣稱多但無第三方 benchmark 對照

#### D. NVIDIA Parakeet-CTC-0.6B-zh-tw

- **唯一專為 Taiwanese Mandarin + English code-switch 訓練的模型**
- 600M params, CC-BY-4.0
- **僅 90 小時 zh-TW 訓練資料**（相對極小）
- 「light code-switching capability」— 字面比其他 multilingual 模型保守
- NeMo framework 依賴重
- **Linux preferred**（Windows 走 WSL2 不確定）
- 透過 NVIDIA NIM container 也可（但要 NIM 環境）

**為何沒進 Top 2**：訓練資料量小（90hr）+ NeMo 重 + Windows uncertain，期望值不高。但「name says Taiwanese Mandarin」是 Tier 2 觀察點。

#### E. SenseVoice (FunAudioLLM)

- 8K stars、Apache-2.0
- 同 FunAsr 隊伍出品（**今天 FunASR Paraformer-zh 在我們的工況輸了**，這是強烈不信任訊號）
- 自稱 Mandarin/Cantonese 比 Whisper 強 — 但這個「自稱」剛好被今天的工況推翻
- 觀察：團隊持續迭代，未來版本仍可重評，**現在不試**

### Tier 3：直接淘汰

| 候選 | 為什麼淘汰 |
|---|---|
| Whisper.cpp | 49K stars 但 NVIDIA GPU 上輸 CTranslate2（faster-whisper backend）— 用 WhisperX 即可 |
| OpenAI Whisper reference | 慢，benchmark 用、production 不選 |
| Apple MLX Whisper / WhisperKit | Mac/iOS only，修修是 Windows |
| NVIDIA Parakeet-TDT-V3 | 25 European languages **無中文** |
| Distil-Whisper | 蒸餾版、英文 only |
| Crisper Whisper | 942 stars，主要改善 word-level timestamps，跟 WhisperX 重疊 |
| ESPnet OWSM | 學術專案、生態成熟度低 |
| Cohere Transcribe | proprietary cloud、5.42 WER 看起來好但無 self-host、跟「local GPU」需求衝突 |
| MCP servers | 都是 OpenAI Whisper API 雲端 wrapper（cloud cost + 不是 local GPU 路徑），不是我們要的抽象層 |

## 4. Top 2 的試水順序

**修修決定**：

| Strategy | 先試 | 後備 | 預期 ship 速度 |
|---|---|---|---|
| **保守** | WhisperX | Qwen3-ASR | ~3 工作天 ship；Qwen 留觀察 |
| **進取** | Qwen3-ASR | WhisperX | ~5 工作天（含 Windows debug + diarization 自組）|
| **平行** | 同時試 | — | ~5 工作天，多耗 1-2 天工程 |

我建議走 **保守 → ship WhisperX**，理由：

1. **MemoAI 已驗證 Whisper V2 路徑可贏 FunASR**；V3 + 我們的 LLM 校正 = 結構上必贏 MemoAI
2. **Windows GPU 已 explicitly supported**，CUDA 12.8（修修開發機已是這配置）零 unknown
3. **Diarization 內建**（解 Line 1 訪談 speaker 分離設計題，省一個 grill 待決問題）
4. **生態最成熟**（21K stars、近 4 年維護、GUI 衛星專案多）
5. Qwen3-ASR 雖 benchmark 強，但 Windows debug + diarization 自組是兩個 unknown，先別兩個一起吃

如果 ship WhisperX 後仍輸 MemoAI（理論上不該），再換 Qwen3-ASR。如果 WhisperX 贏 MemoAI 但修修對品質仍不滿意（追求極限），可以 Day 4-5 試 Qwen3-ASR shadow run 看 delta 值不值得換。

## 5. 改動範圍預估（WhisperX 路徑）

```
shared/transcriber.py
  - _get_asr_model() — FunASR AutoModel → WhisperX load_model
  - model.generate(input=..., batch_size_s=300, hotword=...)
    → model.transcribe(audio, batch_size=...)
  - WhisperX 回傳 word-level segments，需要寫 segments → SRT 邏輯
    （取代既有 _funasr_to_srt + _funasr_char_to_ts_idx）
  - hotwords 在 WhisperX 走 initial_prompt 路線（不同 API）
  
新增：
  - shared/diarization.py（薄 wrapper 包 pyannote pipeline）
  - hf token 走 .env HUGGINGFACE_TOKEN
  - SRT 加 speaker label 格式（[SPEAKER_00] 文字 / [SPEAKER_01] 文字）
  
不動：
  - LLM 校正 (_correct_with_llm)
  - 多模態仲裁 (multimodal_arbiter)
  - SRT 後處理（簡繁、去標點）
  - QC 報告
  - Auphonic 前處理
  - run_transcribe.py CLI
```

unit tests：
- 退場 `test_funasr_to_srt_*` 系列
- 新增 `test_whisperx_to_srt_*` 系列
- 既有 LLM 校正 / 仲裁 / SRT 後處理測試應該全綠

## 6. 修修需要的 parallel 操作

| 項目 | 必要 | 動作 | 時間 |
|---|---|---|---|
| HuggingFace 帳號 | ✅ | https://huggingface.co/join | 2 min |
| HF Read Token | ✅ | https://huggingface.co/settings/tokens → New token (read) | 1 min |
| pyannote/speaker-diarization-3.1 EULA | ✅ | https://huggingface.co/pyannote/speaker-diarization-3.1 → Accept | 1 min |
| pyannote/segmentation-3.0 EULA | ✅ | https://huggingface.co/pyannote/segmentation-3.0 → Accept | 1 min |
| Token 存 .env | ✅ | `HUGGINGFACE_TOKEN=hf_xxx` 加到 `E:\nakama\.env` **不要貼對話** | — |

## 7. Sources

- [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) — CTranslate2 Whisper backend, 22.5K stars
- [m-bain/whisperX](https://github.com/m-bain/whisperX) — Word timestamps + diarization, 21.6K stars
- [QwenLM/Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR) — 2026-01 release, Mandarin SOTA
- [XiaomiMiMo/MiMo-V2.5-ASR](https://github.com/XiaomiMiMo/MiMo-V2.5-ASR) — 2026 code-switch focused
- [FunAudioLLM/SenseVoice](https://github.com/FunAudioLLM/SenseVoice) — 同 FunASR 隊伍
- [nvidia/parakeet-ctc-0.6b-zh-tw](https://huggingface.co/nvidia/parakeet-ctc-0.6b-zh-tw)（NGC 連結為 NIM 容器）
- [nvidia/parakeet-tdt-0.6b-v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) — 25 European 無中文
- [Northflank 2026 STT benchmarks](https://northflank.com/blog/best-open-source-speech-to-text-stt-model-in-2026-benchmarks)
- [Modal: Choosing between Whisper variants](https://modal.com/blog/choosing-whisper-variants)
- [BrassTranscripts WhisperX 2026 benchmark](https://brasstranscripts.com/blog/whisperx-vs-competitors-accuracy-benchmark)
- [今日內部 benchmark — FunASR vs MemoAI](2026-04-30-funasr-vs-whisper-results.md)
