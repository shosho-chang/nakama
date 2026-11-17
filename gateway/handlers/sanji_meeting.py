"""Sanji 開內部會議 — 在 #freedom-fleet 請 Sanji 開一場內部 Zoom 會議並在 Slack 提醒。

2026-11-16 修修需求：給內部參加的會議，只開 Zoom、在 Slack 設提醒，不碰社群。

流程（多輪，flow_name = ``internal_meeting``）：

1. 觸發：訊息含開會關鍵字（見 :data:`TRIGGER_KEYWORDS`）→ :func:`start_flow`
2. LLM 把訊息整理成 JSON（``prompts/sanji/meeting_parse.md``）→ 確認卡；缺主題或時間就只問缺的
3. :func:`continue_flow`：取消 → 結束；確認 → 執行；其他 → 當修改指示
4. 執行：開 Zoom（freedom-fleet ``fleet.events zoom``）→ 用 Slack 官方的排程訊息
   （``chat.scheduleMessage``）在開會前 :data:`REMIND_BEFORE_MIN` 分鐘到提醒頻道貼提醒。
   Zoom 開好後若排程失敗，流程保留；再回「確認」只補排提醒，不會重開 Zoom。

提醒頻道：env ``SANJI_MEETING_CHANNEL``，預設 #freedom-fleet。
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta

from gateway.handlers import sanji_calendar as cal
from gateway.handlers import sanji_event as ev
from gateway.handlers.base import Continuation, HandlerResponse
from shared import fleet_events
from shared.log import get_logger
from shared.prompt_loader import load_prompt

logger = get_logger("nakama.gateway.sanji_meeting")

FLOW_NAME = "internal_meeting"
DEFAULT_DURATION_MIN = 60
REMIND_BEFORE_MIN = 15
DEFAULT_CHANNEL = "C0CG3UDSTGU"  # #freedom-fleet
ENV_CHANNEL = "SANJI_MEETING_CHANNEL"
# Slack 規定排程時間要在未來；太近就不排（直接在討論串提醒就好）
_MIN_LEAD_S = 60

TRIGGER_KEYWORDS = ("內部會議", "開會議", "約會議", "開個會", "排個會", "開一場會")
_TRIGGER_RE = re.compile("|".join(re.escape(k) for k in TRIGGER_KEYWORDS))

_REQUIRED = ("title", "start")
_MISSING_LABELS = {"title": "會議主題", "start": "開會時間（哪一天、幾點開始）"}


def is_meeting_request(text: str) -> bool:
    body, _ = ev.extract_image_lines(text or "")
    return bool(_TRIGGER_RE.search(body))


def _channel() -> str:
    return os.environ.get(ENV_CHANNEL, "").strip() or DEFAULT_CHANNEL


# 內部會議的固定與會者（2026-11-16 修修要求：行事曆寄邀請＋提醒 @ 她）。
# 放在 VPS .env、不進 repo（含 email）。格式：「名字|email|Slack 使用者 ID」，多人用 ; 分隔。
ENV_ATTENDEES = "SANJI_MEETING_ATTENDEES"


def attendees() -> list[dict]:
    out = []
    for chunk in os.environ.get(ENV_ATTENDEES, "").split(";"):
        parts = [p.strip() for p in chunk.split("|")]
        if len(parts) == 3 and parts[0] and "@" in parts[1] and parts[2]:
            out.append({"name": parts[0], "email": parts[1], "slack": parts[2]})
        elif chunk.strip():
            logger.warning(f"{ENV_ATTENDEES} 有一筆格式不對，略過：{chunk.strip()[:40]!r}")
    return out


def _attendee_line() -> str:
    names = "、".join(a["name"] for a in attendees())
    if not names:
        return "與會者：（沒設定，只有你；不會寄邀請）"
    return f"與會者：{names}（確認後寄 Google 行事曆邀請，開會前提醒會 @ 她／他）"


# ── 解析 ────────────────────────────────────────────────────────────────


def _build_prompt(user_message: str, current: dict | None) -> str:
    now = ev._now()
    current_json = "（無）"
    if current:
        keep = ("title", "start", "end", "duration_min")
        visible = {k: v for k, v in current.items() if k in keep}
        current_json = json.dumps(visible, ensure_ascii=False, indent=2)
    return load_prompt(
        "sanji",
        "meeting_parse",
        today=now.strftime("%Y-%m-%d"),
        weekday=f"星期{ev._WEEKDAYS[now.weekday()]}",
        now_time=now.strftime("%H:%M"),
        current_json=current_json,
        user_message=user_message,
    )


def normalize_meeting(data: dict) -> dict:
    title = str(data.get("title") or "").strip()
    start = ev._parse_dt(data.get("start"))
    end = ev._parse_dt(data.get("end"))
    duration = ev._to_int(data.get("duration_min"))
    defaulted = False
    if start is not None:
        if end is None or end <= start:
            if duration is None:
                duration, defaulted = DEFAULT_DURATION_MIN, True
            end = start + timedelta(minutes=duration)
        else:
            duration = int((end - start).total_seconds() // 60)
    else:
        end = None
    missing = []
    if not title:
        missing.append("title")
    if start is None:
        missing.append("start")
    return {
        "title": title,
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "duration_min": duration,
        "duration_defaulted": defaulted,
        "missing": missing,
    }


# ── 確認卡 ──────────────────────────────────────────────────────────────


def _remind_at(meeting: dict) -> datetime | None:
    start = ev._parse_dt(meeting.get("start"))
    return start - timedelta(minutes=REMIND_BEFORE_MIN) if start else None


def build_card(state: dict) -> str:
    m = state["meeting"]
    if m.get("missing"):
        items = "\n".join(f"・{_MISSING_LABELS[k]}" for k in m["missing"])
        return f"要開內部會議，我這邊還缺：\n{items}\n直接在這串回我就好；不開了就回「取消」。"
    when = ev.format_when(m)
    if m.get("duration_defaulted"):
        when += f"（你沒給長度，先抓 {m['duration_min']} 分鐘，要改直接說）"
    remind = _remind_at(m)
    remind_line = (
        f"開會前 {REMIND_BEFORE_MIN} 分鐘（{remind.strftime('%H:%M')}）在 <#{_channel()}> 貼提醒"
        if remind and (remind - ev._now()).total_seconds() > _MIN_LEAD_S
        else "離開會不到 15 分鐘，不另外排提醒"
    )
    return "\n".join(
        [
            "內部會議，先對一下：",
            "",
            f"主題：{m['title']}",
            f"時間：{when}",
            "形式：只開 Zoom，不會發到社群",
            f"提醒：{remind_line}",
            _attendee_line(),
            *cal.card_lines(state.get("calendar"), state.get("force_calendar", False)),
            "",
            "沒問題就回「確認」，我去開 Zoom、寫進修修的 Google 行事曆；要改就直接說；"
            "不開了就回「取消」。",
        ]
    )


def _span(m: dict) -> tuple[datetime, datetime] | None:
    start, end = ev._parse_dt(m.get("start")), ev._parse_dt(m.get("end"))
    return (start, end) if start and end else None


def _card(state: dict) -> HandlerResponse:
    """資料齊了就先查行事曆衝突（跟 Nami 同一套），再出確認卡。"""
    span = None if state["meeting"].get("missing") else _span(state["meeting"])
    state["calendar"] = cal.check(*span) if span else None
    return _respond(state, build_card(state))


def _respond(state: dict, text: str) -> HandlerResponse:
    return HandlerResponse(text=text, continuation=Continuation(flow_name=FLOW_NAME, state=state))


# ── 流程 ────────────────────────────────────────────────────────────────


def start_flow(text: str, user_id: str) -> HandlerResponse:
    body, _ = ev.extract_image_lines(text)
    body = ev.strip_ai_footer(body)
    try:
        meeting = normalize_meeting(ev._ask_json(_build_prompt(body, None)))
    except ev.EventParseError as e:
        logger.error(f"internal_meeting start: parse failed user={user_id}: {e}")
        return HandlerResponse(
            text="抱歉，這場會議的資訊我沒整理成功。請把主題和時間寫在同一則再傳一次。"
        )
    state = {"version": 1, "meeting": meeting, "progress": {}}
    logger.info(f"internal_meeting start: user={user_id} title={meeting['title']!r}")
    return _card(state)


def continue_flow(state: dict, text: str, user_id: str) -> HandlerResponse:
    state = json.loads(json.dumps(state))
    state.setdefault("progress", {})
    body, _ = ev.extract_image_lines(text)
    body = ev.strip_ai_footer(body)
    reply = ev._normalize_reply(body)

    if reply in ev.CANCEL_WORDS:
        zoom = state["progress"].get("zoom")
        extra = (
            f"\n提醒：Zoom 會議已經開好了（{zoom['join_url']}），不用的話記得到 Zoom 後台刪掉。"
            if zoom
            else ""
        )
        return HandlerResponse(text="好，這場會議先不開了。" + extra)

    if reply in cal.FORCE_WORDS and not state["meeting"].get("missing"):
        state["force_calendar"] = True
        return execute(state, user_id)

    if reply in ev.CONFIRM_WORDS:
        if state["meeting"].get("missing"):
            return _respond(state, build_card(state))
        if cal.blocked(state.get("calendar"), state.get("force_calendar", False)):
            return _respond(
                state,
                "這個時段修修的 Google 行事曆已經有行程，我先不排。\n"
                + "\n".join(cal.card_lines(state["calendar"], False)),
            )
        return execute(state, user_id)

    if not body:
        return _respond(state, build_card(state))
    try:
        state["meeting"] = normalize_meeting(ev._ask_json(_build_prompt(body, state["meeting"])))
    except ev.EventParseError as e:
        logger.error(f"internal_meeting revise failed user={user_id}: {e}")
        return _respond(state, "抱歉，這個修改我沒看懂，可以換個說法嗎？不開了就回「取消」。")
    zoom = state["progress"].get("zoom")
    if zoom and zoom.get("key") != _zoom_key(state["meeting"]):
        # Zoom 已開好又改時間／主題：不重開，提醒手動調整
        state["progress"]["zoom_stale"] = True
    state["force_calendar"] = False  # 改了時間要重新看衝突
    return _card(state)


# ── 執行 ────────────────────────────────────────────────────────────────


def _zoom_key(m: dict) -> str:
    return f"{m.get('title')}|{m.get('start')}|{m.get('duration_min')}"


def _schedule_reminder(m: dict, join_url: str) -> str | None:
    """排提醒；回 Slack 的 scheduled_message_id，太近不排回 None。失敗丟例外。"""
    remind = _remind_at(m)
    if remind is None or (remind - ev._now()).total_seconds() <= _MIN_LEAD_S:
        return None
    from slack_sdk import WebClient

    token = os.environ.get("SANJI_SLACK_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("沒有 SANJI_SLACK_BOT_TOKEN")
    start = ev._parse_dt(m["start"])
    mentions = " ".join(f"<@{a['slack']}>" for a in attendees())
    text = (
        (f"{mentions} " if mentions else "")
        + f"⏰ {REMIND_BEFORE_MIN} 分鐘後開會：{m['title']}（{start.strftime('%H:%M')}）\n"
        + f"Zoom：{join_url}"
    )
    resp = WebClient(token=token).chat_scheduleMessage(
        channel=_channel(), post_at=int(remind.timestamp()), text=text
    )
    return str(resp.get("scheduled_message_id") or "")


def execute(state: dict, user_id: str) -> HandlerResponse:
    m = state["meeting"]
    progress = state.setdefault("progress", {})
    step = "準備"
    try:
        if not progress.get("zoom"):
            step = "開 Zoom 會議"
            start = ev._parse_dt(m["start"])
            out = fleet_events.run(
                "zoom",
                {
                    "title": m["title"],
                    "start": start.isoformat(),
                    "duration_min": m["duration_min"],
                },
            )
            progress["zoom"] = {"id": out["id"], "join_url": out["join_url"], "key": _zoom_key(m)}
            logger.info(f"internal_meeting: zoom created id={out['id']} user={user_id}")

        if "reminder" not in progress:
            step = "排 Slack 提醒"
            progress["reminder"] = _schedule_reminder(m, progress["zoom"]["join_url"])

        if "calendar" not in progress and _span(m):
            start, end = _span(m)
            progress["calendar"] = cal.add(
                title=m["title"],
                start=start,
                end=end,
                description=f"Zoom：{progress['zoom']['join_url']}\n（Sanji 開的內部會議）",
                key=f"sanji-meeting-{progress['zoom']['id']}",
                attendees=[a["email"] for a in attendees()],
            )
    except Exception as e:
        logger.error(f"internal_meeting failed at {step!r} user={user_id}: {e}", exc_info=True)
        lines = [f"會議還沒處理完，卡在「{step}」：{e}"]
        if progress.get("zoom"):
            lines.append(f"Zoom 已經開好（不會重開）：{progress['zoom']['join_url']}")
        if getattr(e, "maybe_done", False):
            lines.append(
                "⚠️ 這種錯誤可能其實已經開好了，回「確認」前請先到 Zoom 後台看一下，避免重複。"
            )
        lines.append("處理好之後回「確認」我接著做；不開了就回「取消」。")
        return _respond(state, "\n".join(lines))

    zoom = progress["zoom"]
    remind = _remind_at(m)
    lines = [
        f"內部會議開好了：{m['title']}",
        f"時間：{ev.format_when(m)}",
        f"Zoom：{zoom['join_url']}",
    ]
    if progress.get("reminder") is not None:
        lines.append(f"提醒：{remind.strftime('%H:%M')} 會在 <#{_channel()}> 貼出。")
    else:
        lines.append("離開會不到 15 分鐘，沒有另外排提醒。")
    added = progress.get("calendar") or {}
    if added.get("ok"):
        invited = "、".join(a["name"] for a in attendees())
        lines.append(
            "行事曆：已寫進修修的 Google 行事曆"
            + (f"，並寄邀請給 {invited}。" if invited else "。")
        )
    elif added:
        lines.append(f"行事曆：沒寫進去（{added.get('error')}），請手動加。")
    if progress.get("zoom_stale"):
        lines.append("注意：Zoom 是用改之前的時間／主題開的，記得到 Zoom 後台手動調整。")
    return HandlerResponse(text="\n".join(lines))
