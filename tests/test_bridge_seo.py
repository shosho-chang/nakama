"""Tests for /bridge/seo — SEO 中控台 v1 slices 1 + 2 + 3.

Covers acceptance criteria from issue #227 (slice 1 foundation), issue #229
(slice 2 article list section + WP REST live pull), and issue #230 (slice 3
target keywords section + 找新關鍵字 button):

- GET /bridge/seo with auth → 200 + three section headings + ADR-008 deferred
  note (slice 1)
- GET /bridge/seo without auth → 302 to /login?next=/bridge/seo (slice 1)
- /bridge/seo article list table renders post titles + focus_keyword (#229)
- WP API failure → page still renders, with empty-state message (#229)
- Listing combines wp_shosho + wp_fleet, sorted by `last_modified` desc (#229)
- Grade / last_audited_at columns show "—" placeholder + audit button (#229)
- /bridge/seo §2 reads ``config/target-keywords.yaml`` via
  ``TargetKeywordListV1.model_validate`` (#230)
- Empty / missing yaml → empty-state copy, no crash (#230)
- Each row shows keyword + attack URL + goal_rank ("—" if unset) +
  placeholder current_rank / current_impressions columns (#230)
- ``+ 找新關鍵字`` ghost button links to /bridge/zoro/keyword-research (#230)
"""

from __future__ import annotations

import importlib
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _clear_post_lister_cache():
    """Reset the WP post lister TTL cache between tests."""
    from shared import wp_post_lister

    wp_post_lister.clear_cache()
    yield
    wp_post_lister.clear_cache()


@pytest.fixture
def authed_client(monkeypatch, tmp_path):
    """Bridge router in dev-mode (WEB_PASSWORD/SECRET unset → check_auth True)."""
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_SECRET", raising=False)
    monkeypatch.setenv("DISABLE_ROBIN", "1")
    monkeypatch.setenv("NAKAMA_DEFAULT_USER_ID", "shosho")
    monkeypatch.setenv("NAKAMA_DOC_INDEX_DB_PATH", str(tmp_path / "doc_index.db"))
    # WP creds so wp_post_lister can build a `WordPressClient`; the actual
    # HTTP path is patched per test so these never reach the network.
    monkeypatch.setenv("WP_SHOSHO_BASE_URL", "http://wp.test")
    monkeypatch.setenv("WP_SHOSHO_USERNAME", "u")
    monkeypatch.setenv("WP_SHOSHO_APP_PASSWORD", "p")
    monkeypatch.setenv("WP_FLEET_BASE_URL", "http://wp.test")
    monkeypatch.setenv("WP_FLEET_USERNAME", "u")
    monkeypatch.setenv("WP_FLEET_APP_PASSWORD", "p")

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.bridge as bridge_module

    importlib.reload(auth_module)
    importlib.reload(bridge_module)
    importlib.reload(app_module)
    return TestClient(app_module.app)


@pytest.fixture
def unauthed_client(monkeypatch, tmp_path):
    """Bridge router with WEB_PASSWORD set → check_auth requires the cookie."""
    monkeypatch.setenv("WEB_PASSWORD", "test-password")
    monkeypatch.setenv("WEB_SECRET", "test-secret")
    monkeypatch.setenv("DISABLE_ROBIN", "1")
    monkeypatch.setenv("NAKAMA_DEFAULT_USER_ID", "shosho")
    monkeypatch.setenv("NAKAMA_DOC_INDEX_DB_PATH", str(tmp_path / "doc_index.db"))

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.bridge as bridge_module

    importlib.reload(auth_module)
    importlib.reload(bridge_module)
    importlib.reload(app_module)
    return TestClient(app_module.app)


# ---------------------------------------------------------------------------
# WP listing fixtures — builds a `_wp/v2/posts` listing item.
# ---------------------------------------------------------------------------


def _wp_post(
    *,
    post_id: int,
    title: str,
    modified: str,
    focus_keyword: str | None = None,
    link: str | None = None,
) -> dict[str, Any]:
    """Build a `/wp/v2/posts` listing item (WP 6.9.4 shape, SEOPress meta)."""
    meta: dict[str, Any] = {}
    if focus_keyword is not None:
        meta["_seopress_analysis_target_kw"] = focus_keyword
    return {
        "id": post_id,
        "date": modified,
        "date_gmt": modified,
        "guid": {"rendered": f"http://wp.test/?p={post_id}", "protected": False},
        "modified": modified,
        "modified_gmt": modified,
        "slug": f"post-{post_id}",
        "status": "publish",
        "type": "post",
        "link": link or f"http://wp.test/post-{post_id}/",
        "title": {"rendered": title, "protected": False},
        "content": {"rendered": "<p>body</p>", "protected": False},
        "excerpt": {"rendered": "<p>summary</p>", "protected": False},
        "author": 1,
        "featured_media": 0,
        "comment_status": "open",
        "ping_status": "open",
        "sticky": False,
        "template": "",
        "format": "standard",
        "meta": meta,
        "categories": [1],
        "tags": [],
    }


# Slice 1 + 2 patching strategy: stub `shared.wp_post_lister.list_posts` so
# tests focus on rendering, not on httpx.  Bridge does `from shared import …
# wp_post_lister` then `wp_post_lister.list_posts(...)` — attribute lookup at
# call time, so patching the module's attribute works.
def _patch_lister(_unused_module, return_map: dict[str, list]):
    """Patch `shared.wp_post_lister.list_posts` to return per-target_site rows.

    First arg is kept for callsite readability (test passes the bridge module
    so the test's intent stays explicit) but the patch always targets the
    canonical module attribute.
    """
    from shared import wp_post_lister as _wp

    def _fake(target_site, **_kwargs):
        return list(return_map.get(target_site, []))

    return patch.object(_wp, "list_posts", side_effect=_fake)


def test_seo_page_renders_with_auth(authed_client):
    import thousand_sunny.routers.bridge as bridge_module

    with _patch_lister(bridge_module, {}):
        r = authed_client.get("/bridge/seo")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    body = r.text
    # Page identity
    assert "Bridge · SEO" in body
    assert "SEO 中控台" in body
    # Active chassis-nav marker on this page
    assert 'class="active" aria-current="page">SEO' in body


def test_seo_page_has_three_section_headings(authed_client):
    import thousand_sunny.routers.bridge as bridge_module

    with _patch_lister(bridge_module, {}):
        r = authed_client.get("/bridge/seo")
    assert r.status_code == 200
    body = r.text
    # Section 1: 文章列表
    assert "文章列表" in body
    assert "§ 1 · ARTICLES" in body
    # Section 2: 攻擊關鍵字
    assert "攻擊關鍵字" in body
    assert "§ 2 · TARGET KEYWORDS" in body
    # Section 3: 排名變化
    assert "排名變化" in body
    assert "§ 3 · RANK CHANGE" in body


def test_seo_page_section_3_states_phase_2a_min_dependency(authed_client):
    import thousand_sunny.routers.bridge as bridge_module

    with _patch_lister(bridge_module, {}):
        r = authed_client.get("/bridge/seo")
    assert r.status_code == 200
    body = r.text
    # The ADR-008 phase 2a-min deferred note has to appear verbatim (Acceptance criterion)
    assert "ADR-008 Phase 2a-min 上線後啟用" in body


def test_seo_page_unauth_redirects_to_login(unauthed_client):
    r = unauthed_client.get("/bridge/seo", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/login?next=/bridge/seo"


def test_seo_page_chassis_nav_link_visible_on_other_bridge_pages(authed_client):
    """Smoke: every bridge page should now contain a SEO chassis-nav link."""
    import thousand_sunny.routers.bridge as bridge_module

    with _patch_lister(bridge_module, {}):
        for path in [
            "/bridge",
            "/bridge/drafts",
            "/bridge/memory",
            "/bridge/cost",
            "/bridge/franky",
            "/bridge/health",
            "/bridge/docs",
            "/bridge/logs",
        ]:
            r = authed_client.get(path)
            assert r.status_code == 200, f"{path} did not render"
            assert '<a href="/bridge/seo">SEO <span class="zh">優化</span></a>' in r.text, (
                f"{path} missing SEO chassis-nav link"
            )


# ---------------------------------------------------------------------------
# Slice 2 — issue #229 acceptance: WP REST live pull renders into a table
# ---------------------------------------------------------------------------


def test_seo_page_renders_article_list_with_focus_keyword(authed_client, monkeypatch):
    """Acceptance: TestClient GET `/bridge/seo` authed → response body
    contains 1+ post title from fixture (and its focus_keyword)."""
    from shared import wp_post_lister

    rows = [
        wp_post_lister.WpPostSummaryV1(
            wp_post_id=42,
            title="深層睡眠的飲食策略",
            link="http://wp.test/sleep-diet/",
            focus_keyword="深層睡眠",
            last_modified="2026-04-25T10:30:00",
        ),
    ]

    import thousand_sunny.routers.bridge as bridge_module

    with _patch_lister(bridge_module, {"wp_shosho": rows, "wp_fleet": []}):
        r = authed_client.get("/bridge/seo")

    assert r.status_code == 200
    body = r.text
    # Title visible + linked
    assert "深層睡眠的飲食策略" in body
    assert 'href="http://wp.test/sleep-diet/"' in body
    # Focus keyword surfaces in its own column
    assert "深層睡眠" in body
    # Modified date trimmed to YYYY-MM-DD HH:MM (no T)
    assert "2026-04-25 10:30" in body


def test_seo_page_grade_and_audit_columns_show_placeholder(authed_client):
    """Acceptance: GRADE + LAST AUDITED columns show "—" with `[跑 audit]`
    button placeholder while #232 has not landed."""
    from shared import wp_post_lister

    rows = [
        wp_post_lister.WpPostSummaryV1(
            wp_post_id=1,
            title="post one",
            link="http://wp.test/post-1/",
            focus_keyword="kw",
            last_modified="2026-04-25T00:00:00",
        ),
    ]

    import thousand_sunny.routers.bridge as bridge_module

    with _patch_lister(bridge_module, {"wp_shosho": rows, "wp_fleet": []}):
        r = authed_client.get("/bridge/seo")

    body = r.text
    # `[跑 audit]` button is rendered in disabled state (slice #232 enables).
    assert "跑 audit" in body
    assert "disabled" in body
    # Placeholder dash appears in the rendered table cells.
    assert "article-placeholder" in body


def test_seo_page_sorts_combined_sites_by_last_modified_desc(authed_client):
    """Acceptance: posts sorted by `last_modified` descending (across both
    target sites once #232 lands a sort key change)."""
    from shared import wp_post_lister

    shosho = [
        wp_post_lister.WpPostSummaryV1(
            wp_post_id=1,
            title="OLDEST",
            link="http://wp.test/1/",
            focus_keyword="",
            last_modified="2026-01-01T00:00:00",
        ),
    ]
    fleet = [
        wp_post_lister.WpPostSummaryV1(
            wp_post_id=2,
            title="NEWEST",
            link="http://wp.test/2/",
            focus_keyword="",
            last_modified="2026-04-25T00:00:00",
        ),
    ]

    import thousand_sunny.routers.bridge as bridge_module

    with _patch_lister(bridge_module, {"wp_shosho": shosho, "wp_fleet": fleet}):
        r = authed_client.get("/bridge/seo")

    body = r.text
    assert body.index("NEWEST") < body.index("OLDEST"), "newest post must appear first"
    # And fleet rows are tagged so the user can tell them apart.
    assert "FLEET" in body


def test_seo_page_handles_missing_focus_keyword(authed_client):
    """Empty focus_keyword renders `—` placeholder, not the empty string."""
    from shared import wp_post_lister

    rows = [
        wp_post_lister.WpPostSummaryV1(
            wp_post_id=1,
            title="无 keyword 的文章",
            link="http://wp.test/none/",
            focus_keyword="",
            last_modified="2026-04-25T00:00:00",
        ),
    ]

    import thousand_sunny.routers.bridge as bridge_module

    with _patch_lister(bridge_module, {"wp_shosho": rows, "wp_fleet": []}):
        r = authed_client.get("/bridge/seo")

    body = r.text
    # The post still renders even though focus_keyword is empty.
    assert "无 keyword 的文章" in body
    # The empty focus_keyword cell shows a placeholder.
    assert "article-keyword is-empty" in body


def test_seo_page_renders_when_wp_api_unavailable(authed_client):
    """Acceptance: WP API error → graceful fallback (page still renders;
    no 500)."""
    import thousand_sunny.routers.bridge as bridge_module

    # `list_posts` returns [] on any failure (verified in the lister tests),
    # so simulating that contract here is sufficient.
    with _patch_lister(bridge_module, {}):
        r = authed_client.get("/bridge/seo")

    assert r.status_code == 200
    body = r.text
    # Empty-state copy renders rather than the table.
    assert "沒有抓到文章" in body
    assert "WP REST 暫時無法連線" in body


# ---------------------------------------------------------------------------
# Slice 3 — issue #230 acceptance: §2 target keywords list + 找新關鍵字 button
# ---------------------------------------------------------------------------


def _patch_target_keywords_path(yaml_path: Path):
    """Patch ``shared.target_keywords.default_path`` to return ``yaml_path``.

    Bridge does ``from shared import ... target_keywords`` then
    ``target_keywords.load_target_keywords()`` (no path arg) — attribute lookup
    at call time, so patching ``default_path`` on the canonical module works
    regardless of caller binding.
    """
    from shared import target_keywords as _tk

    return patch.object(_tk, "default_path", return_value=yaml_path)


def _write_target_keywords_yaml(tmp_path: Path, body: str) -> Path:
    """Write a fixture ``target-keywords.yaml`` and return the path."""
    p = tmp_path / "target-keywords.yaml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def test_seo_page_target_keywords_button_links_to_zoro_research(authed_client, tmp_path):
    """Acceptance: ``+ 找新關鍵字`` ghost button placed next to §2 heading,
    links to ``/bridge/zoro/keyword-research``."""
    import thousand_sunny.routers.bridge as bridge_module

    # Empty-state branch is fine for the button check — the button lives in
    # the section-head, not the body, so it renders regardless of list size.
    yaml_path = _write_target_keywords_yaml(
        tmp_path,
        """\
        schema_version: 1
        updated_at: "2026-04-29T00:00:00+08:00"
        keywords: []
        """,
    )

    with _patch_lister(bridge_module, {}), _patch_target_keywords_path(yaml_path):
        r = authed_client.get("/bridge/seo")

    assert r.status_code == 200
    body = r.text
    assert "+ 找新關鍵字" in body
    assert 'href="/bridge/zoro/keyword-research"' in body
    # Ghost-style class (matches zoro_keyword_research.html pattern).
    assert "nk-btn-ghost" in body


def test_seo_page_target_keywords_empty_state(authed_client, tmp_path):
    """Acceptance: empty keyword list → empty-state copy, no crash."""
    import thousand_sunny.routers.bridge as bridge_module

    yaml_path = _write_target_keywords_yaml(
        tmp_path,
        """\
        schema_version: 1
        updated_at: "2026-04-29T00:00:00+08:00"
        keywords: []
        """,
    )

    with _patch_lister(bridge_module, {}), _patch_target_keywords_path(yaml_path):
        r = authed_client.get("/bridge/seo")

    assert r.status_code == 200
    body = r.text
    assert "尚無攻擊關鍵字" in body
    # Empty-state mentions the two writers (Zoro / CLI) per acceptance.
    assert "Zoro" in body
    assert "CLI" in body
    # The keywords table itself does NOT render when empty.
    assert '<table class="keywords-table"' not in body


def test_seo_page_target_keywords_missing_yaml_does_not_crash(authed_client, tmp_path):
    """Acceptance: missing ``config/target-keywords.yaml`` → empty-state,
    not a 500."""
    import thousand_sunny.routers.bridge as bridge_module

    missing = tmp_path / "does-not-exist.yaml"
    assert not missing.exists()

    with _patch_lister(bridge_module, {}), _patch_target_keywords_path(missing):
        r = authed_client.get("/bridge/seo")

    assert r.status_code == 200
    assert "尚無攻擊關鍵字" in r.text


def test_seo_page_target_keywords_renders_rows(authed_client, tmp_path):
    """Acceptance: each row shows keyword + attack URL + goal_rank + placeholder
    columns for current_rank / current_impressions (slice #233)."""
    import thousand_sunny.routers.bridge as bridge_module

    yaml_path = _write_target_keywords_yaml(
        tmp_path,
        """\
        schema_version: 1
        updated_at: "2026-04-29T00:00:00+08:00"
        keywords:
          - schema_version: 1
            keyword: "深層睡眠"
            keyword_en: "deep sleep"
            site: "shosho.tw"
            added_by: "zoro"
            added_at: "2026-04-29T08:00:00+08:00"
            goal_rank: 5
          - schema_version: 1
            keyword: "間歇性斷食"
            site: "fleet.shosho.tw"
            added_by: "usopp"
            added_at: "2026-04-28T08:00:00+08:00"
        """,
    )

    with _patch_lister(bridge_module, {}), _patch_target_keywords_path(yaml_path):
        r = authed_client.get("/bridge/seo")

    assert r.status_code == 200
    body = r.text

    # Both keyword strings render.
    assert "深層睡眠" in body
    assert "間歇性斷食" in body

    # English alias renders alongside zh keyword when present.
    assert "deep sleep" in body

    # Attack URL is `https://<site>` per ADR-008 §6 site convention.
    assert 'href="https://shosho.tw"' in body
    assert 'href="https://fleet.shosho.tw"' in body

    # goal_rank renders as "#5"; missing goal_rank renders the dash placeholder.
    assert "#5" in body
    # The second keyword has no goal_rank → its row should expose the
    # explicit "goal rank unset" aria label so screen readers don't read "—"
    # ambiguously.
    assert "goal rank unset" in body

    # current_rank / current_impressions are placeholders (slice #233).
    assert "current rank populated by slice #233" in body
    assert "impressions populated by slice #233" in body

    # No edit / delete UI per ADR-008 §6 ownership rules.
    assert "編輯" not in body
    assert "刪除" not in body


def test_seo_page_target_keywords_count_in_toolbar(authed_client, tmp_path):
    """Toolbar shows the keyword count so the user can spot empty / large lists
    at a glance (mirrors §1 article list toolbar)."""
    import thousand_sunny.routers.bridge as bridge_module

    yaml_path = _write_target_keywords_yaml(
        tmp_path,
        """\
        schema_version: 1
        updated_at: "2026-04-29T00:00:00+08:00"
        keywords:
          - schema_version: 1
            keyword: "kw1"
            site: "shosho.tw"
            added_by: "zoro"
            added_at: "2026-04-29T08:00:00+08:00"
          - schema_version: 1
            keyword: "kw2"
            site: "shosho.tw"
            added_by: "zoro"
            added_at: "2026-04-29T08:00:00+08:00"
          - schema_version: 1
            keyword: "kw3"
            site: "shosho.tw"
            added_by: "zoro"
            added_at: "2026-04-29T08:00:00+08:00"
        """,
    )

    with _patch_lister(bridge_module, {}), _patch_target_keywords_path(yaml_path):
        r = authed_client.get("/bridge/seo")

    assert r.status_code == 200
    body = r.text
    # Count badge shows 3 (the table also has 3 rows; count_text is the
    # toolbar element).
    assert '共 <span class="count">3</span> 個目標關鍵字' in body
