"""Tests for `/bridge/seo/audits/{id}/review` — slice 5 / issue #234.

Covers issue #234 acceptance:

- GET review page renders with left textarea (WP raw HTML) + right
  suggestion cards (one per fail/warn).
- Each mutation endpoint (approve / reject / edit) happy + 404 (audit /
  rule not found) + 422 (form validation).
- Edit endpoint persists `edited_value`.
- Resume flow: pre-seed varied statuses, GET review page, assert chips +
  is-* classes reflect persisted state.
- Auth gate: unauthenticated → redirect to `/login?next=...`.
- WP raw fetch failure → page still renders + inline notice.
- Non-WP audit (no target_site / wp_post_id) → page renders with
  "external" notice; mutations still work.
"""

from __future__ import annotations

import importlib
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from shared import audit_results_store
from shared.schemas.seo_audit_review import AuditSuggestionV1
from shared.wp_post_raw_fetcher import RawPostFetchResult

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def authed_client(monkeypatch, tmp_path):
    """Bridge router in dev-mode (WEB_PASSWORD/SECRET unset → check_auth True)."""
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_SECRET", raising=False)
    monkeypatch.setenv("DISABLE_ROBIN", "1")
    monkeypatch.setenv("NAKAMA_DEFAULT_USER_ID", "shosho")
    monkeypatch.setenv("NAKAMA_DOC_INDEX_DB_PATH", str(tmp_path / "doc_index.db"))
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
    monkeypatch.setenv("WEB_PASSWORD", "test-password")
    monkeypatch.setenv("WEB_SECRET", "test-secret")
    monkeypatch.setenv("DISABLE_ROBIN", "1")
    monkeypatch.setenv("NAKAMA_DOC_INDEX_DB_PATH", str(tmp_path / "doc_index.db"))

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.bridge as bridge_module

    importlib.reload(auth_module)
    importlib.reload(bridge_module)
    importlib.reload(app_module)
    return TestClient(app_module.app)


@pytest.fixture(autouse=True)
def _stub_wp_raw_fetch(monkeypatch):
    """Default: any review page request returns a fixed raw HTML payload.

    Tests that want to exercise WP fetch failure or external-audit branches
    install their own patch on top.
    """
    import thousand_sunny.routers.bridge as bridge_module

    monkeypatch.setattr(
        bridge_module.wp_post_raw_fetcher,
        "fetch_raw_html",
        lambda **kwargs: RawPostFetchResult(
            raw_html=(
                "<!-- wp:paragraph -->\n"
                "<p>title 太短 here is the body</p>\n"
                "<!-- /wp:paragraph -->\n"
                "<!-- wp:paragraph -->\n"
                "<p>second block</p>\n"
                "<!-- /wp:paragraph -->\n"
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _seed_audit(
    *,
    target_site: str | None = "wp_shosho",
    wp_post_id: int | None = 42,
    suggestions: list[AuditSuggestionV1] | None = None,
    grade: str = "B+",
) -> int:
    if suggestions is None:
        suggestions = [
            AuditSuggestionV1(
                rule_id="M1",
                severity="fail",
                title="title 太短",
                current_value="title 太短",
                suggested_value="title 太短，補到 50-60 字以提升 SERP CTR",
                rationale="WP title 影響 click-through rate；過短會被 Google 截斷補足",
            ),
            AuditSuggestionV1(
                rule_id="L9",
                severity="warn",
                title="missing canonical",
                current_value="(none)",
                suggested_value='<link rel="canonical" href="https://shosho.tw/x"/>',
                rationale="canonical 防止 thin-content 重複收錄",
            ),
        ]
    return audit_results_store.insert_run(
        url="https://shosho.tw/example",
        target_site=target_site,
        wp_post_id=wp_post_id,
        focus_keyword="深層睡眠",
        audited_at=datetime.now(timezone.utc),
        overall_grade=grade,  # type: ignore[arg-type]
        pass_count=20,
        warn_count=5,
        fail_count=4,
        skip_count=1,
        suggestions=suggestions,
        raw_markdown="# audit\n",
    )


# ---------------------------------------------------------------------------
# GET review page
# ---------------------------------------------------------------------------


class TestGetReviewPage:
    def test_renders_left_textarea_and_right_cards(self, authed_client):
        audit_id = _seed_audit()
        r = authed_client.get(f"/bridge/seo/audits/{audit_id}/review")
        assert r.status_code == 200
        body = r.text

        # Left textarea: WP raw HTML present (Jinja autoescapes `<` / `>`
        # so the rendered HTML shows entity-encoded form; browser un-escapes
        # when inserting into <textarea> text content).
        assert 'id="article-body"' in body
        assert "&lt;p&gt;title 太短 here is the body&lt;/p&gt;" in body

        # Right cards: one per suggestion (M1 + L9).
        assert 'data-rule-id="M1"' in body
        assert 'data-rule-id="L9"' in body

        # Severity chips colour-coded.
        assert '<span class="severity-chip fail">fail</span>' in body
        assert '<span class="severity-chip warn">warn</span>' in body

        # Rule pills + titles surface.
        assert ">M1<" in body
        assert ">L9<" in body
        assert "title 太短" in body
        assert "missing canonical" in body

        # All four buttons exist for at least one card (approve / edit /
        # reject / show-in-body).
        assert ">approve<" in body
        assert ">edit<" in body
        assert ">reject<" in body
        assert ">[在左側顯示]<" in body

        # Form actions point at the right endpoints.
        assert f'action="/bridge/seo/audits/{audit_id}/suggestions/M1/approve"' in body
        assert f'action="/bridge/seo/audits/{audit_id}/suggestions/M1/reject"' in body
        assert f'action="/bridge/seo/audits/{audit_id}/suggestions/M1/edit"' in body

        # Edit dialog is rendered inline per-rule.
        assert 'id="edit-dialog-M1"' in body
        assert 'id="edit-dialog-L9"' in body

    def test_resume_flow_shows_persisted_statuses(self, authed_client):
        """Pre-seed approved + edited + rejected + pending; assert chips."""
        audit_id = _seed_audit(
            suggestions=[
                AuditSuggestionV1(
                    rule_id="A1",
                    severity="fail",
                    title="approved one",
                    current_value="x",
                    suggested_value="x'",
                ),
                AuditSuggestionV1(
                    rule_id="E1",
                    severity="warn",
                    title="edited one",
                    current_value="y",
                    suggested_value="y'",
                ),
                AuditSuggestionV1(
                    rule_id="R1",
                    severity="warn",
                    title="rejected one",
                    current_value="z",
                    suggested_value="z'",
                ),
                AuditSuggestionV1(
                    rule_id="P1",
                    severity="fail",
                    title="pending one",
                    current_value="w",
                    suggested_value="w'",
                ),
            ],
        )
        # Mutate three of four to varied states.
        audit_results_store.update_suggestion(audit_id=audit_id, rule_id="A1", status="approved")
        audit_results_store.update_suggestion(
            audit_id=audit_id,
            rule_id="E1",
            status="edited",
            edited_value="my edit",
        )
        audit_results_store.update_suggestion(audit_id=audit_id, rule_id="R1", status="rejected")

        r = authed_client.get(f"/bridge/seo/audits/{audit_id}/review")
        assert r.status_code == 200
        body = r.text

        # Approved → green chip + is-approved class on card.
        assert "is-approved" in body
        assert '<span class="status-chip approved">approved</span>' in body

        # Edited → amber chip + edited_value visible.
        assert "is-edited" in body
        assert '<span class="status-chip edited">edited</span>' in body
        assert "my edit" in body

        # Rejected → strikethrough class + muted chip.
        assert "is-rejected" in body
        assert '<span class="status-chip rejected">rejected</span>' in body

        # Pending one has NO status chip.
        # Use a slice of the page that contains the pending card to dodge
        # other status chips bleeding in.
        # Find the P1 card block.
        p1_idx = body.find('data-rule-id="P1"')
        assert p1_idx > 0
        # Take the next 600 chars (the card) and assert no status chip in it.
        p1_card = body[p1_idx : p1_idx + 1200]
        assert "status-chip" not in p1_card

    def test_external_audit_renders_with_notice(self, authed_client, monkeypatch):
        """Audit with no target_site / wp_post_id → notice in body pane."""
        # The autouse fixture stubs fetch_raw_html, but with no wp_post_id the
        # router skips the call entirely. Replace the stub with a sentinel
        # we can assert was NOT called.
        import thousand_sunny.routers.bridge as bridge_module

        called: list[dict] = []

        def _spy(**kwargs):
            called.append(kwargs)
            return RawPostFetchResult(raw_html="should_not_show")

        monkeypatch.setattr(bridge_module.wp_post_raw_fetcher, "fetch_raw_html", _spy)

        audit_id = _seed_audit(target_site=None, wp_post_id=None)
        r = authed_client.get(f"/bridge/seo/audits/{audit_id}/review")
        assert r.status_code == 200
        body = r.text
        assert "外站 / 非 WP audit" in body
        # The fetch must NOT be called (we can't pull WP raw without ids).
        assert called == []
        assert "should_not_show" not in body

    def test_wp_fetch_failure_renders_with_error(self, authed_client, monkeypatch):
        """WP REST error → render page + inline error notice; cards still show."""
        import thousand_sunny.routers.bridge as bridge_module

        monkeypatch.setattr(
            bridge_module.wp_post_raw_fetcher,
            "fetch_raw_html",
            lambda **kwargs: RawPostFetchResult(error_message="WP fetch failed: timeout"),
        )

        audit_id = _seed_audit()
        r = authed_client.get(f"/bridge/seo/audits/{audit_id}/review")
        assert r.status_code == 200
        body = r.text
        assert "未能載入文章主體" in body
        assert "WP fetch failed: timeout" in body
        # Cards still render.
        assert 'data-rule-id="M1"' in body

    def test_unknown_audit_returns_404(self, authed_client):
        r = authed_client.get("/bridge/seo/audits/999999/review")
        assert r.status_code == 404

    def test_unauth_redirects_to_login(self, unauthed_client):
        r = unauthed_client.get("/bridge/seo/audits/1/review", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/login?next=/bridge/seo/audits/1/review"


# ---------------------------------------------------------------------------
# POST approve
# ---------------------------------------------------------------------------


class TestApprove:
    def test_happy_path_marks_approved_and_redirects(self, authed_client):
        audit_id = _seed_audit()
        r = authed_client.post(
            f"/bridge/seo/audits/{audit_id}/suggestions/M1/approve",
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == f"/bridge/seo/audits/{audit_id}/review"

        row = audit_results_store.get_by_id(audit_id)
        assert row is not None
        m1 = next(s for s in row["suggestions"] if s.rule_id == "M1")
        assert m1.status == "approved"
        assert m1.reviewed_at is not None

    def test_unknown_audit_returns_404(self, authed_client):
        r = authed_client.post(
            "/bridge/seo/audits/999999/suggestions/M1/approve",
            follow_redirects=False,
        )
        assert r.status_code == 404

    def test_unknown_rule_returns_404(self, authed_client):
        audit_id = _seed_audit()
        r = authed_client.post(
            f"/bridge/seo/audits/{audit_id}/suggestions/NOPE/approve",
            follow_redirects=False,
        )
        assert r.status_code == 404

    def test_unauth_redirects_to_login(self, unauthed_client):
        r = unauthed_client.post(
            "/bridge/seo/audits/1/suggestions/M1/approve",
            follow_redirects=False,
        )
        assert r.status_code == 302
        assert "/login?next=/bridge/seo/audits/1/review" in r.headers["location"]


# ---------------------------------------------------------------------------
# POST reject
# ---------------------------------------------------------------------------


class TestReject:
    def test_happy_path_marks_rejected_and_redirects(self, authed_client):
        audit_id = _seed_audit()
        r = authed_client.post(
            f"/bridge/seo/audits/{audit_id}/suggestions/L9/reject",
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == f"/bridge/seo/audits/{audit_id}/review"
        row = audit_results_store.get_by_id(audit_id)
        assert row is not None
        l9 = next(s for s in row["suggestions"] if s.rule_id == "L9")
        assert l9.status == "rejected"
        assert l9.edited_value is None
        assert l9.reviewed_at is not None

    def test_unknown_audit_returns_404(self, authed_client):
        r = authed_client.post(
            "/bridge/seo/audits/999999/suggestions/M1/reject",
            follow_redirects=False,
        )
        assert r.status_code == 404

    def test_unknown_rule_returns_404(self, authed_client):
        audit_id = _seed_audit()
        r = authed_client.post(
            f"/bridge/seo/audits/{audit_id}/suggestions/MISSING/reject",
            follow_redirects=False,
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST edit
# ---------------------------------------------------------------------------


class TestEdit:
    def test_happy_path_writes_edited_value(self, authed_client):
        audit_id = _seed_audit()
        r = authed_client.post(
            f"/bridge/seo/audits/{audit_id}/suggestions/M1/edit",
            data={"edited_value": "我自己改的版本"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == f"/bridge/seo/audits/{audit_id}/review"
        row = audit_results_store.get_by_id(audit_id)
        assert row is not None
        m1 = next(s for s in row["suggestions"] if s.rule_id == "M1")
        assert m1.status == "edited"
        assert m1.edited_value == "我自己改的版本"
        assert m1.reviewed_at is not None

    def test_empty_edited_value_returns_422(self, authed_client):
        audit_id = _seed_audit()
        r = authed_client.post(
            f"/bridge/seo/audits/{audit_id}/suggestions/M1/edit",
            data={"edited_value": ""},
            follow_redirects=False,
        )
        # FastAPI Form(min_length=1) returns 422 on empty.
        assert r.status_code == 422

    def test_missing_form_field_returns_422(self, authed_client):
        audit_id = _seed_audit()
        r = authed_client.post(
            f"/bridge/seo/audits/{audit_id}/suggestions/M1/edit",
            data={},
            follow_redirects=False,
        )
        assert r.status_code == 422

    def test_too_long_edited_value_returns_422(self, authed_client):
        audit_id = _seed_audit()
        too_long = "x" * 8001
        r = authed_client.post(
            f"/bridge/seo/audits/{audit_id}/suggestions/M1/edit",
            data={"edited_value": too_long},
            follow_redirects=False,
        )
        assert r.status_code == 422

    def test_unknown_rule_returns_404(self, authed_client):
        audit_id = _seed_audit()
        r = authed_client.post(
            f"/bridge/seo/audits/{audit_id}/suggestions/MISSING/edit",
            data={"edited_value": "x"},
            follow_redirects=False,
        )
        assert r.status_code == 404

    def test_unknown_audit_returns_404(self, authed_client):
        r = authed_client.post(
            "/bridge/seo/audits/999999/suggestions/M1/edit",
            data={"edited_value": "x"},
            follow_redirects=False,
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Round-trip: POST then GET reflects state
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_post_then_get_reflects_state(self, authed_client):
        audit_id = _seed_audit()
        # 1. approve M1.
        authed_client.post(
            f"/bridge/seo/audits/{audit_id}/suggestions/M1/approve",
            follow_redirects=False,
        )
        # 2. edit L9.
        authed_client.post(
            f"/bridge/seo/audits/{audit_id}/suggestions/L9/edit",
            data={"edited_value": "改寫後的 canonical 建議"},
            follow_redirects=False,
        )
        # 3. GET — both states should surface.
        r = authed_client.get(f"/bridge/seo/audits/{audit_id}/review")
        assert r.status_code == 200
        body = r.text
        assert '<span class="status-chip approved">approved</span>' in body
        assert '<span class="status-chip edited">edited</span>' in body
        assert "改寫後的 canonical 建議" in body
