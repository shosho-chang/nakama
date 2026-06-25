# ADR-049：OpenRouter 作為 api-tier LLM 的統一 transport

- **狀態**：Accepted（2026-06-25）
- **相關**：ADR-026（LLM router auth dimension / Max Plan 訂閱路徑）、`memory/claude/project_multi_model_architecture.md`（Q1 Hybrid：production 直 SDK / bench LiteLLM）
- **範圍**：補充 ADR-026 的 *transport* 維度；**不**取代其 auth 維度
- **PR**：#936–#940 + 本 PR（Slice 1–6）

## Context

Nakama 的 LLM 呼叫早已收斂在 `shared/llm.py` facade（`ask()` / `ask_multi()`，~64 個 call site）。底層按 model prefix 分派到各家原生 SDK（`anthropic_client` / `gemini_client` / `xai_client`），每家一把 key；OpenAI 一直「待擴」未接。

修修在 OpenAI 有約 1 萬美元、Anthropic 有數百美元既有 credit，想：(1) 用掉這些 credit 而非各家重儲；(2) 把「換 model / 接新 provider」收斂到單一整合點，降低 per-model 淘汰的維護成本。

當初（`project_multi_model_architecture.md` Q1）**刻意不用 LiteLLM 跑 production**，因為 LiteLLM 的 Anthropic cache cost 計費 bug（#9812、#17201）會污染 Bridge cost panel 準確度。

## Decision

在 facade 底下插一個 **OpenRouter transport seam**（OpenAI-compatible endpoint，BYOK）。api-tier 的文字呼叫改走單一 `shared/openrouter_client.py`，~64 個 call site 一行不改。定案 D1–D5：

- **D1**：api-tier 的 Anthropic 呼叫也走 OpenRouter（最大化統一）；訂閱呼叫永遠走原生 CLI。
- **D2**：保留 bare model ID（`claude-sonnet-4-6`），在 OpenRouter 邊界用**顯式 map** 翻成 slug（`anthropic/claude-sonnet-4.6`）；registry / env / 面板不動。查無即 fail-fast。
- **D3**：`LLM_TRANSPORT=openrouter|native` 全域 kill-switch + `LLM_TRANSPORT_<AGENT>` per-agent override，預設 `native`。
- **D4**：Canary 漸進（per-agent 先試一個，cost panel 對得上再擴），非 big-bang。
- **D5**：cost 記 OpenRouter 回報的**實際** cost（usage accounting）落 `api_calls.cost_usd`，`pricing.calc_cost` 僅當 fallback。

### 為何 OpenRouter 而非當初排斥的 LiteLLM
痛點是「cache 計費估算不準污染 cost panel」。OpenRouter 在 `usage.include=true` 時回報**實際** cost，直接落庫，不再自行估算 → 痛點消失，比現狀更準。

### 兩個 carve-out（永遠原生，不進 OpenRouter）
1. **Anthropic Max Plan 訂閱**（ADR-026）：`claude -p` subprocess 吃訂閱 quota，OpenRouter 無法使用訂閱。只有 auth 解析為 `api` 的 Anthropic 呼叫進 OpenRouter。
2. **xAI `grok-*`**：2026-06-25 對 OpenRouter `/models`（339 slug）preflight 確認 OpenRouter **不載** `grok-4-fast` tier（只有 `grok-4.20`/`grok-4.3` full tier）。強行轉會 silent 換 model + 變貴 → slug 層 fail-fast、xAI 留原生。

> Sanji（唯一用 grok-4-fast 的 agent）改投便宜的 OpenRouter-carried model（`gemini-2.5-flash` / `gpt-5-mini` A/B）以達成全系統統一；屆時 xAI carve-out 形同無 grok 呼叫。

## Consequences

**正面**
- 單一整合點：換 model / fallback 收斂在 OpenRouter，per-model 淘汰維護成本降低（對 OpenRouter 有上架的 provider）。
- cost panel 顯示真實花費，與後台帳單對得起來。
- OpenAI 解鎖（facade `openai` 分支 → OpenRouter，因 OpenAI 無原生 client），1 萬 credit 經 BYOK 被消化。
- kill-switch + per-agent override → 安全漸進 canary，隨時全退。

**代價 / 風險**
- 多一層 gateway（OpenRouter 無 SLA、單點）。緩解：`LLM_TRANSPORT=native` kill-switch + client `with_retry` + 關鍵站點可配 `models[]` fallback。
- 預設可能路由到重度量化 provider（品質 / CJK 風險）。緩解：`extra_body.provider.quantizations` / pin provider（client 已支援）。
- BYOK 自帶 key 失敗時偷扣 OR credit。緩解：`provider.allow_fallbacks=false` 預設。
- xAI 無法統一（OpenRouter 無便宜 Grok tier）→ 留原生或換 model。

**邊界（維持原狀）**
- Bench/eval 的 LiteLLM 路（`project_multi_model_architecture.md` Q1）。
- tool-use（`call_claude_with_tools`，Anthropic-only）與 audio（Gemini 多模態）維持原生。
- router 解析優先序語意。
