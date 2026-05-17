# LLM Router 擴張 grill prep — 加 Auth 維度（2026-05-16）

## TL;DR

修修今天問「需不需要實作類似 Router 的機制統一管理雲端 model 呼叫 + 選擇 subscription vs API auth」。

**結論：Router 已存在**（`shared/llm_router.py` + `shared/llm.py` facade，2026-04-20 起的 Q1 hybrid 方案，已落地 4 個 provider wrapper + thread-local agent context + 統一 cost tracking）。今天真正的 gap 是 router 缺**第三個維度 = Auth**，目前 subscription vs API 是 process-wide env flag（`NAKAMA_REQUIRE_MAX_PLAN`），無法做 per-(agent, task) 切換。

## 既有 Router 盤點

### 解析鏈（`shared/llm_router.py:42`）

```
caller `model=` (顯式) →
MODEL_<AGENT>_<TASK> env →
MODEL_<AGENT> env →
DEFAULT_MODELS[task]
```

### Facade（`shared/llm.py`）

- `ask` / `ask_multi` — text，Anthropic + xAI + Google 已 wire，OpenAI raise NotImplementedError
- `ask_with_tools` — 只 Anthropic
- `ask_with_audio` — 只 Gemini
- Provider prefix routing：`claude-` / `grok-` / `gemini-` / `gpt-` / `o1-` / `o3-`

### Auth 現況（`shared/anthropic_client.py:35`）

| 條件 | Path | 計費 |
|------|------|------|
| `NAKAMA_REQUIRE_MAX_PLAN=1` + OAuth | `claude_cli_client.ask_via_cli`（subprocess `claude -p`）| **Max Plan 訂閱 quota** |
| `ANTHROPIC_API_KEY` 設了 | bare SDK + `x-api-key` | API 計費 |
| 只 OAuth 沒 flag | bare SDK + `auth_token` | **必 429**（Anthropic 對 bare SDK + OAuth 強硬 rate-limit） |

**關鍵血淚**：bare SDK + OAuth 1 RPS 即 429；訂閱 quota 只有 `claude` CLI binary 帶得到 auth identity header。所以「走訂閱」唯一可靠路徑是 `REQUIRE_MAX_PLAN=1` → CLI subprocess。

### Cost tracking（`shared/llm_observability.py`）

每 call 寫 `state.api_calls`：model / input / output / cache_read / cache_write / latency / agent / run_id。三家 provider 的 usage shape 差異記在 `reference_llm_provider_cost_quirks.md`。

## 真正的 gap（router 沒覆蓋的）

| Gap | 影響 |
|-----|------|
| **Auth 是 process-wide env，不在 router 維度** | 同一 process 內無法「Robin 翻譯走訂閱、Brook compose 走 API」混用；只能切 systemd unit / worktree |
| **OpenAI text dispatch 缺** | router 認 prefix 但 facade 拋 NotImplementedError |
| **xAI / Gemini 無 subscription 路徑** | 只 SDK + API key；Grok Heavy / Gemini Advanced 訂閱沒接 |
| **Translator / 其他高頻 caller 寫死 model** | `shared/translator.py:24` `_DEFAULT_MODEL = "claude-sonnet-4-6"`，不吃 router；EPUB 整本翻譯升級會直接撞這 |
| **無 cost ceiling / circuit breaker** | observability 只記不擋；翻一本書 / 大量 ingest 沒上限 |
| **無 quota awareness / fallback chain** | CLI 收到 429 只是 retry；訂閱用完不會自動降級到 API 或別家 |
| **無 health-aware fallback** | provider down 沒 plan B |

## 術語警告（grill 開始前框住）

修修原話「實作一個類似 Router 的東西，當我們的 Agent 或者是 Function 裡面要呼叫雲端的 Model 的時候，我們有一個機制可以統一管理」— 這個東西 **2026-04-20 已建** 並 production 跑了一個月（Sanji 走 Grok、Robin ingest 走 Gemini，全靠 `MODEL_<AGENT>` env 切換）。

所以 grill 主題應該重 frame 為：**「LLM Router 加 Auth 維度與 Policy 表」**，不是「從零打造 Router」。否則容易誤砍重做。

## 候選方案

### 方案 A — 最小擴：Auth 作為 router 第三維度

- `get_auth_policy(agent, task) → "subscription" | "api" | "auto"`（新增）
- `anthropic_client.ask_claude` 不再 process-wide env 決定，改 per-call 讀 router 回的 policy
- env 對應慣例：`AUTH_<AGENT>_<TASK>` / `AUTH_<AGENT>` / `DEFAULT_AUTH[task]`
- 範圍：router + 1 個 client，~3h
- 不解：cost ceiling、fallback chain、跨 provider subscription

### 方案 B — Policy 表 + Fallback chain

- 中央 YAML（例：`config/llm_policy.yaml`）：(agent, task) → (model, auth, max_cost_usd_per_call, fallback_chain[])
- 加 cost circuit breaker：超 ceiling 拒絕或 fallback
- 加 health-aware fallback：429 / 503 自動切下一個 entry
- 範圍：router 重構 + 新 policy loader + caller integration tests，~2-3 day
- 風險：YAML 漂移、policy semantics 容易過度設計

### 方案 C — 拆兩階段（A 先，B 等真實壓力）

- Phase 1：方案 A 落地（1 PR，~3h）
- Phase 2：等 EPUB 整本書翻譯 / 大量 ingest 真的撞 quota 或 cost ceiling 再做方案 B
- **Why 推薦**：B 的 fallback chain / cost circuit 是 over-engineering 風險高，等真實 workload 驅動設計

## 待 grill 議題

下列議題逐題拍板，grill 完開 ADR 候選 + 1-2 PR：

1. **要不要做這個？** — confirm 修修真正卡的是 auth 維度，不是已存在的 router
2. **Auth dimension 還是 Policy 表？** — A vs B vs C
3. **Auth 詞彙** — `subscription` vs `oauth` vs `max_plan`？跨 provider 推廣後 `subscription` 還能用嗎（Grok Heavy / Gemini Advanced）
4. **Env 慣例** — `AUTH_<AGENT>_<TASK>` 還是 `MODEL_<AGENT>_<TASK>_AUTH`？跟既有 `MODEL_<AGENT>` 對稱性
5. **預設 policy** — production 沒指定時 default subscription 還是 api？翻譯 / ingest / brook 長文哪些強制走 subscription？
6. **Translator 寫死 model 怎麼處理？** — 移除 `_DEFAULT_MODEL` 走 router？還是保留但 caller 可覆寫？影響 EPUB 翻譯升級 plan
7. **跨 provider subscription 範圍** — 只 Anthropic 還是擴 Grok / Gemini？非 Anthropic 用什麼 CLI / OAuth 機制？
8. **Cost ceiling / circuit breaker** — 進這版還是等 Phase 2？per-call 還是 per-run 還是 daily cap？
9. **Fallback chain** — 同 #8，現在做還是等？fallback 優先序由誰決定（router default vs caller override）
10. **Test 策略** — 既有 `caller-binding mock` pattern（`feedback_facade_mock_caller_binding.md`）怎麼擴 auth 維度？
11. **Bridge UI 影響** — Cost panel 要不要加 auth 維度（subscription quota usage vs API spend 分開顯示）？
12. **Migration** — 既有 70+ caller 不需動（auth 默默生效），還是強制每個 caller 宣告 policy？

## 參照

- 既有 router：[`shared/llm_router.py`](../../shared/llm_router.py)
- Facade：[`shared/llm.py`](../../shared/llm.py)
- Auth 切換：[`shared/anthropic_client.py:35-85`](../../shared/anthropic_client.py)
- CLI subscription path：[`shared/claude_cli_client.py`](../../shared/claude_cli_client.py)
- Multi-model 架構決策史：[`memory/claude/project_multi_model_architecture.md`](../../memory/claude/project_multi_model_architecture.md)
- Facade deepening 4-phase：[`memory/claude/project_llm_facade_phase1.md`](../../memory/claude/project_llm_facade_phase1.md)
- Provider cost quirks：[`memory/claude/reference_llm_provider_cost_quirks.md`](../../memory/claude/reference_llm_provider_cost_quirks.md)
- Translator gap：[`docs/plans/2026-05-05-epub-book-translation-grill-prep.md`](2026-05-05-epub-book-translation-grill-prep.md)
