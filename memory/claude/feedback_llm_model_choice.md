---
name: LLM 模型選擇偏好
description: API 呼叫一律用最強模型（Opus），不省錢選弱模型
type: feedback
created: 2026-04-15
updated: 2026-04-15
confidence: high
---

API 呼叫（如 ASR 校正、內容生成等）一律預設使用當下最強的 Claude 模型（目前為 Opus 4.6），不因成本而降級到 Sonnet/Haiku。

**Why:** 修修評估過成本後認為差距微乎其微（例如 1 小時 Podcast 校正 Opus ~$0.40 vs Haiku ~$0.08），品質差異遠大於價差。「實在是太便宜了」。未來新模型出來（如 4.7）也直接用最強的。

**How to apply:** `shared/anthropic_client.py` 的 `ask_claude()` default model 以及所有呼叫端（transcriber、brook 等）的 model 參數，都應該預設為最新最強的 Opus 模型 ID。
