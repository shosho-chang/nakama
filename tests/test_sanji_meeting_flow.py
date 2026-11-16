"""gateway/handlers/sanji_meeting.py — Sanji 開內部會議流程測試（全部 mock）。"""

from __future__ import annotations

import json
import sys
import types
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from gateway.handlers import sanji_event, sanji_meeting
from gateway.handlers.sanji import SanjiHandler
from shared.fleet_events import FleetEventsError
from tests import sanji_fake_calendar

TZ = sanji_event.TZ
FIXED_NOW = datetime(2026, 11, 16, 9, 0, tzinfo=TZ)  # 星期一
ZOOM_URL = "https://zoom.us/j/222?pwd=y"


def _llm(**over) -> str:
    data = {
        "title": "內部週會",
        "start": "2026-11-17T10:00:00+08:00",
        "end": None,
        "duration_min": 60,
        "missing": [],
    }
    data.update(over)
    return json.dumps(data, ensure_ascii=False)


@pytest.fixture
def ext(monkeypatch):
    calls: list[tuple[str, dict]] = []
    fail: dict[str, Exception] = {}

    def fake_run(command, payload):
        calls.append((command, payload))
        if command in fail:
            raise fail[command]
        return {"ok": True, "id": 222, "join_url": ZOOM_URL}

    client = MagicMock()
    client.chat_scheduleMessage.return_value = {"ok": True, "scheduled_message_id": "Q1"}
    slack_sdk = types.ModuleType("slack_sdk")
    slack_sdk.WebClient = MagicMock(return_value=client)

    monkeypatch.setattr(sanji_meeting.fleet_events, "run", fake_run)
    monkeypatch.setattr(sanji_event, "_now", lambda: FIXED_NOW)
    monkeypatch.setitem(sys.modules, "slack_sdk", slack_sdk)
    monkeypatch.setenv("SANJI_SLACK_BOT_TOKEN", "xoxb-sanji")
    monkeypatch.delenv("SANJI_MEETING_CHANNEL", raising=False)
    gcal = sanji_fake_calendar.install(monkeypatch)
    return types.SimpleNamespace(calls=calls, fail=fail, slack=slack_sdk, client=client, gcal=gcal)


def _start(replies, text="@Sanji 開內部會議 明天早上 10 點 內部週會 1 小時"):
    with patch.object(sanji_event, "ask", side_effect=list(replies)):
        return SanjiHandler().handle("general", text, "U1")


def _cont(state, text, replies=()):
    with patch.object(sanji_event, "ask", side_effect=list(replies)) as m:
        return SanjiHandler().continue_flow("internal_meeting", state, text, "U1"), m


@pytest.mark.parametrize("text", ["開內部會議", "幫我開個會", "約會議討論", "排個會"])
def test_trigger(text):
    assert sanji_meeting.is_meeting_request(text)


@pytest.mark.parametrize("text", ["今天好累", "會議好多", "幫我建活動"])
def test_non_trigger(text):
    assert not sanji_meeting.is_meeting_request(text)


def test_event_keyword_wins_over_meeting():
    """「建活動」走社群活動流程，不被開會關鍵字搶走。"""
    with (
        patch.object(sanji_event, "start_flow", return_value="EVENT") as ev_start,
        patch.object(sanji_meeting, "start_flow") as mt_start,
    ):
        SanjiHandler().handle("general", "開個會討論，順便建活動", "U1")
    ev_start.assert_called_once()
    mt_start.assert_not_called()


def test_card_then_confirm_creates_zoom_and_schedules_reminder(ext):
    resp = _start([_llm()])
    assert resp.continuation.flow_name == "internal_meeting"
    assert "主題：內部週會" in resp.text
    assert "2026/11/17（二） 10:00–11:00" in resp.text
    assert "不會發到社群" in resp.text
    assert "09:45" in resp.text and "<#C0CG3UDSTGU>" in resp.text
    assert ext.calls == []  # 確認前什麼都不做

    resp2, _ = _cont(resp.continuation.state, "確認")
    assert ext.calls == [
        ("zoom", {"title": "內部週會", "start": "2026-11-17T10:00:00+08:00", "duration_min": 60})
    ]
    ext.slack.WebClient.assert_called_once_with(token="xoxb-sanji")
    kw = ext.client.chat_scheduleMessage.call_args.kwargs
    assert kw["channel"] == "C0CG3UDSTGU"
    assert kw["post_at"] == int(datetime(2026, 11, 17, 9, 45, tzinfo=TZ).timestamp())
    assert "內部週會" in kw["text"] and ZOOM_URL in kw["text"]
    assert resp2.continuation is None
    assert ZOOM_URL in resp2.text and "09:45" in resp2.text


def test_channel_override(ext, monkeypatch):
    monkeypatch.setenv("SANJI_MEETING_CHANNEL", "CTEST")
    resp = _start([_llm()])
    _cont(resp.continuation.state, "確認")
    assert ext.client.chat_scheduleMessage.call_args.kwargs["channel"] == "CTEST"


def test_too_close_skips_reminder(ext):
    resp = _start([_llm(start="2026-11-16T09:10:00+08:00")])
    assert "不另外排提醒" in resp.text
    resp2, _ = _cont(resp.continuation.state, "確認")
    ext.client.chat_scheduleMessage.assert_not_called()
    assert "沒有另外排提醒" in resp2.text and resp2.continuation is None


def test_default_duration_shown(ext):
    resp = _start([_llm(duration_min=None)])
    assert "先抓 60 分鐘" in resp.text


def test_missing_start_asks(ext):
    resp = _start([_llm(start=None, missing=["start"])])
    assert "開會時間" in resp.text
    resp2, _ = _cont(resp.continuation.state, "確認")
    assert ext.calls == [] and resp2.continuation is not None


def test_schedule_failure_keeps_flow_and_retry_skips_zoom(ext):
    ext.client.chat_scheduleMessage.side_effect = RuntimeError("not_in_channel")
    resp = _start([_llm()])
    resp2, _ = _cont(resp.continuation.state, "確認")
    assert "排 Slack 提醒" in resp2.text and "not_in_channel" in resp2.text
    assert ZOOM_URL in resp2.text and resp2.continuation is not None

    ext.client.chat_scheduleMessage.side_effect = None
    resp3, _ = _cont(resp2.continuation.state, "確認")
    assert len(ext.calls) == 1  # 沒有重開 Zoom
    assert resp3.continuation is None


def test_zoom_maybe_done_warns(ext):
    ext.fail["zoom"] = FleetEventsError("逾時", maybe_done=True)
    resp = _start([_llm()])
    resp2, _ = _cont(resp.continuation.state, "確認")
    assert "避免重複" in resp2.text and resp2.continuation is not None


def test_revision_and_cancel(ext):
    resp = _start([_llm()])
    resp2, m = _cont(
        resp.continuation.state, "改成 11 點", [_llm(start="2026-11-17T11:00:00+08:00")]
    )
    assert "11:00–12:00" in resp2.text
    assert "改成 11 點" in m.call_args.kwargs["prompt"]
    resp3, _ = _cont(resp2.continuation.state, "取消")
    assert resp3.continuation is None and ext.calls == []


def test_meeting_written_to_calendar(ext):
    resp = _start([_llm()])
    assert "行事曆：這個時段沒有其他行程" in resp.text
    _cont(resp.continuation.state, "確認")
    created = ext.gcal.created[0]
    assert created["title"] == "內部週會"
    assert created["idempotency_key"] == "sanji-meeting-222@2026-11-17"
    assert ZOOM_URL in created["description"]


def test_meeting_conflict_blocks_then_force(ext):
    ext.gcal.conflicts = [
        sanji_fake_calendar.event(
            "客戶會議", "2026-11-17T09:30:00+08:00", "2026-11-17T10:30:00+08:00"
        )
    ]
    resp = _start([_llm()])
    assert "行事曆衝突：09:30–10:30「客戶會議」" in resp.text
    resp2, _ = _cont(resp.continuation.state, "確認")
    assert "先不排" in resp2.text and ext.calls == []
    resp3, _ = _cont(resp2.continuation.state, "照排")
    assert len(ext.calls) == 1 and resp3.continuation is None


# ── 與會者（2026-11-16：行事曆邀請＋提醒 @）──────────────────────────────


def test_attendees_parsing_skips_bad_entries(monkeypatch):
    monkeypatch.setenv(
        "SANJI_MEETING_ATTENDEES", "曉宣|xs@example.com|U123; 壞的|no-email|U9 ;; 小明|m@x.com|U456"
    )
    got = sanji_meeting.attendees()
    assert [a["name"] for a in got] == ["曉宣", "小明"]
    assert got[0] == {"name": "曉宣", "email": "xs@example.com", "slack": "U123"}


def test_attendees_invited_and_mentioned(ext, monkeypatch):
    monkeypatch.setenv("SANJI_MEETING_ATTENDEES", "曉宣|xs@example.com|U123")
    resp = _start([_llm()])
    assert "與會者：曉宣" in resp.text and "寄 Google 行事曆邀請" in resp.text
    resp2, _ = _cont(resp.continuation.state, "確認")
    assert ext.gcal.created[0]["attendees"] == ["xs@example.com"]
    assert ext.client.chat_scheduleMessage.call_args.kwargs["text"].startswith("<@U123> ⏰")
    assert "寄邀請給 曉宣" in resp2.text


def test_no_attendees_configured(ext, monkeypatch):
    monkeypatch.delenv("SANJI_MEETING_ATTENDEES", raising=False)
    resp = _start([_llm()])
    assert "與會者：（沒設定" in resp.text
    _cont(resp.continuation.state, "確認")
    assert ext.gcal.created[0]["attendees"] is None
    assert ext.client.chat_scheduleMessage.call_args.kwargs["text"].startswith("⏰")
