---
name: 本地多模態音訊模型候選
description: 可取代 Gemini 2.5 Pro 做 Transcriber 音訊仲裁層的本地開源選項；開源轉手後成本敏感度升高
type: project
created: 2026-04-17
updated: 2026-04-18
confidence: medium
originSessionId: c2ace3b3-f24c-4428-9d8b-ddd315f7d92e
---
## 背景

Transcriber 仲裁層目前走 Gemini 2.5 Pro，**實測 ~USD 1.5/hr 音訊**（2026-04-17，20min=$0.5；見 `feedback_llm_cost_estimation.md`，原估 $0.05–0.20/hr 因漏算 thinking output 低 10x）。

**Why:** 修修計畫把 transcriber 獨立出來開源給其他使用者，**降低每小時仲裁成本是產品級需求**（不只是內部優化）。本地跑 + 小模型方向同時推進。

**How to apply:** 要切換本地前先做 shadow test——同一批 low-confidence 片段同時送 Gemini + 本地，比準確率與延遲；本地 ≥ 雲端 85% 且單片 < 10s 再切，否則留雲端。另一條低風險路線：**先限 Gemini `thinking_budget`** 或 **降級 gemini-2.5-flash**（見 `project_transcriber.md` 降本三選項）。

## Top 候選（16GB VRAM 能跑）

| 模型 | VRAM (Int4) | 中文 benchmark | audio+text prompt | JSON | 備註 |
|---|---|---|---|---|---|
| **Qwen2.5-Omni-7B GPTQ-Int4** | 12-14GB | WenetSpeech 5.9 WER / CV-zh 5.2 WER | ✅ 官方 API | prompt-based | **首推本地方案**；MMAU 65.6% 證明音訊語意推理不只 ASR |
| **Gemma 4 E4B** | ~3GB (Q4) / ~8GB (BF16) | 多語 ASR 訓練（具體中文 WER 未公開） | ✅ | ✅ 原生 structured output | **最輕量**；audio 限 30s/query（transcriber clip 通常 <5s ✅）；與 Robin 本機跑的 26B 同架構，部署友善 |
| **MiniCPM-o 2.6 Int4** | ~9GB | 贏 GPT-4o-realtime | ✅ any-to-any | 無原生 | 硬體最寬裕；繁體 benchmark 不透明；Apache-2.0 商用需申請 |
| **Kimi-Audio-7B** | ~14-16GB（吃緊）| AISHELL-1 0.60 WER SOTA | ✅ AQA 訓練 | 無 | 中文 ASR 最強；無官方量化，FP16 吃滿 |
| **Phi-4-multimodal** | ~12GB | 無中文 benchmark | ✅ | ✅ 原生 | 英文 OpenASR 第一；中文未公開數字 |

## 跑不動（超出 16GB）
- Qwen3-Omni-30B-A3B（BF16 78GB+，無官方量化）

## 什麼情境留 Gemini 2.5 Pro
- **量少**（<30 hr/月）：$1.5/hr × 30 ≈ $45/月，可能還低於本地電費 + 維護（修正後）
- **繁體台灣口音**：開源模型 benchmark 都是簡體普通話
- **JSON schema 嚴格度**：Gemini 原生 `response_schema` 比 prompt-based 穩
- **batch 吞吐**：transformers 推論比雲端 API 慢 3-5 倍

## 調研依據

- [Qwen2.5-Omni GitHub](https://github.com/QwenLM/Qwen2.5-Omni)
- [Qwen2.5-Omni-7B-GPTQ-Int4 HF](https://huggingface.co/Qwen/Qwen2.5-Omni-7B-GPTQ-Int4)
- [Kimi-Audio tech report arXiv 2504.18425](https://arxiv.org/abs/2504.18425)
- [MiniCPM-o 2.6 int4 HF](https://huggingface.co/openbmb/MiniCPM-o-2_6-int4)
- [Phi-4-Mini tech report arXiv 2503.01743](https://arxiv.org/html/2503.01743v2)
