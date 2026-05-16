"""Auth-precedence tests for shared.anthropic_client.get_client.

Covers the NAKAMA_REQUIRE_MAX_PLAN hard lock — workflows that opt in must
never silently fall back to API-key billing even if the env var is set.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def fresh_module(monkeypatch):
    """Return a freshly-imported anthropic_client with cleared singleton + env."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("NAKAMA_REQUIRE_MAX_PLAN", raising=False)

    import shared.anthropic_client as mod

    importlib.reload(mod)
    return mod


def test_max_plan_lock_ignores_api_key_when_oauth_present(fresh_module, monkeypatch):
    monkeypatch.setenv("NAKAMA_REQUIRE_MAX_PLAN", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api-FAKE")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-FAKE")

    client = fresh_module.get_client()
    # Anthropic SDK stores api_key on the instance; auth_token path leaves it None.
    assert client.api_key is None, "API key should be ignored under Max Plan lock"
    # The Authorization header should be a Bearer token from the OAuth path.
    assert client.auth_token == "sk-ant-oat01-FAKE"


def test_max_plan_lock_raises_when_no_oauth(fresh_module, monkeypatch):
    monkeypatch.setenv("NAKAMA_REQUIRE_MAX_PLAN", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api-FAKE")
    # No OAuth token set.

    with pytest.raises(RuntimeError, match="NAKAMA_REQUIRE_MAX_PLAN"):
        fresh_module.get_client()


def test_default_mode_prefers_api_key(fresh_module, monkeypatch):
    """Without the lock, the original precedence is preserved (API key wins)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api-FAKE")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-FAKE")

    client = fresh_module.get_client()
    assert client.api_key == "sk-ant-api-FAKE"


def test_default_mode_falls_back_to_oauth(fresh_module, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-FAKE")

    client = fresh_module.get_client()
    assert client.api_key is None
    assert client.auth_token == "sk-ant-oat01-FAKE"
