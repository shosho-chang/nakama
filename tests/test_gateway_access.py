"""gateway/access.py + bot.py 權限接線測試。

情境（2026-11-13 修修裁決）：社群經理曉宣可以跟 Sanji 對話；Nami、Zoro、Franky
（`/franky` 斜線指令）以及其他 agent 只有修修能用。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

OWNER = "UOWNER"
MANAGER = "UMANAGER"
STRANGER = "USTRANGER"

CFG = {"gateway": {"slack": {"extra_allowed_users": {"sanji": [MANAGER]}}}}


@pytest.fixture(autouse=True)
def _access_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SLACK_USER_ID_SHOSHO", OWNER)
    with patch("gateway.access.load_config", return_value=CFG):
        yield


# ── is_allowed ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("agent", ["nami", "zoro", "sanji", "franky", "orchestrator"])
def test_owner_can_use_every_agent(agent):
    from gateway.access import is_allowed

    assert is_allowed(agent, OWNER)


def test_manager_can_use_sanji_only():
    from gateway.access import is_allowed

    assert is_allowed("sanji", MANAGER)
    for agent in ("nami", "zoro", "franky", "orchestrator"):
        assert not is_allowed(agent, MANAGER), agent


def test_stranger_is_denied_everywhere():
    from gateway.access import is_allowed

    for agent in ("nami", "zoro", "sanji", "franky"):
        assert not is_allowed(agent, STRANGER), agent


def test_empty_user_id_is_denied():
    from gateway.access import is_allowed

    assert not is_allowed("sanji", "")


def test_missing_owner_env_fails_closed(monkeypatch: pytest.MonkeyPatch):
    """沒設 SLACK_USER_ID_SHOSHO 不能變成「誰都可以」。"""
    from gateway import access

    monkeypatch.setattr(access, "_owner_id", lambda: "")
    assert not access.is_allowed("nami", OWNER)
    assert not access.is_allowed("nami", "")
    assert access.is_allowed("sanji", MANAGER)


def test_missing_config_section_means_owner_only():
    from gateway.access import is_allowed

    with patch("gateway.access.load_config", return_value={}):
        assert is_allowed("sanji", OWNER)
        assert not is_allowed("sanji", MANAGER)


def test_repo_config_opens_sanji_to_the_community_manager():
    """真的 config.yaml：曉宣只在 sanji 白名單上。"""
    from shared.config import load_config

    table = load_config()["gateway"]["slack"]["extra_allowed_users"]
    assert table == {"sanji": ["U0CFJS8A8K1"]}


# ── bot.py 接線 ─────────────────────────────────────────────────────────


def _handler():
    h = MagicMock()
    h.handle.return_value = SimpleNamespace(text="ok", continuation=None)
    h.can_handle.return_value = True
    return h


def _mention(agent: str, user: str, thread_ts: str | None = None):
    from gateway import bot

    handler = _handler()
    say = MagicMock()
    event = {"text": "<@UBOT> 嗨", "user": user, "channel": "C1", "ts": "9.0"}
    if thread_ts:
        event["thread_ts"] = thread_ts
    route = SimpleNamespace(agent=agent, intent="general", text="嗨", confidence="exact")
    with (
        patch.object(bot, "get_handler", return_value=handler),
        patch.object(bot, "route_mention", return_value=route),
        patch.object(bot, "_register_continuation"),
        patch.object(bot, "with_attachments", side_effect=lambda t, e, c: t),
    ):
        bot._make_mention_handler(agent)(event, say, MagicMock())
    return handler, say


def test_mention_by_manager_reaches_sanji():
    handler, _ = _mention("sanji", MANAGER)
    handler.handle.assert_called_once()


def test_mention_by_manager_to_nami_is_refused_in_thread():
    from gateway.access import DENY_MESSAGE

    handler, say = _mention("nami", MANAGER)
    handler.handle.assert_not_called()
    say.assert_called_once_with(DENY_MESSAGE, thread_ts="9.0")


def test_mention_refusal_stays_in_existing_thread():
    from gateway.access import DENY_MESSAGE

    _, say = _mention("zoro", STRANGER, thread_ts="5.0")
    say.assert_called_once_with(DENY_MESSAGE, thread_ts="5.0")


def _dm(agent: str, user: str):
    from gateway import bot

    handler = _handler()
    store = MagicMock()
    store.get.return_value = None
    store.get_latest_for_user_and_agent.return_value = None
    say = MagicMock()
    event = {"text": "嗨", "user": user, "channel": "D1", "channel_type": "im", "ts": "1.0"}
    route = SimpleNamespace(agent=agent, intent="general", text="嗨", confidence="exact")
    with (
        patch.object(bot, "get_store", return_value=store),
        patch.object(bot, "get_handler", return_value=handler),
        patch.object(bot, "route_mention", return_value=route),
        patch.object(bot, "with_attachments", side_effect=lambda t, e, c: t),
    ):
        bot._make_thread_message_handler(agent)(event, say, MagicMock())
    return handler, say


def test_dm_by_manager_reaches_sanji():
    handler, _ = _dm("sanji", MANAGER)
    handler.handle.assert_called_once()


def test_dm_by_manager_to_nami_is_refused():
    from gateway.access import DENY_MESSAGE

    handler, say = _dm("nami", MANAGER)
    handler.handle.assert_not_called()
    say.assert_called_once_with(DENY_MESSAGE)


def test_dm_by_owner_to_nami_still_works():
    handler, _ = _dm("nami", OWNER)
    handler.handle.assert_called_once()


def test_thread_continuation_stops_after_removal_from_allowlist():
    from gateway import bot

    handler = _handler()
    conv = SimpleNamespace(
        thread_ts="5.0", user_id=MANAGER, agent_name="sanji", flow_name="f", state={}
    )
    store = MagicMock()
    store.get.return_value = conv
    event = {"text": "繼續", "user": MANAGER, "channel": "C1", "ts": "6.0", "thread_ts": "5.0"}
    with (
        patch("gateway.access.load_config", return_value={}),
        patch.object(bot, "get_store", return_value=store),
        patch.object(bot, "get_handler", return_value=handler),
    ):
        bot._make_thread_message_handler("sanji")(event, MagicMock(), MagicMock())
    handler.continue_flow.assert_not_called()


def _slash(cmd: str, user: str, agent: str):
    from gateway import bot

    handler = _handler()
    respond = MagicMock()
    route = SimpleNamespace(agent=agent, intent="general", text="", confidence="exact")
    with (
        patch.object(bot, "get_handler", return_value=handler),
        patch.object(bot, "route_slash_command", return_value=route),
    ):
        bot._make_slash_command_handler()(
            MagicMock(), {"command": cmd, "text": "", "user_id": user}, respond
        )
    return handler, respond


def test_slash_franky_refused_for_manager():
    from gateway.access import DENY_MESSAGE

    handler, respond = _slash("/franky", MANAGER, "franky")
    handler.handle.assert_not_called()
    respond.assert_called_once_with(DENY_MESSAGE)


def test_slash_sanji_allowed_for_manager():
    handler, _ = _slash("/sanji", MANAGER, "sanji")
    handler.handle.assert_called_once()
