"""Tests for agents/franky/slack_bot.py.

Coverage:
- from_env returns no-op stub when either env var is missing
- from_env returns real bot when both envs present
- post_alert calls conversations_open then chat_postMessage
- DM channel cached across multiple post_alerts
- SlackApiError → returns None, does not raise
- Severity emoji prefixes correctly
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from slack_sdk.errors import SlackApiError

from agents.franky.slack_bot import FrankySlackBot, _NoopSlackStub
from shared.schemas.franky import AlertV1


def _alert(severity: str = "critical") -> AlertV1:
    return AlertV1(
        rule_id="test_rule",
        severity=severity,  # type: ignore[arg-type]
        title="title",
        message="message",
        fired_at=datetime.now(timezone.utc),
        dedup_key="test_rule",
        operation_id="op_12345678",
    )


# ---------------------------------------------------------------------------
# from_env routing
# ---------------------------------------------------------------------------


@pytest.mark.real_slack
def test_from_env_missing_token_returns_stub(monkeypatch):
    monkeypatch.delenv("SLACK_FRANKY_BOT_TOKEN", raising=False)
    monkeypatch.setenv("SLACK_USER_ID_SHOSHO", "U123")
    bot = FrankySlackBot.from_env()
    assert isinstance(bot, _NoopSlackStub)


@pytest.mark.real_slack
def test_from_env_missing_user_returns_stub(monkeypatch):
    monkeypatch.setenv("SLACK_FRANKY_BOT_TOKEN", "xoxb-x")
    monkeypatch.delenv("SLACK_USER_ID_SHOSHO", raising=False)
    bot = FrankySlackBot.from_env()
    assert isinstance(bot, _NoopSlackStub)


@pytest.mark.real_slack
def test_from_env_both_present_returns_real(monkeypatch):
    monkeypatch.setenv("SLACK_FRANKY_BOT_TOKEN", "xoxb-testtoken")
    monkeypatch.setenv("SLACK_USER_ID_SHOSHO", "U07CAFEBABE")
    bot = FrankySlackBot.from_env()
    assert isinstance(bot, FrankySlackBot)


# ---------------------------------------------------------------------------
# Stub always returns None, never calls external
# ---------------------------------------------------------------------------


def test_noop_stub_post_alert_returns_none():
    stub = _NoopSlackStub()
    assert stub.post_alert(_alert()) is None


# ---------------------------------------------------------------------------
# Real bot with mocked WebClient
# ---------------------------------------------------------------------------


def _make_real_bot() -> tuple[FrankySlackBot, MagicMock]:
    client = MagicMock()
    client.conversations_open.return_value = {"channel": {"id": "D123"}}
    client.chat_postMessage.return_value = {"ts": "1700000000.001100"}
    bot = FrankySlackBot(bot_token="xoxb-x", user_id="U123", client=client)
    return bot, client


def test_post_alert_opens_dm_and_posts():
    bot, client = _make_real_bot()
    ts = bot.post_alert(_alert())
    assert ts == "1700000000.001100"
    client.conversations_open.assert_called_once_with(users="U123")
    client.chat_postMessage.assert_called_once()
    call = client.chat_postMessage.call_args
    assert call.kwargs["channel"] == "D123"
    assert "rotating_light" in call.kwargs["text"]  # critical emoji
    assert call.kwargs["unfurl_links"] is False


def test_post_alert_caches_dm_channel():
    """conversations_open should be called only once across multiple post_alerts."""
    bot, client = _make_real_bot()
    bot.post_alert(_alert())
    bot.post_alert(_alert())
    bot.post_alert(_alert())
    assert client.conversations_open.call_count == 1
    assert client.chat_postMessage.call_count == 3


def test_post_alert_returns_none_on_slack_error():
    bot, client = _make_real_bot()
    client.chat_postMessage.side_effect = SlackApiError(
        "bad", response={"ok": False, "error": "invalid_auth"}
    )
    assert bot.post_alert(_alert()) is None


@pytest.mark.parametrize(
    "severity,emoji",
    [
        ("critical", "rotating_light"),
        ("warning", "warning"),
        ("info", "information_source"),
    ],
)
def test_severity_emoji_in_text(severity, emoji):
    bot, client = _make_real_bot()
    bot.post_alert(_alert(severity=severity))
    text = client.chat_postMessage.call_args.kwargs["text"]
    assert emoji in text
