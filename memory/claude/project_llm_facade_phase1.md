---
name: LLM facade deepening progress
description: Phase 1-4 all merged (#208/#222/#223/#224)；Phase 5 (re-export 退場) in PR；plan 完整路徑 + 每 Phase 教訓記錄
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
- Test mock target 11 檔同步更新到 caller-module binding 層（仍未到 facade 層）

**Phase 3 — PR #223 merged 2026-04-28 為 `c520103`**：
- gateway/* 5 檔（router.py / orchestrator.py / handlers/{nami,sanji,zoro}.py）同 pattern migrate

**Phase 4 — PR #224 merged 2026-04-28 為 `caa7c51`** — **plan deviation**：
- 原 plan「mock 收斂到 `shared.llm.ask` 層」**verified invalid**：caller 用 `from shared.llm import ask` 把 ask bound 進自己 namespace 後，`patch("shared.llm.ask")` 完全不生效。`from X import Y` 是 attribute copy 不是 reference indirection。
- 教訓寫進 [feedback_facade_mock_caller_binding.md](feedback_facade_mock_caller_binding.md)：caller-binding mock（`patch("agents.brook.compose.ask_multi")`）才是正確設計，**不是** brittle pattern。
- 降級為 cosmetic-only：3 處 docstring/變數名稱 follow-up 收尾。

**Phase 5 — re-export 退場（in PR）**：
- 移除 `shared/anthropic_client.py` 的 `set_current_agent` / `start_usage_tracking` / `stop_usage_tracking` re-export 與 `__all__` 的 `_local`
- 移除 `shared/gemini_client.py` 的 `set_current_agent` re-export
- 7 處 test 改 `from shared.llm_context import set_current_agent`（含 `gc.set_current_agent` 一處 module-aliased access — grep `\.set_current_agent\b` 才抓得到）
- `_local` 仍保留 internal import（client 自己讀 `_local.agent` 走 router 解析）
- 淨刪 ~10 LOC

**Why**：之前 `anthropic_client._local` 是跨 provider thread-local 的隨意擁有者，silent coupling；三個 client 重複 retry/cost-tracking 樣板；Facade 不完整導致 70+ caller 直接 import provider。

**How to apply**：
- 寫新 LLM caller 走 `shared.llm.ask*`
- 改 thread-local 行為走 `shared/llm_context.py` 一處
- 新 provider wire 進 `shared/llm.py` 的 dispatch + 加 `_require_*_model` guard
- 寫測試 mock LLM 走 caller-module 自己 imported name（per `feedback_facade_mock_caller_binding.md`）— **不要** mock `shared.llm.ask` 層
- Phase 5 後 `from shared.anthropic_client import set_current_agent` 不再 work；要用 `from shared.llm_context import set_current_agent`

**Stacked PR 注意**：每個 Phase squash merge 後再從 main 開下一分支，不堆疊（per `feedback_stacked_pr_squash_conflict.md`）

**Plan**：[docs/plans/2026-04-27-llm-facade-deepening.md](../../docs/plans/2026-04-27-llm-facade-deepening.md)
