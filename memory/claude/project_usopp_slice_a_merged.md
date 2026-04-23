---
name: Usopp Publisher Slice A 已 merged（PR #73）
description: 2026-04-23 Phase 1 Usopp 第一刀 merged；WP client + advisory lock + external schemas + Docker WP staging；剩 Slice B/C
type: project
tags: [usopp, phase-1, wordpress, pr-73, slice-a]
---

## 現況（2026-04-23）

PR #73 squash merged to main as `6a8a61c`。11 檔 / +2067 行。

## 交付範圍

- `shared/wordpress_client.py`（543 行）— WP REST v2 同步 client（agent-agnostic）；1 req/sec rate limit；5xx/timeout 3-try exponential retry；Basic auth password 遮罩
- `shared/locks.py`（156 行）— SQLite `BEGIN IMMEDIATE` advisory lock（ADR-005b §2.1 race fix）
- `shared/schemas/external/wordpress.py` / `seopress.py` — anti-corruption layer
- `tests/fixtures/wp_staging/` — Docker WP 6.x + MySQL 8.0 + SEOPress 9.4.1 + seed.sh
- 28 新測試全綠（26 initial + 2 follow-up）；697 total 無 regression

## Code review 發現（已在 PR #73 內修）

- **真 bug**：`upload_media` 雙 `headers=` → TypeError；fix commit `e35e5b1`：`_request()` pop 出 extra headers 再 merge
- **Borderline items（score < 80 未 surface）**：retry 缺 jitter（Phase 1 單 worker 不必要）、8s retry dead code（docstring 小不符）、`advisory_locks` schema 沒進 `state.py:_init_tables`（既有 `agent_memory.py` 也是 lazy 模式，非違規）

## ADR 一致性

實作過程發現 3 個 ADR 沒寫清的細節（都已補回 ADR-005a/ADR-006）：
1. `_ast_and_html_consistent` validator 與 `build()` 的無限遞迴（`model_construct()` 解）
2. Python sqlite3 隱式 transaction 跟 `BEGIN IMMEDIATE` 打架 → 改用單 `UPDATE...RETURNING` + singleton conn mutex
3. `reviewer_compliance_ack` 從 payload 到 DB column 要手動 propagate

## 下一步：Slice B + C

- **Slice B**（publisher 主流程）：`agents/usopp/publisher.py` + `shared/seopress_writer.py`（三層降級）+ `shared/litespeed_purge.py`（Day 1 實測 endpoint）+ `shared/compliance/medical_claim_vocab.py` + `migrations/002_publish_jobs.sql`
- **Slice C**（daemon + E2E）：`agents/usopp/__main__.py` daemon + `/healthz` WP 連線檢查 + E2E test on Docker staging + runbooks + capability card

完整六要素 prompt：[docs/task-prompts/phase-1-usopp-publisher.md](../../docs/task-prompts/phase-1-usopp-publisher.md)

## 開源準備

Slice A 的 `WordPressClient` 已 agent-agnostic（無 hardcoded "usopp"），可獨立成 `nakama-wordpress-client` package。配 Capability card 待 Slice C 收工時寫。
