"""Tests for `GET /healthz` (thousand_sunny/routers/franky.py).

Verification targets (task prompt §5):
- /healthz returns 200 + HealthzResponseV1 shape
- Response time < 200 ms (we assert a generous 200ms; unit test should be far faster)
- No DB, no Slack, no external HTTP calls in the hot path
- No secrets in payload
"""

from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from shared.schemas.franky import HealthzResponseV1
from thousand_sunny.routers import franky as franky_router


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(franky_router.router)
    return TestClient(app)


def test_healthz_returns_200(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200


def test_healthz_matches_schema(client):
    resp = client.get("/healthz")
    data = resp.json()
    parsed = HealthzResponseV1.model_validate(data)
    assert parsed.status == "ok"
    assert parsed.service == "nakama-gateway"
    assert parsed.schema_version == 1
    assert parsed.uptime_seconds >= 0
    assert any(c.name == "process" for c in parsed.checks)


def test_healthz_sub_200ms(client):
    # warm up
    client.get("/healthz")
    started = time.monotonic()
    resp = client.get("/healthz")
    elapsed_ms = (time.monotonic() - started) * 1000
    assert resp.status_code == 200
    # task prompt §5 requires < 200ms; a single in-memory GET should be << 200ms.
    assert elapsed_ms < 200, f"/healthz took {elapsed_ms:.1f}ms, exceeding 200ms budget"


def test_healthz_has_no_store_cache_header(client):
    resp = client.get("/healthz")
    assert resp.headers.get("Cache-Control") == "no-store"


def test_healthz_does_not_leak_secrets(client, monkeypatch):
    """Guard against accidentally stuffing env vars into the response."""
    monkeypatch.setenv("WP_SHOSHO_APP_PASSWORD", "super-secret-value")
    monkeypatch.setenv("SLACK_FRANKY_BOT_TOKEN", "xoxb-do-not-leak")
    resp = client.get("/healthz")
    body = resp.text
    assert "super-secret-value" not in body
    assert "xoxb-do-not-leak" not in body


def test_healthz_no_auth_required(client):
    """External UptimeRobot probe has no Authorization header."""
    resp = client.get("/healthz")  # no auth header
    assert resp.status_code == 200


def test_healthz_response_only_has_expected_top_level_keys(client):
    resp = client.get("/healthz")
    data = resp.json()
    # extra="forbid" on schema means unexpected keys would've failed validation upstream,
    # but we also guard the outgoing response surface.
    expected = {"schema_version", "status", "service", "version", "uptime_seconds", "checks"}
    assert set(data.keys()) == expected
