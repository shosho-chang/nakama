"""頻道裡不在 thread 的話，不能被接進這個人還沒結束的流程（「找這人最新的對話」只限私訊）。

2026-11-17：曉宣在 #freedom-fleet 問修修事情、附截圖（沒 @Sanji、不在 thread），
被接進她的建活動流程，截圖變成封面主圖、還照樣建了活動。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

MANAGER = "UMANAGER"
CFG = {"gateway": {"slack": {"extra_allowed_users": {"sanji": [MANAGER]}}}}


@pytest.fixture(autouse=True)
def _access_env(monkeypatch):
    monkeypatch.setenv("SLACK_USER_ID_SHOSHO", "UOWNER")
    with patch("gateway.access.load_config", return_value=CFG):
        yield


def _message(event):
    from gateway import bot

    conv = SimpleNamespace(
        thread_ts="5.0", user_id=MANAGER, agent_name="sanji", flow_name="event_draft", state={}
    )
    handler = MagicMock()
    handler.continue_flow.return_value = SimpleNamespace(text="流程回覆", continuation=None)
    handler.preempt_dm.return_value = None
    store = MagicMock()
    store.get.side_effect = lambda ts: conv if ts == "5.0" else None
    store.get_latest_for_user_and_agent.return_value = conv
    say = MagicMock()
    with (
        patch.object(bot, "get_store", return_value=store),
        patch.object(bot, "get_handler", return_value=handler),
        patch.object(bot, "with_attachments", side_effect=lambda t, e, c, **kw: t),
    ):
        bot._make_thread_message_handler("sanji")(event, say, MagicMock())
    return handler, store


def test_top_level_channel_message_is_not_pulled_into_flow():
    event = {"text": "修修，這個圖示是什麼？", "user": MANAGER, "channel": "C1", "ts": "9.0"}
    event["channel_type"] = "channel"
    handler, store = _message(event)
    handler.continue_flow.assert_not_called()
    store.get_latest_for_user_and_agent.assert_not_called()


def test_reply_in_flow_thread_still_continues():
    event = {"text": "照排", "user": MANAGER, "channel": "C1", "ts": "9.0", "thread_ts": "5.0"}
    event["channel_type"] = "channel"
    handler, _ = _message(event)
    handler.continue_flow.assert_called_once()


def test_dm_without_thread_still_finds_latest_flow():
    event = {"text": "照排", "user": MANAGER, "channel": "D1", "ts": "9.0", "channel_type": "im"}
    handler, store = _message(event)
    store.get_latest_for_user_and_agent.assert_called_once()
    handler.continue_flow.assert_called_once()
