"""gateway 使用者白名單測試。

Slack workspace 邀了其他成員後，agent（尤其 Nami：Gmail / 行事曆 / vault）不能
對任何人開放。白名單 = `SLACK_ALLOWED_USER_IDS` ∪ `SLACK_USER_ID_SHOSHO`；
兩個都沒設時 gateway 拒絕啟動（fail-closed）。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

OWNER = "U_OWNER"
STRANGER = "U_STRANGER"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    for key in list(os.environ.keys()):
        if key.endswith("_SLACK_BOT_TOKEN") or key.endswith("_SLACK_APP_TOKEN"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("SLACK_ALLOWED_USER_IDS", raising=False)
    monkeypatch.delenv("SLACK_USER_ID_SHOSHO", raising=False)


# ── 白名單解析 ──────────────────────────────────────────────


def test_allowed_users_empty_when_nothing_set():
    from gateway.access import allowed_user_ids

    assert allowed_user_ids() == frozenset()


def test_owner_id_is_allowed_by_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SLACK_USER_ID_SHOSHO", OWNER)
    from gateway.access import is_allowed

    assert is_allowed(OWNER)
    assert not is_allowed(STRANGER)


def test_extra_ids_parsed_from_comma_and_space_separated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SLACK_USER_ID_SHOSHO", OWNER)
    monkeypatch.setenv("SLACK_ALLOWED_USER_IDS", " U_A, U_B  U_C ,, ")
    from gateway.access import allowed_user_ids

    assert allowed_user_ids() == frozenset({OWNER, "U_A", "U_B", "U_C"})


def test_empty_user_id_never_allowed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SLACK_USER_ID_SHOSHO", OWNER)
    from gateway.access import is_allowed

    assert not is_allowed("")


def test_run_refuses_to_start_without_allowlist(monkeypatch: pytest.MonkeyPatch):
    """有 bot token 但沒設任何白名單 → 不能默默對整個 workspace 開放。"""
    monkeypatch.setenv("NAMI_SLACK_BOT_TOKEN", "xoxb-nami")
    monkeypatch.setenv("NAMI_SLACK_APP_TOKEN", "xapp-nami")
    from gateway import bot

    with (
        patch("gateway.bot.load_config"),
        pytest.raises(RuntimeError, match="SLACK_ALLOWED_USER_IDS"),
    ):
        bot.run()


# ── 三個 Slack 入口 ─────────────────────────────────────────


@pytest.fixture
def fake_handler():
    from gateway.handlers.base import HandlerResponse

    handler = MagicMock()
    handler.handle.return_value = HandlerResponse(text="ok")
    handler.can_handle.return_value = True
    with (
        patch("gateway.bot.get_handler", return_value=handler),
        patch("gateway.bot.with_attachments", side_effect=lambda text, *a, **k: text),
        patch("gateway.bot.format_agent_response", return_value=("ok", [])),
    ):
        yield handler


@pytest.fixture
def owner_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_USER_ID_SHOSHO", OWNER)


def _mention(user: str) -> dict:
    return {"text": "<@UBOT> 今天有什麼行程", "user": user, "channel": "C1", "ts": "1.0"}


def _dm(user: str) -> dict:
    return {"text": "幫我寄信", "user": user, "channel": "D1", "ts": "2.0", "channel_type": "im"}


def test_mention_from_stranger_is_ignored(owner_only, fake_handler):
    from gateway import bot

    say = MagicMock()
    with patch("gateway.bot.route_mention") as route:
        bot._make_mention_handler("nami")(_mention(STRANGER), say, MagicMock())

    fake_handler.handle.assert_not_called()
    route.assert_not_called()  # 連 intent 分類（Haiku）都不該花
    say.assert_not_called()


def test_mention_from_owner_is_handled(owner_only, fake_handler):
    from gateway import bot
    from gateway.router import RouteResult

    say = MagicMock(return_value={"ts": "1.1"})
    route = RouteResult(agent="nami", intent="general", text="今天有什麼行程", confidence="exact")
    with patch("gateway.bot.route_mention", return_value=route):
        bot._make_mention_handler("nami")(_mention(OWNER), say, MagicMock())

    fake_handler.handle.assert_called_once()
    say.assert_called_once()


def test_dm_from_stranger_is_ignored(owner_only, fake_handler):
    from gateway import bot

    say = MagicMock()
    with patch("gateway.bot.route_mention") as route:
        bot._make_thread_message_handler("nami")(_dm(STRANGER), say, MagicMock())

    fake_handler.handle.assert_not_called()
    route.assert_not_called()
    say.assert_not_called()


def test_dm_from_owner_is_handled(owner_only, fake_handler):
    from gateway import bot
    from gateway.router import RouteResult

    say = MagicMock()
    route = RouteResult(agent="nami", intent="general", text="幫我寄信", confidence="exact")
    with (
        patch("gateway.bot.route_mention", return_value=route),
        patch("gateway.bot.get_store") as store,
    ):
        store.return_value.get.return_value = None
        store.return_value.get_latest_for_user_and_agent.return_value = None
        bot._make_thread_message_handler("nami")(_dm(OWNER), say, MagicMock())

    fake_handler.handle.assert_called_once()


def test_slash_command_from_stranger_is_denied(owner_only, fake_handler):
    from gateway import bot

    ack, respond = MagicMock(), MagicMock()
    command = {"command": "/nami", "text": "列出任務", "user_id": STRANGER}
    with patch("gateway.bot.route_slash_command") as route:
        bot._make_slash_command_handler()(ack, command, respond)

    ack.assert_called_once()  # Slack 要求 3 秒內 ack，擋人也要 ack
    route.assert_not_called()
    fake_handler.handle.assert_not_called()
    respond.assert_called_once()
    assert "權限" in respond.call_args.args[0]


def test_slash_command_from_owner_is_handled(owner_only, fake_handler):
    from gateway import bot
    from gateway.router import RouteResult

    ack, respond = MagicMock(), MagicMock()
    command = {"command": "/nami", "text": "列出任務", "user_id": OWNER}
    route = RouteResult(agent="nami", intent="general", text="列出任務", confidence="exact")
    with patch("gateway.bot.route_slash_command", return_value=route):
        bot._make_slash_command_handler()(ack, command, respond)

    fake_handler.handle.assert_called_once()
