"""Nami create_calendar_event 的 zoom 參數：連結寫進事件、失敗降級、孤兒會議 rollback。"""

from __future__ import annotations

from unittest.mock import patch

from gateway.handlers.nami import NamiHandler
from shared.google_calendar import CalendarEvent
from shared.zoom import ZoomError, ZoomMeeting

_ARGS = {
    "title": "跟 Angie 開會",
    "start": "2026-04-25T15:00:00",
    "end": "2026-04-25T16:00:00",
    "category": "work",
    "also_create_task": False,
}
_MEETING = ZoomMeeting(id="881", join_url="https://zoom.us/j/881?pwd=abc", passcode="pw1")


def _event() -> CalendarEvent:
    return CalendarEvent(
        id="evt1",
        title="跟 Angie 開會",
        start="2026-04-25T15:00:00+08:00",
        end="2026-04-25T16:00:00+08:00",
        html_link="https://calendar.google.com/evt1",
    )


def _run(args: dict, *, create_event=None, create_meeting=None, conflicts=None):
    with (
        patch(
            "gateway.handlers.nami.google_calendar.find_conflicts",
            return_value=conflicts or [],
        ) as find_conflicts,
        patch(
            "gateway.handlers.nami.google_calendar.create_event",
            **(create_event or {"return_value": _event()}),
        ) as ce,
        patch(
            "gateway.handlers.nami.zoom_client.create_meeting",
            **(create_meeting or {"return_value": _MEETING}),
        ) as cm,
        patch("gateway.handlers.nami.zoom_client.delete_meeting") as dm,
    ):
        outcome = NamiHandler()._tool_create_calendar_event(args)
    return outcome, ce, cm, dm, find_conflicts


def test_zoom_true_puts_link_in_description_and_location():
    outcome, ce, cm, dm, _ = _run({**_ARGS, "zoom": True, "description": "討論 Q2"})

    assert not outcome.is_error
    cm.assert_called_once_with(
        topic="跟 Angie 開會",
        start="2026-04-25T15:00:00",
        end="2026-04-25T16:00:00",
        agenda="討論 Q2",
    )
    kwargs = ce.call_args.kwargs
    assert kwargs["location"] == _MEETING.join_url
    assert kwargs["description"].startswith("討論 Q2\n\n🎥 Zoom 會議")
    assert _MEETING.join_url in kwargs["description"]
    assert "Passcode：pw1" in kwargs["description"]
    assert kwargs["check_conflict"] is False  # 已在建 Zoom 前自己查過
    assert _MEETING.join_url in outcome.content
    assert outcome.event["payload"]["zoom_meeting_id"] == "881"
    dm.assert_not_called()


def test_zoom_default_false_does_not_touch_zoom():
    outcome, ce, cm, _, find_conflicts = _run(dict(_ARGS))

    assert not outcome.is_error
    cm.assert_not_called()
    find_conflicts.assert_not_called()
    assert "location" not in ce.call_args.kwargs
    assert ce.call_args.kwargs["check_conflict"] is True
    assert outcome.event["payload"]["zoom_meeting_id"] is None


def test_zoom_failure_still_creates_event_and_warns():
    outcome, ce, _, _, _ = _run(
        {**_ARGS, "zoom": True},
        create_meeting={"side_effect": ZoomError("Zoom 授權失敗：xxx")},
    )

    assert not outcome.is_error  # 事件已建立
    ce.assert_called_once()
    assert "location" not in ce.call_args.kwargs
    assert "Zoom 會議沒建成" in outcome.content
    assert "Zoom 授權失敗" in outcome.content
    assert "必須註明" in outcome.content


def test_conflict_checked_before_zoom_so_no_orphan_meeting():
    conflict = _event()
    outcome, ce, cm, dm, _ = _run({**_ARGS, "zoom": True}, conflicts=[conflict])

    assert outcome.is_error
    assert "時段衝突" in outcome.content
    cm.assert_not_called()  # 衝突時根本不該去建 Zoom 會議
    ce.assert_not_called()
    dm.assert_not_called()


def test_force_skips_conflict_check_but_still_creates_zoom():
    outcome, _, cm, _, find_conflicts = _run(
        {**_ARGS, "zoom": True, "force": True}, conflicts=[_event()]
    )

    assert not outcome.is_error
    find_conflicts.assert_not_called()
    cm.assert_called_once()


def test_create_event_conflict_race_discards_zoom_meeting():
    """預查沒撞、建事件時才撞（極小時間差）→ 回傳衝突 list，Zoom 會議要刪掉。"""
    outcome, _, _, dm, _ = _run({**_ARGS, "zoom": True}, create_event={"return_value": [_event()]})

    assert outcome.is_error
    dm.assert_called_once_with("881")


def test_create_event_raises_discards_zoom_meeting_and_reraises():
    import pytest

    with pytest.raises(RuntimeError, match="gcal down"):
        _run({**_ARGS, "zoom": True}, create_event={"side_effect": RuntimeError("gcal down")})

    # _run 內的 patch 已退出，單獨驗 delete 有被呼叫：重跑一次抓 dm
    with (
        patch("gateway.handlers.nami.google_calendar.find_conflicts", return_value=[]),
        patch(
            "gateway.handlers.nami.google_calendar.create_event",
            side_effect=RuntimeError("gcal down"),
        ),
        patch("gateway.handlers.nami.zoom_client.create_meeting", return_value=_MEETING),
        patch("gateway.handlers.nami.zoom_client.delete_meeting") as dm,
    ):
        with pytest.raises(RuntimeError):
            NamiHandler()._tool_create_calendar_event({**_ARGS, "zoom": True})
    dm.assert_called_once_with("881")


def test_task_write_failure_rolls_back_calendar_and_zoom():
    args = {**_ARGS, "zoom": True, "also_create_task": True}
    with (
        patch("gateway.handlers.nami.google_calendar.find_conflicts", return_value=[]),
        patch("gateway.handlers.nami.google_calendar.create_event", return_value=_event()),
        patch("gateway.handlers.nami.google_calendar.delete_event") as del_event,
        patch("gateway.handlers.nami.zoom_client.create_meeting", return_value=_MEETING),
        patch("gateway.handlers.nami.zoom_client.delete_meeting") as dm,
        patch("gateway.handlers.nami.list_files", return_value=[]),
        patch("gateway.handlers.nami.write_page", side_effect=OSError("disk full")),
    ):
        outcome = NamiHandler()._tool_create_calendar_event(args)

    assert outcome.is_error
    del_event.assert_called_once_with("evt1")
    dm.assert_called_once_with("881")


def test_zoom_rejects_all_day_event():
    outcome, ce, cm, _, _ = _run(
        {**_ARGS, "start": "2026-04-25", "end": "2026-04-26", "zoom": True}
    )

    assert outcome.is_error
    assert "整天" in outcome.content
    cm.assert_not_called()
    ce.assert_not_called()


def test_delete_failure_during_rollback_does_not_mask_original_outcome():
    with (
        patch("gateway.handlers.nami.google_calendar.find_conflicts", return_value=[]),
        patch("gateway.handlers.nami.google_calendar.create_event", return_value=[_event()]),
        patch("gateway.handlers.nami.zoom_client.create_meeting", return_value=_MEETING),
        patch(
            "gateway.handlers.nami.zoom_client.delete_meeting",
            side_effect=ZoomError("zoom down"),
        ),
    ):
        outcome = NamiHandler()._tool_create_calendar_event({**_ARGS, "zoom": True})

    assert outcome.is_error
    assert "時段衝突" in outcome.content
