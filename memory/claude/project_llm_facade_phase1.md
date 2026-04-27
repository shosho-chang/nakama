---
name: LLM facade deepening Phase 1 done
description: PR #208 merged 2026-04-27 — llm_context + llm_observability 抽出，facade 加 ask_with_tools/ask_with_audio；Phase 2-5 queued
type: project
originSessionId: 3d901e7b-183e-450a-a0bf-2f06311b6452
---
PR #208 merged 2026-04-27 為 `e043e2a`：

- 新建 `shared/llm_context.py`（thread-local + set_current_agent + start/stop_usage_tracking 唯一 owner）
- 新建 `shared/llm_observability.py`（`record_call()` 統一 cost-tracking 入口）
- 三個 provider client（anthropic / gemini / xai）改瘦：移除 _local 自己 own、移除重複 retry/cost-tracking 樣板，只留 request building + response parsing + provider-specific guard
- Facade `shared/llm.py` 擴大：新增 `ask_with_tools`（route to anthropic）+ `ask_with_audio`（route to gemini），其他 provider raise `NotImplementedError`
- Backward-compat re-exports（`_local` / `set_current_agent` / `start/stop_usage_tracking`）保留在 `shared.anthropic_client`，70+ caller 不用一次全改

**Why**：之前 `anthropic_client._local` 是跨 provider thread-local 的隨意擁有者，gemini / xai re-import — silent coupling。三個 client 各自 200~400 行重複 retry/cost-tracking。Facade 不完整（tool-use / audio 不在 facade），導致 caller 直接 import provider，70+ 處 lock-in。

**How to apply**：
- 寫新 LLM caller 時走 `shared.llm.ask*`（不要 `from shared.anthropic_client import ask_claude` — 那是 backward-compat、不該作為新 default）
- 改 thread-local 行為走 `shared/llm_context.py` 一處，不再多點散
- 新 provider wire 進 `shared/llm.py` 的 dispatch + 加 `_require_*_model` guard（對稱現有三家）
- 寫測試 mock LLM 時 mock at `shared.llm.ask` 層（不要 mock `shared.anthropic_client.ask_claude_multi` — 那是 brittle pattern）

**Phase 2-5 queued（細節在 plan doc）**：[docs/plans/2026-04-27-llm-facade-deepening.md](../../docs/plans/2026-04-27-llm-facade-deepening.md)
- PR #2 — agents/* 改用 `shared.llm.ask` 取代直接 import
- PR #3 — gateway/* + scripts/* 同樣 migration
- PR #4 — caller test mock 改在 facade 層收斂（消滅 brittle mock pattern）
- PR #5 — re-export 退場（migration 收完才動）
