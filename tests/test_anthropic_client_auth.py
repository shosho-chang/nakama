"""get_client() auth fallback: API key → OAuth token → RuntimeError.

Stage 4 sandcastle containers carry only ``CLAUDE_CODE_OAUTH_TOKEN``;
host runs carry ``ANTHROPIC_API_KEY``. Singleton must satisfy both, in that
precedence order, and fail loud when neither is set.
"""

from __future__ import annotations

import pytest

import shared.anthropic_client as ac


@pytest.fixture(autouse=True)
def _reset_singleton():
    ac._client = None
    yield
    ac._client = None


def test_api_key_path(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-test")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    client = ac.get_client()
    assert client.api_key == "sk-ant-api03-test"
    assert client.auth_token is None


def test_oauth_fallback(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-test")
    client = ac.get_client()
    assert client.auth_token == "sk-ant-oat01-test"


def test_api_key_takes_precedence(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-test")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-test")
    client = ac.get_client()
    assert client.api_key == "sk-ant-api03-test"
    assert client.auth_token is None


def test_neither_set_raises(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="Neither ANTHROPIC_API_KEY"):
        ac.get_client()
