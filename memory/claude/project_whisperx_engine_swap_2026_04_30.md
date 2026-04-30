---
name: WhisperX 引擎 swap 完成 2026-04-30
description: FunASR Paraformer-zh 退場、WhisperX V3 + pyannote diarization 接手；裸 ASR 在 76min 真實訪談已贏 FunASR 全部維度 + 平手 MemoAI（Whisper V2）
type: project
created: 2026-04-30
confidence: high
---

[ADR-013](../../docs/decisions/ADR-013-transcribe-engine-reconsideration.md) 落地。今日（2026-04-30）一日內完成 ADR + 實作 + 驗收。

## 結論先行

| 維度 | FunASR (前) | WhisperX-V3 (後) |
|---|---|---|
| `數位遊牧` | ❌ `蘇味遊牧` | ✅ |
| `心酸` | ❌ `辛酸` | ✅ |
| `跟Paul` | ❌ `跟友` | ✅ |
| `Traveling Village` | ❌ `potravelling village` | ✅ |
| `Hell yes` | ❌ 拼音化 | ✅ |
| 重複字 `本本尊` `花蓮花蓮` | ❌ 有 | ✅ 無 |
| 簡繁混 `着` `羣` | ❌ 有 | ✅ 無（s2twp）|
| Wall clock | 1.3 min | **1.0 min**（76× RT）|

## 程式改動

`shared/transcriber.py`：
- `_get_asr_model` FunASR loader → WhisperX loader（含 `initial_prompt` 走 `asr_options`）
- 新 helpers：`_get_align_model`、`_get_diarize_pipeline`、`_build_initial_prompt`、`_whisperx_to_srt`（含 ≤20 字 sub-cue chunking + 線性插值 timestamp）
- 退場：`_funasr_to_srt`、`_funasr_char_to_ts_idx`、`_get_ts_values`、`_FUNASR_NO_TS_CHAR`
- `_get_cc()` `s2t` → `s2twp`（台灣慣用詞彙轉換）
- `_ZH_MID_PUNCTUATION` / `_ZH_END_PUNCTUATION` 加 ASCII `,;:` `.!?`（code-switch 文字內 ASCII 標點視為斷點）

`scripts/run_transcribe.py`：加 `--no-diarization` flag。

`requirements.txt` + `pyproject.toml`：deprecated `funasr`、新增 `whisperx>=3.8` + `pyannote.audio>=4.0` 為 transcription extras。

`tests/test_transcriber.py`：68 tests 全綠（11 個 FunASR-specific test 退場、5 個 WhisperX-specific test + 3 個 `_build_initial_prompt` test 新增、4 個 transcribe() integration test 改 mock WhisperX）。

## 安裝踩坑教訓

1. **whisperx pip 會降 torch 到 CPU 版本**：`pip install whisperx` 把 torch 2.11.0+cu128 換成 2.8.0+cpu，CUDA 掉。修：再跑一次 `pip install torch torchaudio torchvision --index-url https://download.pytorch.org/whl/cu128 --upgrade`。教訓進 [feedback_dependency_check.md](feedback_dependency_check.md)
2. **WhisperX `transcribe()` 不接 `initial_prompt`**：要走 `load_model(asr_options={"initial_prompt": ...})`。Singleton key 包 prompt，prompt 變會 reload model
3. **WhisperX `DiarizationPipeline` 在 `whisperx.diarize` 子模組**：不是 `whisperx.DiarizationPipeline`（top-level 沒 export）
4. **WhisperX 預設輸出 segments 是 sentence-level，可能 100+ 字**：要在 `_whisperx_to_srt` 內用 `_split_sentences` + `_force_break` 拆 ≤20 字 sub-cue，timestamps 走線性插值

## 待做

- 修修跑 [setup-huggingface-pyannote.md](../../docs/runbooks/setup-huggingface-pyannote.md) 拿 HF token + accept 兩個 pyannote EULA → 開 diarization 試水
- 完整 pipeline 跑一輪（Auphonic + WhisperX + LLM 校正 + Gemini 仲裁）驗天花板
- benchmark 結果 [docs/research/2026-04-30-whisperx-vs-memoai-results.md](../../docs/research/2026-04-30-whisperx-vs-memoai-results.md)
