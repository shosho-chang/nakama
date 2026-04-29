## Parent

#255

## What to build

Persist keyword-research results to a new `keyword_research_runs` table with `triggered_by='web'` after each successful web-UI run. Add `查看歷史` button on the result section of `/bridge/zoro/keyword-research`. Implement list page `/bridge/zoro/keyword-research/history` and detail page `/bridge/zoro/keyword-research/history/{id}`.

LifeOS dataviewjs path stays unchanged (no db write from there in this slice).

## Acceptance criteria

### Schema

- [ ] `migrations/007_keyword_research_runs.sql` exists with the table per PRD (id PK auto, topic NOT NULL, en_topic nullable, content_type CHECK in (blog, youtube), report_md NOT NULL, created_at NOT NULL, triggered_by CHECK in (web, lifeos))
- [ ] `shared/state.py` has matching CREATE TABLE for dual-source consistency (existing pattern)
- [ ] Both indices created: `idx_keyword_research_created_at`, `idx_keyword_research_topic`

### Storage module `shared/keyword_research_history_store.py` (new)

- [ ] `insert_run(topic, en_topic, content_type, report_md, triggered_by)` → returns id
- [ ] `list_runs(limit=20, offset=0)` → list of dicts sorted by created_at DESC
- [ ] `count_runs()` → total int
- [ ] `get_run(run_id)` → dict or None

### POST side-effect (`bridge_zoro.py`)

- [ ] After `POST /bridge/zoro/keyword-research` successfully renders a report, the run is inserted with `triggered_by='web'`
- [ ] If insert fails, log the error but DO NOT break the user-facing render (best-effort persist)

### List page

- [ ] `GET /bridge/zoro/keyword-research/history` requires cookie auth (redirect `/login?next=...` if not authed)
- [ ] Renders `templates/bridge/zoro_keyword_research_history.html` with paginated rows (20/page) showing: topic / en_topic / content_type / created_at (Asia/Taipei display)
- [ ] Each row links to detail page
- [ ] Pagination via `?offset=N` query param; "Previous" / "Next" links shown only when applicable
- [ ] chassis-nav: `nav_active='zoro'`; breadcrumb shows trail back to keyword-research form

### Detail page

- [ ] `GET /bridge/zoro/keyword-research/history/{id}` requires cookie auth
- [ ] Renders `templates/bridge/zoro_keyword_research_history_detail.html` with full markdown (marked + DOMPurify, same pattern as existing zoro_keyword_research.html result phase)
- [ ] Includes server-side download form (POST to existing `/bridge/zoro/keyword-research/download` endpoint with hidden form fields holding report_md)
- [ ] 404 if id not found
- [ ] chassis-nav: `nav_active='zoro'`; breadcrumb shows trail back to history list

### Result page button

- [ ] `zoro_keyword_research.html` result section adds a `查看歷史 →` button (next to `+ 新研究`) linking to `/bridge/zoro/keyword-research/history`

### Tests

- [ ] `tests/shared/test_keyword_research_history_store.py` — round-trip insert/list/get/count with sqlite in-memory state
- [ ] `tests/test_bridge_zoro_history.py` — list/detail GET 200/401, pagination, 404, ZORO chassis-nav active, breadcrumb rendered
- [ ] `tests/test_bridge_zoro.py` extends — POST keyword-research succeeds → db has new row with `triggered_by='web'`; if insert fails (mock RuntimeError), user still sees report
- [ ] `pytest` + `ruff` green

## Out of scope

- LifeOS dataviewjs writing to db (LifeOS path unchanged this round)
- Backfilling existing vault .md files into db
- Detail page edit / delete actions (read-only this round)

## Blocked by

- Blocked by #256
