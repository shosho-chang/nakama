"""Tests for thousand_sunny.routers.brook — chat page + 5 API endpoints.

Auth uses dev-mode（WEB_PASSWORD / WEB_SECRET 未設）→ `require_auth_or_key` 放行。
底層 compose.* 全 mock，不打真 Anthropic API（feedback_test_api_isolation.md）。
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    """TestClient with dev-mode auth + Robin disabled（加快 app load）。"""
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_SECRET", raising=False)
    monkeypatch.setenv("DISABLE_ROBIN", "1")

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.brook as brook_module

    importlib.reload(auth_module)
    importlib.reload(brook_module)
    importlib.reload(app_module)
    return TestClient(app_module.app, follow_redirects=False)


# ---------------------------------------------------------------------------
# GET /brook/chat (HTML page — auth via cookie)
# ---------------------------------------------------------------------------


def test_chat_page_dev_mode_returns_html(client):
    """WEB_SECRET 未設時 check_auth 放行 → 應該 200 回 HTML，不 redirect。"""
    r = client.get("/brook/chat")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")


def test_chat_page_redirects_to_login_when_auth_required(monkeypatch):
    """WEB_SECRET 有值時，無 cookie 的 GET 應該 302 redirect 到 /login。"""
    monkeypatch.setenv("WEB_PASSWORD", "testpass")
    monkeypatch.setenv("WEB_SECRET", "testsecret")
    monkeypatch.setenv("DISABLE_ROBIN", "1")

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.brook as brook_module

    importlib.reload(auth_module)
    importlib.reload(brook_module)
    importlib.reload(app_module)
    local_client = TestClient(app_module.app, follow_redirects=False)

    r = local_client.get("/brook/chat")
    assert r.status_code == 302
    assert "/login" in r.headers["location"]


# ---------------------------------------------------------------------------
# POST /brook/start
# ---------------------------------------------------------------------------


def test_start_empty_topic_returns_400(client):
    r = client.post("/brook/start", data={"topic": "   "})
    assert r.status_code == 400
    assert "topic" in r.json()["detail"].lower()


def test_start_happy_path_without_kb_query(client, monkeypatch):
    captured = {}

    def _fake_start(topic, kb_context):
        captured["topic"] = topic
        captured["kb_context"] = kb_context
        return {
            "conversation_id": "conv-123",
            "message": "大綱輸出",
            "phase": "outline",
        }

    monkeypatch.setattr("agents.brook.compose.start_conversation", _fake_start)

    r = client.post("/brook/start", data={"topic": "睡眠品質"})
    assert r.status_code == 200
    body = r.json()
    assert body["conversation_id"] == "conv-123"
    assert body["phase"] == "outline"
    assert captured["topic"] == "睡眠品質"
    assert captured["kb_context"] == ""  # no kb_query → empty context


def test_start_with_kb_query_passes_context(client, monkeypatch):
    """kb_query → 呼叫 search_kb → 組成 kb_context 字串餵 start_conversation。"""
    monkeypatch.setattr(
        "thousand_sunny.routers.brook.search_kb",
        lambda query, vault: [
            {"title": "睡眠節律", "type": "article", "relevance_reason": "main topic"},
            {"title": "光照治療", "type": "note", "relevance_reason": "related"},
        ],
    )
    captured = {}

    def _fake_start(topic, kb_context):
        captured["kb_context"] = kb_context
        return {"conversation_id": "c1", "message": "x", "phase": "outline"}

    monkeypatch.setattr("agents.brook.compose.start_conversation", _fake_start)

    r = client.post("/brook/start", data={"topic": "睡眠", "kb_query": "melatonin"})
    assert r.status_code == 200
    assert "睡眠節律" in captured["kb_context"]
    assert "光照治療" in captured["kb_context"]


def test_start_kb_search_failure_still_succeeds(client, monkeypatch):
    """KB 查失敗不該擋 start — kb_context 回空字串。"""

    def _boom(*a, **kw):
        raise RuntimeError("vault unreachable")

    monkeypatch.setattr("thousand_sunny.routers.brook.search_kb", _boom)
    captured = {}

    def _fake_start(topic, kb_context):
        captured["kb_context"] = kb_context
        return {"conversation_id": "c2", "message": "x", "phase": "outline"}

    monkeypatch.setattr("agents.brook.compose.start_conversation", _fake_start)

    r = client.post("/brook/start", data={"topic": "睡眠", "kb_query": "melatonin"})
    assert r.status_code == 200
    assert captured["kb_context"] == ""


def test_start_underlying_error_returns_502(client, monkeypatch):
    def _boom(*a, **kw):
        raise RuntimeError("claude API quota exhausted")

    monkeypatch.setattr("agents.brook.compose.start_conversation", _boom)

    r = client.post("/brook/start", data={"topic": "睡眠"})
    assert r.status_code == 502
    assert "quota" in r.json()["detail"]


# ---------------------------------------------------------------------------
# POST /brook/message
# ---------------------------------------------------------------------------


def test_message_empty_returns_400(client):
    r = client.post("/brook/message", data={"conversation_id": "c1", "message": "   "})
    assert r.status_code == 400


def test_message_unknown_conversation_returns_404(client, monkeypatch):
    def _boom(conv_id, msg):
        raise ValueError(f"Conversation not found: {conv_id}")

    monkeypatch.setattr("agents.brook.compose.send_message", _boom)

    r = client.post("/brook/message", data={"conversation_id": "ghost", "message": "hello"})
    assert r.status_code == 404
    assert "not found" in r.json()["detail"].lower()


def test_message_happy_path(client, monkeypatch):
    monkeypatch.setattr(
        "agents.brook.compose.send_message",
        lambda cid, msg: {"message": "reply", "phase": "outline", "turn_count": 3},
    )

    r = client.post("/brook/message", data={"conversation_id": "c1", "message": "continue"})
    assert r.status_code == 200
    body = r.json()
    assert body["message"] == "reply"
    assert body["turn_count"] == 3


def test_message_underlying_runtime_error_returns_502(client, monkeypatch):
    """send_message 丟 ValueError 以外的 exception → 502 + log。"""

    def _boom(cid, msg):
        raise RuntimeError("claude quota exhausted")

    monkeypatch.setattr("agents.brook.compose.send_message", _boom)

    r = client.post("/brook/message", data={"conversation_id": "c1", "message": "hello"})
    assert r.status_code == 502
    assert "quota" in r.json()["detail"]


# ---------------------------------------------------------------------------
# GET /brook/conversations
# ---------------------------------------------------------------------------


def test_list_conversations(client, monkeypatch):
    monkeypatch.setattr(
        "agents.brook.compose.get_conversations",
        lambda: [
            {"id": "c1", "topic": "A", "phase": "outline"},
            {"id": "c2", "topic": "B", "phase": "done"},
        ],
    )

    r = client.get("/brook/conversations")
    assert r.status_code == 200
    body = r.json()
    assert len(body["conversations"]) == 2
    assert body["conversations"][0]["id"] == "c1"


# ---------------------------------------------------------------------------
# GET /brook/conversation/{id}
# ---------------------------------------------------------------------------


def test_get_conversation_not_found(client, monkeypatch):
    monkeypatch.setattr("agents.brook.compose.get_conversation", lambda cid: None)

    r = client.get("/brook/conversation/ghost")
    assert r.status_code == 404


def test_get_conversation_happy(client, monkeypatch):
    monkeypatch.setattr(
        "agents.brook.compose.get_conversation",
        lambda cid: {"id": cid, "topic": "A", "phase": "outline", "messages": []},
    )

    r = client.get("/brook/conversation/c1")
    assert r.status_code == 200
    assert r.json()["id"] == "c1"


# ---------------------------------------------------------------------------
# POST /brook/export/{id}
# ---------------------------------------------------------------------------


def test_export_not_found(client, monkeypatch):
    def _boom(cid):
        raise ValueError(f"Conversation not found: {cid}")

    monkeypatch.setattr("agents.brook.compose.export_draft", _boom)

    r = client.post("/brook/export/ghost")
    assert r.status_code == 404


def test_export_happy(client, monkeypatch):
    monkeypatch.setattr(
        "agents.brook.compose.export_draft",
        lambda cid: "# 最終文章\n\n完整內容...",
    )

    r = client.post("/brook/export/c1")
    assert r.status_code == 200
    assert "最終文章" in r.json()["draft"]


def test_export_underlying_error_returns_502(client, monkeypatch):
    def _boom(cid):
        raise RuntimeError("export prompt template missing")

    monkeypatch.setattr("agents.brook.compose.export_draft", _boom)

    r = client.post("/brook/export/c1")
    assert r.status_code == 502
