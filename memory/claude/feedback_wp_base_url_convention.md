---
name: WP_*_BASE_URL must NOT include /wp-json suffix — WordPressClient appends it itself
description: .env.example had /wp-json suffix in BASE_URL; _request() unconditionally appends /wp-json/ — double-path would 404, all mocked tests missed it
type: feedback
tags: [wordpress, usopp, env, convention, url-construction]
---

`WP_SHOSHO_BASE_URL` / `WP_FLEET_BASE_URL` must be the **site root** (e.g. `https://shosho.tw` or `http://localhost:8888`), **NOT** the REST base. `WordPressClient._request()` at `shared/wordpress_client.py:185` hardcodes `url = f"{self._base_url}/wp-json/{path}"` — if `base_url` already ends with `/wp-json`, every REST call would hit `/wp-json/wp-json/wp/v2/posts` and 404.

**Why:** PR #101 Slice C2a code review surfaced this on 2026-04-24. `.env.example` line 75 had `WP_SHOSHO_BASE_URL=https://shosho.tw/wp-json` since inception, but the bug was invisible because:
- All unit tests (`tests/shared/test_wordpress_client.py`, `tests/agents/usopp/test_publisher.py`) mock httpx — URL construction is never exercised against a real server.
- Usopp hadn't been deployed to VPS yet (PR #98 materials only, no actual service running).
- PR #101 adds the first live-HTTP consumer (`pytest -m live_wp tests/e2e/`), which would be the first to catch it.

The fixup (f8d5692 on branch `feature/usopp-slice-c2a-e2e`) dropped `/wp-json` from both `.env.example` AND `tests/fixtures/wp_staging/run.sh`, and pinned the convention with a one-liner comment above the block.

**How to apply:**
- Before adding any new WordPress REST integration (Chopper → `wp_fleet`, any Phase 2+ work), confirm the consumer uses site-root `base_url` + relative path (e.g. `wp/v2/posts`), NOT prefixed with `/wp-json/`.
- If someone proposes "fix this by making `_request` tolerant" — that's a second-best alternative; the convention fix is cleaner and `.env.example` is now the authoritative source of truth.
- When landing live HTTP E2E work for a new service, assume convention mismatches like this are hiding behind mocked unit tests. Explicitly trace one request from config → URL line-by-line before trusting existing tests.
