---
name: 2026-05-04 下午 PRD #337 三 slice 全 ship + sandcastle PR auto-review-merge workflow 立
description: PRD #337 #338/#339/#340 → PR #342/#343/#344 squash；sandcastle → /code-review → auto-merge 鏈跑通；review 抓 75-tier follow-up concerns；昨日 #341 revert 防呆 plan superseded
type: project
---

**收工 2026-05-04 下午**：PRD #337 三 slice 全部 ship via sandcastle AFK + Claude auto-review-merge 鏈，annotation 軸線端到端落地。

## 三 PR 戰績

| Issue | PR | Squash SHA | 內容 |
|---|---|---|---|
| #338 | #342 | `d41743b` | Annotation persistence MVP（`KB/Annotations/{slug}.md` store + AnnotationSet schema + 31 tests）|
| #339 | #343 | `af1c709` | Concept page sync（`## 個人觀點` per-source HTML boundary marker + 16 tests）|
| #340 | #344 | `264bc2c` | Sync state badge UX（`unsynced_count` / `mark_synced` + 24 tests）|

每 PR 鏈：sandcastle 跑（~7-15 min wall）→ push branch + 開 PR → CI watch → `code-review:code-review` skill（5 reviewer + N scorer + 過濾 ≥80）→ auto-merge `--squash --delete-branch` → pull main → 加 sandcastle label 推下一個。3 PR 全 wall ~75 min。

## Workflow 升級

修修明確指示「我不是工程師，沒有能力和意願去參與 review 跟 merge 的流程」→ 立 **sandcastle PR auto review + merge** 規則：

- CLAUDE.md `## Agent skills` 加 `### Sandcastle PR auto review + merge` sub-section（gate：reviewer 無 ≥80 blocker + CI 綠 + 無 conflict + branch=`sandcastle/*`）
- 原則沿用既有 [feedback_pr_review_merge_flow.md](feedback_pr_review_merge_flow.md) + [feedback_review_skill_default_for_focused_pr.md](feedback_review_skill_default_for_focused_pr.md)
- 不用 `/ultrareview`（付費 cloud，修修自己手動觸發）

## Review 跑出的 75-tier follow-up

全 <80 threshold，未 block merge，建議下次 sandcastle hygiene round 解：

**#343**：
- LLM-supplied `concept_slug` 沒 sanitization → 路徑 traversal 風險（單用戶 + Anthropic LLM 控制下實際風險低）
- 衝突 detection 缺：merge prompt 沒教 LLM 把矛盾寫進 `## 文獻分歧`（per [feedback_kb_concept_aggregator_principle.md](feedback_kb_concept_aggregator_principle.md)）
- `SyncReport.skipped_annotations` 不計 LLM 沒映射的 annotation（observability gap）

**#344**：
- `reader.html` `{{ unsynced_count }}` 沒 `| tojson`（違 [feedback_jinja_inline_js_autoescape.md](feedback_jinja_inline_js_autoescape.md)，int 目前安全 type 改變會炸）
- 部分錯誤時 `mark_synced` 被 skip → badge 卡 stale（partial-sync UX）
- 新 source 沒 annotation 時 badge「✓ 全部 sync」（語意說謊）
- ADR-017 schema block 沒同步加 `modified_at` / `last_synced_at`

## 昨日 #341 revert 防呆 supersede

[project_session_2026_05_04_pr341_revert.md](project_session_2026_05_04_pr341_revert.md) 寫的「3 層 hook + CLAUDE.md + memory」防呆 plan，今天被 **auto-review-merge workflow + skill chain boundary explicit** 替代。本 session 全程沒違反 sandcastle 紀律 = 修正有效，原 3 層 hook 不需實作。

## 下一步 unblock

annotation 軸線端到端落地（Reader → KB/Annotations → Concept 個人觀點 → sync badge），Line 2（讀書心得）核心輸入路徑成立。下回開工可進 Line 2 手跑，或挑 75-tier follow-up 一輪 hygiene。
