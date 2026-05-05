---
name: PR sync sweep handoff — 2026-05-05 late（compact 前狀態）
description: 本 session 起手做 PR sync sweep，做到一半 compact；下次起手繼續：等 #419/#435/#397 CI green → squash → 處理 #398/#399 stack rebase → dispatch #431 sandcastle
type: project
created: 2026-05-05
---

修修 compact 前 sync 狀態。下次起手讀這個續做。

## 已完成

- ✅ **PR #425 squash merged** (oid `99f8d4a1`) — 5/5 evening Reader QA memory
- ✅ **PR #410 closed** — 內容已被 ed92641 retrofit，squash 反而會刪掉 main 上 6 行後續 entries
- ✅ **PR #418 closed** — main 上 PR #428 的 Queued↔Cancel toggle 已涵蓋誤觸救濟，confirm modal 冗餘
- ✅ **PR #397 rebase 衝突解** — MEMORY.md 1 處 conflict 解（用 SUPERSEDED 版替換 grill 版 + 跳過 epub_reader_prd / feedback_auto_mode 重複行，保 main 全部後續 entries），4 commits force-push 成功，新 head = `2a2852f`

## 等 CI green 後 squash（CI 進行中時 compact）

| # | branch | 狀態 |
|---|---|---|
| **#419** | fix/slice-5-wiring-v2-dispatch | lint-and-test IN_PROGRESS（critical prod fix，145+/69-）|
| **#435** | docs/memory-2026-05-05-late-line2-digest | lint-and-test QUEUED（5/5 late memory）|
| **#397** | feat/zotero-pivot-slice-1 | lint-and-test IN_PROGRESS（rebase 後重跑）|

下次起手：

```bash
gh pr view 419 --json mergeStateStatus,statusCheckRollup
gh pr view 435 --json mergeStateStatus,statusCheckRollup
gh pr view 397 --json mergeStateStatus,statusCheckRollup
```

mergeStateStatus = CLEAN 即可 squash：

```bash
gh pr merge <id> --squash --delete-branch
# 每 squash 一個其他變 BEHIND，下個要 update-branch + 等 CI 重跑
```

## 待處理：Zotero stack #398 / #399

兩個 PR 的 `baseRefName = main`（不是真 stacked），但 head branch 含 #397 舊 hash 的 commits。

#397 squash merged 後：
- #398 head 跟新 main 比 diff 會包含 #397 內容（patch 已 in main，hash 不同）→ 需 rebase --onto
- #399 同上

預期路徑：
1. Squash #397
2. `git fetch origin main`
3. `gh pr update-branch 398` — 看 GH 自動 rebase 是否解（patch 已 in main 應該 auto-skip）
4. 失敗就 manual：`git rebase --onto origin/main <#397-old-head> feat/zotero-slice-2-pdf-fallback`，然後 force-push
5. 等 CI green → squash #398
6. #399 同樣流程

依 feedback_stacked_pr_squash_conflict（memory 187）：沒 force-push 權限走 merge-main + --ours 退路；我有 force-push 權限可正規 rebase。

## 待處理：dispatch #431 sandcastle

修修 compact 後續任務（依 5/5 late session memory）：

- **issue #431**：S1 Hybrid retrieval engine Phase 1a — kb_indexer + kb_embedder + kb_hybrid_search + engine flag
- 起手 dispatch via **sandcastle single-worktree AFK**（依 feedback_phase3_single_worktree_proven）
- 工程量 ~600 LOC，sandcastle 一輪可完成
- 起手前 verify branch（不在 main，依 feedback_dual_window_worktree）

Sandcastle template / runbook：
- `docs/runbooks/sandcastle.md`
- `docs/runbooks/sandcastle-templates/`（凍結 templates，依 reference_sandcastle.md）

依 feedback_minimal_subagent_prompt（memory 已 retrofit 進 main）：subagent prompt 不要 P7 完整報告（5-10k 字），改成 commit hash + 一句 trade-off。

## 現場狀態

- 本地 main HEAD = `ed92641`（PR #435 commit），origin/main HEAD = `99f8d4a1`（#425 merge）— 兩者 diverge
- 沒做 `git reset --hard origin/main`（被 deny rule 擋）— #435 squash 後自然 catch up
- working tree clean except `.claude/worktrees/` untracked
- 本地殘 branch：`pr-410-tmp` / `pr-410-rebase` / `pr-418-tmp` / `pr-419-tmp` / `pr-425-tmp` / `pr-397-tmp` — 都可清（不需保留）

## Reference

- 5/5 late session memory：[project_session_2026_05_05_late_line2_digest_prd.md](project_session_2026_05_05_late_line2_digest_prd.md)
- PRD：https://github.com/shosho-chang/nakama/issues/430
- S1 issue：https://github.com/shosho-chang/nakama/issues/431
