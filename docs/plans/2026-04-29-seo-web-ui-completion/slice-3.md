## Parent

#255

## What to build

Add `/bridge/seo/posts/{wp_post_id}/audits` standalone surface showing the timeline of all audits run on a single WP post. Read-only; no schema change (`audit_results` already supports multi-row per post per `migrations/006_audit_results.sql:34` — verified 2026-04-29).

## Acceptance criteria

### Storage module

- [ ] `shared/audit_results_store.py` adds `list_audits_by_post(target_site, wp_post_id)` returning all audit rows sorted by `audited_at DESC` (each row carrying `id`, `audited_at`, `overall_grade`, parsed suggestion counts, `review_status`, `approval_queue_id` if exported)
- [ ] Helper extracts `(fail_count, warn_count)` from each row's parsed `suggestions_json`

### Surface

- [ ] `GET /bridge/seo/posts/{wp_post_id}/audits` requires cookie auth (redirect `/login?next=...` if not)
- [ ] If wp_post_id has zero audits, renders empty-state ("尚無 audit 紀錄" + "← 回 SEO 中控台" link)
- [ ] If wp_post_id has audits, renders `templates/bridge/seo_audit_history.html` with a table:
  - audit_id (row.id)
  - audited_at (Asia/Taipei display)
  - grade (row.overall_grade)
  - fail / warn count (from parsed suggestions_json)
  - review status badge: `⏸ pending` / `📝 partial` / `✅ exported #N` (where N = approval_queue_id when exported)
  - actions: `看詳細 →` link to `/bridge/seo/audits/{audit_id}/review`; `+ 重 audit` link triggering existing audit kick-off pre-filled with this post
- [ ] Sort by `audited_at DESC` (latest first)
- [ ] No pagination (assume <30 rows lifetime per post)
- [ ] chassis-nav: `nav_active='seo'`; breadcrumb shows trail back to SEO 中控台
- [ ] Visual: reuse existing `nk-*` design tokens (no new tokens introduced)

### Tests

- [ ] `tests/shared/test_audit_results_store.py` extends — `list_audits_by_post` returns multi-row sorted DESC; severity counter returns correct counts for various suggestion lists
- [ ] `tests/test_bridge_seo_audit_history.py` (new) — GET 200 / 401 / empty-state render / multi-row sort / status badge variations / 看詳細 link to review page
- [ ] `pytest` + `ruff` green

## Out of scope

- Grade trend chart (v2)
- Per-rule diff between two audits (v2)

## Blocked by

- Blocked by #256
- Blocked by #257 (Slice 1 wires the row button on `/bridge/seo` article list pointing here)
