---
name: 早報 — 2026-05-06 overnight 4 issue sandcastle 全 ship + PR sync 收尾
description: 修修睡覺期間我獨力收 PR sync (#419/#435/#397/#398/#399 全 squash) + sandcastle batch ship #431/#432/#433/#434 4 個 issue 全 merged；Line 2 digest + hybrid retrieval engine + wikilink lane + ground truth signal 全產線就緒
type: project
created: 2026-05-06
---

修修 5/5 23:00 去睡前授權「四個都讓你用 sandcastle 直接執行完畢，切記不能爆主線的 context」。AFK 自主 run，5/6 02:51 全收工。

## 1. PR sync 收尾（睡前 stale 5 條）

| PR | 動作 | head | merged |
|---|---|---|---|
| #435 5/5 late memory + handoff | direct squash | — | ✅ 6872ef0 |
| #419 Slice 5 v2 wiring | update-branch + squash | — | ✅ e6863ef |
| #397 Zotero Slice 1 | update-branch + squash | — | ✅ 3e2380c |
| #398 Zotero Slice 2 | manual rebase + force push + cancel/rerun stuck CI + squash | 0d02a23 | ✅ 941b2a8 |
| #399 Zotero Slice 3 | manual rebase（skip 2 memory commits）+ force push + squash | 006b616 | ✅ 717f0ff |

**主要踩坑**：CI stuck at "Install ffmpeg" step 多次（≥31 min unmoved）— `gh run cancel + rerun` 即可解。固定模式：拿到 BEHIND PR 後 `update-branch` → CI 跑 → 若 stuck 21+ min 直接 cancel + rerun。

## 2. Sandcastle batch ship 4 issue

| Run | Branch | Iter | Wall | Issue(s) | PR | merged |
|---|---|---|---|---|---|---|
| 1 | sandcastle/issue-431 | 1 | ~20 min | #431 | #436 | ✅ 20d8c85 |
| 2 | sandcastle/issue-432-433 | 2 | ~50 min | #432, #433 | #437 | ✅ a9de2a1 |
| 3 | sandcastle/issue-434 | 1 | ~25 min | #434 | #438 | ✅ ce7cbeb |

全 sonnet-4-6、merge-to-head、TDD red→green 守住、ruff 全綠、no regression。**4/4 sandcastle 全成功，0 escalation**。

### 各 issue 落地

**#431 Hybrid retrieval engine Phase 1a**（PR #436）— `shared/kb_indexer.py` + `shared/kb_embedder.py`（model2vec potion-base-8M lazy-load 256d）+ `shared/kb_hybrid_search.py`（BM25 + vec0 RRF k=60）+ `agents/robin/kb_search.py` 加 `engine="haiku"|"hybrid"` flag（既有 caller 零改動）。53 tests、RRF math 手算驗證、mtime_ns 增量 OK。

**#432 Book digest writer**（PR #437 內第 1 commit `bbde16d`）— `agents/robin/book_digest_writer.write_digest(book_id)` 機械整理頁、`purpose="book_review"` framing、Reader sync 端點 background dispatch 加 `digest_status: "queued"`。23 tests。

**#433 Wikilink lane Phase 1b**（PR #437 內 `1ced41c` + `871a2a1`）— `kb_wikilinks` 表 + `_wikilink_lane()` 1-hop 鄰居展開 + 3-lane RRF（`lanes=("bm25","vec","wikilink")` opt-in）+ `_normalize_wikilink()` + capability card 更新。32 tests。

**#434 👍/👎 ground truth signal**（PR #438）— `migrations/013_kb_search_feedback.sql` + `shared/kb_search_feedback_store.py`（`upsert_feedback()` ON CONFLICT DO UPDATE idempotent）+ `book_digest_writer` 加 `[ ] 👍/[ ] 👎` checkbox + `<!-- fb: cfi=... path=... -->` markers + `parse_existing_feedback()` 抽 (item_cfi, hit_path, signal) → upsert → re-render 保留標記。17 tests。

## 3. 等下次起手 manual smoke（HITL）

四個 issue 都有 manual smoke acceptance 待修修親手驗證：

- **#431**：`python -c "from agents.robin.kb_search import search_kb; print(search_kb('過度訓練', vault_path, engine='hybrid'))"` 看 top-10 hits 合理
- **#432**：讀一本 EPUB → 標 5 H/A + 1 C → POST annotations → 5-15 秒後 vault 出現 `KB/Wiki/Sources/Books/{id}/digest.md`
- **#433**：對比 `lanes=("bm25","vec")` vs `lanes=("bm25","vec","wikilink")`，看 wikilink lane 拉的 page 是否真補上「結構性 co-mention」
- **#434**：標 3 條 digest hits（2 👍 + 1 👎）→ sync → DB 新增 3 row + digest 重生保留 `[x]`

## 4. 現場狀態

- HEAD: `ce7cbeb` (PR #438 squash 後)
- main 與 origin/main 同步，working tree clean（除 `.claude/worktrees/` 既有 untracked）
- 本地殘 branch：`pr-398-rebase` / `pr-399-rebase` 已刪
- **全 5/5 stale PR + 全 4 個 PRD slice 全 ship，line 2 critical path unblocked**
- vault 仍 845 pages 8.45x zaferdace 警戒線；hybrid retrieval engine 已 ready 但 default 仍 `engine="haiku"`，PRD §S5 條件 swap 待觀察 1-3 個月後再開單獨 PR

## 5. 下個 session 起手

**先做 manual smoke**（§3）— 4 個 issue 都需要修修親手驗證才算真的 ship。manual smoke 通過後才進下一個 PRD。

PRD #430 子任務 5 個 slice：S1/S2/S3/S4 已落地，**S5（engine default swap from haiku to hybrid）defer**等 1-3 個月實證後再開。

## 6. 教訓

### 6.1 Sandcastle 4/4 通過 — multi-issue batch + sonnet-4-6 仍站得住

PR #426 trial 4（5/5 first multi-issue batch / 3 issue）+ 本次 trial 5（5/6 / 4 issue 三輪）→ **5/5 trials 通過，採用穩固**。其中 #431（600 LOC、最大 issue）sonnet-4-6 一輪解完 53 tests 全綠 — 容量擔心解除。

### 6.2 PR title lint 不接 `<scope>+<scope>`

第一次 #437 PR title 寫 `feat(reader+kb-hybrid): ...` lint-pr-title 失敗（pattern `<type>(<scope>): <desc>` scope 只允許 `[a-z\-]+`）。修：改 `feat(reader): ...` 一個 scope。

### 6.3 GH Actions ffmpeg apt install 偶發 stuck

3/5 PR CI run 卡 "Install ffmpeg" step 21+ min — `gh run cancel + gh run rerun` 後新 run 4-7 min 內過。原因不明可能是 GH runner pool 暫時健康度差。**模式**：CI 過 20 min 仍 pending → cancel rerun 一次。

## Reference

- PRD：https://github.com/shosho-chang/nakama/issues/430
- PR #436 (#431)：https://github.com/shosho-chang/nakama/pull/436
- PR #437 (#432, #433)：https://github.com/shosho-chang/nakama/pull/437
- PR #438 (#434)：https://github.com/shosho-chang/nakama/pull/438
- [project_session_2026_05_05_late_line2_digest_prd.md](project_session_2026_05_05_late_line2_digest_prd.md) — 直接前置：grill 凍結 + PRD ship
- [reference_sandcastle.md](reference_sandcastle.md) — sandcastle runbook（待補 5/6 trial 5 結果到 trial table）
- [CONTENT-PIPELINE.md](../../CONTENT-PIPELINE.md) Stage 2→3 Line 2 critical path
