# 2026-04-27 — LLM Facade Deepening

> Skill `improve-codebase-architecture` 第一輪 audit 候選 ① + ⑤ 合併處理。
> 目標：把 `shared.llm` 從 shallow dispatcher 變成唯一 caller seam，把跨 provider 共用 infra 從 `anthropic_client` 抽出獨立 module。

## Why

**今天的形狀（fact-checked）：**

- `shared/llm_router.py` (86L) — pure routing，env override → model ID → provider；零 I/O，已是 deep。
- `shared/llm.py` (135L) — `ask` / `ask_multi` shallow dispatcher；**只 cover text completion**。
- `shared/anthropic_client.py` (290L) — 擁有跨 provider 共用的 `_local` thread-local；提供 `ask_claude` / `ask_claude_multi` / `call_claude_with_tools` + `start/stop_usage_tracking`。
- `shared/gemini_client.py` (434L) — 從 anthropic_client `import _local`、`re-export set_current_agent`（gemini_client.py:22）。提供 `ask_gemini` / `ask_gemini_multi` / `ask_gemini_audio`。
- `shared/xai_client.py` (197L) — 同樣 `import _local` from anthropic_client（xai_client.py:23）。提供 `ask_grok` / `ask_grok_multi`。

**痛點：**

1. **Silent coupling** — `_local` 由 anthropic_client 擁有但 gemini / xai 共讀。改 anthropic 內部會無聲影響其他 provider。
2. **三 client 重複 retry + cost-tracking + latency 計時 pattern** — 各自 200~400 行幾乎一樣。
3. **Facade 不完整** — `call_claude_with_tools` / `ask_gemini_audio` 不在 facade，caller 必須直接 `from shared.anthropic_client import ...`，造成 70+ 處 import 鎖死 provider。
4. **測試 mock 走錯 seam** — 既有 caller test mock `ask_claude_multi` 直接，refactor 內部就 break。

## What changes（Branch B：compose around shared infra）

### 新增 module

**`shared/llm_context.py`**
- `_local` — thread-local（`agent`, `run_id`）的唯一 owner
- `set_current_agent(agent: str, run_id: int | None) -> None`
- `start_usage_tracking() -> None`
- `stop_usage_tracking() -> list[dict]`
- 所有 client 從這裡 read，**不再從 anthropic_client 偷**

**`shared/llm_observability.py`**
- `record_call(agent, model, *, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, run_id, latency_ms)` — 單一 cost tracking 入口
- 內含 try/except，失敗不影響主流程（保留既有語意）
- 同時把 `(model, input_tokens, output_tokens, cache_*)` append 到 `_local.usage_buffer`（給 opt-in tracking 用）

### 改瘦 client（三個）

`anthropic_client.py` / `gemini_client.py` / `xai_client.py` 三個檔：

- 移除 `_local` 定義 / `import`
- 移除 `set_current_agent` / `start/stop_usage_tracking`（改 import from llm_context）
- 移除 `_record_usage` / `_record_usage_to_buffer` 內聯邏輯（改 call observability.record_call）
- `ask_*` 函式只負責：build request → call SDK → parse response → return text
- retry / latency / cost-tracking 由外圍 wrapper 注入

**保留**：
- `_require_*_model` guard（fail-fast 邏輯，純函式，留在 client）
- Provider-specific helpers（`_audio_mime_type`、`_extract_system_messages`、`_clamp_thinking_budget`、`_describe_finish`）— 不 leak 到 facade
- `get_client()` singleton — 仍在 client module

### Facade 擴大

`shared/llm.py`：

- `ask(prompt, *, system, model, max_tokens, temperature, thinking_budget) -> str` — 維持
- `ask_multi(messages, ...) -> str` — 維持
- **新增** `ask_with_tools(messages, tools, *, system, model, max_tokens) -> Message` — 目前 route 到 anthropic；其他 provider raise `NotImplementedError`（同 ask 既有 pattern）
- **新增** `ask_with_audio(audio_path, prompt, *, response_schema, system, model, temperature, max_output_tokens, thinking_budget) -> str | BaseModel` — 目前 route 到 gemini
- `import _local` 改從 `shared.llm_context`，不再從 anthropic_client

### Backward-compat re-exports（過渡期保留）

`shared.anthropic_client.set_current_agent` 等 — 一個 minor 版本內保留，內部從 llm_context import。70+ caller migration 走後續 PR。

---

## Tests

### 活下來

- `tests/test_llm_router.py` — pure logic，零變動
- `tests/test_anthropic_client_guard.py` — `_require_claude_model` 留下，import path 改用 backward-compat re-export
- `tests/test_llm_facade.py` — **擴大**：加 `ask_with_tools` / `ask_with_audio` route case
- `tests/test_gemini_client.py` / `test_xai_client.py` — shrink：移除 retry / tracking 內聯測試，留 request building + response parsing

### 該死或重寫（後續 PR）

不在 PR #1 scope，列為 follow-up：
- `tests/agents/brook/test_compose_*.py` 及其他 mock `ask_claude_multi` / `ask_gemini` 直接的測試 → 改 mock `shared.llm.ask` / `ask_multi`
- 共 ~10 處 caller test 需要 audit

---

## Out of scope（這次不做）

- **Caller migration**：70+ 處 `from shared.anthropic_client import ask_claude` 不在 PR #1 改。`shared.llm.ask(model="claude-...")` 是新 default，但既有 caller 留用；後續 PR 漸進式換。
- **Schema unification**：tool-use 的 cache_control / Gemini 的 thinking config 等 provider-specific kwargs 維持原樣，**不**強行 unify。
- **`shared/llm/` 子 package 重組**（Branch C）：先做 B 的 module split，folder 重組看後續是否需要。
- **OpenAI provider wire**：`get_provider("gpt-4o")` 仍 raise `NotImplementedError`，這次不擴。

---

## ADR 影響

**無新 ADR**。純 refactor，沒改架構決策（router 邏輯、provider 選擇、cost tracking 落地、retry policy 都不變）。observability.md / reliability.md 原則不變。

---

## Migration phases

| PR | scope | 範圍 |
|---|---|---|
| **#1** （這次） | infra split + facade 擴大 | 新建 llm_context + llm_observability，改瘦三 client，擴大 facade，現有 caller import path 不動（靠 re-export 保命） |
| #2（後續） | caller migration A | agents/* 改用 `shared.llm.ask` 取代直接 `from shared.anthropic_client import` |
| #3（後續） | caller migration B | gateway/* + scripts/* 同樣處理 |
| #4（後續） | test mock 收斂 | mock 改在 `shared.llm.ask` 層；移除直接 mock `ask_claude_multi` 的 fixture |
| #5（後續） | re-export 退場 | 確認所有 caller 都走 facade 後，移除 anthropic_client.set_current_agent 等 backward-compat re-export |

---

## Acceptance（PR #1）

- [ ] `pytest tests/` 全綠（特別是 `test_llm_facade.py` / `test_anthropic_client_guard.py` / `test_llm_router.py` / `test_gemini_client.py` / `test_xai_client.py`）
- [ ] `ruff check && ruff format --check` 全綠
- [ ] 新 module `llm_context.py` / `llm_observability.py` 各自獨立可測（不 import 任何 provider client）
- [ ] 三 client 的 `ask_*` / `call_*` 公開簽名**完全不變**（caller 接口零 break）
- [ ] `set_current_agent` import path 兩條都 work：
    - 新：`from shared.llm_context import set_current_agent`
    - 舊：`from shared.anthropic_client import set_current_agent`（re-export）
- [ ] `shared/llm.py` 新 `ask_with_tools` / `ask_with_audio` 有 facade test cover
