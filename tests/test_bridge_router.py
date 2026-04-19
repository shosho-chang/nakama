"""Tests for thousand_sunny.routers.bridge — memory CRUD + cost API."""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from shared import agent_memory, state


@pytest.fixture
def client(monkeypatch):
    """Bridge router with dev-mode auth (WEB_SECRET unset → check_key returns True)."""
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_SECRET", raising=False)
    monkeypatch.setenv("DISABLE_ROBIN", "1")
    monkeypatch.setenv("NAKAMA_DEFAULT_USER_ID", "shosho")

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.bridge as bridge_module

    importlib.reload(auth_module)
    importlib.reload(bridge_module)
    importlib.reload(app_module)
    return TestClient(app_module.app)


@pytest.fixture
def seed_memories():
    """Insert three memories for nami + one for zoro."""
    agent_memory.add(
        agent="nami",
        user_id="shosho",
        type="preference",
        subject="work_hours",
        content="修修偏好早上深度工作",
    )
    agent_memory.add(
        agent="nami", user_id="shosho", type="fact", subject="location", content="修修在台灣"
    )
    agent_memory.add(
        agent="nami",
        user_id="shosho",
        type="decision",
        subject="project_choice",
        content="先做 Bridge UI",
    )
    agent_memory.add(
        agent="zoro",
        user_id="shosho",
        type="preference",
        subject="keyword_channel",
        content="偏好 YouTube 關鍵字",
    )


# ---------------------------------------------------------------------------
# HTML pages
# ---------------------------------------------------------------------------


def test_bridge_index_renders_html(client):
    r = client.get("/bridge")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    body = r.text
    assert "Nakama Bridge" in body
    assert 'href="/bridge/memory"' in body
    assert 'href="/bridge/cost"' in body
    assert 'href="/brook/chat"' in body


def test_bridge_index_hides_robin_when_disabled(client):
    # Fixture sets DISABLE_ROBIN=1 → Robin tile shows as disabled with a note.
    r = client.get("/bridge")
    assert "DISABLE_ROBIN" in r.text


def test_memory_page_renders_html(client):
    r = client.get("/bridge/memory")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    body = r.text
    assert "Bridge · Memory" in body
    assert "/bridge/api/memory" in body


def test_cost_page_renders_html(client):
    r = client.get("/bridge/cost")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    body = r.text
    assert "Bridge · Cost" in body
    assert "/bridge/api/cost" in body


# ---------------------------------------------------------------------------
# Memory endpoints
# ---------------------------------------------------------------------------


def test_list_agents_returns_distinct_agents(client, seed_memories):
    r = client.get("/bridge/api/memory/agents")
    assert r.status_code == 200
    body = r.json()
    assert set(body["agents"]) == {"nami", "zoro"}


def test_memory_list_filters_by_agent(client, seed_memories):
    r = client.get("/bridge/api/memory?agent=nami")
    assert r.status_code == 200
    body = r.json()
    assert body["agent"] == "nami"
    assert body["user_id"] == "shosho"
    assert len(body["memories"]) == 3
    assert {m["subject"] for m in body["memories"]} == {"work_hours", "location", "project_choice"}


def test_memory_list_empty_for_unknown_agent(client, seed_memories):
    r = client.get("/bridge/api/memory?agent=robin")
    assert r.status_code == 200
    assert r.json()["memories"] == []


def test_memory_patch_content_only(client, seed_memories):
    target = agent_memory.list_all(agent="nami", user_id="shosho")[0]
    r = client.patch(
        f"/bridge/api/memory/{target.id}",
        json={"content": "改過的內容"},
    )
    assert r.status_code == 200
    assert r.json()["content"] == "改過的內容"
    # Untouched fields preserved
    assert r.json()["type"] == target.type
    assert r.json()["subject"] == target.subject


def test_memory_patch_type_and_confidence(client, seed_memories):
    target = agent_memory.list_all(agent="nami", user_id="shosho")[0]
    r = client.patch(
        f"/bridge/api/memory/{target.id}",
        json={"type": "fact", "confidence": 0.5},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "fact"
    assert body["confidence"] == 0.5


def test_memory_patch_rejects_bad_confidence(client, seed_memories):
    target = agent_memory.list_all(agent="nami", user_id="shosho")[0]
    r = client.patch(
        f"/bridge/api/memory/{target.id}",
        json={"confidence": 1.5},
    )
    assert r.status_code == 422  # Pydantic validation


def test_memory_patch_404_on_unknown_id(client, seed_memories):
    r = client.patch("/bridge/api/memory/99999", json={"content": "x"})
    assert r.status_code == 404


def test_memory_delete(client, seed_memories):
    target = agent_memory.list_all(agent="nami", user_id="shosho")[0]
    r = client.delete(f"/bridge/api/memory/{target.id}")
    assert r.status_code == 200
    assert r.json()["ok"] is True

    remaining = agent_memory.list_all(agent="nami", user_id="shosho")
    assert target.id not in {m.id for m in remaining}


def test_memory_delete_404_on_unknown_id(client, seed_memories):
    r = client.delete("/bridge/api/memory/99999")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Cost endpoint
# ---------------------------------------------------------------------------


def _seed_api_calls():
    state.record_api_call(
        agent="nami",
        model="claude-sonnet-4-6",
        input_tokens=1000,
        output_tokens=500,
        cache_read_tokens=200,
        cache_write_tokens=100,
    )
    state.record_api_call(
        agent="nami",
        model="claude-haiku-4-5",
        input_tokens=500,
        output_tokens=100,
    )
    state.record_api_call(
        agent="zoro",
        model="claude-sonnet-4-6",
        input_tokens=2000,
        output_tokens=1000,
    )


def test_cost_overview_7d_summary_shape(client):
    _seed_api_calls()
    r = client.get("/bridge/api/cost?range=7d")
    assert r.status_code == 200
    body = r.json()

    assert body["range"] == "7d"
    assert body["days"] == 7
    assert body["bucket"] == "day"
    assert body["agent_filter"] is None

    summary = {(row["agent"], row["model"]): row for row in body["summary"]}
    assert ("nami", "claude-sonnet-4-6") in summary
    assert ("nami", "claude-haiku-4-5") in summary
    assert ("zoro", "claude-sonnet-4-6") in summary

    nami_sonnet = summary[("nami", "claude-sonnet-4-6")]
    assert nami_sonnet["input_tokens"] == 1000
    assert nami_sonnet["output_tokens"] == 500
    assert nami_sonnet["cache_read_tokens"] == 200
    assert nami_sonnet["cache_write_tokens"] == 100
    # (1000*3 + 500*15 + 200*0.30 + 100*3.75) / 1e6 = 10935 / 1e6 = 0.010935
    assert nami_sonnet["cost_usd"] == pytest.approx(0.010935, abs=1e-6)


def test_cost_overview_filters_by_agent(client):
    _seed_api_calls()
    r = client.get("/bridge/api/cost?range=7d&agent=nami")
    assert r.status_code == 200
    body = r.json()
    assert body["agent_filter"] == "nami"
    agents_in_summary = {row["agent"] for row in body["summary"]}
    assert agents_in_summary == {"nami"}


def test_cost_overview_24h_uses_hour_bucket(client):
    _seed_api_calls()
    r = client.get("/bridge/api/cost?range=24h")
    assert r.status_code == 200
    body = r.json()
    assert body["bucket"] == "hour"
    # Each bucket should look like 'YYYY-MM-DDTHH:00'
    for row in body["timeseries"]:
        assert len(row["bucket"]) == 16
        assert row["bucket"][13:] == ":00"


def test_cost_overview_rejects_unknown_range(client):
    r = client.get("/bridge/api/cost?range=1y")
    assert r.status_code == 400


def test_cost_overview_empty_when_no_calls(client):
    r = client.get("/bridge/api/cost?range=7d")
    assert r.status_code == 200
    body = r.json()
    assert body["summary"] == []
    assert body["timeseries"] == []
    assert body["total_cost_usd"] == 0.0


def test_cost_overview_pricing_map_includes_seen_models(client):
    _seed_api_calls()
    r = client.get("/bridge/api/cost?range=7d")
    body = r.json()
    assert set(body["pricing"].keys()) == {"claude-sonnet-4-6", "claude-haiku-4-5"}
    assert body["pricing"]["claude-sonnet-4-6"]["input_usd_per_mtok"] == 3.0
