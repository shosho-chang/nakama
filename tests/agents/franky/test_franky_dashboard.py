"""Tests for /bridge/franky dashboard route (Slice 3).

Verifies:
- Unauth redirects to /login
- Auth via cookie renders 200 with status cards
- Page reflects health_probe_state / alert_state / r2_backup_checks rows
- psutil is mocked (no reading real system in CI)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
def client(monkeypatch):
    # Set WEB_PASSWORD + WEB_SECRET so check_auth works deterministically
    monkeypatch.setenv("WEB_PASSWORD", "testpw")
    monkeypatch.setenv("WEB_SECRET", "testsecret")

    # Reload auth + franky modules to pick up env
    import importlib

    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.franky as franky_module

    importlib.reload(auth_module)
    importlib.reload(franky_module)

    # Build minimal app with just the franky router
    app = FastAPI()
    app.include_router(franky_module.router)
    app.include_router(franky_module.page_router)

    # Provide a minimal login route for the redirect target to exist
    from fastapi.responses import PlainTextResponse

    @app.get("/login")
    def login(next: str = ""):
        return PlainTextResponse(f"login next={next}")

    return TestClient(app, follow_redirects=False)


def _auth_cookie():
    """Build a valid session cookie for the fixture's WEB_PASSWORD."""
    from thousand_sunny.auth import make_token

    return {"nakama_auth": make_token("testpw")}


def _mock_psutil_patch():
    """Patch psutil.* used by the dashboard handler."""
    vm = MagicMock(percent=42.0, used=1_000_000_000, total=4_000_000_000)
    sw = MagicMock(percent=0.0)
    disk = MagicMock(percent=30.0)
    return patch.multiple(
        "psutil",
        cpu_percent=MagicMock(return_value=15.0),
        virtual_memory=MagicMock(return_value=vm),
        swap_memory=MagicMock(return_value=sw),
        disk_usage=MagicMock(return_value=disk),
    )


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_bridge_franky_unauth_redirects_to_login(client):
    resp = client.get("/bridge/franky")
    assert resp.status_code == 302
    assert "/login" in resp.headers["location"]


def test_bridge_franky_auth_renders_200(client):
    with _mock_psutil_patch():
        resp = client.get("/bridge/franky", cookies=_auth_cookie())
    assert resp.status_code == 200
    assert "Franky Monitor" in resp.text


# ---------------------------------------------------------------------------
# Content — probes + alerts + R2
# ---------------------------------------------------------------------------


def test_dashboard_shows_all_four_probe_labels(client):
    with _mock_psutil_patch():
        resp = client.get("/bridge/franky", cookies=_auth_cookie())
    body = resp.text
    for label in (
        "Nakama Gateway",
        "VPS Resources",
        "WordPress · shosho.tw",
        "WordPress · fleet.shosho.tw",
    ):
        assert label in body


def test_dashboard_shows_recent_firing_alert(client):
    from shared.state import _get_conn

    now = _now()
    conn = _get_conn()
    conn.execute(
        """INSERT INTO alert_state
              (dedup_key, rule_id, last_fired_at, suppress_until, state, last_message, fire_count)
           VALUES ('k1', 'vps_disk_critical', ?, ?, 'firing', 'disk at 97%', 3)""",
        (now.isoformat(), (now + timedelta(minutes=10)).isoformat()),
    )
    conn.commit()

    with _mock_psutil_patch():
        resp = client.get("/bridge/franky", cookies=_auth_cookie())
    assert resp.status_code == 200
    assert "vps_disk_critical" in resp.text
    assert "firing" in resp.text
    assert "disk at 97%" in resp.text


def test_dashboard_shows_empty_state_when_no_alerts(client):
    with _mock_psutil_patch():
        resp = client.get("/bridge/franky", cookies=_auth_cookie())
    assert "no alerts in the last 24 hours" in resp.text


def test_dashboard_shows_probe_status_from_db(client):
    from shared.state import _get_conn

    now = _now()
    conn = _get_conn()
    conn.execute(
        """INSERT INTO health_probe_state
              (target, consecutive_fails, last_check_at, last_status, last_error)
           VALUES ('nakama_gateway', 0, ?, 'ok', NULL)""",
        (now.isoformat(),),
    )
    conn.commit()

    with _mock_psutil_patch():
        resp = client.get("/bridge/franky", cookies=_auth_cookie())
    assert "Responding" in resp.text


def test_dashboard_shows_r2_backup_when_present(client):
    from shared.state import _get_conn

    now = _now()
    conn = _get_conn()
    conn.execute(
        """INSERT INTO r2_backup_checks
              (checked_at, latest_object_key, latest_object_size,
               latest_object_mtime, status, detail)
           VALUES (?, 'daily/2026-04-22.tar.zst', 150000000, ?, 'ok', '')""",
        (now.isoformat(), now.isoformat()),
    )
    conn.commit()

    with _mock_psutil_patch():
        resp = client.get("/bridge/franky", cookies=_auth_cookie())
    assert "daily/2026-04-22.tar.zst" in resp.text
    # 150000000 bytes / 1MiB ≈ 143
    assert "143 MB" in resp.text


def test_dashboard_no_secrets_leaked(client, monkeypatch):
    monkeypatch.setenv("SLACK_FRANKY_BOT_TOKEN", "xoxb-super-secret-123")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "r2-super-secret-456")
    with _mock_psutil_patch():
        resp = client.get("/bridge/franky", cookies=_auth_cookie())
    assert "xoxb-super-secret-123" not in resp.text
    assert "r2-super-secret-456" not in resp.text
