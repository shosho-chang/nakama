---
name: Transcribe 第二輪測試結論 2026-04-30
description: PR #274 prompt-leak fix 驗證通過 + Qwen3-ASR-1.7B (D2 路徑) 試水失敗；WhisperX 維持主路徑，FunASR/Qwen3-ASR 大廠 Mandarin SOTA benchmark 對台灣 podcast 工況不適用同款教訓
type: project
created: 2026-04-30
confidence: high
---

## 結論先行

**WhisperX (Whisper Large V3) 維持主路徑，Qwen3-ASR-1.7B 不適合台灣 podcast 工況**。

詳細報告：[docs/research/2026-04-30-transcribe-round2-results.md](../../docs/research/2026-04-30-transcribe-round2-results.md)

## Phase A：PR #274 fix 驗證

| 指標 | v1 | v2 | 結論 |
|---|---|---|---|
| 「主持人 張修修」幻覺 | 10 處 | **0 處** | ✅ 主要目標達成 |
| 詞邊界切到 cue（jieba） | 21 處 | 16 處 | ⚠️ 部分修，76% 還在 |
| 「花蓮花蓮」重複（within-segment） | 0 | 2 處 | ⚠️ 新回歸（`condition_on_previous_text=False` 副作用） |
| Cue 結構 | 2055 cue / avg 11.5 字 | 1326 cue / avg 18.0 字 | ⚠️ pack 太滿 |
| Wall clock | 1.0 min | 1.4 min | 76× RT |

**Ship 條件達成但有 follow-up**：詞邊界 16 處 + 花蓮花蓮重複 + cue 過長 → 下一輪再優化。

## Phase B：Qwen3-ASR-1.7B 試水失敗

`pip install qwen-asr`（PyPI 0.0.6，aliyun 官方）安裝順利 — transformers 4.57.6 對齊既有 env，VRAM ~10GB 不衝。

但 76 min 訪談轉出來：
- **「數位遊牧」→「諸位遊牧」**（podcast 主題詞）
- **「Traveling Village」→「超柏林部落」**（podcast 核心社群名）
- **「Hell Yes」→「Hello」**（開頭錯）
- **「心酸 / 辛酸」、「本尊 / 本本尊」、「花蓮花蓮」** 一連串簡體 + 重複 + 同音字錯
- **全篇簡體**（無 s2twp）
- **9.8 min wall-clock vs WhisperX 1.4 min**（慢 7×）
- 23323 字級時間戳要後處理合併

**關鍵教訓對位 ADR-013**：

> AIShell1/WenetSpeech = 朗讀 PRC 普通話 + 零 code-switch + 零台灣腔。**完全不對應**台灣 podcast 訪談工況。

**Qwen3-ASR 重蹈 FunASR 覆轍** — 阿里大廠訓練資料偏 PRC 普通話 + 大陸用詞，台灣腔 + 在地名詞系統性失誤。Mandarin WER 4.97 paper benchmark 跟業務工況解耦。

## 規則化教訓

**新 ASR 引擎評估必須**：
1. 不採信 paper benchmark（AIShell / WenetSpeech / Common Voice）做選型
2. 必跑**真實業務工況音檔**（台灣 podcast、訪談、code-switch、在地名詞）
3. 對位 ADR-013 已建立的「諸位遊牧 / Traveling Village / Hell Yes / 本尊 / 花蓮 / 心酸 / Paul」7 點 acceptance set
4. 大陸大廠（阿里 / 字節 / 騰訊 / 小米）的中文模型對台灣腔系統性偏弱，需特別警惕

## How to apply

- 下次有人推薦「中文 SOTA ASR」或丟新模型來，先用 7 點 acceptance set 跑 1 hour 真實 podcast
- 新模型自稱 code-switch 也別信，跑「Traveling Village」這種混合語名詞測試
- 不要被 1.7B 小模型吸引（Qwen3-ASR 1.7B vs Whisper V3 1.5B 同量級），台灣腔表現跟模型大小無關，跟訓練資料分布有關

## 下一步（修修待做）

1. **WhisperX 收尾**：詞邊界 16 處 / 花蓮花蓮回歸 / cue 結構過長 三件 follow-up
2. **天花板驗證**：跑 `python scripts/run_transcribe.py tests/files/20260415.wav --no-auphonic` 含 LLM 校正 + Gemini 仲裁，看完整 pipeline 天花板
3. **Qwen2.5-Omni-7B**（**仲裁層替代 Gemini 用，跟本次測試的 Qwen3-ASR 不同**）— 仍是 [project_local_multimodal_audio_models.md](project_local_multimodal_audio_models.md) backlog
