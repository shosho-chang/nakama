"""Tests for `/bridge/seo/audits/{id}/export` — slice 6 / issue #235.

Covers issue #235 acceptance:

- Happy path: 2 approved + 1 edited + 1 rejected + 1 pending → export →
  queue row exists with the 3 actionable suggestions only; rejected and
  pending are excluded; `audit_results.review_status='exported'` and
  `approval_queue_id` is set; 303 redirect to `/bridge/drafts`.
- Edited suggestions ship `edited_value`, NOT `suggested_value`.
- `change_summary` is auto-generated and lists rule ids.
- `proposed_changes` markdown round-trips to the queue payload's
  `patch.proposed_changes` and includes per-rule sections.
- Re-export of an already-exported audit returns 409 with the prior
  queue id surfaced.
- Empty actionable set (everything pending or rejected) returns 409.
- Non-WP audit (no target_site / wp_post_id) returns 422.
- Unknown audit id → 404.
- Unauthenticated → 302 redirect to `/login?next=/bridge/seo/audits/<id>/review`.
- Review page renders the export button enabled iff there's ≥1
  approved/edited and the audit is WP-bound + not yet exported.
"""

from __future__ import annotations

import importlib
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from shared import approval_queue, audit_results_store
from shared.schemas.approval import ApprovalPayloadV1Adapter, UpdateWpPostV1
from shared.schemas.seo_audit_review import AuditSuggestionV1
from shared.wp_post_raw_fetcher import RawPostFetchResult

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def authed_client(monkeypatch, tmp_path):
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
    """Default stub for WP raw HTML fetch — review page rendering needs it."""
    import thousand_sunny.routers.bridge as bridge_module

    monkeypatch.setattr(
        bridge_module.wp_post_raw_fetcher,
        "fetch_raw_html",
        lambda **kwargs: RawPostFetchResult(raw_html="<p>body</p>"),
    )


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _make_suggestion(
    rule_id: str,
    severity: str = "fail",
    title: str | None = None,
    current_value: str = "current X",
    suggested_value: str = "suggested Y",
    rationale: str = "why it matters",
) -> AuditSuggestionV1:
    return AuditSuggestionV1(
        rule_id=rule_id,
        severity=severity,  # type: ignore[arg-type]
        title=title or f"rule {rule_id}",
        current_value=current_value,
        suggested_value=suggested_value,
        rationale=rationale,
    )


def _seed_mixed_review(
    *,
    target_site: str | None = "wp_shosho",
    wp_post_id: int | None = 42,
    grade: str = "C",
) -> int:
    """Insert an audit with the canonical mix:

    - M1 (fail) approved
    - M2 (fail) approved
    - L9 (warn) edited
    - R1 (warn) rejected
    - P1 (fail) pending  (untouched)
    """
    audit_id = audit_results_store.insert_run(
        url="https://shosho.tw/sleep-deep",
        target_site=target_site,
        wp_post_id=wp_post_id,
        focus_keyword="深層睡眠",
        audited_at=datetime.now(timezone.utc),
        overall_grade=grade,  # type: ignore[arg-type]
        pass_count=10,
        warn_count=4,
        fail_count=5,
        skip_count=1,
        suggestions=[
            _make_suggestion(
                "M1",
                title="title 太短",
                current_value="深層睡眠",
                suggested_value="深層睡眠：科學說的 7 件事",
            ),
            _make_suggestion(
                "M2",
                title="meta description 缺失",
                current_value="(none)",
                suggested_value="一篇談睡眠週期 + 飲食的綜述",
            ),
            _make_suggestion(
                "L9",
                severity="warn",
                title="canonical 缺失",
                current_value="(none)",
                suggested_value='<link rel="canonical" href="https://shosho.tw/sleep-deep"/>',
            ),
            _make_suggestion(
                "R1",
                severity="warn",
                title="OG image 缺失",
                current_value="(none)",
                suggested_value='<meta property="og:image" content="...">',
            ),
            _make_suggestion(
                "P1",
                title="H1 太多",
                current_value="3 個 H1",
                suggested_value="改為 1 個 H1",
            ),
        ],
        raw_markdown="# audit\n",
    )
    audit_results_store.update_suggestion(audit_id=audit_id, rule_id="M1", status="approved")
    audit_results_store.update_suggestion(audit_id=audit_id, rule_id="M2", status="approved")
    audit_results_store.update_suggestion(
        audit_id=audit_id,
        rule_id="L9",
        status="edited",
        edited_value='<link rel="canonical" href="https://shosho.tw/sleep-deep"/> (改寫)',
    )
    audit_results_store.update_suggestion(audit_id=audit_id, rule_id="R1", status="rejected")
    return audit_id


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestExportHappyPath:
    def test_filters_approved_edited_only(self, authed_client):
        audit_id = _seed_mixed_review()

        r = authed_client.post(
            f"/bridge/seo/audits/{audit_id}/export",
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/bridge/drafts"

        # audit_results row updated
        audit_row = audit_results_store.get_by_id(audit_id)
        assert audit_row is not None
        assert audit_row["review_status"] == "exported"
        queue_id = audit_row["approval_queue_id"]
        assert queue_id is not None

        # approval_queue row exists with payload of 3 actionable suggestions
        queue_row = approval_queue.get_by_id(queue_id)
        assert queue_row is not None
        assert queue_row["status"] == "pending"
        assert queue_row["source_agent"] == "brook"
        assert queue_row["action_type"] == "update_post"
        assert queue_row["target_site"] == "wp_shosho"

        payload = ApprovalPayloadV1Adapter.validate_python(json.loads(queue_row["payload"]))
        assert isinstance(payload, UpdateWpPostV1)
        assert payload.wp_post_id == 42
        assert payload.target_site == "wp_shosho"

        proposed = payload.patch["proposed_changes"]
        # Header line shows count = 3 (approved×2 + edited×1)
        assert "## SEO audit 改動建議（3 條）" in proposed
        # Each approved/edited rule appears
        assert "[M1]" in proposed
        assert "[M2]" in proposed
        assert "[L9]" in proposed
        # Rejected + pending excluded
        assert "[R1]" not in proposed
        assert "[P1]" not in proposed

    def test_edited_uses_edited_value_not_suggested(self, authed_client):
        audit_id = _seed_mixed_review()
        authed_client.post(f"/bridge/seo/audits/{audit_id}/export", follow_redirects=False)

        audit_row = audit_results_store.get_by_id(audit_id)
        queue_row = approval_queue.get_by_id(audit_row["approval_queue_id"])
        payload = ApprovalPayloadV1Adapter.validate_python(json.loads(queue_row["payload"]))
        proposed = payload.patch["proposed_changes"]

        # The edited L9 has edited_value="...(改寫)" — that should appear,
        # and the original suggested_value text should NOT (other than for
        # the parts that overlap, which is just the canonical fragment).
        assert "(改寫)" in proposed
        # Sanity: suggested_value of L9 minus its tail does still appear (it's
        # a substring), so we assert on the explicit reviewer-edited marker.

    def test_change_summary_lists_rule_ids(self, authed_client):
        audit_id = _seed_mixed_review()
        authed_client.post(f"/bridge/seo/audits/{audit_id}/export", follow_redirects=False)
        audit_row = audit_results_store.get_by_id(audit_id)
        queue_row = approval_queue.get_by_id(audit_row["approval_queue_id"])

        payload = ApprovalPayloadV1Adapter.validate_python(json.loads(queue_row["payload"]))
        # change_summary must mention the count + at least the first rule id
        assert "SEO audit: 接受 3 條建議" in payload.change_summary
        assert "M1" in payload.change_summary

    def test_operation_id_includes_audit_id(self, authed_client):
        audit_id = _seed_mixed_review()
        authed_client.post(f"/bridge/seo/audits/{audit_id}/export", follow_redirects=False)
        audit_row = audit_results_store.get_by_id(audit_id)
        queue_row = approval_queue.get_by_id(audit_row["approval_queue_id"])
        assert queue_row["operation_id"] == f"seo_audit_export_{audit_id}"

    def test_only_approved_one_suggestion(self, authed_client):
        """Single-suggestion export still works (smallest happy path)."""
        audit_id = audit_results_store.insert_run(
            url="https://shosho.tw/x",
            target_site="wp_shosho",
            wp_post_id=99,
            focus_keyword="x",
            audited_at=datetime.now(timezone.utc),
            overall_grade="B",
            pass_count=1,
            warn_count=0,
            fail_count=1,
            skip_count=0,
            suggestions=[_make_suggestion("M1")],
            raw_markdown="# audit\n",
        )
        audit_results_store.update_suggestion(audit_id=audit_id, rule_id="M1", status="approved")

        r = authed_client.post(f"/bridge/seo/audits/{audit_id}/export", follow_redirects=False)
        assert r.status_code == 303

        audit_row = audit_results_store.get_by_id(audit_id)
        assert audit_row["review_status"] == "exported"
        queue_row = approval_queue.get_by_id(audit_row["approval_queue_id"])
        payload = ApprovalPayloadV1Adapter.validate_python(json.loads(queue_row["payload"]))
        assert "## SEO audit 改動建議（1 條）" in payload.patch["proposed_changes"]


# ---------------------------------------------------------------------------
# Re-export 409
# ---------------------------------------------------------------------------


class TestReExport:
    def test_re_export_returns_409_with_existing_queue_id(self, authed_client):
        audit_id = _seed_mixed_review()
        first = authed_client.post(f"/bridge/seo/audits/{audit_id}/export", follow_redirects=False)
        assert first.status_code == 303

        audit_row = audit_results_store.get_by_id(audit_id)
        existing_queue_id = audit_row["approval_queue_id"]
        assert existing_queue_id is not None

        # Pre-count queue rows so we can prove no second row was inserted.
        before_count = len(approval_queue.list_by_status("pending"))

        second = authed_client.post(f"/bridge/seo/audits/{audit_id}/export", follow_redirects=False)
        assert second.status_code == 409
        assert f"already exported as queue #{existing_queue_id}" in second.text

        after_count = len(approval_queue.list_by_status("pending"))
        assert after_count == before_count

        # audit row's queue id unchanged.
        audit_row2 = audit_results_store.get_by_id(audit_id)
        assert audit_row2["approval_queue_id"] == existing_queue_id


# ---------------------------------------------------------------------------
# Empty actionable set
# ---------------------------------------------------------------------------


class TestEmptyActionable:
    def test_all_pending_returns_409(self, authed_client):
        audit_id = audit_results_store.insert_run(
            url="https://shosho.tw/x",
            target_site="wp_shosho",
            wp_post_id=42,
            focus_keyword="x",
            audited_at=datetime.now(timezone.utc),
            overall_grade="B",
            pass_count=10,
            warn_count=2,
            fail_count=1,
            skip_count=0,
            suggestions=[_make_suggestion("M1"), _make_suggestion("L9", severity="warn")],
            raw_markdown="# audit\n",
        )
        r = authed_client.post(f"/bridge/seo/audits/{audit_id}/export", follow_redirects=False)
        assert r.status_code == 409
        assert "no approved or edited suggestions" in r.text

    def test_all_rejected_returns_409(self, authed_client):
        audit_id = audit_results_store.insert_run(
            url="https://shosho.tw/x",
            target_site="wp_shosho",
            wp_post_id=42,
            focus_keyword="x",
            audited_at=datetime.now(timezone.utc),
            overall_grade="B",
            pass_count=10,
            warn_count=0,
            fail_count=2,
            skip_count=0,
            suggestions=[_make_suggestion("M1"), _make_suggestion("L9", severity="warn")],
            raw_markdown="# audit\n",
        )
        audit_results_store.update_suggestion(audit_id=audit_id, rule_id="M1", status="rejected")
        audit_results_store.update_suggestion(audit_id=audit_id, rule_id="L9", status="rejected")

        r = authed_client.post(f"/bridge/seo/audits/{audit_id}/export", follow_redirects=False)
        assert r.status_code == 409
        # audit row not flipped.
        audit_row = audit_results_store.get_by_id(audit_id)
        assert audit_row["review_status"] != "exported"
        assert audit_row["approval_queue_id"] is None


# ---------------------------------------------------------------------------
# Non-WP audit
# ---------------------------------------------------------------------------


class TestNonWpAudit:
    def test_no_target_site_returns_422(self, authed_client):
        audit_id = audit_results_store.insert_run(
            url="https://external.example.com/article",
            target_site=None,
            wp_post_id=None,
            focus_keyword="x",
            audited_at=datetime.now(timezone.utc),
            overall_grade="B",
            pass_count=5,
            warn_count=0,
            fail_count=1,
            skip_count=0,
            suggestions=[_make_suggestion("M1")],
            raw_markdown="# audit\n",
        )
        audit_results_store.update_suggestion(audit_id=audit_id, rule_id="M1", status="approved")

        r = authed_client.post(f"/bridge/seo/audits/{audit_id}/export", follow_redirects=False)
        assert r.status_code == 422
        assert "non-WP audit" in r.text.lower() or "target_site" in r.text


# ---------------------------------------------------------------------------
# Unknown audit / unauth
# ---------------------------------------------------------------------------


class TestNotFoundAndAuth:
    def test_unknown_audit_returns_404(self, authed_client):
        r = authed_client.post("/bridge/seo/audits/999999/export", follow_redirects=False)
        assert r.status_code == 404

    def test_unauth_redirects_to_login(self, unauthed_client):
        r = unauthed_client.post("/bridge/seo/audits/1/export", follow_redirects=False)
        assert r.status_code == 302
        assert "/login?next=/bridge/seo/audits/1/review" in r.headers["location"]


# ---------------------------------------------------------------------------
# Review page export button states
# ---------------------------------------------------------------------------


class TestReviewPageExportButton:
    def test_button_disabled_when_all_pending(self, authed_client):
        audit_id = audit_results_store.insert_run(
            url="https://shosho.tw/x",
            target_site="wp_shosho",
            wp_post_id=42,
            focus_keyword="x",
            audited_at=datetime.now(timezone.utc),
            overall_grade="B",
            pass_count=10,
            warn_count=0,
            fail_count=2,
            skip_count=0,
            suggestions=[_make_suggestion("M1"), _make_suggestion("L9", severity="warn")],
            raw_markdown="# audit\n",
        )
        r = authed_client.get(f"/bridge/seo/audits/{audit_id}/review")
        assert r.status_code == 200
        body = r.text
        # Disabled button → has the disabled attribute.
        assert "匯出至 approval queue" in body
        # Find the button block and check it carries `disabled`.
        btn_idx = body.find('class="export-btn"')
        assert btn_idx > 0
        btn_open = body.rfind("<button", 0, btn_idx)
        btn_close = body.find(">", btn_idx)
        btn_attrs = body[btn_open : btn_close + 1]
        assert "disabled" in btn_attrs

    def test_button_enabled_when_one_approved(self, authed_client):
        audit_id = audit_results_store.insert_run(
            url="https://shosho.tw/x",
            target_site="wp_shosho",
            wp_post_id=42,
            focus_keyword="x",
            audited_at=datetime.now(timezone.utc),
            overall_grade="B",
            pass_count=10,
            warn_count=0,
            fail_count=1,
            skip_count=0,
            suggestions=[_make_suggestion("M1")],
            raw_markdown="# audit\n",
        )
        audit_results_store.update_suggestion(audit_id=audit_id, rule_id="M1", status="approved")
        r = authed_client.get(f"/bridge/seo/audits/{audit_id}/review")
        assert r.status_code == 200
        body = r.text
        btn_idx = body.find('class="export-btn"')
        assert btn_idx > 0
        btn_open = body.rfind("<button", 0, btn_idx)
        btn_close = body.find(">", btn_idx)
        btn_attrs = body[btn_open : btn_close + 1]
        assert "disabled" not in btn_attrs
        # Form action points at export endpoint.
        assert f'action="/bridge/seo/audits/{audit_id}/export"' in body

    def test_button_disabled_for_external_audit(self, authed_client):
        """Non-WP audit (no wp_post_id) cannot export → button disabled."""
        audit_id = audit_results_store.insert_run(
            url="https://ext.example/x",
            target_site=None,
            wp_post_id=None,
            focus_keyword="x",
            audited_at=datetime.now(timezone.utc),
            overall_grade="B",
            pass_count=5,
            warn_count=0,
            fail_count=1,
            skip_count=0,
            suggestions=[_make_suggestion("M1")],
            raw_markdown="# audit\n",
        )
        audit_results_store.update_suggestion(audit_id=audit_id, rule_id="M1", status="approved")
        r = authed_client.get(f"/bridge/seo/audits/{audit_id}/review")
        assert r.status_code == 200
        body = r.text
        btn_idx = body.find('class="export-btn"')
        assert btn_idx > 0
        btn_open = body.rfind("<button", 0, btn_idx)
        btn_close = body.find(">", btn_idx)
        btn_attrs = body[btn_open : btn_close + 1]
        assert "disabled" in btn_attrs

    def test_exported_audit_shows_queue_link(self, authed_client):
        audit_id = _seed_mixed_review()
        # Export so review_status flips to 'exported'.
        authed_client.post(f"/bridge/seo/audits/{audit_id}/export", follow_redirects=False)
        audit_row = audit_results_store.get_by_id(audit_id)
        queue_id = audit_row["approval_queue_id"]

        r = authed_client.get(f"/bridge/seo/audits/{audit_id}/review")
        assert r.status_code == 200
        body = r.text
        # No active export form should remain — the bar shows status only.
        assert f"已匯出至 approval queue #{queue_id}" in body
        assert f"/bridge/drafts/{queue_id}" in body
        # Form to /export should not be present in the export bar.
        assert f'action="/bridge/seo/audits/{audit_id}/export"' not in body
