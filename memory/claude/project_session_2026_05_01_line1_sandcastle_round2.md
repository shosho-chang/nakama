---
name: 收工 — 2026-05-01 Line 1 Slice 2/3/4 merged + sandcastle round 2 + multi-agent review pattern
description: 三 PR 連 merge (Slice 2 / Slice 3+4 / memory feedback)；sandcastle round 1+2 戰績；multi-agent review 替代 ultrareview 首批實證；Line 1 critical path 4/10 done，等 2026-05-02 FB/IG 樣本 unblock
type: project
created: 2026-05-01
---

修修 2026-05-01 上午用 sandcastle AFK + multi-agent review pattern 推進 Line 1 podcast 軸線。三個 PR 全 merged，建立了「不經由修修接手」最高指導原則的首批實證。

## 三 PR 戰績

| PR | 內容 | LOC | Tests | Review findings |
|---|---|---|---|---|
| **#295** Slice 2 RepurposeEngine | engine + 2 Protocols + Bridge UI 骨架 + ADR-014 | +1937 | 36 | 1 BLOCKER (path traversal) + 5 BORDERLINE + 9 NIT 全修 |
| **#296** memory feedback | feedback_no_handoff_to_user_mid_work.md | +29 | — | — |
| **#297** Slice 3+4 (Line1Extractor + BlogRenderer) | 2 modules + 35 tests | +1937 | 35 | 5 BLOCKER + 7 BORDERLINE + 5 NIT 全修 |

## Multi-agent review 替代 ultrareview（首批實證）

修修 2026-05-01 對 PR #295 ultrareview 提示拒絕 → 拍板「不用經由我來接手」高於品質 > 速度 > 省錢。落地走 3 sub-agent 並行 review pattern：

- **Agent 1**：behavior + edge case correctness（替代 ultrareview behavioral bias）
- **Agent 2**：data/config/contract drift（schema、API 契約、constants reuse、existing pattern alignment）
- **Agent 3**：PRD acceptance + LLM prompt quality（Slice 對齊度、prompt 嚴謹性、ADR 一致性）

兩 PR review 抓到 **17 個真實問題，0 false positive**：
- security blocker (path traversal `..%2F` escape)
- YAML scalar safety (newlines collapse missing per existing repo convention)
- defensive Stage1 dict access (KeyError/IndexError on partial schemas)
- episode_type prompt missing definitions → 下游 IGRenderer routing 風險
- `BLOG_FILENAME` constant duplication
- 等

每次 review ~3 min wall, ~$0.50 cost。零打擾修修。

## Sandcastle round 1 + round 2 實證戰績

Trial #1 (PR #295/#263 等價) — 1 issue, 5 min wall, ~$0.30
Trial #2 (PR #297 等價) — 2 issue 序列 (sequential dep chain #288 → #289), 17 min wall, ~$0.65

**正式採用 → 4/4 通過**（含原 #240/#239 兩 trial）。

### Round 2 教訓（與 runbook 不符）

1. **`gh issue close` 沒被 sandbox 擋** — runbook 第 4 坑說會擋，這次 round 1+2 都成功 close。容器 / gh 配置可能改了。
2. **Sandcastle 留 stale local branch** (`feat/transcribe-stable-ts-swap`) — sandcastle 的 worktree branch 名稱跟內容無關，cleanup 不必要但要意識到。
3. **Sequential dep chain 完全可吃** — round 2 同一容器內 merge-to-head 後解下個 issue，#289 看到 #288 的 commit。maxIter=5 + queue 序列化是天生 fit。
4. **Sandcastle 不會在 issue queue 中 inspect dependency labels** — 但 sandcastle agent 自己會看 issue body `Blocked by` 段判斷適合度（round 2 跳過 #292 因為 #290/#291 needs-info）。

### Round 2 演算法 prompt 設定升級

`E:\sandcastle-test\.sandcastle\main.mts`:
- `maxIterations: 1 → 5`（升級實證乾淨）
- model 維持 sonnet-4-6（足夠寫 protocol-compliant code）

### Sandcastle code 品質 ≈ 70%

- ✅ 架構正確、tests 寫得好、follows existing pattern
- ❌ **盲點**（review 才抓到）：
  - LLM prompt 細節（episode_type 定義 / 字數 prompt-validator drift / blockquote format）
  - Security（path traversal）
  - Schema strictness（meta_description length bound）
  - Constants reuse（hardcode `"blog.md"` instead of `BLOG_FILENAME`）
  - Existing pattern bypass（roll-own loader instead of `style_profile_loader`）

## Line 1 軸線 4/10 done

```
✅ #285 Slice 2  RepurposeEngine + Bridge UI 骨架 (+1937 LOC)
✅ #288 Slice 3  Line1Extractor (Stage 1) (+593 LOC, 15 tests)
✅ #289 Slice 4  BlogRenderer (Stage 2) (+622 LOC, 20 tests)
⏳ #286 Slice 5  FB profile authoring  (needs-info — 等修修 2026-05-02 4 tonal samples)
⏳ #287 Slice 6  IG profile authoring  (needs-info — 等修修 2026-05-02 reference carousels)
⏸ #290 Slice 7  FB renderer            (blocked by #286)
⏸ #291 Slice 8  IG renderer            (blocked by #287)
⏸ #292 Slice 9  CLI orchestrator       (blocked by #290/#291)
⏸ #293 Slice 10 Bridge UI mutation     (blocked by #292; UI 美學留修修做 Claude Design)
```

## 修修回來要做的

1. **2026-05-02 交 FB samples + IG reference carousels** — unblock #286/#287
2. （非必須）**瀏覽器驗收 Bridge UI**：`uvicorn thousand_sunny.app:app --reload` → `/bridge/repurpose` + `/bridge/repurpose/<id>` + nav 有 REPURPOSE 高亮

樣本交完 → sandcastle round 3：#286 + #287 → #290 + #291 → #292 (Bridge UI #293 留修修 Claude Design)。

## 共用 working tree 警告（dual-window 違例）

修修 2026-05-01 上午同時：
- 視窗 A（我）：sandcastle round 1+2 + multi-agent review + 三 PR merge
- 視窗 B（修修）：iter4 stable-ts swap PoC 修 transcriber timestamp bug（`scripts/iter4_test.py` + `scripts/iter4_compare.py` 仍 untracked）

兩視窗共用 `E:/nakama` working tree（**沒開 git worktree**），破了 `feedback_dual_window_worktree.md`。本次運氣好沒 collide（我只改 memory/，他改 transcriber/scripts/）。下次同類 dual-window 必先開 worktree。

## 相關記憶 cross-ref

- [feedback_no_handoff_to_user_mid_work.md](feedback_no_handoff_to_user_mid_work.md) — 最高指導原則（高於品質）
- [feedback_dual_window_worktree.md](feedback_dual_window_worktree.md) — 同機雙視窗必開 worktree
- [reference_sandcastle.md](reference_sandcastle.md) — sandcastle 工具背景
- [feedback_review_skill_default_for_focused_pr.md](feedback_review_skill_default_for_focused_pr.md) — focused PR review 預設
- [project_three_content_lines.md](project_three_content_lines.md) — Line 1 凍結需求
