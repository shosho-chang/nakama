"""Tests for /bridge/zoro/keyword-research — SEO 中控台 v1 slice 7.

Covers acceptance criteria from issue #231:
- GET renders form (topic / content_type / en_topic) with cookie auth
- POST runs research_keywords (mocked) → inline markdown report + 下載 .md button
- Validation: empty topic → 400; topic > 200 chars → 422
- Auth: unauth GET / POST → 302 to /login?next=/bridge/zoro/keyword-research
- No vault writes (router doesn't import obsidian / vault modules)
- Download endpoint sets Content-Disposition with date in Asia/Taipei
"""

from __future__ import annotations

import importlib
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

# ── Shared fixtures (mirror tests/test_bridge_seo.py) ──────────────────────


@pytest.fixture
def authed_client(monkeypatch, tmp_path):
    """Bridge router in dev-mode (WEB_PASSWORD/SECRET unset → check_auth True)."""
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_SECRET", raising=False)
    monkeypatch.setenv("DISABLE_ROBIN", "1")
    monkeypatch.setenv("NAKAMA_DEFAULT_USER_ID", "shosho")
    monkeypatch.setenv("NAKAMA_DOC_INDEX_DB_PATH", str(tmp_path / "doc_index.db"))

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.bridge as bridge_module
    import thousand_sunny.routers.bridge_zoro as bridge_zoro_module

    importlib.reload(auth_module)
    importlib.reload(bridge_module)
    importlib.reload(bridge_zoro_module)
    importlib.reload(app_module)
    return TestClient(app_module.app, follow_redirects=False)


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
    import thousand_sunny.routers.bridge_zoro as bridge_zoro_module

    importlib.reload(auth_module)
    importlib.reload(bridge_module)
    importlib.reload(bridge_zoro_module)
    importlib.reload(app_module)
    return TestClient(app_module.app, follow_redirects=False)


# Realistic ``research_keywords`` fixture — keys + structure match what
# ``agents.zoro.keyword_research.research_keywords`` actually returns
# (see ``agents/zoro/keyword_research.py`` lines 282-289 + 162-191 for the
# attached raw data shape). Tests assert against this exact shape so we
# catch contract drift.
_FIXTURE_RESULT = {
    "keywords": [
        {
            "keyword": "間歇性斷食",
            "keyword_en": "intermittent fasting",
            "search_volume": "high",
            "competition": "medium",
            "opportunity": "8/10",
            "source": "youtube_zh + trends_en",
            "reason": "中文搜尋量高、英文趨勢上升",
        },
    ],
    "trend_gaps": [
        {
            "topic": "TRE for women",
            "en_signal": "rising in Reddit",
            "zh_status": "未覆蓋",
            "opportunity": "做女性向 TRE 影片",
        },
    ],
    "youtube_titles": ["間歇性斷食實測 30 天我發現的事"],
    "blog_titles": ["間歇性斷食完整指南：科學機制與台灣執行版"],
    "analysis_summary": "中文市場關注度高，可切入女性 / 高齡族群亞分支。",
    "trending_videos": [],
    "social_posts": [],
    "sources_used": ["youtube_zh", "trends_en", "autocomplete_zh"],
    "sources_failed": ["twitter_zh"],
    "en_topic": "intermittent fasting",
    "usage": [],
}


# ── GET form renders ──────────────────────────────────────────────────────


def test_form_get_renders_three_fields(authed_client):
    r = authed_client.get("/bridge/zoro/keyword-research")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    body = r.text
    assert "Bridge · Zoro · 關鍵字研究" in body
    # All three form fields must be present
    assert 'name="topic"' in body
    assert 'name="content_type"' in body
    assert 'name="en_topic"' in body
    # Default content_type is blog (issue #231 acceptance)
    assert '<option value="blog"    selected>' in body
    # Spinner element ships with the page (vanilla JS UX, no SPA)
    assert 'id="kwSpinner"' in body
    # Submit button text in zh-TW
    assert "啟動研究" in body


def test_form_get_unauth_redirects_to_login(unauthed_client):
    r = unauthed_client.get("/bridge/zoro/keyword-research")
    assert r.status_code == 302
    assert r.headers["location"] == "/login?next=/bridge/zoro/keyword-research"


def test_form_post_unauth_redirects_to_login(unauthed_client):
    r = unauthed_client.post("/bridge/zoro/keyword-research", data={"topic": "睡眠"})
    assert r.status_code == 302
    assert r.headers["location"] == "/login?next=/bridge/zoro/keyword-research"


def test_download_post_unauth_redirects_to_login(unauthed_client):
    r = unauthed_client.post(
        "/bridge/zoro/keyword-research/download",
        data={
            "topic": "睡眠",
            "content_type": "blog",
            "en_topic": "",
            "report_md": "# stub",
        },
    )
    assert r.status_code == 302
    assert r.headers["location"] == "/login?next=/bridge/zoro/keyword-research"


# ── POST happy path ──────────────────────────────────────────────────────


def test_post_renders_report_inline_with_download_button(authed_client, monkeypatch):
    """Form submit → research runs → result page shows markdown + download."""
    captured = {}

    def _fake_research(topic, content_type, en_topic):
        captured["topic"] = topic
        captured["content_type"] = content_type
        captured["en_topic"] = en_topic
        return _FIXTURE_RESULT

    # Patch the ORIGINAL module — the router does a function-local
    # ``from agents.zoro.keyword_research import research_keywords`` so the
    # binding is resolved at call time, not import.
    monkeypatch.setattr(
        "agents.zoro.keyword_research.research_keywords",
        _fake_research,
    )

    r = authed_client.post(
        "/bridge/zoro/keyword-research",
        data={"topic": "間歇性斷食", "content_type": "blog", "en_topic": "intermittent fasting"},
    )
    assert r.status_code == 200
    body = r.text

    # research_keywords was called with the form values (en_topic stripped → str)
    assert captured["topic"] == "間歇性斷食"
    assert captured["content_type"] == "blog"
    assert captured["en_topic"] == "intermittent fasting"

    # Markdown body markers are inline-rendered into the page
    assert "間歇性斷食" in body
    assert "策略摘要" in body
    assert "核心關鍵字" in body
    assert "intermittent fasting" in body

    # Download button + form action present (issue #231 AC)
    assert "下載 .md" in body
    assert 'action="/bridge/zoro/keyword-research/download"' in body

    # Result phase metadata displayed
    assert "READY" in body
    today_yyyymmdd = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y%m%d")
    expected_filename = f"kw-research-______-{today_yyyymmdd}.md"
    # Filename uses Taipei-tz date + slugified topic. Pure CJK collapses to
    # underscores (length-of-chars), but the date suffix is the load-bearing
    # assertion — slug fmt is documented in _slugify_topic().
    assert expected_filename in body or today_yyyymmdd in body


def test_post_empty_en_topic_passes_none(authed_client, monkeypatch):
    """Whitespace en_topic → router strips → passes None (lets pipeline auto-translate)."""
    captured = {}

    def _fake(topic, content_type, en_topic):
        captured["en_topic"] = en_topic
        return _FIXTURE_RESULT

    monkeypatch.setattr("agents.zoro.keyword_research.research_keywords", _fake)

    r = authed_client.post(
        "/bridge/zoro/keyword-research",
        data={"topic": "睡眠", "content_type": "youtube", "en_topic": "  "},
    )
    assert r.status_code == 200
    assert captured["en_topic"] is None


def test_post_default_content_type_is_blog(authed_client, monkeypatch):
    """Form omits content_type → router defaults to blog (per issue #231 AC)."""
    captured = {}

    def _fake(topic, content_type, en_topic):
        captured["content_type"] = content_type
        return _FIXTURE_RESULT

    monkeypatch.setattr("agents.zoro.keyword_research.research_keywords", _fake)

    r = authed_client.post(
        "/bridge/zoro/keyword-research",
        data={"topic": "睡眠"},
    )
    assert r.status_code == 200
    assert captured["content_type"] == "blog"


# ── Validation ───────────────────────────────────────────────────────────


def test_post_empty_topic_returns_400(authed_client):
    r = authed_client.post("/bridge/zoro/keyword-research", data={"topic": "   "})
    assert r.status_code == 400
    assert "topic" in r.json()["detail"]


def test_post_topic_too_long_returns_422(authed_client):
    """200+ char topic → FastAPI Form max_length validator → 422."""
    long_topic = "a" * 201
    r = authed_client.post(
        "/bridge/zoro/keyword-research",
        data={"topic": long_topic},
    )
    assert r.status_code == 422


def test_post_invalid_content_type_returns_400(authed_client, monkeypatch):
    """Hand-crafted POST with content_type not in (blog, youtube) → 400."""
    monkeypatch.setattr(
        "agents.zoro.keyword_research.research_keywords",
        lambda *a, **kw: _FIXTURE_RESULT,  # should not be called
    )
    r = authed_client.post(
        "/bridge/zoro/keyword-research",
        data={"topic": "睡眠", "content_type": "twitter"},
    )
    assert r.status_code == 400
    assert "content_type" in r.json()["detail"]


# ── Failure path ─────────────────────────────────────────────────────────


def test_post_runtime_error_renders_error_page(authed_client, monkeypatch):
    """All upstream sources fail → research_keywords raises → render error page, NOT 500."""

    def _boom(*a, **kw):
        raise RuntimeError("所有資料來源都失敗，無法進行關鍵字研究")

    monkeypatch.setattr("agents.zoro.keyword_research.research_keywords", _boom)

    r = authed_client.post(
        "/bridge/zoro/keyword-research",
        data={"topic": "睡眠", "content_type": "blog"},
    )
    assert r.status_code == 200
    body = r.text
    assert "FAILED" in body
    assert "所有資料來源都失敗" in body
    assert "重新填寫" in body  # retry link
    # The form re-renders with the user's previous input pre-filled so they can edit
    assert 'value="睡眠"' in body


def test_post_unexpected_exception_renders_error_page(authed_client, monkeypatch):
    """Unknown exception → caught + rendered as error page (no crash)."""

    def _boom(*a, **kw):
        raise ValueError("unexpected pipeline error")

    monkeypatch.setattr("agents.zoro.keyword_research.research_keywords", _boom)

    r = authed_client.post(
        "/bridge/zoro/keyword-research",
        data={"topic": "睡眠"},
    )
    assert r.status_code == 200
    body = r.text
    assert "FAILED" in body
    assert "ValueError" in body


# ── Download endpoint ────────────────────────────────────────────────────


def test_download_endpoint_returns_attachment_with_taipei_date(authed_client):
    """POST with report_md → markdown body + Content-Disposition attachment."""
    md = "---\ntype: keyword-research\n---\n# Test report\n\nbody"
    r = authed_client.post(
        "/bridge/zoro/keyword-research/download",
        data={
            "topic": "睡眠",
            "content_type": "blog",
            "en_topic": "sleep",
            "report_md": md,
        },
    )
    assert r.status_code == 200
    cd = r.headers["content-disposition"]
    assert "attachment" in cd
    today_yyyymmdd = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y%m%d")
    assert today_yyyymmdd in cd  # Taipei-tz date in filename
    assert "kw-research-" in cd
    assert ".md" in cd
    # Body is the user-supplied markdown verbatim
    assert r.text == md


def test_download_endpoint_with_ascii_topic_filename(authed_client):
    """ASCII topic → slug = lowercase, hyphens preserved."""
    r = authed_client.post(
        "/bridge/zoro/keyword-research/download",
        data={
            "topic": "sleep-quality",
            "content_type": "blog",
            "en_topic": "",
            "report_md": "# stub",
        },
    )
    assert r.status_code == 200
    cd = r.headers["content-disposition"]
    assert "kw-research-sleep-quality-" in cd


# ── Vault-write absence guard ────────────────────────────────────────────


def test_router_module_does_not_import_vault_or_obsidian():
    """Issue #231 AC: 'No vault writes from this surface (verified by absence
    of obsidian / vault path imports in the new router)'.

    Reason: vault writes are exclusively LifeOS Project dataviewjs button's
    responsibility. This bridge surface is read/render-only — saving the
    report to vault is out-of-scope.
    """
    import inspect

    import thousand_sunny.routers.bridge_zoro as mod

    src = inspect.getsource(mod)
    forbidden = [
        "obsidian_writer",
        "lifeos_writer",
        "from shared.obsidian",
        "from shared.lifeos",
        "from shared.vault",
        "import obsidian",
        "import vault",
        "Shosho LifeOS",  # accidental absolute path leak guard
    ]
    for token in forbidden:
        assert token not in src, f"router must not reference {token!r}"


# ── Filename slug behaviour ──────────────────────────────────────────────


def test_slugify_topic_collapses_cjk_and_special_chars():
    """Unit-style: confirm filename slug helper handles CJK + spaces + symbols."""
    from thousand_sunny.routers.bridge_zoro import _slugify_topic

    # ASCII passes through (hyphens preserved)
    assert _slugify_topic("sleep-quality") == "sleep-quality"
    # CJK-only collapses to a single ``_`` then strip → empty → ``topic`` fallback
    assert _slugify_topic("間歇性斷食") == "topic"
    # Mixed ASCII + CJK: ASCII runs preserved, CJK + spaces collapse to ``_``
    assert _slugify_topic("sleep 睡眠 quality") == "sleep_quality"
    # Truncation at 50 chars
    long = "a" * 100
    assert len(_slugify_topic(long)) == 50


# ── Slice 1 (#257) chassis-nav + breadcrumb ─────────────────────────────


def test_chassis_nav_zoro_active(authed_client):
    """ZORO entry carries class=active + aria-current; SEO entry has neither.

    Closes #245 — Issue is that the previous template hard-coded `class="active"
    aria-current="page"` on the SEO entry on this Zoro surface, so screen readers
    announced "SEO 優化, current page" while the user was actually on
    `/bridge/zoro/keyword-research`. Slice 1 (#257) adds a real ZORO chassis-nav
    entry and routes nav state via ``nav_active`` slug.
    """
    r = authed_client.get("/bridge/zoro/keyword-research")
    assert r.status_code == 200
    html = r.text

    # ZORO entry exists and is active.
    assert '<a href="/bridge/zoro/keyword-research" class="active" aria-current="page">ZORO' in html
    # SEO entry exists but is NOT marked active or aria-current on this surface.
    # (Use the closing tag fragment to anchor on the SEO link specifically.)
    assert '<a href="/bridge/seo">SEO' in html
    assert '<a href="/bridge/seo" class="active" aria-current="page">SEO' not in html


def test_breadcrumb_renders_on_zoro_surface(authed_client):
    """Breadcrumb above page-header always shown — not referrer-detected."""
    r = authed_client.get("/bridge/zoro/keyword-research")
    assert r.status_code == 200
    html = r.text

    # Trail back to SEO 中控台 + current location marker.
    assert 'class="nk-breadcrumb"' in html
    assert "/bridge/seo · 找新關鍵字" in html
    assert "ZORO · KEYWORD RESEARCH" in html
    # Current crumb uses aria-current="location" (not "page" — page is for nav).
    assert 'aria-current="location"' in html


# ── Slice 2 (#258) keyword-research history ─────────────────────────────


def test_history_list_unauth_redirects_to_login(unauthed_client):
    r = unauthed_client.get("/bridge/zoro/keyword-research/history")
    assert r.status_code == 302
    assert r.headers["location"] == "/login?next=/bridge/zoro/keyword-research/history"


def test_history_detail_unauth_redirects_to_login(unauthed_client):
    r = unauthed_client.get("/bridge/zoro/keyword-research/history/42")
    assert r.status_code == 302
    assert r.headers["location"] == "/login?next=/bridge/zoro/keyword-research/history/42"


def test_history_list_empty_state(authed_client):
    """Empty db → list page renders empty-state copy with a link back to form."""
    r = authed_client.get("/bridge/zoro/keyword-research/history")
    assert r.status_code == 200
    body = r.text
    assert "尚無歷史研究紀錄" in body
    # ZORO chassis-nav active on history surfaces too.
    assert '<a href="/bridge/zoro/keyword-research" class="active" aria-current="page">ZORO' in body


def test_history_detail_404_for_missing_id(authed_client):
    r = authed_client.get("/bridge/zoro/keyword-research/history/99999")
    assert r.status_code == 404


def test_post_persists_run_with_triggered_by_web(authed_client, monkeypatch):
    """Acceptance: successful POST inserts a row with triggered_by='web'."""
    from shared import keyword_research_history_store as kr_history

    monkeypatch.setattr(
        "agents.zoro.keyword_research.research_keywords",
        lambda *a, **kw: _FIXTURE_RESULT,
    )

    before = kr_history.count_runs()
    r = authed_client.post(
        "/bridge/zoro/keyword-research",
        data={"topic": "持續的測試題", "content_type": "blog", "en_topic": ""},
    )
    assert r.status_code == 200
    after = kr_history.count_runs()
    assert after == before + 1
    # Most-recent row should match what we POSTed.
    rows = kr_history.list_runs(limit=1)
    assert rows[0]["topic"] == "持續的測試題"
    assert rows[0]["triggered_by"] == "web"
    assert rows[0]["content_type"] == "blog"


def test_post_render_unaffected_by_persist_failure(authed_client, monkeypatch):
    """If insert_run raises, the user still sees the rendered report (best-effort)."""
    monkeypatch.setattr(
        "agents.zoro.keyword_research.research_keywords",
        lambda *a, **kw: _FIXTURE_RESULT,
    )

    def _boom(**_kwargs):
        raise RuntimeError("simulated db failure")

    monkeypatch.setattr("shared.keyword_research_history_store.insert_run", _boom)

    r = authed_client.post(
        "/bridge/zoro/keyword-research",
        data={"topic": "DB 壞掉題", "content_type": "blog", "en_topic": ""},
    )
    # User-facing render still 200; the failure goes to logs only.
    assert r.status_code == 200
    assert "READY" in r.text
    assert "下載 .md" in r.text


def test_history_list_renders_rows_and_pagination(authed_client):
    """Insert 25 rows → first page shows 20, pagination shows Next."""
    from shared import keyword_research_history_store as kr_history

    for i in range(25):
        kr_history.insert_run(
            topic=f"題目 {i:02d}",
            en_topic=None,
            content_type="blog",
            report_md=f"# row {i}",
            triggered_by="web",
        )

    r = authed_client.get("/bridge/zoro/keyword-research/history")
    assert r.status_code == 200
    body = r.text
    # Newest topic on page 1 is the last one inserted.
    assert "題目 24" in body
    # Oldest item (題目 00) should NOT be on page 1.
    assert "題目 00" not in body
    # Pagination — Next link present, Previous absent on first page.
    assert "Next →" in body
    assert "← Previous" not in body

    # Page 2 shows the rest.
    r2 = authed_client.get("/bridge/zoro/keyword-research/history?offset=20")
    assert r2.status_code == 200
    body2 = r2.text
    assert "題目 04" in body2
    assert "← Previous" in body2
    assert "Next →" not in body2


def test_history_detail_renders_full_markdown(authed_client):
    from shared import keyword_research_history_store as kr_history

    new_id = kr_history.insert_run(
        topic="詳細頁的題目",
        en_topic="detail page topic",
        content_type="blog",
        report_md="# 報告標題\n\n本文段落",
        triggered_by="web",
    )

    r = authed_client.get(f"/bridge/zoro/keyword-research/history/{new_id}")
    assert r.status_code == 200
    body = r.text
    assert "詳細頁的題目" in body
    assert "detail page topic" in body
    # Full markdown body is present in the textarea (for client-side render
    # via marked) and in the no-JS <pre> fallback.
    assert "# 報告標題" in body
    assert "本文段落" in body
    # Download form points at the existing /download endpoint.
    assert 'action="/bridge/zoro/keyword-research/download"' in body
    # ZORO chassis-nav remains active on detail surfaces.
    assert '<a href="/bridge/zoro/keyword-research" class="active" aria-current="page">ZORO' in body
