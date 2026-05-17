"""ADR-026 Slice 2 — anthropic_client per-call auth_policy dispatch matrix.

Verifies that `ask_claude` / `ask_claude_multi` / `call_claude_with_tools`
route to CLI vs SDK based on the (auth_policy, oauth_available, cli_available,
supports_cli) tuple, and that observability rows carry the right
auth_requested / auth_actual / fallback_reason.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import shared.anthropic_client as ac


@pytest.fixture(autouse=True)
def _reset_anthropic_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear singleton + auth env between cases so dispatch reads our fixture state."""
    ac._client = None
    for key in (
        "NAKAMA_REQUIRE_MAX_PLAN",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "NAKAMA_CLAUDE_CLI",
    ):
        monkeypatch.delenv(key, raising=False)
    # Default: pretend OAuth + CLI both unavailable. Tests opt in.
    monkeypatch.setattr(ac, "_oauth_token_available", lambda: False)
    monkeypatch.setattr(ac, "_cli_binary_available", lambda: False)


# ---------------------------------------------------------------------------
# _resolve_effective_policy
# ---------------------------------------------------------------------------


def test_resolve_default_is_api() -> None:
    assert ac._resolve_effective_policy(None) == "api"


def test_resolve_passthrough() -> None:
    assert ac._resolve_effective_policy("subscription_preferred") == "subscription_preferred"
    assert ac._resolve_effective_policy("api") == "api"


def test_resolve_hard_lock_outranks_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NAKAMA_REQUIRE_MAX_PLAN", "1")
    assert ac._resolve_effective_policy("api") == "subscription_required"
    assert ac._resolve_effective_policy(None) == "subscription_required"


# ---------------------------------------------------------------------------
# _plan_dispatch — softlinks / hard-locks
# ---------------------------------------------------------------------------


def test_plan_api_always_api() -> None:
    assert ac._plan_dispatch("api", supports_cli=True) == ("api", None)
    assert ac._plan_dispatch("api", supports_cli=False) == ("api", None)


def test_plan_preferred_softlinks_when_no_oauth(monkeypatch: pytest.MonkeyPatch) -> None:
    assert ac._plan_dispatch("subscription_preferred", supports_cli=True) == (
        "api",
        ac._REASON_NO_OAUTH,
    )


def test_plan_required_raises_when_no_oauth() -> None:
    with pytest.raises(RuntimeError, match="no OAuth token"):
        ac._plan_dispatch("subscription_required", supports_cli=True)


def test_plan_preferred_softlinks_when_no_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ac, "_oauth_token_available", lambda: True)
    assert ac._plan_dispatch("subscription_preferred", supports_cli=True) == (
        "api",
        ac._REASON_CLI_NOT_FOUND,
    )


def test_plan_required_raises_when_no_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ac, "_oauth_token_available", lambda: True)
    with pytest.raises(RuntimeError, match="'claude' CLI"):
        ac._plan_dispatch("subscription_required", supports_cli=True)


def test_plan_subscription_when_all_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ac, "_oauth_token_available", lambda: True)
    monkeypatch.setattr(ac, "_cli_binary_available", lambda: True)
    assert ac._plan_dispatch("subscription_preferred", supports_cli=True) == (
        "subscription",
        None,
    )
    assert ac._plan_dispatch("subscription_required", supports_cli=True) == (
        "subscription",
        None,
    )


def test_plan_tool_use_preferred_softlinks(monkeypatch: pytest.MonkeyPatch) -> None:
    """tool_use under subscription_preferred → soft-link to api with reason."""
    monkeypatch.setattr(ac, "_oauth_token_available", lambda: True)
    monkeypatch.setattr(ac, "_cli_binary_available", lambda: True)
    assert ac._plan_dispatch("subscription_preferred", supports_cli=False) == (
        "api",
        ac._REASON_TOOL_USE_VIA_CLI,
    )


def test_plan_tool_use_required_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ac, "_oauth_token_available", lambda: True)
    monkeypatch.setattr(ac, "_cli_binary_available", lambda: True)
    with pytest.raises(NotImplementedError, match="tool-use under subscription_required"):
        ac._plan_dispatch("subscription_required", supports_cli=False)


# ---------------------------------------------------------------------------
# _classify_cli_error
# ---------------------------------------------------------------------------


def test_classify_401_as_auth_expired() -> None:
    assert ac._classify_cli_error(Exception("got 401 Invalid auth")) == ac._REASON_CLI_AUTH_EXPIRED


def test_classify_other_as_subprocess_error() -> None:
    assert ac._classify_cli_error(Exception("subprocess died")) == ac._REASON_CLI_ERROR


# ---------------------------------------------------------------------------
# ask_claude routing — high-level
# ---------------------------------------------------------------------------


def test_ask_claude_subscription_calls_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ac, "_oauth_token_available", lambda: True)
    monkeypatch.setattr(ac, "_cli_binary_available", lambda: True)

    captured: dict = {}

    def fake_via_cli(prompt: str, **kw):
        captured.update(kw)
        captured["prompt"] = prompt
        return "cli-result"

    with patch("shared.claude_cli_client.ask_via_cli", side_effect=fake_via_cli):
        result = ac.ask_claude(
            "hi",
            model="claude-sonnet-4-6",
            auth_policy="subscription_preferred",
        )

    assert result == "cli-result"
    assert captured["auth_requested"] == "subscription_preferred"
    assert captured["auth_actual"] == "subscription"
    assert captured["fallback_reason"] is None


def test_ask_claude_subscription_preferred_softlinks_to_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No OAuth → silently fall back to SDK and emit fallback_reason."""
    # OAuth unavailable (default fixture), so dispatch resolves to api before
    # reaching the network. The SDK call mocked out — we just want to verify
    # the recorded usage carries the right auth cols.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-test")  # let get_client succeed

    fake_response = type(
        "Resp",
        (),
        {
            "content": [type("Block", (), {"text": "api-result"})()],
            "usage": type(
                "U",
                (),
                {
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            )(),
        },
    )()

    recorded: dict = {}

    def fake_record(**kw):
        recorded.update(kw)

    with patch("shared.anthropic_client.with_retry", return_value=fake_response):
        with patch("shared.anthropic_client.get_client"):
            with patch("shared.anthropic_client.record_call", side_effect=fake_record):
                result = ac.ask_claude(
                    "hi",
                    model="claude-sonnet-4-6",
                    auth_policy="subscription_preferred",
                )

    assert result == "api-result"
    assert recorded["auth_requested"] == "subscription_preferred"
    assert recorded["auth_actual"] == "api"
    assert recorded["fallback_reason"] == ac._REASON_NO_OAUTH


def test_ask_claude_required_no_oauth_raises() -> None:
    with pytest.raises(RuntimeError, match="no OAuth token"):
        ac.ask_claude("hi", model="claude-sonnet-4-6", auth_policy="subscription_required")


def test_call_claude_with_tools_required_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """tool-use under subscription_required must raise — CLI can't carry tool JSON."""
    monkeypatch.setattr(ac, "_oauth_token_available", lambda: True)
    monkeypatch.setattr(ac, "_cli_binary_available", lambda: True)
    with pytest.raises(NotImplementedError, match="tool-use"):
        ac.call_claude_with_tools(
            messages=[{"role": "user", "content": "hi"}],
            tools=[{"name": "x", "description": "x", "input_schema": {"type": "object"}}],
            model="claude-haiku-4-5",
            auth_policy="subscription_required",
        )
