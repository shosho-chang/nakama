"""ADR-070 D5（S2a，issue #1321）：``GET /api/llm-lane`` — 唯讀 lane 狀態端點。

涵蓋：無認證 403；``X-Robin-Key`` 認證通過後回傳目前的 lane 狀態 JSON，
形狀跟 ``shared.llm_lane`` 的 dispatch 快取讀取端（``_state_from_json``）對得上。
"""

from __future__ import annotations

import importlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from shared import llm_lane


@pytest.fixture
def client(monkeypatch):
    # WEB_PASSWORD 也要設：check_auth 在沒設密碼時視為 dev bypass（everyone's
    # authenticated），不設就測不出 403（見 thousand_sunny/auth.py::check_auth）。
    monkeypatch.setenv("WEB_PASSWORD", "testpassword")
    monkeypatch.setenv("WEB_SECRET", "testsecret")

    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.llm_lane as llm_lane_router_module

    importlib.reload(auth_module)
    importlib.reload(llm_lane_router_module)

    app = FastAPI()
    app.include_router(llm_lane_router_module.router)
    return TestClient(app)


def test_requires_auth(client):
    resp = client.get("/api/llm-lane")
    assert resp.status_code == 403


def test_returns_current_state_with_key(client):
    llm_lane.record_exhausted(
        "batch", model="opus", rate_limit_type="seven_day_opus", resets_at=1790000000
    )
    resp = client.get("/api/llm-lane", headers={"X-Robin-Key": "testsecret"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["interactive"]["status"] == "subscription"
    assert data["batch"]["status"] == "exhausted"
    assert data["batch"]["blocked_family"] == "opus"
    assert "version" in data and "updated_at" in data


def test_response_parses_back_through_llm_lane_client(client, monkeypatch):
    """回應形狀要能餵回 ``shared.llm_lane`` 桌機端的解析器（同一份 schema）。"""
    llm_lane.record_exhausted(
        "interactive", model="sonnet", rate_limit_type="five_hour", resets_at=1
    )
    resp = client.get("/api/llm-lane", headers={"X-Robin-Key": "testsecret"})
    parsed = llm_lane._state_from_json(resp.json())
    assert parsed.interactive.status == "openrouter_auto"
