"""Tests for /bridge/seo/posts/{wp_post_id}/audits — Slice 3 / #259 / E.

Acceptance criteria from PRD #255:
- 401 unauth → /login redirect
- Empty wp_post_id → empty-state copy
- Multi-row sort: newest first
- Status badges: pending / partial / exported #N
- 看詳細 link → /bridge/seo/audits/{audit_id}/review
- chassis-nav: nav_active='seo' (not 'zoro')
"""

from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import shared.state as state_module
from shared import audit_results_store
from shared.schemas.seo_audit_review import AuditSuggestionV1


@pytest.fixture
def state_db(monkeypatch, tmp_path):
    """Tmp NAKAMA_STATE_DB_PATH so tests don't share state."""
    db_path = tmp_path / "state.db"
    monkeypatch.setenv("NAKAMA_STATE_DB_PATH", str(db_path))
    state_module._conn = None  # type: ignore[attr-defined]
    yield db_path
    state_module._conn = None  # type: ignore[attr-defined]


@pytest.fixture
def authed_client(monkeypatch, tmp_path, state_db):
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_SECRET", raising=False)
    monkeypatch.setenv("DISABLE_ROBIN", "1")
    monkeypatch.setenv("NAKAMA_DEFAULT_USER_ID", "shosho")
    monkeypatch.setenv("NAKAMA_DOC_INDEX_DB_PATH", str(tmp_path / "doc_index.db"))

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.bridge as bridge_module

    importlib.reload(auth_module)
    importlib.reload(bridge_module)
    importlib.reload(app_module)
    return TestClient(app_module.app, follow_redirects=False)


@pytest.fixture
def unauthed_client(monkeypatch, tmp_path, state_db):
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
    return TestClient(app_module.app, follow_redirects=False)


def _suggestion(severity: str = "fail") -> AuditSuggestionV1:
    return AuditSuggestionV1(
        rule_id="M1",
        severity=severity,  # type: ignore[arg-type]
        title="title 太短",
        current_value="x",
        suggested_value="xxxxxxx",
        rationale="reason",
    )


def _seed_audit(
    *,
    wp_post_id: int = 42,
    target_site: str = "wp_shosho",
    grade: str = "B+",
    audited_at: datetime | None = None,
    review_status: str = "fresh",
    approval_queue_id: int | None = None,
    fail_count: int = 1,
    warn_count: int = 3,
) -> int:
    """Insert one audit row matching the helper used by the store tests."""
    audit_id = audit_results_store.insert_run(
        url="https://shosho.tw/example",
        target_site=target_site,
        wp_post_id=wp_post_id,
        focus_keyword="深層睡眠",
        audited_at=audited_at or datetime.now(timezone.utc),
        overall_grade=grade,  # type: ignore[arg-type]
        pass_count=12,
        warn_count=warn_count,
        fail_count=fail_count,
        skip_count=0,
        suggestions=[_suggestion()],
        raw_markdown="# audit\n",
    )
    if review_status != "fresh":
        # Use the lower-level transitions for state setup.
        if review_status == "exported":
            audit_results_store.mark_exported(audit_id, queue_id=approval_queue_id or 0)
        elif review_status == "in_review":
            # update_suggestion side-effect transitions fresh → in_review per
            # store contract (see test_bridge_seo_review.py).
            audit_results_store.update_suggestion(
                audit_id=audit_id,
                rule_id="M1",
                status="approved",
            )
    return audit_id


# ── Auth ─────────────────────────────────────────────────────────────────


def test_unauth_get_redirects_to_login(unauthed_client):
    r = unauthed_client.get("/bridge/seo/posts/42/audits")
    assert r.status_code == 302
    assert r.headers["location"].startswith("/login?next=/bridge/seo/posts/42/audits")


# ── Empty state ──────────────────────────────────────────────────────────


def test_empty_state_when_post_never_audited(authed_client):
    r = authed_client.get("/bridge/seo/posts/99999/audits")
    assert r.status_code == 200
    body = r.text
    assert "尚無 audit 紀錄" in body
    # Empty state offers a path back to the SEO 中控台 article list.
    assert "回 SEO 中控台" in body
    # nav_active='seo' on this surface (it's a child of /bridge/seo).
    assert '<a href="/bridge/seo" class="active" aria-current="page">SEO' in body


# ── Multi-row + sort ─────────────────────────────────────────────────────


def test_multi_row_sorted_newest_first(authed_client):
    base = datetime.now(timezone.utc)
    oldest_id = _seed_audit(wp_post_id=42, audited_at=base - timedelta(days=2), grade="D")
    middle_id = _seed_audit(wp_post_id=42, audited_at=base - timedelta(days=1), grade="C")
    newest_id = _seed_audit(wp_post_id=42, audited_at=base, grade="A")

    r = authed_client.get("/bridge/seo/posts/42/audits")
    assert r.status_code == 200
    body = r.text

    # Order check: anchor on the table cell so we don't collide with other
    # `#nnn` references (PRD #255, post #42 in title, etc).
    cell_newest = f'<td class="col-id">#{newest_id}</td>'
    cell_middle = f'<td class="col-id">#{middle_id}</td>'
    cell_oldest = f'<td class="col-id">#{oldest_id}</td>'
    pos_newest = body.find(cell_newest)
    pos_middle = body.find(cell_middle)
    pos_oldest = body.find(cell_oldest)
    assert -1 < pos_newest < pos_middle < pos_oldest


def test_target_site_renders_correct_audits(authed_client):
    """Sanity: each audit row's id is uniquely findable via its <td>."""
    audit_id = _seed_audit(wp_post_id=42)
    r = authed_client.get("/bridge/seo/posts/42/audits")
    assert r.status_code == 200
    assert f'<td class="col-id">#{audit_id}</td>' in r.text


# ── Status badges ────────────────────────────────────────────────────────


def test_status_badge_pending_for_fresh(authed_client):
    _seed_audit(wp_post_id=42, review_status="fresh")
    r = authed_client.get("/bridge/seo/posts/42/audits")
    assert r.status_code == 200
    assert "⏸ pending" in r.text


def test_status_badge_partial_for_in_review(authed_client):
    _seed_audit(wp_post_id=42, review_status="in_review")
    r = authed_client.get("/bridge/seo/posts/42/audits")
    assert r.status_code == 200
    assert "📝 partial" in r.text


def test_status_badge_exported_with_queue_id(authed_client):
    _seed_audit(wp_post_id=42, review_status="exported", approval_queue_id=7)
    r = authed_client.get("/bridge/seo/posts/42/audits")
    assert r.status_code == 200
    assert "✅ exported #7" in r.text


# ── 看詳細 link → review surface ─────────────────────────────────────────


def test_audit_row_links_to_review_page(authed_client):
    audit_id = _seed_audit(wp_post_id=42)
    r = authed_client.get("/bridge/seo/posts/42/audits")
    assert r.status_code == 200
    body = r.text
    assert f"/bridge/seo/audits/{audit_id}/review" in body
    assert "看詳細 →" in body


# ── target_site query filter ─────────────────────────────────────────────


def test_target_site_query_param_scopes_results(authed_client):
    shosho_id = _seed_audit(target_site="wp_shosho", wp_post_id=42, grade="A")
    fleet_id = _seed_audit(target_site="wp_fleet", wp_post_id=42, grade="F")

    r_merged = authed_client.get("/bridge/seo/posts/42/audits")
    body_merged = r_merged.text
    # Anchor checks on the <td class="col-id"> markup so PRD references like
    # `#255` don't pollute substring lookups.
    assert f'<td class="col-id">#{shosho_id}</td>' in body_merged
    assert f'<td class="col-id">#{fleet_id}</td>' in body_merged

    r_shosho = authed_client.get("/bridge/seo/posts/42/audits?target_site=wp_shosho")
    body_shosho = r_shosho.text
    assert f'<td class="col-id">#{shosho_id}</td>' in body_shosho
    assert f'<td class="col-id">#{fleet_id}</td>' not in body_shosho


# ── Fail / warn counts visible ───────────────────────────────────────────


def test_fail_warn_counts_render(authed_client):
    _seed_audit(wp_post_id=42, fail_count=4, warn_count=7)
    r = authed_client.get("/bridge/seo/posts/42/audits")
    assert r.status_code == 200
    body = r.text
    assert "4F" in body
    assert "7W" in body
