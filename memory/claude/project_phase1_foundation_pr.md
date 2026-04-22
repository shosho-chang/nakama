---
name: Phase 1 foundation PR + implementation notes
description: PR #72 foundation layer merged + 實作發現的 3 個 ADR 沒寫到的細節 + 6 個 high follow-up items
type: project
tags: [phase-1, adr-005a, adr-005b, adr-006, implementation-notes]
created: 2026-04-22
---

## PR #72 合併紀錄

Feature branch `feature/phase-1-infra` → main。`0eb4ff0` commit（合併後 squash 為新 sha）。
Code review by 5 agents (1 Opus + 5 Sonnet + 5 Haiku scorers)，所有 issue 都 < 80 threshold。

## 實作時發現 ADR 沒寫到的 3 個細節

### 1. `GutenbergHTMLV1._ast_and_html_consistent` validator 無限遞迴
**問題**：validator 呼叫 `gutenberg_builder.build()`，`build()` 又實例化 `GutenbergHTMLV1` → 驗證 → build → …
**解法**：`build()` 用 `model_construct()` 繞 validator。builder 本身是 canonical constructor，validator 僅存在於外部手動建構場景（tests、migrations）
**ADR-005a 補**：應在 §2 的 `_ast_and_html_consistent` 附近加備註

### 2. Python sqlite3 implicit transaction 跟 `BEGIN IMMEDIATE` 打架
**問題**：ADR-006 §3 原設計用 `BEGIN IMMEDIATE`，但 Python sqlite3 預設 isolation_level="" 會自動管 transaction；兩者在多 thread shared conn 情境會炸 "cannot commit, SQL in progress" / "cannot rollback, no transaction"
**解法**：改用 SQLite 單一 statement `UPDATE ... RETURNING` + WAL + singleton conn mutex，語意等價於 BEGIN IMMEDIATE 但不跟 Python 打架
**ADR-006 補**：§3 atomic claim 範例改用 `UPDATE...RETURNING`，刪除 `db.transaction(isolation="IMMEDIATE")`

### 3. `reviewer_compliance_ack` 從 payload 到 DB column 要手動 propagate
**問題**：ADR-005b §10 聲明 payload 有此欄位，ADR-006 把它加到 DB schema，但 INSERT 要主動帶才會同步
**解法**：`enqueue()` 用 `getattr(payload_model, "reviewer_compliance_ack", False)` 讀出來寫入 DB column
**ADR-006 補**：§2 enqueue sample 要帶這個欄位到 INSERT

## Code Review 發現的 6 個 borderline items（score 50-75，非 blocker）

1. **Missing PRAGMA at connection-open (score 75)** — ADR-006 §5 checklist 要求 `synchronous=NORMAL` + `busy_timeout=5000` 在 `_get_conn()` 設一次。目前只 `WAL`，`busy_timeout` 只在 `claim_approved_drafts` 逐次設
2. **UnknownPayloadVersionError mid-loop strands claimed rows (score 50)** — 批次中某一 row payload_version != 1 → exception → 其他已 committed claimed 的 rows 孤兒 10 分鐘（等 `reset_stale_claims`）。Phase 1 不會命中（全 V1 schema）
3. **`list` block children 沒限制為 `list_item` type (score 50)** — `BlockNodeV1.children: list[BlockNodeV1]` 無 type constraint。LLM 若產生 `list` → `paragraph` 會默生破 HTML。Phase 1 尚未有 LLM→AST，Phase 2 前要修
4. **`claim_approved_drafts` docstring 過度宣稱 mutex 跨 worker 同步 (score 50)** — 實際跨 process（systemd service）沒共用 mutex，只靠 WAL
5. **compliance-gate docstring 說「filter out」不準 (score 50)** — 實際流程：row 先 committed 為 claimed，才 mark_failed。文字改「transient claim + fail」更準
6. **approval_queue module docstring 說「DB CHECK 由 dict 反向生成」(score 50)** — 實際是 hand-written + test 斷言 parity，不是 generated

## 測試覆蓋

- 39 新 tests（15 builder + 24 queue）
- 全 repo 669 tests pass，無 regression
- Skipped 1：多 thread stress（需要 multiprocess harness 才有意義，production 每 worker 獨立 systemd service）

## 下一輪 work items（Agent B 給的，未做的）

- `shared/gutenberg_validator.py`（ADR-005a §4，round-trip + whitelist + attr JSON 驗）
- `agents/brook/compose.py` + style_profile_loader + tag_filter + compliance_scan
- `agents/usopp/publisher.py` + wp_client + seopress_writer + litespeed_purge + advisory locks
- `shared/schemas/external/wordpress.py` + `external/seopress.py`（anti-corruption）
- Bridge `/bridge/drafts` UI + routes + CLI fallback
- `agents/franky/` 6 模組 + `/healthz` endpoint + weekly digest
- `config/style-profiles/*.yaml` 三個（book-review / people / science）
- `migrations/001_approval_queue.sql`（如果決定轉 yoyo 或獨立 SQL 檔案）

## 判斷依據

- 實作順序：ADR-006 queue (✅) → ADR-005a schema (✅) → ADR-005b publisher → ADR-007 Franky
- baseline 壓測結果 2026-04-23 早上看，RED 就考慮升 8GB VPS
- yoyo-migrations vs executescript：目前採 executescript 跟既有 convention，Phase 1 不卡這個
