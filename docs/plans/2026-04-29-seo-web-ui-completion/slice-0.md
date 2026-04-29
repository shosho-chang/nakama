## Parent

#255

## What to build

Extract the chassis-nav block (currently inlined in ~10 bridge templates as copy-paste) into a single Jinja partial `templates/bridge/_chassis_nav.html`. Active state and `aria-current` are driven by a single template variable `nav_active` (slug like `"seo"`, `"memory"`, `"cost"`, etc.). Replace inline copies in all bridge templates with `{% include "_chassis_nav.html" with context %}` (or equivalent Jinja idiom).

Per CONTEXT-MAP `chassis-nav` term + `project_lifeos_template_drift.md` precedent.

## Acceptance criteria

- [ ] `templates/bridge/_chassis_nav.html` exists and contains the complete chassis-nav block (10 entries: BRIDGE / DRAFTS / SEO / MEMORY / COST / FRANKY / HEALTH / DOCS / LOGS / VAULT)
- [ ] Partial accepts `nav_active` template variable; sets `class="active"` and `aria-current="page"` on the matching entry only
- [ ] All bridge templates (any file under `templates/bridge/*.html` containing `<nav class="chassis-nav">`) use the include — verify via grep that zero inline copies remain
- [ ] Each affected route sets `nav_active` to the correct slug (`"seo"` / `"memory"` / `"cost"` / `"franky"` / `"health"` / `"docs"` / `"logs"` / `"drafts"` / `"bridge"`). Note: ZORO entry is NOT yet added — that ships in Slice 1.
- [ ] `pytest` green (existing tests must not regress)
- [ ] `ruff check` + `ruff format --check` green
- [ ] No visual regression: at minimum smoke-check 3 bridge surfaces and confirm chassis-nav still renders identically

## Out of scope

- Adding ZORO entry (Slice 1)
- Breadcrumb partial (Slice 1)
- Any router behavior change beyond passing `nav_active` to template context

## Blocked by

None — can start immediately.
