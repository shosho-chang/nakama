---
name: LLM 成本估算要連 thinking output 一起估
description: 估 reasoning model 成本時不能只看 input，output（含 thinking）往往才是主成本
type: feedback
created: 2026-04-17
confidence: high
---

估 LLM 成本時必須分開算 input 與 output，且對 reasoning model（Claude thinking、Gemini 2.5 Pro dynamic thinking、o1 系列）**output token 才是主成本**，不是 input。

**Why:** 2026-04-17 Transcriber 多模態仲裁估算翻車——原估 1 小時 podcast Gemini 仲裁 $0.05–0.20，實測 20 分鐘就花 USD 0.5（推回 1 小時 ~$1.5，高 10x）。根因是只算 audio input token（$1.25/M × 320 tok ≈ $0.0004/clip），完全漏算 dynamic thinking 會吃滿 `max_output_tokens=8192`，而 Gemini 2.5 Pro output 是 $10/M（input 8 倍），每 clip thinking+JSON output ~3-5K tok ≈ $0.03-0.05 才是主成本。

**How to apply:**
1. **估成本前先查該模型的 input / output 分開定價**（Claude Opus 4.7 input $15 / output $75；Gemini 2.5 Pro input $1.25 / output $10；Haiku 4.5 便宜但差距類似）
2. **Reasoning model 一定要估 output，且假設會吃到 `max_output_tokens` 上限**（thinking 會擴張，不是只吃 answer 長度）
3. **若能關 thinking 或設 `thinking_budget`，先做這個評估**（Gemini 2.5 Pro 支援、Claude thinking 也支援）
4. **寫給使用者的成本估算要標註「含/不含 thinking」**，不要給一個模糊數字
5. **有實測數字後回頭修記憶**，不要留原估錯誤值誤導下次決策
