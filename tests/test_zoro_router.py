"""Tests for thousand_sunny.routers.zoro — keyword-research endpoint."""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_SECRET", raising=False)
    monkeypatch.setenv("DISABLE_ROBIN", "1")

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.zoro as zoro_module

    importlib.reload(auth_module)
    importlib.reload(zoro_module)
    importlib.reload(app_module)
    return TestClient(app_module.app, follow_redirects=False)


def test_keyword_research_empty_topic_returns_400(client):
    r = client.post("/zoro/keyword-research", data={"topic": "   "})
    assert r.status_code == 400
    assert "topic" in r.json()["detail"].lower()


def test_keyword_research_happy_path(client, monkeypatch):
    captured = {}

    def _fake_research(topic, content_type, en_topic):
        captured["topic"] = topic
        captured["content_type"] = content_type
        captured["en_topic"] = en_topic
        return {
            "keywords": ["sleep", "insomnia"],
            "titles": ["How to sleep better"],
            "cross_lang_gaps": [],
        }

    monkeypatch.setattr("agents.zoro.keyword_research.research_keywords", _fake_research)

    r = client.post(
        "/zoro/keyword-research",
        data={"topic": "睡眠", "content_type": "blog", "en_topic": "sleep"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "keywords" in body
    assert captured["topic"] == "睡眠"
    assert captured["content_type"] == "blog"
    assert captured["en_topic"] == "sleep"


def test_keyword_research_empty_en_topic_becomes_none(client, monkeypatch):
    """空字串 en_topic → router 傳 None 給 research_keywords（讓下游決定行為）。"""
    captured = {}

    def _fake(topic, content_type, en_topic):
        captured["en_topic"] = en_topic
        return {"keywords": []}

    monkeypatch.setattr("agents.zoro.keyword_research.research_keywords", _fake)

    r = client.post("/zoro/keyword-research", data={"topic": "睡眠", "content_type": "youtube"})
    assert r.status_code == 200
    assert captured["en_topic"] is None


def test_keyword_research_runtime_error_returns_502(client, monkeypatch):
    """研究流程裡的 upstream failure（e.g. YouTube API quota）→ 502 with message."""

    def _boom(*a, **kw):
        raise RuntimeError("YouTube API quota exceeded")

    monkeypatch.setattr("agents.zoro.keyword_research.research_keywords", _boom)

    r = client.post("/zoro/keyword-research", data={"topic": "睡眠"})
    assert r.status_code == 502
    assert "quota" in r.json()["detail"]


def test_keyword_research_default_content_type_is_youtube(client, monkeypatch):
    captured = {}

    def _fake(topic, content_type, en_topic):
        captured["content_type"] = content_type
        return {"keywords": []}

    monkeypatch.setattr("agents.zoro.keyword_research.research_keywords", _fake)

    r = client.post("/zoro/keyword-research", data={"topic": "睡眠"})
    assert r.status_code == 200
    assert captured["content_type"] == "youtube"
