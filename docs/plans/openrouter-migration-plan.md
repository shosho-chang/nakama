# P9 規劃 — OpenRouter 作為 Nakama 的統一 LLM Transport

> 交付給 Claude Code 執行的規劃文件。**這是 plan，不是 code**——執行的 Claude 讀完先回 task-by-task 的 P7 方案再動手。
> 模式：P9（跨 5+ 檔、多 slice）。三條紅線（閉環 / 事實驅動 / 窮盡一切）全程適用。

---

## 0. 為什麼這個 plan 跟你想的不一樣（先讀這段）

你原本的想像是「把所有呼叫搬到 OpenRouter 統一」。但你的 codebase **已經有統一的 facade 了**——`shared/llm.py` 的 `ask()` / `ask_multi()` 已經是所有 agent 的單一入口（~64 個 call site 都走它）。所以這次要做的**不是重寫路由**，而是換掉底層 transport：

- **現狀**：facade 依 model prefix 把 api-tier 呼叫分派到各家原生 SDK（`anthropic_client` / `xai_client` / `gemini_client`），每家各自一把 key。OpenAI 根本**還沒接**（`shared/llm.py:94-97` 對未接 provider 直接 `raise NotImplementedError`，`shared/llm_router.py:14` 註明「OpenAI 待擴」）。
- **目標**：在 facade 底下插一個 OpenRouter transport seam。api-tier 呼叫改走單一 `shared/openrouter_client.py`（OpenAI-compatible，BYOK），**~64 個 call site 一行都不用改**。這才是「單一整合點」的真正落地。

**兩個現有架構約束，這個 plan 必須尊重（否則就是 regression）：**

1. **成本計費準確度** — 你們當初（`memory/claude/project_multi_model_architecture.md` Q1）**刻意不用 LiteLLM 跑 production**，理由是 LiteLLM 的 Anthropic cache 計費 bug 會污染 Bridge cost panel。→ 本 plan 對 OpenRouter 呼叫**直接記錄 OpenRouter 回傳的實際 cost**（usage accounting），不靠 `shared/pricing.py` 的 prefix 估算。這其實比現狀更準。
2. **Claude Max Plan 訂閱路徑（ADR-026）** — `shared/anthropic_client.py:80` `_plan_dispatch` 會在 auth policy 為 subscription 且有 OAuth token + `claude` CLI 在 PATH 時走 `shared/claude_cli_client.py`（`claude -p` subprocess，吃 Max Plan quota）。**OpenRouter 無法使用 Max 訂閱**。→ 訂閱路徑必須原封不動保留；只有 api-tier 呼叫進 OpenRouter。
   - 註：`DEFAULT_AUTH` 預設是 **`api`**（`shared/llm_router.py:180`），subscription 是 **opt-in**（`NAKAMA_REQUIRE_MAX_PLAN=1` 或 `AUTH_<AGENT>[_<TASK>]=subscription_*`），程式碼註解標明主要用於 **textbook ingest 批次 / sandcastle**。所以這個 carve-out 範圍其實很窄——絕大多數呼叫本來就是 api-tier，會進 OpenRouter。

---

## 1. 現狀盤點（事實驅動 — 帶 file:line）

| 元件 | 檔案 | 角色 |
|---|---|---|
| Router | `shared/llm_router.py:205` `get_model(agent,task)` | (agent,task)→bare model ID；解析序 override→`MODEL_<A>_<T>`→`MODEL_<A>`→registry→`DEFAULT_MODELS` |
| Provider 推斷 | `shared/llm_router.py:299` `get_provider()` + `:193` `_PROVIDER_PREFIXES` | prefix→provider；**`gpt-`/`o1-`/`o3-` 已在表內** |
| Auth policy | `shared/llm_router.py:249` `get_auth_policy()`（ADR-026） | api / subscription_preferred / subscription_required |
| Registry | `shared/llm_router.py:55` `MODEL_REGISTRY` + `:74` `KNOWN_MODELS` | 具名 (agent,task) 站點，餵 Bridge 面板 |
| Facade | `shared/llm.py:32` `ask()` / `:100` `ask_multi()` / `:160` `ask_with_tools()` / `:204` `ask_with_audio()` | 跨 provider dispatch；未接 provider `raise`（`:94`） |
| OpenAI-compat 範本 | `shared/xai_client.py:43` `get_client()` / `:63` `ask_grok()` / `:171` `_record_xai_usage()` | **OpenRouter client 直接照抄這支** |
| 訂閱路徑 | `shared/claude_cli_client.py:258` `ask_via_cli()` / `:300` `ask_multi_via_cli()` | Max Plan（auth_actual="subscription"） |
| 觀測/計費 | `shared/llm_observability.py:22` `record_call()` | 寫 `state.api_calls`；失敗不影響主流程 |
| 定價表 | `shared/pricing.py:147` `get_pricing()` / `:166` `calc_cost()` | prefix 估算；**無 OpenAI 條目** |
| Bridge 面板 | `thousand_sunny/routers/` + `shared/llm_router.py:322` `list_model_sites()` | N531 `/bridge/models` 即時改 model（寫 `data/model_overrides.json`） |
| Env 慣例 | `.env.example` | `ANTHROPIC_API_KEY`/`GEMINI_API_KEY`/`XAI_API_KEY`；**無 `OPENAI_API_KEY`**；`MODEL_*` / `AUTH_*` 慣例 |
| Bench/eval | （另路）LiteLLM | 維持不動，本 plan 不碰 |

**好消息**：抽象層已存在，blast radius 集中在 `shared/` 的 4-5 支檔 + Bridge 面板，**call site 不動**。

**邊界**（phase 1 不碰，維持原生）：`ask_with_tools`（tool-use，目前 Anthropic-only，OpenAI-compat 的 tool schema 與回傳 Message 形狀不同）、`ask_with_audio`（Gemini 多模態音訊）。

---

## 2. 核心設計決策（✅ 已定案 — 全採建議預設）

| # | 決策 | 定案（採用） | 當初的替代選項 |
|---|---|---|---|
| D1 | api-tier 的 Anthropic 呼叫要不要也走 OpenRouter？ | **要**（最大化統一；cost 用 OR 實際回報，準確度無損）。訂閱呼叫永遠走原生 CLI 不變。 | 只把 OpenAI/xAI/Gemini 走 OpenRouter，Anthropic 全留原生（風險更低，但 Anthropic-API 不統一） |
| D2 | model ID 格式 | **保留 bare ID**（`claude-sonnet-4-6`），在 OpenRouter 邊界用一張 map 翻成 slug（`anthropic/claude-sonnet-4.6`）。registry/env/面板全不動。 | 全面改成 slug 格式（blast radius 大、且斷掉原生 fallback 退路） |
| D3 | 切換機制 | **`LLM_TRANSPORT=openrouter\|native` 全域旗標 + kill-switch**，預設先 `native`，canary 後再切。 | 直接硬切（無退路，不建議） |
| D4 | 推出方式 | **Canary**：先單一低風險 agent（建議 Sanji 或 Zoro）驗證 cost panel 準確，再逐步擴。 | Big-bang（不符合你們 ADR 文化） |
| D5 | 計費來源 | **記 OpenRouter 回傳實際 cost**（`usage.cost`），`pricing.py` 僅當 fallback。 | 沿用 prefix 估算（就是當初排斥 LiteLLM 的痛點，不建議） |

> **已採用左欄定案。** D1 只影響 auth 解析為 `api` 的 Anthropic 呼叫；解析為 subscription 的（`NAKAMA_REQUIRE_MAX_PLAN=1` 或 `AUTH_<AGENT>=subscription_*`）永遠走原生 `claude -p` CLI，OpenRouter 不介入。若日後想讓 Anthropic 全留原生，Slice 2 的 dispatch 條件改一行即可。

---

## 3. 目標架構（一張圖）

```
agent call site (~64 處，不動)
        │  shared.llm.ask() / ask_multi()
        ▼
shared/llm.py  facade  ── 解析 model / provider / auth_policy
        │
        ├─ provider==anthropic 且 subscription 生效 ──► claude_cli_client（Max Plan，不變）
        │
        ├─ LLM_TRANSPORT==openrouter（api-tier）─────► shared/openrouter_client.py
        │         · OpenAI SDK，base_url=https://openrouter.ai/api/v1
        │         · slug map：bare ID → provider/model
        │         · extra_body：models[] fallback、provider.quantizations 等
        │         · 記錄 OpenRouter 實際 cost → record_call()
        │
        └─ LLM_TRANSPORT==native（kill-switch）──────► 原生 anthropic/xai/gemini client（現狀）

tool_use / audio：phase 1 永遠走原生（邊界）
```

---

## 4. 前置作業（Operator — 修修你本人先做，非 code）

1. **OpenRouter 帳號**：建立 → 產生 `OPENROUTER_API_KEY`。
2. **BYOK 接上既有 credit**（這是「用掉 1 萬 OpenAI + 幾百 Anthropic」的關鍵）：在 OpenRouter 後台 → Integrations / Keys，貼上你的 **OpenAI API key** 與 **Anthropic API key**。錢留在原帳號、被 draw down，OpenRouter 每月前 1M 請求不抽成。
3. **資料政策**：開 `data_collection: deny`（或帳號層級設定），避免 podcast/未發布內容被記錄。需要時對特定 agent 加 ZDR。
4. **小額儲值**：存少量 OpenRouter credit，用於①沒有自帶 key 的 model（Gemini/DeepSeek 等）②超過 1M 請求時的 5% BYOK 費。
5. **關閉 BYOK 自動繞道計費**（避免你的 key 失敗時偷扣 OR credit）：相關 provider 設 `allow_fallbacks=false`，或在 client 端 `extra_body` 控制（Slice 1 會做）。
6. 把 `OPENROUTER_API_KEY` 暫存，Slice 4 寫進 VPS `.env`。

---

## 5. Worktree 設定（執行的 Claude 第一步，遵守工作面紀律）

```powershell
# 主倉庫只做同步，不在 E:\nakama 改檔
cd E:\nakama
git switch main
git fetch --prune
git pull --ff-only
git worktree add E:\nakama-N5xx-openrouter-transport -b feat/openrouter-transport origin/main
cd E:\nakama-N5xx-openrouter-transport
```

- 所有改動只在這個 sibling worktree。**禁止 `git add .`**，只 stage 明確列出的 path。
- 刪檔走 PowerShell 回收桶（見 CLAUDE.md），不用 `rm`。
- Memory / handoff 不寫進這個 feature branch。

---

## 6. 實作 Slices（每個 = 六要素 Task Prompt；依序，後者依賴前者）

### Slice 1 — `openrouter_client.py` + slug map + 實際 cost 記錄

1. **目標**：建立單一 OpenRouter transport client，行為與 cost 記錄對齊既有 observability。
2. **範圍**：新增 `shared/openrouter_client.py`；新增 `shared/openrouter_models.py`（slug map）；`tests/shared/test_openrouter_client.py`。**不**改 facade（Slice 2 才接）。
3. **輸入**：範本 `shared/xai_client.py`（整支結構照抄）；`shared/llm_observability.py:22` `record_call` 簽章；`shared/retry.py` `with_retry`；OpenRouter docs（chat completions、usage accounting、provider routing）。
4. **輸出**：
   - `get_client()` singleton：`OpenAI(base_url="https://openrouter.ai/api/v1", api_key=os.environ["OPENROUTER_API_KEY"])`，帶 attribution headers（`HTTP-Referer` / `X-OpenRouter-Title`）。
   - `ask_openrouter()` / `ask_openrouter_multi()`：簽章對齊 `ask_grok*`；`extra_body` 支援 `models`（fallback 陣列）、`provider`（`quantizations`、`allow_fallbacks`、`sort`）。
   - `to_openrouter_slug(bare_id)`：用 `get_provider()` + 顯式 map 把 bare ID 轉 slug；查無 → `raise`（fail fast，不 silent 404）。
   - cost 記錄：請求帶 usage accounting，從回應取 **OpenRouter 實際 cost**，連同 token 數呼叫 `record_call()`；取不到 cost 時 fallback 到 `pricing.calc_cost()` 並記 debug log。
   - `_require_*` 風格的 guard（對稱 `xai_client.py:155`）。
5. **驗收**：`pytest tests/shared/test_openrouter_client.py` 全綠；測試涵蓋 (a) slug 轉換正確 + 查無即 raise、(b) usage 解析含實際 cost、(c) cost 取不到時走 fallback 且不丟例外、(d) `record_call` 被正確呼叫（mock）。`ruff` 乾淨。**不**真打網路（mock OpenAI client）。
6. **邊界**：不碰 facade、不碰其他 client、不碰 tool-use / audio。

### Slice 2 — Facade transport seam（接線 + kill-switch）

1. **目標**：api-tier 的文字呼叫改走 OpenRouter，訂閱路徑（`claude -p` CLI）與 kill-switch 都原封保留。
2. **範圍**：`shared/anthropic_client.py`（只在 `_plan_dispatch` 算出 `actual=="api"` 的分支插 OpenRouter，**subscription 分支不碰**）、`shared/xai_client.py` / `shared/gemini_client.py`（恆 api，頂部加 transport 判斷）、視集中式需求 `shared/llm.py`；`tests/shared/test_llm_dispatch.py`。
3. **輸入**：`shared/anthropic_client.py:80-128 _plan_dispatch`（subscription/api 決策已存在）、`:262` 與 `:424`（`actual=="subscription"` 分支）；`shared/llm.py:32-157`；`shared/llm_router.py` 的 `get_auth_policy`；Slice 1 的 client。
4. **輸出**：transport 決策（見 §3 圖）：
   - **Anthropic**：`_plan_dispatch` 仍決定 subscription vs api（不動）。當 `actual=="subscription"` → 原生 `claude -p` CLI（完全不碰）；當 `actual=="api"` 且 `LLM_TRANSPORT=openrouter` → `ask_openrouter*`，否則維持原生 SDK。
   - **xAI / Gemini**：恆 api → `LLM_TRANSPORT=openrouter` 時走 `ask_openrouter*`。
   - **kill-switch**：`LLM_TRANSPORT` 未設或 `native` → 行為與改動前逐位元相同。
   - 實作選型：建議在各 client 的 api 分支插 ~3 行 guard（subscription carve-out 零複製、blast radius 最小）；若偏好單點集中，改在 `shared/llm.py`，但需讓 facade 先向 `anthropic_client` 查詢 `_plan_dispatch` 結果，避免重算/邏輯漂移。
5. **驗收**：
   - 新測試：三條路徑各自 dispatch 到正確目標（mock）；`LLM_TRANSPORT` 未設時行為與改動前**逐位元相同**（回歸保護）。
   - **全量回歸**：`pytest tests/`（含既有 `tests/shared`、`tests/agents`、`tests/gateway`）全綠。
   - 三問自審回答清楚（方案/影響/回歸）。
6. **邊界**：不改 call site；不改 router 解析序；`ask_with_tools` / `ask_with_audio` 維持原生不動。

### Slice 3 — Cost panel 準確度（接 OpenRouter 實際 cost）

1. **目標**：Bridge cost panel 在 OpenRouter 呼叫下顯示**真實花費**，不退回 prefix 估算。
2. **範圍**：`shared/llm_observability.py`（如需把 cost 透傳）；`shared/state.py` 的 `record_api_call`（如需新增 `cost_usd` 欄位 + migration）；`shared/pricing.py`（補 OpenAI family 作 fallback）；對應 migration 檔 + 測試。
3. **輸入**：`shared/llm_observability.py:22`；`shared/state.py` `record_api_call` 與 `api_calls` schema；`migrations/`；`shared/pricing.py:55-116`。
4. **輸出**：`record_call` 接受並落庫實際 `cost_usd`（沿用「失敗不影響主流程」語意）；cost panel 優先顯示實際 cost，缺值才 `calc_cost`；`pricing.py` 補 `gpt-`/`o-` family 預設。
5. **驗收**：migration 可前進可回滾並有測試；panel 在實際 cost / fallback 兩種情況都正確；`pytest tests/` 綠。
6. **邊界**：不改既有非 OpenRouter 紀錄的計費；schema 改動同步任何相關 doc。

### Slice 4 — Router / registry / env（解鎖 OpenAI + fallback 鏈）

1. **目標**：把 OpenAI 模型納入可選清單，給關鍵站點配 fallback，補齊 env 與 BYOK 說明。
2. **範圍**：`shared/llm_router.py`（`KNOWN_MODELS`、必要的 registry fallback 欄位、更新 `:14` 「OpenAI 待擴」註解）；`.env.example`；`config.yaml`（若要把 transport/fallback 放設定）。
3. **輸入**：`shared/llm_router.py:28-83`；`.env.example` 的 `MODEL_*`/`AUTH_*` 區塊。
4. **輸出**：`KNOWN_MODELS` 增列 OpenAI（如 `gpt-5`、`gpt-5-mini`）等實際要用的；視 D2 在 `ModelSite` 增 optional `fallbacks: tuple[str,...]` 並讓 facade 傳成 `models[]`；`.env.example` 新增 `OPENROUTER_API_KEY`、`OPENAI_API_KEY`（BYOK 用）、`LLM_TRANSPORT`，並註明 BYOK 設定步驟。
5. **驗收**：`list_model_sites()` 正確列出新站點與 provider；**新增測試：`KNOWN_MODELS` 每個 bare ID 都能 `to_openrouter_slug()` 成功**（避免上線才發現某 slug 不存在）；`pytest`/`ruff` 綠。
6. **邊界**：不改解析優先序語意；env 預設值不可讓系統「靜默花錢」（沿用 ADR-026 精神）。

### Slice 5 — Bridge models 面板（UI）

1. **目標**：面板顯示 transport（openrouter/native）、provider、實際 cost，必要時顯示 fallback 鏈。
2. **範圍**：`thousand_sunny/routers/`（bridge models 路由 + template/static）；對應前端測試。
3. **輸入**：`shared/llm_router.py:322` `list_model_sites()` 回傳結構；**出手前先讀 `docs/design-system.md`**（美學是 first-class）。
4. **輸出**：面板新增欄位/標示；沿用既有 design tokens（CSS custom properties，不硬寫色碼）；states（default/loading/empty/error/hover/focus）都顧到；a11y 不妥協。
5. **驗收**：截圖自審非 AI slop default；對照 `docs/design-system.md`；既有 Playwright/前端測試綠；P7 完工含 Aesthetic direction 段。
6. **邊界**：只動 models 面板，不改其他 Bridge 頁；不引入新字型/紫漸層/均勻 card grid。

### Slice 6 — Canary 推出 + ADR + 文件

1. **目標**：安全切換並留下決策紀錄。
2. **範圍**：`docs/decisions/ADR-030-openrouter-transport.md`（下一個可用編號，現狀最新 ADR-029）；`docs/plans/openrouter-transport.md`（放本 plan）；更新 `CONTENT-PIPELINE.md` / `CONTEXT-MAP.md` / `memory/claude/project_multi_model_architecture.md` 相關段落；canary runbook。
3. **輸入**：全部前序 slice 產出；§2 決策表。
4. **輸出**：
   - ADR-030 記錄 D1-D5 與「為何 OpenRouter 而非當初排斥的 LiteLLM」（cost 用實際回報解掉痛點）。
   - **Canary 前置盤點**：先查 VPS `.env` 是否有 `AUTH_*` / `NAKAMA_REQUIRE_MAX_PLAN`，列出哪些 (agent,task) 目前解析為 subscription（這些會留在 CLI、不被 OpenRouter 接管）。本機 `E:\nakama\.env` 已確認**無**這些設定（全 api-tier）。
   - Canary 程序：先對單一 agent 設 `MODEL_<AGENT>` + `LLM_TRANSPORT=openrouter`（建議 Sanji，社群口吻、低風險），跑 1-3 天，比對 cost panel 與 OpenRouter 後台帳單一致 → 再逐 agent 擴。
   - 回滾程序：`LLM_TRANSPORT=native` 即全退。
5. **驗收**：canary agent 實跑成功、cost panel 數字與 OpenRouter 後台對得起來（±誤差說明）；ADR/doc 同步；`memory` 更新走 paths-ignore、不混進本 feature PR。
6. **邊界**：不在這個 PR 動 bench/eval 的 LiteLLM 路；不一次切全部 agent。

---

## 7. 全域邊界（任何 slice 都不可碰）

- **Bench/eval 的 LiteLLM 路**（`project_multi_model_architecture.md` Q1）維持。
- **Max Plan 訂閱路徑**（`claude_cli_client.py`）語意不變。
- **call site**（~64 處 `shared.llm.ask*`）不改。
- **router 解析優先序**語意不變（只擴 KNOWN_MODELS / optional fallback 欄位）。
- **tool-use / audio** 維持原生。
- **Vault 寫入規則** 與 **memory 寫入紀律**（不在 feature branch commit memory）照 CLAUDE.md。

## 8. 整體 Definition of Done

1. `LLM_TRANSPORT=openrouter` 時，api-tier 文字呼叫經 OpenRouter；訂閱呼叫仍走 Max Plan；tool-use/audio 仍原生。
2. `LLM_TRANSPORT=native`（或未設）行為與改動前完全一致（kill-switch 驗證過）。
3. Cost panel 顯示 OpenRouter 實際花費，與後台帳單對得起來。
4. OpenAI 模型可用（1 萬 credit 經 BYOK 被消耗）。
5. `pytest tests/` 全綠、`ruff` 乾淨、`KNOWN_MODELS` slug 全部可解析。
6. canary agent 實跑通過；ADR-030 + 文件同步；PR 不含 memory/unrelated 檔。

## 9. 風險與緩解（來自社群研究）

| 風險 | 緩解 |
|---|---|
| OpenRouter 預設路由到重度量化 provider，品質掉/CJK 亂碼 | 品質敏感 agent（Brook 長文、Robin 機制草稿）在 `extra_body.provider.quantizations=["fp8","fp16"]` 或 pin provider；列入 Slice 1 client 能力 |
| OpenRouter 無 SLA、gateway 單點 | 保留 `LLM_TRANSPORT=native` kill-switch；client 端 `with_retry` 已有；關鍵站點配 `models[]` fallback |
| BYOK 你的 key 失敗時偷扣 OR credit | `allow_fallbacks=false`（前置作業 5 + Slice 1） |
| fallback 只在 error 觸發，不擋「200 但內容是垃圾」 | 維持既有應用端輸出驗證；不依賴 gateway 保證內容 |
| 某些 model slug 在 OpenRouter 不存在 | Slice 4 驗收強制 `KNOWN_MODELS` 全部可解析；canary 先驗 |
| Anthropic-API 經 OpenRouter 後 cache 計費差異 | 用 OpenRouter 實際回報 cost（Slice 1/3），不自行估算 |

---

### 附：給執行 Claude 的起手指令（可直接貼）

> 我要把 Nakama 的 api-tier LLM 呼叫統一改走 OpenRouter（BYOK 消化既有 OpenAI/Anthropic credit），保留 Claude Max Plan 訂閱路徑與 Bridge cost panel 準確度。完整規劃見附檔 `docs/plans/openrouter-transport.md`（§2 的 D1–D5 已定案，照做即可）。
>
> 請先讀：`E:\nakama` 的 `CLAUDE.md`、`shared/llm.py`、`shared/llm_router.py`、`shared/anthropic_client.py`、`shared/claude_cli_client.py`、`shared/xai_client.py`、`shared/llm_observability.py`、`shared/pricing.py`、`memory/claude/project_multi_model_architecture.md`、`docs/decisions/ADR-026-llm-router-auth-dimension.md`。
>
> 然後依 plan 第 5 節開 sibling worktree `E:\nakama-N5xx-openrouter-transport`，**逐 slice（1→6）** 進行：每個 slice 先回六要素對應的 P7 方案 + 影響分析給我確認，再實作，完成用 `[P7-COMPLETION]` 格式交付。遵守三條紅線、worktree 紀律、禁止 `git add .`、memory 不進 feature branch。Slice 5 動 Bridge UI 前先讀 `docs/design-system.md`。
