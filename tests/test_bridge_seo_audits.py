"""Tests for `/bridge/seo/audits` — issue #232 router acceptance.

Covers:

- POST `/bridge/seo/audits` form → BackgroundTask dispatched + 303 redirect
  to `/bridge/seo/audits/{job_id}`
- BackgroundTask assertion: `_run_audit_job` is invoked (we don't actually
  run the audit pipeline; we patch `audit_runner.run`).
- Form validation: missing url → 422; invalid scheme → 422; invalid
  target_site → 422.
- Auth gate: unauthenticated POST/GET → redirect.
- GET `/bridge/seo/audits/{job_id}` shows the progress page; unknown id → 404.
- GET `/bridge/seo/audits/{job_id}/status` returns running / done / error JSON.
- GET `/bridge/seo/audits/{job_id}/result`:
    - while running → 303 to progress page
    - on done → renders result page with grade + counts + raw markdown
- GET `/bridge/seo/audits/by-id/{audit_id}` direct lookup of a past row.
- `/bridge/seo` section 1 join: rows with audit history show grade chip +
  audited_at; never-audited rows show `—` placeholder.
"""

from __future__ import annotations

import importlib
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from agents.brook.audit_runner import AuditRunResult

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_post_lister_cache():
    from shared import wp_post_lister

    wp_post_lister.clear_cache()
    yield
    wp_post_lister.clear_cache()


@pytest.fixture(autouse=True)
def _clear_audit_jobs():
    """Avoid cross-test bleed of in-flight audit job state."""
    import thousand_sunny.routers.bridge as bridge_module

    if hasattr(bridge_module, "_audit_jobs"):
        bridge_module._audit_jobs.clear()
    yield
    if hasattr(bridge_module, "_audit_jobs"):
        bridge_module._audit_jobs.clear()


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
    monkeypatch.setenv("NAKAMA_DEFAULT_USER_ID", "shosho")
    monkeypatch.setenv("NAKAMA_DOC_INDEX_DB_PATH", str(tmp_path / "doc_index.db"))

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.bridge as bridge_module

    importlib.reload(auth_module)
    importlib.reload(bridge_module)
    importlib.reload(app_module)
    return TestClient(app_module.app)


def _patch_lister(return_map: dict[str, list]):
    from shared import wp_post_lister as _wp

    def _fake(target_site, **_kwargs):
        return list(return_map.get(target_site, []))

    return patch.object(_wp, "list_posts", side_effect=_fake)


# ---------------------------------------------------------------------------
# POST /bridge/seo/audits — kick-off
# ---------------------------------------------------------------------------


class TestKickOff:
    def test_post_dispatches_background_task_and_redirects(self, authed_client):
        """Acceptance: POST → BackgroundTask add_task called; 303 to progress."""
        import thousand_sunny.routers.bridge as bridge_module

        # Patch the BackgroundTask body so the test does NOT run the audit.
        spy = MagicMock()
        with patch.object(bridge_module, "_run_audit_job", spy):
            r = authed_client.post(
                "/bridge/seo/audits",
                data={
                    "url": "https://shosho.tw/example",
                    "target_site": "wp_shosho",
                    "wp_post_id": "42",
                    "focus_keyword": "深層睡眠",
                },
                follow_redirects=False,
            )

        assert r.status_code == 303
        location = r.headers["location"]
        assert location.startswith("/bridge/seo/audits/")
        # The progress URL contains a hex job_id.
        job_id = location.rsplit("/", 1)[-1]
        assert len(job_id) == 32  # uuid4().hex
        assert all(c in "0123456789abcdef" for c in job_id)

        # The BackgroundTask spy was called exactly once with the right kwargs.
        assert spy.call_count == 1
        kwargs = spy.call_args.kwargs
        assert kwargs["url"] == "https://shosho.tw/example"
        assert kwargs["target_site"] == "wp_shosho"
        assert kwargs["wp_post_id"] == 42
        assert kwargs["focus_keyword"] == "深層睡眠"
        assert kwargs["job_id"] == job_id

        # The job entry exists with status='running'.
        job = bridge_module._get_audit_job(job_id)
        assert job is not None
        assert job["status"] == "running"
        assert job["url"] == "https://shosho.tw/example"

    def test_post_external_url_with_empty_target_site(self, authed_client):
        """target_site can be empty (external / non-WP audit)."""
        import thousand_sunny.routers.bridge as bridge_module

        with patch.object(bridge_module, "_run_audit_job", MagicMock()):
            r = authed_client.post(
                "/bridge/seo/audits",
                data={"url": "https://example.com/x", "target_site": ""},
                follow_redirects=False,
            )
        assert r.status_code == 303

    def test_post_invalid_target_site_returns_422(self, authed_client):
        r = authed_client.post(
            "/bridge/seo/audits",
            data={"url": "https://shosho.tw/x", "target_site": "wp_unknown"},
            follow_redirects=False,
        )
        assert r.status_code == 422

    def test_post_url_without_scheme_returns_422(self, authed_client):
        r = authed_client.post(
            "/bridge/seo/audits",
            data={"url": "shosho.tw/x"},
            follow_redirects=False,
        )
        assert r.status_code == 422

    def test_post_missing_url_returns_422(self, authed_client):
        # FastAPI's form validation rejects missing required field.
        r = authed_client.post("/bridge/seo/audits", data={}, follow_redirects=False)
        assert r.status_code == 422

    def test_post_unauth_redirects_to_login(self, unauthed_client):
        r = unauthed_client.post(
            "/bridge/seo/audits",
            data={"url": "https://shosho.tw/x", "target_site": "wp_shosho"},
            follow_redirects=False,
        )
        assert r.status_code == 302
        assert r.headers["location"] == "/login?next=/bridge/seo"


# ---------------------------------------------------------------------------
# Progress page + status JSON
# ---------------------------------------------------------------------------


class TestProgressPage:
    def test_get_progress_page_renders_for_running_job(self, authed_client):
        import thousand_sunny.routers.bridge as bridge_module

        with patch.object(bridge_module, "_run_audit_job", MagicMock()):
            kick = authed_client.post(
                "/bridge/seo/audits",
                data={"url": "https://shosho.tw/x"},
                follow_redirects=False,
            )
        job_id = kick.headers["location"].rsplit("/", 1)[-1]

        r = authed_client.get(f"/bridge/seo/audits/{job_id}")
        assert r.status_code == 200
        body = r.text
        assert "Bridge · SEO Audit · Running" in body
        assert job_id in body
        # The progress page wires JS polling on /status.
        assert "/bridge/seo/audits/' + encodeURIComponent(jobId) + '/status" in body

    def test_unknown_job_returns_404(self, authed_client):
        r = authed_client.get("/bridge/seo/audits/deadbeef")
        assert r.status_code == 404

    def test_progress_unauth_redirects_to_login(self, unauthed_client):
        r = unauthed_client.get(
            "/bridge/seo/audits/abc123",
            follow_redirects=False,
        )
        assert r.status_code == 302
        assert "/login?next=" in r.headers["location"]


class TestStatusJSON:
    def test_status_running(self, authed_client):
        import thousand_sunny.routers.bridge as bridge_module

        with patch.object(bridge_module, "_run_audit_job", MagicMock()):
            kick = authed_client.post(
                "/bridge/seo/audits",
                data={"url": "https://shosho.tw/x"},
                follow_redirects=False,
            )
        job_id = kick.headers["location"].rsplit("/", 1)[-1]

        r = authed_client.get(f"/bridge/seo/audits/{job_id}/status")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "running"
        assert data["audit_id"] is None
        assert data["redirect_to"] is None

    def test_status_done_returns_redirect_to(self, authed_client):
        """When the BackgroundTask reports done, /status returns redirect_to."""
        import thousand_sunny.routers.bridge as bridge_module

        # Manually flip an existing job to 'done' (simulate worker finishing).
        with patch.object(bridge_module, "_run_audit_job", MagicMock()):
            kick = authed_client.post(
                "/bridge/seo/audits",
                data={"url": "https://shosho.tw/x"},
                follow_redirects=False,
            )
        job_id = kick.headers["location"].rsplit("/", 1)[-1]
        bridge_module._set_audit_job(job_id, status="done", audit_id=99)

        r = authed_client.get(f"/bridge/seo/audits/{job_id}/status")
        data = r.json()
        assert data["status"] == "done"
        assert data["audit_id"] == 99
        assert data["redirect_to"] == f"/bridge/seo/audits/{job_id}/result"

    def test_status_error_surfaces_stage(self, authed_client):
        import thousand_sunny.routers.bridge as bridge_module

        with patch.object(bridge_module, "_run_audit_job", MagicMock()):
            kick = authed_client.post(
                "/bridge/seo/audits",
                data={"url": "https://shosho.tw/x"},
                follow_redirects=False,
            )
        job_id = kick.headers["location"].rsplit("/", 1)[-1]
        bridge_module._set_audit_job(
            job_id,
            status="error",
            error_stage="subprocess",
            error_message="boom",
        )

        data = authed_client.get(f"/bridge/seo/audits/{job_id}/status").json()
        assert data["status"] == "error"
        assert data["error_stage"] == "subprocess"
        assert data["error_message"] == "boom"
        assert data["redirect_to"] is None

    def test_status_missing_returns_404(self, authed_client):
        r = authed_client.get("/bridge/seo/audits/missing-job/status")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Result page (/bridge/seo/audits/{job_id}/result)
# ---------------------------------------------------------------------------


def _seed_audit_row(*, target_site="wp_shosho", wp_post_id=42, grade="B+") -> int:
    from shared import audit_results_store
    from shared.schemas.seo_audit_review import AuditSuggestionV1

    return audit_results_store.insert_run(
        url="https://shosho.tw/example",
        target_site=target_site,
        wp_post_id=wp_post_id,
        focus_keyword="深層睡眠",
        audited_at=datetime.now(timezone.utc),
        overall_grade=grade,
        pass_count=20,
        warn_count=5,
        fail_count=4,
        skip_count=1,
        suggestions=[
            AuditSuggestionV1(
                rule_id="M1",
                severity="fail",
                title="title 太短",
                current_value="48 chars",
                suggested_value="50-60 chars",
                rationale="extend",
            )
        ],
        raw_markdown="# rendered audit markdown\n\nbody...",
    )


class TestResultPage:
    def test_done_renders_grade_counts_markdown(self, authed_client):
        import thousand_sunny.routers.bridge as bridge_module

        audit_id = _seed_audit_row()
        with patch.object(bridge_module, "_run_audit_job", MagicMock()):
            kick = authed_client.post(
                "/bridge/seo/audits",
                data={"url": "https://shosho.tw/example"},
                follow_redirects=False,
            )
        job_id = kick.headers["location"].rsplit("/", 1)[-1]
        bridge_module._set_audit_job(job_id, status="done", audit_id=audit_id)

        r = authed_client.get(f"/bridge/seo/audits/{job_id}/result")
        assert r.status_code == 200
        body = r.text
        assert "SEO Audit · B+" in body  # title contains grade
        assert "B+" in body
        # Counts surface
        assert ">20<" in body  # pass count
        assert ">5<" in body  # warn count
        assert ">4<" in body  # fail count
        # Raw markdown rendered
        assert "rendered audit markdown" in body
        # review → button stub links to slice #234 route
        assert f"/bridge/seo/audits/{audit_id}/review" in body

    def test_running_redirects_to_progress(self, authed_client):
        import thousand_sunny.routers.bridge as bridge_module

        with patch.object(bridge_module, "_run_audit_job", MagicMock()):
            kick = authed_client.post(
                "/bridge/seo/audits",
                data={"url": "https://shosho.tw/x"},
                follow_redirects=False,
            )
        job_id = kick.headers["location"].rsplit("/", 1)[-1]

        # Job still running — visiting result bounces back to progress.
        r = authed_client.get(
            f"/bridge/seo/audits/{job_id}/result",
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == f"/bridge/seo/audits/{job_id}"

    def test_unknown_job_returns_404(self, authed_client):
        r = authed_client.get("/bridge/seo/audits/missing-job/result")
        assert r.status_code == 404


class TestByIdLookup:
    def test_view_existing_audit_by_id(self, authed_client):
        audit_id = _seed_audit_row()
        r = authed_client.get(f"/bridge/seo/audits/by-id/{audit_id}")
        assert r.status_code == 200
        assert "rendered audit markdown" in r.text
        assert "B+" in r.text

    def test_unknown_audit_id_returns_404(self, authed_client):
        r = authed_client.get("/bridge/seo/audits/by-id/999999")
        assert r.status_code == 404

    def test_unauth_redirects(self, unauthed_client):
        r = unauthed_client.get(
            "/bridge/seo/audits/by-id/1",
            follow_redirects=False,
        )
        assert r.status_code == 302


# ---------------------------------------------------------------------------
# /bridge/seo section 1 join — issue #232 acceptance
# ---------------------------------------------------------------------------


class TestSection1Join:
    def test_audited_post_shows_grade_chip_and_audited_at(self, authed_client):
        from shared import wp_post_lister

        # Seed an audit row first.
        audit_id = _seed_audit_row(target_site="wp_shosho", wp_post_id=42, grade="A")

        post = wp_post_lister.WpPostSummaryV1(
            wp_post_id=42,
            title="深層睡眠的飲食策略",
            link="http://wp.test/sleep/",
            focus_keyword="深層睡眠",
            last_modified="2026-04-25T10:30:00",
        )

        with _patch_lister({"wp_shosho": [post], "wp_fleet": []}):
            r = authed_client.get("/bridge/seo")

        assert r.status_code == 200
        body = r.text
        assert "深層睡眠的飲食策略" in body
        # grade chip with the right grade-letter class
        assert "grade-chip grade-a" in body
        assert ">A<" in body  # the chip content
        # The `[查 audit]` link to the past result is wired
        assert f"/bridge/seo/audits/by-id/{audit_id}" in body

    def test_never_audited_post_shows_dash_placeholder(self, authed_client):
        from shared import wp_post_lister

        post = wp_post_lister.WpPostSummaryV1(
            wp_post_id=1,
            title="Brand new post",
            link="http://wp.test/new/",
            focus_keyword="",
            last_modified="2026-04-29T00:00:00",
        )

        with _patch_lister({"wp_shosho": [post], "wp_fleet": []}):
            r = authed_client.get("/bridge/seo")

        body = r.text
        assert "Brand new post" in body
        # placeholder dash instead of grade chip
        assert "article-placeholder" in body
        # No chip span rendered in the table body. We split on the table
        # marker so the CSS rule definition higher in the file doesn't
        # confuse the assertion.
        table_section = body.split('<table class="articles-table"', 1)[-1]
        assert 'class="grade-chip' not in table_section
        # The `[跑 audit]` form-post button is wired.
        assert 'action="/bridge/seo/audits"' in body
        assert 'name="url" value="http://wp.test/new/"' in body
        assert 'name="target_site" value="wp_shosho"' in body
        assert 'name="wp_post_id" value="1"' in body

    def test_only_latest_audit_grade_shows(self, authed_client):
        """When a post has 2 audits, only the newer grade surfaces."""
        from shared import audit_results_store, wp_post_lister
        from shared.schemas.seo_audit_review import AuditSuggestionV1

        # Older C audit
        audit_results_store.insert_run(
            url="https://shosho.tw/x",
            target_site="wp_shosho",
            wp_post_id=42,
            focus_keyword="",
            audited_at=datetime.now(timezone.utc) - timedelta(days=2),
            overall_grade="C",
            pass_count=0,
            warn_count=0,
            fail_count=0,
            skip_count=0,
            suggestions=[
                AuditSuggestionV1(
                    rule_id="M1",
                    severity="fail",
                    title="t",
                )
            ],
            raw_markdown="",
        )
        # Newer A audit
        audit_results_store.insert_run(
            url="https://shosho.tw/x",
            target_site="wp_shosho",
            wp_post_id=42,
            focus_keyword="",
            audited_at=datetime.now(timezone.utc),
            overall_grade="A",
            pass_count=0,
            warn_count=0,
            fail_count=0,
            skip_count=0,
            suggestions=[],
            raw_markdown="",
        )

        post = wp_post_lister.WpPostSummaryV1(
            wp_post_id=42,
            title="evolving",
            link="http://wp.test/x/",
            focus_keyword="",
            last_modified="2026-04-29T00:00:00",
        )

        with _patch_lister({"wp_shosho": [post], "wp_fleet": []}):
            r = authed_client.get("/bridge/seo")

        body = r.text
        table_section = body.split('<table class="articles-table"', 1)[-1]
        # The latest grade is "A"; the older "C" must not appear in the chip.
        assert "grade-chip grade-a" in table_section
        assert "grade-chip grade-c" not in table_section


# ---------------------------------------------------------------------------
# BackgroundTask body — invokes audit_runner.run
# ---------------------------------------------------------------------------


class TestRunAuditJob:
    def test_run_audit_job_writes_done_on_success(self, monkeypatch):
        """Direct unit test of `_run_audit_job` — patches `audit_runner.run`."""
        import thousand_sunny.routers.bridge as bridge_module

        bridge_module._set_audit_job(
            "jobX",
            status="running",
            url="https://shosho.tw/x",
            started_at="2026-04-29T00:00:00+00:00",
        )

        ok_result = AuditRunResult(audit_id=123, status="ok")
        monkeypatch.setattr(
            "agents.brook.audit_runner.run",
            MagicMock(return_value=ok_result),
        )

        bridge_module._run_audit_job(
            job_id="jobX",
            url="https://shosho.tw/x",
            target_site="wp_shosho",
            wp_post_id=None,
            focus_keyword="",
        )

        job: dict[str, Any] = bridge_module._get_audit_job("jobX")  # type: ignore[assignment]
        assert job["status"] == "done"
        assert job["audit_id"] == 123
        assert job["finished_at"]

    def test_run_audit_job_writes_error_on_failure(self, monkeypatch):
        import thousand_sunny.routers.bridge as bridge_module

        bridge_module._set_audit_job("jobY", status="running")
        err = AuditRunResult(
            audit_id=None,
            status="error",
            error_stage="subprocess",
            error_message="exit code 1",
        )
        monkeypatch.setattr(
            "agents.brook.audit_runner.run",
            MagicMock(return_value=err),
        )

        bridge_module._run_audit_job(
            job_id="jobY",
            url="https://shosho.tw/x",
            target_site=None,
            wp_post_id=None,
            focus_keyword="",
        )
        job: dict[str, Any] = bridge_module._get_audit_job("jobY")  # type: ignore[assignment]
        assert job["status"] == "error"
        assert job["error_stage"] == "subprocess"
        assert "exit code 1" in job["error_message"]

    def test_run_audit_job_traps_uncaught(self, monkeypatch):
        import thousand_sunny.routers.bridge as bridge_module

        bridge_module._set_audit_job("jobZ", status="running")
        monkeypatch.setattr(
            "agents.brook.audit_runner.run",
            MagicMock(side_effect=RuntimeError("oops")),
        )

        bridge_module._run_audit_job(
            job_id="jobZ",
            url="https://shosho.tw/x",
            target_site=None,
            wp_post_id=None,
            focus_keyword="",
        )
        job: dict[str, Any] = bridge_module._get_audit_job("jobZ")  # type: ignore[assignment]
        assert job["status"] == "error"
        assert job["error_stage"] == "bridge"
        assert "RuntimeError" in job["error_message"]
