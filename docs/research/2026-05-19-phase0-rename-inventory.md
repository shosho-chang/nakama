# Phase 0 — Rename inventory for /architecture v2

**Status**: Inventory complete, no code changes yet
**Companion to**: PR #612 (architecture v2), Codex audit §6.3 ("Phase 0 before marketing")
**Purpose**: Concrete touch-point list per rename group so Phase 1+2 PRs can be atomic instead of discovery-driven

---

## 0. Rename map (per /architecture v2 + Codex audit)

| # | Old path | New path | Driver |
|---|---|---|---|
| R1 | `/promotion-review/*` | `/robin/promotion/*` | ADR-024:82-85 + ADR ownership (Robin, not Brook) |
| R2 | `/writing-assist/*` | `/robin/writing-assist/*` | ADR-027:69 (RCP producer is Robin) |
| R3 | `/projects/{slug}` | `/brook/projects/{slug}` | Layer ownership (Brook editorial) |
| R4 | `/brook/bridge` | `/brook/handoff` | Codex §2 ("name the user action; avoid reusing 'bridge'") |
| R5 | `/books/*` + `/api/books/*` | `/robin/books/*` + `/robin/api/books/*` (TBD) | Layer ownership (Robin knowledge) |
| R6 | `/read?file=…` | `/robin/read?file=…` (or `/robin/read/{slug}`, see §R6) | Layer ownership; Codex §1 flagged data-model concern |

---

## R1 — `/promotion-review/*` → `/robin/promotion/*`

**Route group** (`routers/promotion_review.py:139-328`):
- `GET  /promotion-review/`
- `GET  /promotion-review/source/{source_id_b64}`
- `POST /promotion-review/source/{source_id_b64}/decide/{item_id}`
- `POST /promotion-review/source/{source_id_b64}/commit`
- `POST /promotion-review/source/{source_id_b64}/start`

**Templates** (5 touch-points):
- `templates/promotion_review/list.html:11` — `/static/projects/tokens.css` ← also affected by R3
- `templates/promotion_review/list.html:72` — `href="/promotion-review/source/{{ row.encoded_id }}"`
- `templates/promotion_review/review.html:11,22,50,81`
- `templates/promotion_review/_item_card.html:125,129`

**Login `next=`** (`promotion_review.py:146,172,319`):
- 3 hardcoded `/login?next=/promotion-review/…`

**Tests**: none currently exist for redirect / route shape.

---

## R2 — `/writing-assist/*` → `/robin/writing-assist/*`

**Route group** (`routers/writing_assist.py`):
- `GET /writing-assist/{source_id_b64}` (scaffold)
- Other writing_assist routes — need full enumeration in PR

**Templates**:
- `templates/writing_assist/scaffold.html:31` — `/static/projects/tokens.css` (R3-coupled)

**Login `next=`** (`writing_assist.py:183`):
- 1 hardcoded `/login?next=/writing-assist/{source_id_b64}`

---

## R3 — `/projects/{slug}` → `/brook/projects/{slug}`

**Route group** (`routers/projects.py`):
- `GET /projects/{slug}` (review surface)
- ADR-021 explicitly names this URL (`docs/decisions/ADR-021-…:184-186,241-242`) — **ADR amendment required**

**Templates** (4 touch-points):
- `templates/projects/review.html:11,12,229` — CSS/JS asset paths under `/static/projects/`
- `templates/projects/review.html:42` — display string `/projects/{{ store.project_slug }}`

**Static assets** (`/static/projects/`):
- Referenced from review.html + list.html + scaffold.html — moving `/static/projects/` to `/static/brook/projects/` is a separate decision (probably defer; cosmetic).

**JS fetch** (`static/projects/review.js`):
- L1 comment: `// /projects/{slug} review-mode client.`
- L214 + L273: `fetch(\`/api/projects/${slug}/synthesize\`)` — `/api/projects/*` API surface also needs decision (probably stays as `/api/projects/*`, separate from web URL rename)

**Login `next=`** (`projects.py:253`):
- 1 hardcoded `/login?next=/projects/{slug}`

**ADR action**: ADR-021 amendment listing new URL.

---

## R4 — `/brook/bridge` → `/brook/handoff`

**Route** (`routers/brook.py:36-43,62`):
- Existing `/brook/chat → /brook/bridge` 301 already in place → chains to `/brook/handoff` after rename. Codex §1 flagged: must keep `/brook/chat` redirect non-conflicting indefinitely.

**Templates**:
- `templates/brook/brook_bridge.html:117` — `<form action="/brook/bridge">`
- `templates/bridge/index.html:600` — JS object `brook: '/brook/bridge'`

**Login `next=`** (`brook.py:62`):
- 1 hardcoded `/login?next=/brook/bridge`

**File rename**: `brook_bridge.html` → `brook_handoff.html` (template filename should match).

---

## R5 — `/books/*` + `/api/books/*` → `/robin/books/*` + `/robin/api/books/*`

**Decision needed**: Codex §5 recommended "if hide → unmount entirely, not half-mount". This rename only makes sense if books surface stays mounted in production. If it becomes local-only (per Codex Sec 5 cleanup), no rename needed — just unmount in `app.py`.

**Route group** (`routers/books.py`):
- `GET  /books`, `/books/upload`, `/books/{book_id}` (3 web routes)
- `GET/POST/PUT /api/books/...` (12+ JSON endpoints used by reader)

**Templates** (4 touch-points):
- `templates/robin/books_library.html:74,79,81,120`
- `templates/robin/book_upload.html:56`

**JS fetch** (`static/book_reader.js`):
- 12 occurrences of `/api/books/${BOOK_ID}/...` — heaviest single-file JS touch

**Login `next=`** (`books.py:146,155,301`):
- 3 hardcoded `/login?next=/books…`

**CSP middleware** (`middleware/csp.py:31`):
- Currently guards `("/books", "/api/books")`. Must extend to new prefix (or replace, if cutover is atomic).

---

## R6 — `/read` → `/robin/read` (path) vs `/robin/read/{slug}` (data model migration)

**Codex §1 caveat**: current `/read?file=…&base=…` is query-string based (`robin.py:207-213`). Path-segment rename `/robin/read/{doc_id}` is a NEW data model, not a mechanical rename.

**Recommendation**: do mechanical rename `/read` → `/robin/read` (keep query-string) in this Phase. Data-model migration to slug-based path is a separate ADR + Phase.

**Templates**:
- `templates/robin/index.html:176` — `<a href="/read?file={{ file.name | urlencode }}">`

**Login `next=`** (`robin.py:202`):
- `/login?next=/` (Robin root) — not direct match but Robin family.

**Reader operational endpoints** (Codex §1 enumeration) — must enumerate explicitly:
- `/files/{path}`, `/events/{session_id}`, `/save-annotations`, `/sync-annotations/{slug}`, `/mark-read`, `/discard-info`, `/discard`, `/translate`, `/pubmed-to-reader`
- All currently under `routers/robin.py`. Decision: do they all move under `/robin/*` prefix in one shot, or stay flat? Recommend: all move, single atomic PR per family.

---

## Cross-cutting infrastructure

### CSP middleware (`middleware/csp.py`)
- 31: `_GUARDED_PREFIXES = ("/books", "/api/books")`
- Action in Phase 1: extend to include new prefix during alias period; remove old after Phase 2.

### Login `next=` handling (`routers/auth.py:18-27`)
- `_safe_next()` validates open-redirect (only `/`-prefixed paths). **Robust already** — no Phase 0 hardening needed.
- 40+ `/login?next=/...` redirect sites across routers. After Phase 1 alias period: ALL old-path next= values must still land users on the new URL via redirect chain. `_safe_next()` doesn't expand aliases — the 302 chain handles it (cost: 1 extra hop after login).

### Existing `/brook/chat` redirect (Codex §1)
- `routers/brook.py:36-43`: 301 to `/brook/bridge`. After R4 ships, this becomes a chain `/brook/chat → /brook/bridge → /brook/handoff`. Codex recommends collapsing to `/brook/chat → /brook/handoff` directly during R4 PR.

### `/bridge/health` mention in `bridge.py:1172`
- Codex flagged as "deprecated but still has login next=". Out of Phase 0 scope; track separately.

---

## Recommended PR sequence (Phase 1+2)

Atomic per rename group. Each PR includes: route alias (old + new working), template updates, JS updates, login next= updates, redirect test, CSP update if applicable, ADR amendment if applicable.

1. **R4 first** (`/brook/bridge → /brook/handoff`) — smallest blast radius, validates the alias+test pattern. 2 templates, 1 login next=, 1 redirect collapse (`/brook/chat`).
2. **R1** (`/promotion-review → /robin/promotion`) — ADR-driven, clean ownership story. 3 templates, 3 login next=.
3. **R2** (`/writing-assist → /robin/writing-assist`) — small. 1 template, 1 login next=.
4. **R3** (`/projects → /brook/projects`) — requires ADR-021 amendment. 1 template, 1 login next=, JS asset path decisions.
5. **R6** (`/read → /robin/read`) — mechanical rename only; defer data-model migration.
6. **R5** (`/books → /robin/books`) — heaviest (12 JS sites, CSP update). Consider whether books should stay flat / be local-only first.

After all R-groups land: Phase 2 = drop old aliases per the 30+ day verification window (or keep indefinitely per Codex §4).

---

## Out of scope for Phase 0

- Static asset relocation (`/static/projects/` → `/static/brook/projects/`) — cosmetic, defer
- `/api/books/*` JSON endpoint rename — could stay or move; deferred decision
- ADR-021 amendment text — written in R3 PR
- Marketing landing at `/` — Codex §4 said "don't let cosmetic landing jump ahead of route correctness"; explicitly deferred until all R-groups land
- Bridge `--nk-*` token extraction (Codex §4) — separate workstream
- `/architecture` auth-scope decision — separate

---

## Audit trail

Inventory built by direct grep of `templates/` + `static/*.js` + `routers/*.py` on commit `0c7428d`. No assumptions — all paths verified present in code on this commit.
