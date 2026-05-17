# ADR-026: LLM Router 加 Auth 維度（api / subscription_preferred / subscription_required）

**Status:** Accepted (v3 post-implementation, one deviation documented in §Implementation deviation)
**Date:** 2026-05-16 (drafted) / 2026-05-17 (accepted)
**Implementation:** #580 (Slice 1 — pure-additive infra) + #581 (Slice 2 — per-call dispatch + translator de-hardcoding)
**Deciders:** shosho-chang, Claude Opus 4.7, Codex GPT-5, Gemini 2.5 Pro
**Related:** ADR-001, `memory/claude/project_multi_model_architecture.md`, `memory/claude/project_llm_facade_phase1.md`, `memory/claude/feedback_llm_model_choice.md`, `memory/claude/feedback_cost_management.md`, `memory/claude/feedback_oauth_env_pinning_long_batch.md`, `docs/plans/2026-05-16-llm-router-auth-dimension-grill-prep.md`
**Panel audits:** `docs/research/2026-05-16-codex-adr026-audit.md`, `docs/research/2026-05-16-gemini-adr026-audit.md`

---

## Context

2026-04-20 起的 Q1 hybrid routing 方案已落地（PR #50-#55、#208-#224），`shared/llm_router.py` 解析 `(agent, task) → model_id`、`shared/llm.py` facade 跨 provider dispatch、`shared/llm_observability.py` 統一 cost tracking。Anthropic / xAI / Google 三家 wrapper 在 production 跑了一個月。

但 router 目前只覆蓋 **model 維度**，沒覆蓋 **auth 維度**。Anthropic 呼叫究竟走「API key + bare SDK」（API 計費）還是「OAuth + `claude` CLI subprocess」（Max Plan 訂閱 quota）是 process-wide 的 env flag `NAKAMA_REQUIRE_MAX_PLAN`，無法做 per-(agent, task) 切換。

### 關鍵約束

- **Bare SDK + OAuth = 觀察到 1 RPS 即 429**（Nakama 內部觀察行為，非 Anthropic 官方文件斷言）：Stage 4.0 textbook ingest 跑 sandcastle 時撞到 anti-automation 429，handoff 紀錄在 `memory/claude/project_session_2026_05_07_pm_stage4_batch_handoff.md`，並驅動 `docs/plans/2026-05-07-textbook-ingest-v3-path-b-rewrite.md` 加 dry-run gate 檢查 `429`/`auth`/`rate limit` 字串。Anthropic 官方文件只列 RPM/ITPM/OTPM 與一般 rate limit 行為，並未承諾 OAuth + bare SDK 不被特別限制。所以「subscription 必走 CLI」是 Nakama operational 結論，不是 vendor SLA。
- **修修長期維持 Max Plan 訂閱**。`feedback_llm_model_choice.md` 寫「一律用最強 Claude 模型」；但 `feedback_cost_management.md` 修正：daily work 用 Sonnet 4.6、Opus 1M 只用於 P9/P10 / 複雜 debug，因為 Opus 1M 曾跑到 `$200-400/day`。**整合原則：quality-first by workload，subscription path 是其執行載體，不是「Opus 灑滿」的同義詞**。
- 非 Anthropic provider（Grok / Gemini / OpenAI）目前沒有對應的 CLI subscription 路徑。Gemini Advanced / Grok Heavy 各自的訂閱整合 story 不同於 Anthropic 的 CLI subprocess 模式（panel Gemini Section 4 pressure-tested 過）— Phase 1 不嘗試統一介面，Phase 2 視真實需求重設 vocabulary（見 Remaining work）。

### 具體場景驅動

- `shared/translator.py:24` 寫死 `_DEFAULT_MODEL = "claude-sonnet-4-6"`，翻譯 BMJ 326 段文章 ~3min × Sonnet，量大；EPUB 整本書翻譯（grill prep `docs/plans/2026-05-05-epub-book-translation-grill-prep.md`）會放大這個量
- `scripts/run_s8_batch.py:1004` 與 `scripts/run_s8_preflight.py:208` 目前各自設 `NAKAMA_REQUIRE_MAX_PLAN=1` 來強制走訂閱（textbook ingest 不可碰 API budget）— migration 必須覆蓋這些 caller
- 同 process 內 Brook Opus 長文不應被 Robin 高頻翻譯把訂閱配額吃光（quota starvation 風險 Phase 2 才解，Phase 1 透過 `subscription_required` 部分緩解）

## Decision

LLM router 加 **Auth 維度**作為第三個解析欄位（在 model 之外、provider 之上）。

### Canonical vocabulary

| Term | Meaning |
|------|---------|
| **Auth policy** | 一次 LLM call 的計費路徑語意，**三元值**：`api` / `subscription_preferred` / `subscription_required` |
| **`api`** | 走 API key 計費路徑（bare SDK + `x-api-key`）。所有 provider 通用 |
| **`subscription_preferred`** | 偏好走訂閱（Anthropic = `claude` CLI subprocess via Max Plan）；條件不滿足時**軟降 `api`** + 寫 `fallback_reason` |
| **`subscription_required`** | 必須走訂閱；條件不滿足時**raise**。給 textbook ingest / 不可碰 API budget 的 caller 用 |
| **Hard-lock override** | 既有 `NAKAMA_REQUIRE_MAX_PLAN=1` env，process-wide 最高優先序，**映射為 `subscription_required`**（不是獨立語意層） |
| **Fallback reason** | 軟降 / raise 時記錄的具體成因 enum：`NO_OAUTH_TOKEN` / `PROVIDER_NOT_SUPPORTED` / `CLI_BINARY_NOT_FOUND` / `CLI_SUBPROCESS_ERROR` / `CLI_AUTH_EXPIRED` / `TOOL_USE_NOT_SUPPORTED_VIA_CLI` |

**為何三元不是二元**（panel 推動的關鍵改動）：
- Codex Section 4：「`subscription/api` 把 billing intent 跟 fallback behavior 混在一起；`subscription` 在 ADR 實際是『試訂閱、再 API』，那是 fallback chain 偽裝成單字」
- Gemini Section 5：推 `RoutingDecision` dataclass。Phase 1 不建 dataclass，但三元值已涵蓋 `RoutingDecision` 的「primary + fallback」核心語意，Phase 2 演化到 dataclass 是平滑擴展不是 breaking change

### 解析優先序（鏡像 model 維度）

```
NAKAMA_REQUIRE_MAX_PLAN=1                          (process-wide hard-lock)
  → 解析為 subscription_required
  → 否則 → AUTH_<AGENT>_<TASK> env
  → 否則 → AUTH_<AGENT> env
  → 否則 → DEFAULT_AUTH[task]
  → 否則 → DEFAULT_AUTH["default"]
```

```python
# v3 post-implementation — 從 "subscription_preferred" 翻為 "api"，
# 詳見 §Implementation deviation。
DEFAULT_AUTH = {
    "default": "api",
    "tool_use": "api",  # CLI 不暴露 tool-use JSON，固定 api
}
```

**保守 default 哲學**：沒明示就走 API（顯式計費路徑）；要走訂閱 quota 必須 operator 明確 opt-in。`subscription_preferred` 是 per-agent / per-task 的選擇（`AUTH_<AGENT>=subscription_preferred` 在 `.env`）；`subscription_required` 是 caller 顯式宣告「我不可降」，給 textbook ingest 這種有硬性 budget 約束的工作流用。`NAKAMA_REQUIRE_MAX_PLAN=1` 是 process-wide hard-lock，映射為 `subscription_required`。

### Dispatch 行為

`shared/anthropic_client.ask_claude` 不再讀 process-wide `NAKAMA_REQUIRE_MAX_PLAN`（這 flag 改由 router 在解析層映射為 `subscription_required`），改讀 router 回的 auth policy：

| Resolved policy | Provider | OAuth token 存在？ | CLI binary 可用？ | 行為 |
|---|---|---|---|---|
| `subscription_required` | Anthropic | ✓ | ✓ | `claude_cli_client.ask_via_cli` |
| `subscription_required` | Anthropic | ✗ or ✗ | — | **raise** + `fallback_reason` |
| `subscription_required` | 非 Anthropic | — | — | **raise** `PROVIDER_NOT_SUPPORTED` |
| `subscription_preferred` | Anthropic | ✓ | ✓ | `claude_cli_client.ask_via_cli` |
| `subscription_preferred` | Anthropic | ✗ or ✗ | — | 軟降 `api` + warn log + record `fallback_reason` |
| `subscription_preferred` | 非 Anthropic | — | — | 軟降 `api` + warn log + record `PROVIDER_NOT_SUPPORTED` |
| `api` | 任意 | — | — | bare SDK + API key |

### Tool-use 路徑特別處理

`call_claude_with_tools` 目前在 `NAKAMA_REQUIRE_MAX_PLAN=1` 下 raise（CLI 不暴露 tool-use JSON）。**這個 raise 必須保留**：

- 解析 policy 為 `subscription_required` + tool-use call → raise `TOOL_USE_NOT_SUPPORTED_VIA_CLI`
- 解析 policy 為 `subscription_preferred` + tool-use call → 軟降 `api` + warn + record `fallback_reason=TOOL_USE_NOT_SUPPORTED_VIA_CLI`

Caller 想跑 tool-use 又想吃訂閱配額沒有解（CLI 限制）— ADR 在這層誠實顯式 fail / fallback 而不偽裝統一。

### Observability

`api_calls` schema 加三個 columns（`migrations/008_api_calls_auth.sql`）：

- `auth_requested TEXT` — caller / router 解析後的 policy
- `auth_actual TEXT` — 實際走的路徑（`subscription` 或 `api`）
- `fallback_reason TEXT` — `auth_requested` ≠ `auth_actual` 時的成因 enum，否則 NULL

`shared/llm_observability.record_call` 多收這三個 kwarg。Bridge UI 暫不動（subscription quota 視覺化 deferred 到 Phase 2，先累積一週資料再設計面板）。

**為何三欄不是單欄**（panel 推動）：
- Gemini Section 1：「auto-downgrade with warn log 是 anti-pattern；要把『要求』與『實際』與『成因』顯式記下來，否則無法 audit silent fallback」
- Codex Section 4：「`feedback_vps_env_drift_check.md` 存在就是因為 silent env fallback 危險過」

### Translator de-hardcoding + facade task 維度

`shared/translator.py` 移除 `_DEFAULT_MODEL = "claude-sonnet-4-6"` 硬編碼，改 `model=None` 走 router。但 **既有 facade 有 gap 必須先補**（panel Codex Section 1 抓到的 P0 bug）：

`shared/llm.py:54` 的 `ask()` 與 `:115` 的 `ask_multi()` 目前 hardcode `task="default"`：
```python
model = get_model(agent=getattr(_local, "agent", None), task="default")
```

**Phase 1 PR 必須**：
1. `ask()` / `ask_multi()` / `ask_with_audio()` 新增 `task: str = "default"` 參數
2. Translator 改呼 `ask(..., task="translate")`（caller-supplied semantic task）
3. VPS `.env` 加 `MODEL_ROBIN_TRANSLATE=claude-sonnet-4-6` 釘住現行 model
4. `DEFAULT_MODELS` 加 `"translate"` key（fallback 用 `claude-sonnet-4-20250514`，跟既有 default 對齊）

否則 `MODEL_ROBIN_TRANSLATE` 是 dead config（Codex 已抓出）。

### `/translate` route thread-local

panel Codex Section 1 P0 bug：FastAPI BackgroundTasks 在 response 送出後執行，**execution context 與原 request handler 切離**。`set_current_agent("robin")` 必須在 `_translate_in_background()` body 內第一行設，不能只在 route handler 設（會被切斷）。

實作：

```python
# thousand_sunny/routers/robin.py
def _translate_in_background(*, source_path, bilingual_path):
    set_current_agent("robin")              # ← 必在 BG body 內
    try:
        content = read_text(source_path)
        ...
```

### 不在這 ADR 範圍（Phase 2 grill）

- **Vocabulary refactor**：Gemini 推 `billing_source` 取代 `auth`，values 用 provider-specific（`anthropic_max_plan` / `google_ai_premium` / ...）。理由是 `subscription` 字面通用但實作不通用。Phase 2 接 Grok Heavy / Gemini Advanced 時 grill — Phase 1 因為只 Anthropic 有訂閱路徑，三元語意足夠
- **`RoutingDecision` dataclass**：Gemini Section 6 推。Phase 1 三元值已涵蓋 primary + fallback 核心語意；Phase 2 演化到 dataclass + 顯式 `fallback_chain: List[RoutingTarget]` 是平滑擴展
- **Cost ceiling / circuit breaker**
- **Subscription quota starvation 預防**（task class quota bucket，例如 Robin translate 一桶、Brook compose 另一桶）— Codex Section 5、Gemini Section 1 都提到。Phase 1 透過 `subscription_required` 給高價值任務優先權，Phase 2 加 budget bucket
- **Multimodal**：CLI 對 binary data (圖片) break（Gemini Section 3）。Phase 1 系統純文字，Phase 2 加圖片時要重設 dispatch 路徑
- **YAML policy file**：Codex Section 4 提到 `MODEL_*` + `AUTH_*` env 在 35+ files / 56+ call lines 後不 scale。Phase 1 env 三元語意還在可控範圍，Phase 2 視 env 暴增切 YAML

## Implementation deviation

ADR v2 specified `DEFAULT_AUTH["default"] = "subscription_preferred"` (subscription-preferred 哲學)。Slice 2 (#581) 改成 `"api"`，原因 + 取捨：

**觀察到的問題**：實作完 anthropic_client 的 per-call dispatch 後，跑 `tests/shared/seo_audit/test_llm_review.py` 失敗。trace：

1. Test 用 `monkeypatch.setattr("shared.anthropic_client.get_client", lambda: mock_client)` mock SDK 層
2. 但新 dispatch 邏輯先檢查 `_oauth_token_available()`（讀 `ANTHROPIC_AUTH_TOKEN` / `CLAUDE_CODE_OAUTH_TOKEN` / `~/.claude/.credentials.json`）+ `_cli_binary_available()`（`shutil.which("claude")`）
3. Dev 機器 (Windows) 兩個條件都成立 → 不去 SDK 層 → 直接 `ask_via_cli` 跑真實 `claude -p` subprocess → 1 分鐘多 retry 後失敗
4. Test 期望 status=`"pass"`、實際拿到 fallback error

**選項**：
- (a) 在 conftest autouse fixture mock 掉 `_oauth_token_available` / `_cli_binary_available`（侵入大量測試）
- (b) `_oauth_token_available` 不再檢查 `credentials.json`，只看 env vars（operator 必須顯式 export token 才算 opt-in；但這跟 `feedback_oauth_env_pinning_long_batch.md` 規範的「長 batch 不要 pin env」矛盾）
- (c) **翻 `DEFAULT_AUTH["default"]` 為 `"api"`**，operator 在 `.env` 顯式設 `AUTH_<AGENT>=subscription_preferred` 才走訂閱（採用）

**為什麼 (c)**：
- 跟 Codex audit §4「default should not silently spend money when operator thought they were using Max」對稱 — default 也不該 silently 消耗 Max quota when operator didn't opt in
- Operator 明確 opt-in 比 silent default 更可審計
- 不需要在測試層 mock pollution
- `NAKAMA_REQUIRE_MAX_PLAN=1` hard-lock 仍然 work（textbook ingest / sandcastle 不受影響）
- Per-agent opt-in 顆粒度照 ADR vocabulary 已經支援

**未動**：
- `subscription_preferred` / `subscription_required` 三元 vocabulary 本身保留
- Soft-downgrade / hard-raise 語意保留
- Hard-lock 映射保留

如果 Phase 2 證明大部分 agent 都該走訂閱，可以再翻回 `subscription_preferred` 並加 conftest mock。但 Phase 1 採保守 default。

## Considered Options

| 方案 | 範圍 | 為何沒選 |
|------|------|---------|
| A — 最小擴：只加 auth 維度 | router + 1 個 client + translator | **採用核心**；但需配合 facade `task=` 補丁與 BG thread-local 修正才完整 |
| B — Policy 表 + fallback chain + cost ceiling | 中央 YAML + circuit breaker + health-aware routing | over-engineering 風險高（fallback / cost 語意未撞過真實場景） |
| **C — A + facade task 補丁 + BG thread-local 修，B 延後**（採用） | 上 PR 範圍：router + facade + 1 client + translator + BG task + schema migration | 規模配 workload，panel review 後加入 P0 bug fix |

Migration 策略候選：

- (i) Hard cutover — 落地當天強制 subscription：本機 dev / VPS 沒 OAuth 直接炸，太硬
- (ii) Opt-in flag `NAKAMA_AUTH_VIA_ROUTER=1`：dead-code 風險，永遠沒人 flip
- **(iii) Capability detect + `subscription_preferred` 軟降，`subscription_required` 硬 raise**（採用）— 跟 subscription-preferred 哲學一致；雙層機制（`_required` 用於必須 100% 走訂閱的 caller）涵蓋既有 sandcastle 強制需求

Auth 命名候選：

- 二元 `subscription` / `api`（v1 提案）— panel 兩家都拒：混 billing intent 與 fallback behavior，且 `subscription` 跨 provider 不真的通用
- **三元 `api` / `subscription_preferred` / `subscription_required`**（v2 採用）— 顯式拆 fallback 語意；`NAKAMA_REQUIRE_MAX_PLAN=1` 映射為 `_required`
- Gemini `billing_source` + provider-specific values — Phase 2 grill，理由是 Phase 1 只 Anthropic 訂閱、三元語意夠用，Phase 2 接其他 provider 時重設

## Consequences

### Positive

- caller 不需動 LLM call site：既有 70+ `shared.llm.ask*` caller 透過 thread-local agent context 自動吃到對的 auth
- subscription-preferred 是預設哲學，配合修修「subscription 能用就優先用」的真實使用模式
- 雙層 fail-safe：`_preferred` 軟降給日常 dev、`_required` 硬 raise 給 sandcastle / textbook ingest
- 顯式 observability（`auth_requested` / `auth_actual` / `fallback_reason`）讓 silent fallback 變 audit-able
- Phase 2 設計空間保留：vocabulary refactor、cost ceiling、fallback chain、`RoutingDecision` dataclass、quota bucket 都能在不破現有介面下追加

### Negative / Risk

- **CLI subprocess 是 tactical workaround，非 strategic API contract**（panel Gemini Section 3 重點）— `claude` binary 的 flags / `--output-format json` schema / 認證機制可能在 `brew upgrade claude` 後變動。Phase 1 接受這個風險換 Max Plan 配額，但 ADR 明確 flag 這是 brittle 整合點。長期應推 Anthropic 開正式 OAuth + 高 RPS API
- **CLI subprocess env-leak / token-expiry**（`feedback_oauth_env_pinning_long_batch.md` 2026-05-16 incident）— `claude_cli_client.py:166` 只 scrub `ANTHROPIC_API_KEY`，其他 env var 包括 `CLAUDE_CODE_OAUTH_TOKEN` 全部 leak 進 subprocess。長 batch (≥1h) 若 parent shell pin 了 OAuth token，token expire 後整批 401 cascade（SN textbook ingest 2026-05-16 丟失 ch4-17 約 14 章工作）。Phase 1 dispatch 層必須能辨識 401 fail-mode 並對 `subscription_preferred` 軟降到 `api`、對 `subscription_required` raise + `fallback_reason=CLI_AUTH_EXPIRED`。Parent shell env 紀律由 `feedback_oauth_env_pinning_long_batch.md` 規範
- **多模態工作流會破 CLI 路徑**（panel Gemini Section 3）—`claude` CLI 對 binary image 不友善。Phase 1 系統純文字，但若 Brook / Robin 未來吃圖片，必須回到 SDK path（auto-downgrade 到 `api`）。在 dispatch 層加 modality 檢測是 Phase 2 工作
- **Quota starvation**（panel Codex Section 5 + Gemini Section 1）—Robin 高頻翻譯可能吃光 Max Plan 配額，讓 Brook Opus 長文 fallback / 排隊。Phase 1 用 `subscription_required` 給高價值任務優先權部分緩解；完整解 = Phase 2 budget bucket
- **`NAKAMA_REQUIRE_MAX_PLAN` 仍是 process-wide**：跟 router per-call 解析共存，看起來雙層機制有點 awkward。但移除它會 break sandcastle textbook ingest 的硬保證，留著值得；本 ADR 把它語意化為「映射到 `subscription_required`」而不是獨立語意層
- **Scripts migration**：`scripts/run_s8_batch.py:1004` 與 `scripts/run_s8_preflight.py:208` 內部設 `NAKAMA_REQUIRE_MAX_PLAN=1`。Phase 1 PR 同步審視這些 caller — 因為 `REQUIRE_MAX_PLAN` 仍 work（映射為 `_required`），不需要立即改；但 release note 應提供 cleaner 寫法（caller 改用 `set_auth_policy("subscription_required")` 之類顯式 API）

### Migration / Rollout

- 一次性 PR：router + facade `task=` + anthropic_client + translator + robin route BG body + schema migration + tests
- 不需 caller 改動 LLM call site
- VPS `.env` 增補：
  - `MODEL_ROBIN_TRANSLATE=claude-sonnet-4-6`（釘住翻譯 model）
  - 可選：`AUTH_FRANKY=api`（讓 health check 不吃訂閱）等 per-agent override
- `NAKAMA_REQUIRE_MAX_PLAN=1` 仍 work（映射到 `_required`），既有 sandcastle / textbook ingest unit 不需動
- Release note 標出：
  - 新 env 慣例 `AUTH_*`
  - 三元 auth 值語意
  - `auth_requested` / `auth_actual` / `fallback_reason` 新 columns
  - Tool-use 路徑在 `subscription_*` 下的 raise/fallback 行為
  - 既有 `REQUIRE_MAX_PLAN` flag 行為等價於 `subscription_required`

### Remaining work（不在這 PR 範圍）

- Phase 2 grill：vocabulary refactor (`billing_source` + provider-specific values)、`RoutingDecision` dataclass、cost ceiling、fallback chain、quota bucket
- Bridge UI subscription quota 視覺化（等 `auth_actual` + `fallback_reason` 累積一週資料 → 設計面板）
- `DEFAULT_MODELS["default"]` 目前 `claude-sonnet-4-20250514`（Sonnet 4 dated 2025-05-14，**不是** Sonnet 4.5；panel Codex Section 3 抓出 v1 此處 model id 寫錯）。與 `feedback_cost_management.md` 的「daily Sonnet 4.6」對齊需要另開 task — 分離的 drift bug
- Multimodal dispatch path（圖片 / 音訊 binary data 在 CLI subprocess 之外的路徑）
- Anthropic 官方文件 / support ticket 確認 OAuth + bare SDK 的 RPS 限制（panel Codex Section 3：1 RPS 目前是 Nakama 觀察行為，非 vendor SLA）
- 多 provider subscription 接入（Grok Heavy / Gemini Advanced — 各自的訂閱整合 story 與 Anthropic CLI 不同）
