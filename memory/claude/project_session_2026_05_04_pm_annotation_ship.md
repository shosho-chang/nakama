---
name: 收工 — 2026-05-04 下午 PRD #337 三 slice ship
description: PR #342/#343/#344 全 merged + ADR-017 凍結 — Line 2 critical path blocker（annotation 沒 owner）解掉；Reader 本機 only，VPS 改動只 vault_rules 純新增
type: project
created: 2026-05-04
---

從早上七層架構凍結 → 下午把 Line 2 critical path 第一個 blocker 解掉。修修明確要求 5/4 開始手跑 Line 2 讀書心得，annotation 永久存活 + cross-source aggregate 是必要前置。

## 1. 三 PR 全 merged

- **PR #342** (`d41743b`) — annotation persistence MVP：`shared/annotation_store.py` + `KB/Annotations/{slug}.md` schema + Reader endpoint 改寫不再 mutate 原檔
- **PR #343** (`af1c709`) — sync to Concept page：`agents/robin/annotation_merger.py` 用 boundary marker per-source full replace，merge 進 `## 個人觀點` section
- **PR #344** (`264bc2c`) — sync state badge UX：`unsynced_count` / `mark_synced` / Reader header badge

3 PR 全 auto code-review 綠（無 follow-up flag）。

## 2. ADR-017 凍結

`docs/decisions/ADR-017-annotation-kb-integration.md` — 10 題 grill 結論：詞彙分（Highlight vs Annotation）/ Annotation 進獨立 section / 物理獨立檔 / Highlight asymmetric / re-sync = full-replace per-source / boundary marker 機制。

## 3. VPS deploy = vault_rules only

Robin = Reader = 本機 only（VPS `DISABLE_ROBIN=1`，see [feedback_compute_tier_split](feedback_compute_tier_split.md)）。VPS thousand-sunny restart 後實質生效改動只有 `shared/vault_rules.py` 純新增 `assert_reader_can_write` + `READER_WRITE_WHITELIST`，不破壞 Nami 既有 API；nakama-gateway 不需 restart。

VPS HEAD = `264bc2c`，service active 2026-05-04 14:34 Taipei restart clean。

## 4. 結構性 unblock

[CONTENT-PIPELINE.md](../../CONTENT-PIPELINE.md) 觀察 #3「Annotation 沒 owner（Line 2 critical path）」由此 PRD 解掉。Stage 2 閱讀註記 → Stage 3 整合的銜接路徑就位：Reader 標 annotation → 同步按鈕 → Concept page `## 個人觀點` aggregate。

## 5. 下個 session 起手（修修手跑 Line 2）

工程都就位，下個 session 重點是修修**真實使用 Reader**：

**起手 sanity check 三件事**（早上 memo 已列）：
- Robin Reader 開中文 EPUB（**未實測**）
- `/project-bootstrap` 開「讀書心得」project
- EPUB 雙語 reader 能讀英文書嗎

**手跑流程**：選書 → Reader 讀（中/英）→ 邊讀邊 annotation → 按 sync → 在 Project 頁面手寫心得（Stage 4 LLM 不介入）→ 痛點記 Slack DM Nami 或 vault Inbox

**禁止 over-design**（早上原則保留）：等修修手跑痛點浮現再 grill 凍結 next feature；不在手跑前設計 reading session 邊界、不先 build 統整功能、不先想 Stage 5 怎麼接。

## 6. Out of scope（已歸檔）

- Stage 4 心得 outline 工具：deferred
- Reading session 邊界：first-class 概念延後
- Migration script：vault 0 條 inline annotation 沒東西搬
- HITL approval queue：個人 vault 不套對外發布 gate
- Highlight 進 Concept page：Q4 凍結 asymmetric

## Reference

- [project_session_2026_05_04_pipeline_arch.md](project_session_2026_05_04_pipeline_arch.md) — 早上七層架構凍結
- ADR：[docs/decisions/ADR-017-annotation-kb-integration.md](../../docs/decisions/ADR-017-annotation-kb-integration.md)
- PRD：GH issue #337 (closed)
- [feedback_compute_tier_split.md](feedback_compute_tier_split.md) — Reader 本機 only
