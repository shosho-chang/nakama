"""私訊攔截：Sanji 收到會議紀錄核准／修改、行動項目「做完了」時，先交給 freedom-fleet 判斷。

2026-11-17 修修裁決：會議紀錄由曉宣核准，要改直接私訊 Sanji；行動項目每天提醒、回「1 做完了」記錄。
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gateway.handlers.base import HandlerResponse
from shared import fleet_events

OWNER = "UOWNER"
MANAGER = "UMANAGER"
CFG = {"gateway": {"slack": {"extra_allowed_users": {"sanji": [MANAGER]}}}}


@pytest.fixture(autouse=True)
def _access_env(monkeypatch):
    monkeypatch.setenv("SLACK_USER_ID_SHOSHO", OWNER)
    with patch("gateway.access.load_config", return_value=CFG):
        yield


def _dm(text, *, user=MANAGER, preempt=None, conv=None):
    from gateway import bot

    handler = MagicMock()
    handler.handle.return_value = SimpleNamespace(text="一般回覆", continuation=None)
    handler.continue_flow.return_value = SimpleNamespace(text="流程回覆", continuation=None)
    handler.preempt_dm.return_value = preempt
    store = MagicMock()
    store.get.return_value = None
    store.get_latest_for_user_and_agent.return_value = conv
    say = MagicMock()
    event = {"text": text, "user": user, "channel": "D1", "channel_type": "im", "ts": "1.0"}
    route = SimpleNamespace(agent="sanji", intent="general", text=text, confidence="exact")
    with (
        patch.object(bot, "get_store", return_value=store),
        patch.object(bot, "get_handler", return_value=handler),
        patch.object(bot, "route_mention", return_value=route),
        patch.object(bot, "with_attachments", side_effect=lambda t, e, c, **kw: t),
    ):
        bot._make_thread_message_handler("sanji")(event, say, MagicMock())
    return handler, say


def test_preempted_dm_replies_and_skips_normal_routing():
    handler, say = _dm("核准", preempt=HandlerResponse(text="收到，第 1 版核准！"))
    handler.preempt_dm.assert_called_once_with("核准", MANAGER, in_flow=False)
    handler.handle.assert_not_called()
    assert "收到，第 1 版核准！" in say.call_args.kwargs["text"]


def test_not_preempted_falls_through_to_handle():
    handler, _ = _dm("嗨")
    handler.handle.assert_called_once()


def test_in_flow_flag_and_flow_continues_when_not_preempted():
    conv = SimpleNamespace(
        thread_ts="5.0", user_id=MANAGER, agent_name="sanji", flow_name="event_draft", state={}
    )
    handler, _ = _dm("週四晚上八點", conv=conv)
    assert handler.preempt_dm.call_args.kwargs == {"in_flow": True}
    handler.continue_flow.assert_called_once()


def test_preempt_error_falls_back_to_normal_routing():
    from gateway import bot

    handler = MagicMock()
    handler.preempt_dm.side_effect = RuntimeError("boom")
    say = MagicMock()
    with patch.object(bot, "get_handler", return_value=handler):
        assert bot._preempt_dm("sanji", {"text": "x", "user": MANAGER}, say, None) is False
    say.assert_not_called()


def test_stranger_dm_is_never_preempted():
    from gateway import bot

    handler = MagicMock()
    with patch.object(bot, "get_handler", return_value=handler):
        assert (
            bot._preempt_dm("sanji", {"text": "核准", "user": "USTRANGER"}, MagicMock(), None)
            is False
        )
    handler.preempt_dm.assert_not_called()


# ── 頻道 @Sanji ──────────────────────────────────────────────────────────


def _mention(text, *, user=MANAGER, preempt=None, thread_ts=None):
    from gateway import bot

    handler = MagicMock()
    handler.handle.return_value = SimpleNamespace(text="一般回覆", continuation=None)
    handler.preempt_mention.return_value = preempt
    say = MagicMock(return_value={"ts": "9.0"})
    event = {"text": text, "user": user, "channel": "C1", "ts": "2.0"}
    if thread_ts:
        event["thread_ts"] = thread_ts
    route = SimpleNamespace(agent="sanji", intent="general", text=text, confidence="exact")
    with (
        patch.object(bot, "get_store", return_value=MagicMock()),
        patch.object(bot, "get_handler", return_value=handler),
        patch.object(bot, "route_mention", return_value=route),
        patch.object(bot, "with_attachments", side_effect=lambda t, e, c, **kw: t),
    ):
        bot._make_mention_handler("sanji")(event, say, MagicMock())
    return handler, say


def test_mention_preempted_replies_in_thread_and_skips_routing():
    handler, say = _mention("<@USANJI> 3 做完了", preempt=HandlerResponse(text="✓ 3. 大旗"))
    handler.preempt_mention.assert_called_once_with("3 做完了", MANAGER)
    handler.handle.assert_not_called()
    assert say.call_args.kwargs["thread_ts"] == "2.0"
    assert "✓ 3. 大旗" in say.call_args.kwargs["text"]


def test_mention_not_preempted_goes_to_handle():
    handler, _ = _mention("<@USANJI> 幫我建活動：週四讀書會")
    handler.handle.assert_called_once()


def test_mention_preempt_error_falls_back():
    from gateway import bot

    handler = MagicMock()
    handler.preempt_mention.side_effect = RuntimeError("boom")
    say = MagicMock()
    assert bot._preempt_mention("sanji", handler, "<@U1> 待辦", MANAGER, say, "1.0") is False
    say.assert_not_called()


def test_sanji_preempt_mention_asks_freedom_fleet():
    from gateway.handlers.sanji import SanjiHandler

    with patch.object(fleet_events, "minutes_mention", return_value="清單") as m:
        assert SanjiHandler().preempt_mention("待辦", OWNER).text == "清單"
    m.assert_called_once_with(OWNER, "待辦")
    with patch.object(fleet_events, "minutes_mention") as m:
        assert SanjiHandler().preempt_mention("幫我建活動：週四讀書會", OWNER) is None
    m.assert_not_called()


# ── Sanji.preempt_dm ─────────────────────────────────────────────────────


def test_sanji_preempt_asks_freedom_fleet():
    from gateway.handlers.sanji import SanjiHandler

    with patch.object(fleet_events, "minutes_dm", return_value="記下來了") as m:
        resp = SanjiHandler().preempt_dm("1 做完了", OWNER, in_flow=False)
    m.assert_called_once_with(OWNER, "1 做完了", in_flow=False)
    assert resp.text == "記下來了"


def test_sanji_preempt_leaves_event_requests_alone():
    from gateway.handlers.sanji import SanjiHandler

    with patch.object(fleet_events, "minutes_dm") as m:
        assert SanjiHandler().preempt_dm("幫我建活動：週四讀書會", MANAGER, in_flow=False) is None
    m.assert_not_called()


def test_sanji_preempt_none_when_not_minutes_related():
    from gateway.handlers.sanji import SanjiHandler

    with patch.object(fleet_events, "minutes_dm", return_value=None):
        assert SanjiHandler().preempt_dm("今天天氣好", MANAGER, in_flow=False) is None


# ── fleet_events.minutes_dm ──────────────────────────────────────────────


def _proc(stdout="", returncode=0):
    return SimpleNamespace(stdout=stdout, returncode=returncode, stderr="")


@pytest.fixture
def ff_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("FREEDOM_FLEET_DIR", str(tmp_path))
    monkeypatch.delenv("FREEDOM_FLEET_PYTHON", raising=False)
    return tmp_path


def test_minutes_dm_calls_tracker(ff_dir):
    out = {"ok": True, "handled": True, "reply": "收到"}
    with patch.object(subprocess, "run", return_value=_proc(json.dumps(out))) as m:
        assert fleet_events.minutes_dm(MANAGER, "核准", in_flow=True) == "收到"
    args, kw = m.call_args
    assert args[0][1:] == ["-m", "fleet.minutes.tracker", "dm"]
    assert json.loads(kw["input"]) == {
        "user": MANAGER,
        "text": "核准",
        "in_flow": True,
        "where": "dm",
    }


def test_minutes_mention_asks_channel_mode(ff_dir):
    out = {"ok": True, "handled": True, "reply": "✓ 3."}
    with patch.object(subprocess, "run", return_value=_proc(json.dumps(out))) as m:
        assert fleet_events.minutes_mention(OWNER, "3 做完了") == "✓ 3."
    assert json.loads(m.call_args.kwargs["input"]) == {
        "user": OWNER,
        "text": "3 做完了",
        "where": "channel",
    }


def test_minutes_dm_not_handled(ff_dir):
    body = json.dumps({"ok": True, "handled": False})
    with patch.object(subprocess, "run", return_value=_proc(body)):
        assert fleet_events.minutes_dm(MANAGER, "嗨", in_flow=False) is None


def test_minutes_dm_failure_never_blocks_chat(ff_dir):
    with patch.object(subprocess, "run", return_value=_proc("Traceback", returncode=1)):
        assert fleet_events.minutes_dm(MANAGER, "核准", in_flow=False) is None
    with patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired("x", 20)):
        assert fleet_events.minutes_dm(MANAGER, "核准", in_flow=False) is None
