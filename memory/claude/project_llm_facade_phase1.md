---
name: LLM facade deepening progress
description: PR #208 (Phase 1) + PR #222 (Phase 2) merged；Phase 3-5 queued (gateway migration / test mock 收斂 / re-export 退場)
type: project
originSessionId: 3d901e7b-183e-450a-a0bf-2f06311b6452
---
**Phase 1 — PR #208 merged 2026-04-27 為 `e043e2a`**：
- 新建 `shared/llm_context.py`（thread-local + set_current_agent + start/stop_usage_tracking 唯一 owner）
- 新建 `shared/llm_observability.py`（`record_call()` 統一 cost-tracking 入口）
- 三個 provider client 改瘦：移除 _local 自己 own、移除重複 retry/cost-tracking 樣板
- Facade `shared/llm.py` 擴大：新增 `ask_with_tools`（anthropic）+ `ask_with_audio`（gemini），其他 provider raise `NotImplementedError`
- Backward-compat re-exports（`_local` / `set_current_agent` / `start/stop_usage_tracking`）保留在 `shared.anthropic_client` / `shared.gemini_client`

**Phase 2 — PR #222 merged 2026-04-28 為 `f900c34`**：
- agents/* 16 檔 + shared/* 6 檔 caller migrate 到 `shared.llm.{ask, ask_multi, ask_with_audio}` facade
- `set_current_agent` / `start/stop_usage_tracking` import path 從 backward-compat re-export 換成 `shared.llm_context` 直接 import
- Test mock target 11 檔同步更新到 caller-module binding 層（仍未到 facade 層 — Phase 4 才做）
- pytest 2437 passed / ruff clean，零 behaviour 變化

**Why**：之前 `anthropic_client._local` 是跨 provider thread-local 的隨意擁有者，silent coupling；三個 client 重複 retry/cost-tracking 樣板；Facade 不完整導致 70+ caller 直接 import provider。

**How to apply**：
- 寫新 LLM caller 走 `shared.llm.ask*`（不要 `from shared.anthropic_client import ask_claude` — 那是 backward-compat、不該作為新 default）
- 改 thread-local 行為走 `shared/llm_context.py` 一處
- 新 provider wire 進 `shared/llm.py` 的 dispatch + 加 `_require_*_model` guard
- 寫測試 mock LLM **目前**仍 mock 在 caller-module 自己的 imported name（per `feedback_pytest_monkeypatch_where_used.md`）；Phase 4 後才會把 mock 收斂到 `shared.llm.ask` 層
- Phase 2 follow-up（cosmetic，Phase 4 順手清）：`tests/e2e/test_agent_brook_e2e.py:7` docstring path 過時、`tests/test_transcriber.py:166` docstring 還寫 `mock ask_claude`、`tests/test_translator.py` `mock_claude` 變數名 + `test_translate_segments_uses_claude` 函式名 misleading

**Phase 3-5 queued**：plan 在 [docs/plans/2026-04-27-llm-facade-deepening.md](../../docs/plans/2026-04-27-llm-facade-deepening.md)
- **Phase 3**：gateway/* 5 檔（router.py / orchestrator.py / handlers/{nami,sanji,zoro}.py）migrate 同樣 pattern
- **Phase 4**：caller test mock 改 mock 在 `shared.llm.ask` 層（消滅 brittle mock pattern）+ Phase 2 cosmetic follow-up
- **Phase 5**：移除 anthropic_client / gemini_client 的 backward-compat re-export（確認所有 caller 都走 facade 後）

**Stacked PR 注意**：每個 Phase squash merge 後再從 main 開下一分支，不堆疊（per `feedback_stacked_pr_squash_conflict.md`）
