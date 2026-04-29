# PRD: SEO 中控台 v1 (中控台 + audit review + ADR-008 Phase 2a-min)

> Drafted from /grill-with-docs session 2026-04-29.
> 待 file 成 GitHub issue（label: `enhancement`, `needs-triage`）。
> Grilling decision table 在 [`memory/claude/project_seo_control_center_design_2026_04_29.md`](../../memory/claude/project_seo_control_center_design_2026_04_29.md)。

---

## Problem Statement

修修每天 / 每週要從多個入口操作 SEO solution（keyword research / audit / enrich），全是 CLI script 或 conversation-driven skill，沒有 web 化的「集中觀察 + 操作」surface：

- 想看「shosho.tw 100 篇文章哪幾篇 SEO 不及格」要逐一跑 audit，沒地方總覽
- 想看「目前攻擊中的目標關鍵字、最近排名變化」要去 GSC dashboard 看
- audit 結果是純 markdown report，看完要自己把建議搬進 WP，沒 review session 持久化、沒「個人語氣修飾後再 publish」的閉環
- keyword research 在 LifeOS Project dataviewjs 已 work，但「心血來潮跑一份」沒 web 入口

## Solution

在 `/bridge/seo` 新增 SEO 中控台 surface，把 SEO 體檢的「觀察 + 操作」集中：

- **首頁三 section**：(1) WP 文章列表 + 各篇 lazy audit 分數、(2) 攻擊中目標關鍵字（讀 `config/target-keywords.yaml`）、(3) 排名變化（v1 placeholder，v1.1 等 ADR-008 Phase 2a-min 落地後接 db）
- **點文章 → 跑新 audit**：FastAPI BackgroundTask + progress page polling + 完成後 redirect 進 review
- **Y+ 左右對照 review**：左 textarea 文章主體（可編輯，WP REST raw HTML source）、右 audit 建議卡片陣列（per-rule fail/warn = 一張卡片）、卡片上有 `[在左側顯示]` button scroll-to-anchor + 黃底 highlight
- **Review 持久化**：每條 suggestion status (pending/approved/edited/rejected) + edited_value 落 `audit_results.suggestions_json`，永遠 resumable
- **Export 走既有 HITL**：review 完點按鈕 → 收集 approved + edited 條目 → 組 `UpdateWpPostV1` payload → 進 ADR-006 `approval_queue` → 走 `/bridge/drafts` 既有審核流 → publish；**不直接寫 WP**
- **Keyword research UI 觸發點**：中控台「攻擊關鍵字」section 旁 button → 跳 `/bridge/zoro/keyword-research` 新 surface（為 Zoro 將來 trends / SERP 預留 namespace）

## User Stories

1. As 修修, I want to see a list of all my shosho.tw blog posts sorted by SEO grade, so that I can prioritize which to fix first
2. As 修修, I want each blog post row to show its focus keyword + last audit timestamp + grade, so that I know whether the score is fresh
3. As 修修, I want posts that have never been audited to show "—" with a clear "audit me" affordance, so that I know they're missing data, not broken
4. As 修修, I want to click a blog post and see its audit history (past audits with grades + dates), so that I can track improvement over time
5. As 修修, I want to click "跑新 audit" on a blog post and see a progress indicator while it runs, so that I'm not stuck on a frozen page for 30-60s
6. As 修修, I want the audit progress page to auto-redirect me to the review page when done, so that I don't need to manually refresh
7. As 修修, I want clear error messaging on the progress page when audit fails mid-pipeline (which step + why), so that I can retry or investigate
8. As 修修, I want the review page to show the article body on the left (editable) and audit suggestion cards on the right, so that I can compare and edit in place
9. As 修修, I want each audit suggestion card to show rule_id, severity, current value, suggested value, and rationale, so that I understand why it's flagged
10. As 修修, I want to click `[在左側顯示]` on a suggestion card and have the left textarea scroll to the relevant text + briefly highlight in yellow, so that I can find what to edit
11. As 修修, I want to approve / edit / reject each suggestion individually, so that I can choose which advice to follow
12. As 修修, I want to edit suggestion text in my own voice before approving (inline textarea), so that the published version sounds like me, not the LLM
13. As 修修, I want my review progress to persist across sessions, so that I can review 30 conditions on Monday and continue Wednesday without losing state
14. As 修修, I want to export my reviewed audit (approved + edited only) as a single payload to the existing approval_queue, so that publish governance still goes through ADR-006 HITL gate
15. As 修修, I want "Export to queue" to be disabled until at least 1 suggestion is approved or edited, so that I don't accidentally export an empty payload
16. As 修修, I want to see the list of target keywords from `config/target-keywords.yaml` on the dashboard, so that I know what we're attacking
17. As 修修, I want each target keyword row to show its attack URL + goal_rank + (v1.1) current rank + impressions, so that I can spot which need attention
18. As 修修, I want a clear placeholder for "rank change over time" in v1, with explanation that it activates after Phase 2a-min, so that I have realistic expectations
19. As 修修 (v1.1), I want to see 28-day rank change for each target keyword (current vs prev 28 days), so that I can detect rising or sinking keywords
20. As 修修, I want a "找新關鍵字" button next to the target keywords section, so that I can run keyword-research without leaving the dashboard
21. As 修修, on `/bridge/zoro/keyword-research`, I want to fill topic + content_type (blog/youtube) + optional en_topic and submit, so that I can trigger Zoro's research from the UI
22. As 修修, after keyword research completes, I want to see the markdown report inline + a "下載 .md" button, so that I can save it without writing to vault from the UI (vault path stays exclusive to LifeOS Project dataviewjs)
23. As 修修, I want the SEO 中控台 link in the bridge chassis-nav (after `DRAFTS`), so that I can reach it with one click from any bridge page
24. As 修修, I want all SEO 中控台 surfaces to require the same `nakama_auth` cookie as other bridge surfaces, so that auth consistency is preserved
25. As 修修 (Phase 2a-min), I want a daily cron to pull GSC rows into local db, so that rank-change queries don't hit GSC API on every page load
26. As 修修 (Phase 2a-min), I want the cron to be idempotent (re-runs same day write same rows), so that retries don't corrupt data
27. As future contributor, I want each module (audit_results_store / wp_post_lister / audit_runner / gsc_rows_store) to have a short, deep interface, so that they can be tested in isolation
28. As future contributor, I want the audit pipeline (`seo-audit-post` skill / scripts) untouched, so that the web wrapper is purely additive

## Implementation Decisions

**Agent ownership** (per ADR-012):
- audit + enrich → Brook
- keyword research → Zoro
- `shared/seo_audit/llm_review.py` 既有 `set_current_agent("brook")` 對齊；`seo-keyword-enrich` 的 LLM call follow-up 同步補

**UI surface routing**:
- topic-rooted `/bridge/seo` for control center（沿用 `/bridge/drafts` / `/bridge/memory` 慣例）
- agent-rooted `/bridge/zoro/keyword-research` for keyword research（為 Zoro 將來 trends / SERP 預留 namespace）

**Frontend stack** (no new dep):
- vanilla JS + Jinja2 templates（no SPA / no build step / no TypeScript）
- form post + 303 redirect 範式（PR #140 凍結，bridge mutation 一致）
- review 頁用 textarea + native `<dialog>` + scroll-to-anchor JS（無 editor library）

**WP article source**:
- WP REST `/wp/v2/posts?per_page=100` live pull + 1h server-side TTL cache
- review 頁的「左邊主體」用 WP raw HTML（Gutenberg block 結構保留以便寫回）

**Audit kick-off**:
- FastAPI BackgroundTasks（不引入 worker queue）
- progress page polling status endpoint 每 2s
- 完成後 auto redirect to review page

**Audit result schema**:
- `audit_results` table，主鍵 `(target_site, wp_post_id)`
- `url` secondary index 給外站 audit / non-WP fallback
- `suggestions_json` 整包 `list[AuditSuggestionV1]`（含每條 status / edited_value / reviewed_at）
- `review_status` enum: `fresh` / `in_review` / `exported` / `archived`

**Review semantics**:
- Per-rule 粒度（一條 fail / warn = 一張卡片；pass / skip 不出現）
- Approve = 純 marking（不寫 WP）
- Edit = 改 `edited_value`，export 時用 edited_value 替換 suggested_value
- Reject = 排除出 export
- 持久化直接寫進 `audit_results.suggestions_json`，無另開 session 表

**Export to approval_queue**:
- 收集所有 approved + edited suggestions → 組單一 `UpdateWpPostV1` payload
- Insert 進既有 ADR-006 `approval_queue`
- Reviewer 在 `/bridge/drafts` 看到 → 走既有 HITL → publish
- Trigger gate: "Export" button 只在 `>= 1 suggestion in (approved, edited)` 時 enabled

**Schema additions** (in `shared/schemas/seo_audit_review.py`):
- `AuditSuggestionV1` (rule_id / severity / title / current_value / suggested_value / rationale / status / edited_value / reviewed_at)
- `AuditReviewSessionV1` (audit_id / post_url / target_site / audited_at / overall_grade / suggestions / exported_to_approval_queue / approval_queue_id)

**Migrations**:
- `004_audit_results.sql` — Phase 1 SEO 中控台 v1
- `003_seo_observability.sql` — Phase 2a-min（gsc_rows，per ADR-008 §2 schema）

**Phase 2a-min cron** (`gsc_daily`):
- Daily cron pulls 7d GSC rows × `target-keywords.yaml` keywords → UPSERT
- Idempotent re-run（PRIMARY KEY `(site, date, query, page, country, device)` per ADR-008 §2）
- **不含** alert rules / weekly digest（那是 Phase 2a-full，不在本 PRD）
- Date math: `end_date = today - 4` (GSC delay 2-4 天 hard knowledge per ADR-008 §2)

**Rank change query** (v1.1):
- `gsc_rows_store.rank_change_28d(keyword, url) -> {current_avg_pos, prev_avg_pos, delta, current_impressions}`
- Page load 直接 query db（無快取需求；本地 sqlite）

**Bridge chassis-nav**:
- 在 `DRAFTS` 後加 `<a href="/bridge/seo">SEO <span class="zh">優化</span></a>`
- bridge index 暫不加 readout cell（保持精簡）

**Auth**:
- 與其他 bridge surface 共用 `nakama_auth` cookie
- Page route 走 `page_router`（cookie → 302 redirect to `/login?next=...`）
- API route 走 `router` with `Depends(require_auth_or_key)`

## Testing Decisions

**測試原則**：external behavior（function input/output、DB row state、HTTP response）為主；不測 implementation details（SQL string、internal helper signature）。

**Deep modules** (integration tests with real sqlite + mocked external):

| Module | 測什麼 |
|---|---|
| `audit_results_store` | insert / get / list / per-suggestion update / multiple audits per post (upsert + history) |
| `wp_post_lister` | list pull + cache hit + cache TTL expiry + WP API error fallback |
| `audit_runner` | mocked subprocess + parsing audit script output → `AuditSuggestionV1` list + persistence + error mapping |
| `gsc_rows_store` (Phase 2a-min) | UPSERT idempotency + window queries (28d / prev-28d / delta) + filter by site / keyword |
| `gsc_daily` cron (Phase 2a-min) | mocked GSC API + assert correct rows written + retry on 429 + idempotent re-run |

**Routers** (TestClient):
- Each endpoint: 1 happy + 1-2 sad (404 / 409 conflict / 422 form validation)
- Auth gate: unauth → 302 redirect to `/login?next=...`
- BackgroundTask kick-off: assert task dispatched (不測完整 audit 跑完，那是 `audit_runner` 的事)

**Templates**: smoke only — render with fixture context，assert no exception + 重要 element exists (grid / form / button id)。

**Prior art reference**:
- `tests/agents/brook/test_compose*.py` — schema regression test pattern (snapshot byte-identical)
- `tests/thousand_sunny/test_bridge_drafts*.py` — bridge mutation TestClient form post + 303 + DB row assertion
- `tests/shared/test_approval_queue*.py` — schema migration + state transition test

**不測**:
- Migration files（migration runs = test）
- FastAPI BackgroundTasks plumbing（framework internal）
- Vanilla JS scroll-to-anchor / dialog open（visual / DOM behavior，超出 unit test 範疇 — 留 manual smoke）
- WP REST API contract（external, 外部 API 變動我們不能測）

## Out of Scope

- **Auto-rerun audit on stale entries** (cron-driven scoring) — 留 Phase 2 觀察期後評估
- **Bulk audit**（一次跑 100 篇）— v1 只支援 single-post trigger；bulk 需 worker queue 基礎建設
- **Audit history graph / trendline**（per-post 歷次 grade 趨勢圖）— v1.1 後評估
- **GA4 / Cloudflare 整合** — ADR-008 Phase 2b/c 範疇
- **Phase 2a-full**（alert rules + weekly digest GSC 段） — 後續 PR
- **Keyword research result archive surface**（過去跑過的 keyword research 列表 / search）— v1 只跑單次顯示，archive 走 LifeOS Project file 既有路徑
- **Brook compose 整合 web UI** — round 3 grilling 拍板「不做新 UI」（chat / skill chain 已 cover）
- **Multi-reviewer permissions** — 沿用既有 ADR-006 hard-coded `shosho` 單一 reviewer
- **WP raw HTML 改良 editor**（WYSIWYG / Gutenberg block 視覺化）— v1 純 textarea；如卡了再評估升 htmx / 引入 editor lib
- **`target-keywords.yaml` 編輯 UI**（add / remove / set goal_rank from bridge）— v1 只讀；Zoro / Usopp 寫入路徑既有，不需 UI 改造
- **跨站 audit aggregation**（`fleet.shosho.tw` 分開列）— v1 兩站合一列以 `target_site` column 區分；split view v1.1 後可加

## Further Notes

- **ADR-012 已寫**：`docs/decisions/ADR-012-zoro-brook-boundary.md` 凍結 Zoro / Brook = 向外 / 對內 分界
- **CONTEXT-MAP.md 已 inline 更新**：Zoro / Brook bullet + `SEO solution` / `SEO 中控台` / `audit review session` 名詞段
- **Sequencing**：本 PRD covers 階段 1（SEO 中控台 v1）+ 階段 2（ADR-008 Phase 2a-min cron + db）+ 階段 3（v1.1 接 db 顯示排名變化）；階段 4（Phase 2a-full alert + digest）/ 階段 5（GA4 + Cloudflare）不在本 PRD
- **ADR-009 D5 既有 Brook compose `seo_context` 整合不動**（已 production）
- **`seo-audit-post` skill / pipeline 既有不動**：本 PRD 只在外圍加 web wrapper（`audit_runner` subprocess invokes）+ structured suggestion list 解析
- **Phase 1 / Phase 2a-min 前置已滿足**：ADR-007 Phase 1 完工 + Franky news digest 上線 + GSC service account 已建（ADR-009 Open Items #3 reuse `nakama-monitoring` GCP project + `nakama-franky` service account）
- **單篇 audit wall-clock**: ~30-60s; single-post UX 用 BackgroundTasks 不引入 worker；如未來 bulk audit 需求出現再補 worker
