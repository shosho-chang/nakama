---
name: 訂閱時代 model 政策：全面最新最強 Claude、新 model 直接採用
description: 修修 2026-08-19 裁決：agent 內容任務一律最新最強 Claude（現為 Opus 5），Anthropic 出新 model 直接採用不過 eval
type: feedback
---

修修裁決（2026-08-19，全系統訂閱-first 完成後）：

1. **Agent 內容任務一律用最新最強 Claude model**（裁決當下 = `claude-opus-5`）。
   訂閱額度下沒有 per-token 成本差，模型選擇只剩品質與延遲考量。
2. **Anthropic 出新 model 直接採用，不需要經過 eval 驗證**。原話理由：「因為我是
   個人使用，所以有什麼差異，我修改起來很快。」
3. **過期 model 要從 Bridge 下拉清掉**——`KNOWN_MODELS` 是 curation 責任，新 model
   上市時同步下架被取代的（含 dated pin）。

**Why：** 2026-08-17 額度事故後全系統改吃訂閱（ADR-026 Amendment 2026-08-19），
「省錢用 Sonnet」的邏輯（[[feedback_cost_management]]）對 **agent 呼叫**不再適用。
注意 [[feedback_cost_management]] 只是被**部分**取代：它談的 Claude Code 互動開發
成本（Opus 1M tier、/clear 紀律）仍然有效——那是 API 計費的開發工作流，不是 agent。

**How to apply：**
- 新 Anthropic model 上市 → 更新 `shared/llm_router.py` 三處：`DEFAULT_MODELS`、
  `MODEL_REGISTRY`、`KNOWN_MODELS`（下架舊的），加 `shared/openrouter_models.py`
  slug（先對 OpenRouter /models API 驗證存在），改測試裡的預設斷言 → 部署
- **換 model 前必跑訂閱 CLI 探針**（一次 `--print` 呼叫確認該 model id 在訂閱
  tier 可用）——2026-08-19 實測 opus-5/sonnet-5/fable-5 全過的方法
- **兩個揭露過的例外維持 Haiku**：`DEFAULT_MODELS["tool_use"]`（gateway 路由分類
  器，每則 Slack 訊息都跑，延遲 > 能力）與 `shared/memory_extractor.py` 的
  hardcode（背景記憶抽取同理）。修修若要一致化，設 `MODEL_<AGENT>_TOOL_USE` 即可
- Bridge override store（`data/model_overrides.json`）優先序最高——檢查 model
  來源時先看它（2026-08-19 事案：Robin 全釘 Sonnet 的元兇就是省成本時期的舊 override）

相關：[[reference_agent_sdk_supports_oauth]]、[[feedback_no_gemini_default_openai]]
