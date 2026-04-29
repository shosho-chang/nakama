**Category:** enhancement (multi-slice PRD)

## Problem Statement

SEO 中控台 v1 (PRs #226 → #250) closed the audit→review→approval_queue→publish loop, leaving three UI debt items:

1. **B′** — `aria-current="page"` on `/bridge/zoro/keyword-research` mis-attributes the current page to SEO entry (#245). chassis-nav also has no ZORO top-level entry → screen-reader UX is wrong; Zoro namespace is invisible in nav.
2. **A′** — keyword-research results are render-and-forget. The web UI trigger doesn't persist (vault writes forbidden by issue #231 acceptance); LifeOS path persists to vault but web UI can't read it. No way to find last week's research without leaving the bridge surface.
3. **E** — `audit_results` already stores multi-row per post (PK `id INTEGER AUTOINCREMENT` per `migrations/006_audit_results.sql:34` — verified 2026-04-29; no schema change needed) but `/bridge/seo` article list only shows the latest grade; historic audits and review-status timeline are not surfaced.

## Solution

User-facing:

1. chassis-nav grows a `ZORO 偵察` top-level entry beside `SEO 優化` / `FRANKY 船匠` (agent-rooted lane). Zoro surfaces add a breadcrumb above page-header to express the journey trail; `aria-current` strictly aligns with URL (URL-keyed, not journey-keyed).
2. `/bridge/zoro/keyword-research` after a successful run auto-saves to a new `keyword_research_runs` table with `triggered_by='web'`. New `/keyword-research/history` list + detail surfaces let the user browse and re-download any past report.
3. `/bridge/seo` article list rows grow a `查看歷史` button → standalone `/bridge/seo/posts/{wp_post_id}/audits` surface showing all past audits, grade, fail/warn counts, and review status (pending / partial / exported #N).

## User Stories

1. Screen-reader user on `/bridge/zoro/keyword-research` hears "ZORO, current page" instead of "SEO 優化, current page" — knows the actual namespace
2. User landing on Zoro surface from SEO 中控台 sees a breadcrumb above page-header: `← /bridge/seo · 找新關鍵字 → ZORO · KEYWORD RESEARCH` — knows the trail
3. User direct-URLing to a Zoro surface still sees the breadcrumb (always-shown, not referrer-detected) — UX consistency
4. chassis-nav ZORO entry is visually identical to FRANKY / SEO (same tokens, hover, focus)
5. Clicking ZORO chassis-nav entry lands on `/bridge/zoro/keyword-research` directly (no hub page; Zoro currently has only one web surface)
6. After web UI keyword-research finishes, the run auto-saves to db without manual save action
7. User looking for last week's "間歇性斷食" research enters history page → sees list → clicks into detail → reads full markdown
8. History list shows topic / en_topic / content_type / created_at, sorted by created_at DESC, paginated 20/page
9. History detail renders full markdown (marked + DOMPurify) with re-download .md button
10. LifeOS dataviewjs trigger does NOT auto-write db — that path stays unchanged this round
11. SEO 中控台 article row gets a `查看歷史` button beside `跑新 audit`
12. audit history surface shows time-ordered table: audit_id / audited_at / grade / fail / warn / review status / actions
13. Click `看詳細` → existing `/bridge/seo/audits/{id}/review` (reuse Y+ tier review UI)
14. Review status badge distinguishes `⏸ pending` / `📝 partial` / `✅ exported #N`

## Implementation Decisions

### Modules

**Backend:**
- `migrations/007_keyword_research_runs.sql` (new)
- `shared/state.py` (add `keyword_research_runs` schema, dual-source pattern)
- `shared/keyword_research_history_store.py` (new — list/insert/get/count)
- `shared/audit_results_store.py` (add `list_audits_by_post`)
- `thousand_sunny/routers/bridge_zoro.py` (add `/history` + `/history/{id}`; existing POST adds db side-effect)
- `thousand_sunny/routers/seo.py` (add `/posts/{wp_post_id}/audits`)

**Frontend (templates):**
- `_chassis_nav.html` (new partial — Slice 0 refactor)
- `_breadcrumb.html` (new partial)
- `zoro_keyword_research.html` (use partial; add breadcrumb; add 查看歷史 button on result)
- `zoro_keyword_research_history.html` (new — list view)
- `zoro_keyword_research_history_detail.html` (new — detail view)
- `seo.html` (use partial; add 查看歷史 button on row)
- `seo_audit_history.html` (new — table view)
- ~9 other bridge templates (use partial via include)

### Schema

```sql
-- migrations/007_keyword_research_runs.sql
CREATE TABLE IF NOT EXISTS keyword_research_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    topic        TEXT NOT NULL,
    en_topic     TEXT,
    content_type TEXT NOT NULL CHECK(content_type IN ('blog', 'youtube')),
    report_md    TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    triggered_by TEXT NOT NULL CHECK(triggered_by IN ('web', 'lifeos'))
);
CREATE INDEX idx_keyword_research_created_at ON keyword_research_runs(created_at DESC);
CREATE INDEX idx_keyword_research_topic ON keyword_research_runs(topic, created_at DESC);
```

### chassis-nav / breadcrumb design (per CONTEXT-MAP)

- Entry inclusion: agent-rooted top-level until nav becomes too wide → then dropdown (not yet)
- aria-current strictly URL-keyed (drop misleading SEO `aria-current` from Zoro surfaces)
- breadcrumb always-shown, uses `nk-caps` token, position above page-header

### API contracts

- `GET /bridge/zoro/keyword-research/history` — list, paginated 20/page (offset cursor)
- `GET /bridge/zoro/keyword-research/history/{id}` — detail with markdown + download form
- `POST /bridge/zoro/keyword-research` (existing) — adds db side-effect (`triggered_by='web'`)
- `GET /bridge/seo/posts/{wp_post_id}/audits` — read-only audit history table

## Testing Decisions

- Test external behavior (HTTP via FastAPI TestClient + cookie auth + sqlite in-memory state)
- Reuse pattern from `tests/test_bridge_seo_audits.py`, `tests/test_bridge_seo_review.py`

Tests added:
- `tests/shared/test_keyword_research_history_store.py` (new)
- `tests/test_bridge_zoro_history.py` (new)
- `tests/test_bridge_seo_audit_history.py` (new)
- `tests/test_bridge_zoro.py` (extend) — POST has db side-effect

## Out of Scope

- Grade trend chart on audit history (v2)
- Per-rule diff between two audits (v2)
- LifeOS dataviewjs path writing to db (LifeOS path stays unchanged this round)
- Backfill of existing vault .md files into db (history accumulates from this PR forward)
- chassis-nav dropdown (not until 4+ active agent surfaces)
- ADR-008 Phase 2a-min (separate, blocked by upstream)

## Further Notes

**Dependency chain:**

- Slice 0 (refactor _chassis_nav.html partial) — no blockers
- Slice 1 (B′: ZORO entry + breadcrumb + #245 + row button) — blocked by Slice 0
- Slice 2 (A′: keyword-research history) — blocked by Slice 0
- Slice 3 (E: audit history surface) — blocked by Slice 0 + Slice 1 (row button wiring)

**Slice 0 rationale:** chassis-nav is currently inlined in ~10 bridge templates. Mechanical update for ZORO addition would touch all 10; partials reduce future cost from N to 1. Precedent: `project_lifeos_template_drift.md` (LifeOS dispatcher + 4 body partials).

PRD frozen via `/grill-with-docs` session 2026-04-29 evening; CONTEXT-MAP terms `chassis-nav` and `breadcrumb` added in commit `135d2b8`.
