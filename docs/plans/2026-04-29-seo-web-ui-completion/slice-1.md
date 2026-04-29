## Parent

#255

## What to build

Add `ZORO 偵察` entry to `_chassis_nav.html` between SEO and MEMORY. Drop misleading `aria-current="page"` from SEO entry on Zoro surfaces (closes #245). Add `templates/bridge/_breadcrumb.html` partial showing user-journey trail above page-header. Add `查看歷史` button to `/bridge/seo` article-list rows wired to `/bridge/seo/posts/{wp_post_id}/audits` (the destination page is implemented in Slice 3, but the row layout settles here).

Closes #245.

## Acceptance criteria

### chassis-nav

- [ ] `_chassis_nav.html` includes ZORO entry between SEO and MEMORY: `<a href="/bridge/zoro/keyword-research">ZORO <span class="zh">偵察</span></a>`
- [ ] On `/bridge/zoro/keyword-research`: `nav_active='zoro'`, ZORO has `class="active" aria-current="page"`, SEO has neither
- [ ] On `/bridge/seo`: `nav_active='seo'`, SEO has both, ZORO has neither
- [ ] All other bridge surfaces continue to mark only their own entry active (no regression)

### breadcrumb partial

- [ ] `templates/bridge/_breadcrumb.html` accepts `crumbs` list (each item: `{label, href, is_current: bool}`); renders inline using `nk-caps` token; positioned above page-header
- [ ] `zoro_keyword_research.html` shows breadcrumb: `← /bridge/seo · 找新關鍵字 → ZORO · KEYWORD RESEARCH` (always-shown, not referrer-detected)

### article-list button

- [ ] On `/bridge/seo` article list, each row has a `查看歷史` button beside the `跑新 audit` button
- [ ] Button `href` = `/bridge/seo/posts/{wp_post_id}/audits` (200 OK only after Slice 3 lands; until then 404 is acceptable)

### Tests

- [ ] `tests/test_bridge_zoro.py::test_chassis_nav_zoro_active` — ZORO active on `/bridge/zoro/keyword-research`, SEO not
- [ ] `tests/test_bridge_seo.py::test_chassis_nav_seo_active` (new or extend existing) — SEO active on `/bridge/seo`, ZORO not
- [ ] `tests/test_bridge_seo.py::test_article_row_has_history_button` — row HTML contains `查看歷史` link with correct href
- [ ] `pytest` + `ruff` green

## Out of scope

- The audit history destination page itself (Slice 3)
- keyword-research history (Slice 2)

## Blocked by

- Blocked by #256
