"""gateway/handlers/sanji_event.py — Sanji 建社群活動流程測試。

執行端（freedom-fleet ``fleet.events``）一律 patch ``sanji_event.fleet_events.run``；
LLM 一律 patch ``sanji_event.ask``。不打任何真網路 / 真模型 / 子行程。
"""

from __future__ import annotations

import base64
import json
import types
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from gateway.handlers import sanji_event
from gateway.handlers.sanji import SanjiHandler
from shared.fleet_events import FleetEventsError
from tests import sanji_fake_calendar

TZ = sanji_event.TZ
FIXED_NOW = datetime(2026, 11, 13, 10, 0, tzinfo=TZ)  # 星期五

COPY = "這個月我們一起讀《原子習慣》。\n\n一起聊聊怎麼讓好習慣自動發生！"
RAW = f"幫我建活動\n活動名稱：六月讀書會《原子習慣》\n時間：11/28 晚上八點\n文案：\n{COPY}"
IMAGE_LINE = (
    "[附件圖片] atomic habits.jpg https://files.slack.com/files-pri/T1-F1/atomic.jpg image/jpeg"
)
COVER_URL = "https://fleet.shosho.tw/wp-content/uploads/c.jpg"
ZOOM_URL = "https://zoom.us/j/111?pwd=x"
PERMALINK = "https://fleet.shosho.tw/portal/space/test-dock/post/p-1"


def _llm_json(**overrides) -> str:
    data = {
        "title": "六月讀書會《原子習慣》",
        # LLM 把雙換行吃成單換行 → 守門要能找回原文片段
        "description": "這個月我們一起讀《原子習慣》。\n一起聊聊怎麼讓好習慣自動發生！",
        "start": "2026-11-28T20:00:00+08:00",
        "end": None,
        "duration_min": None,
        "kind": "book_club",
        "is_online": True,
        "location": "",
        "address": "",
        "cover_location_label": "",
        "cover_eyebrow": "Captain's Book Club",
        "cover_headline_lines": ["六月選書"],
        "illustration_topic": "",
        "send_announcement_email": False,
        "slug": "",
        "missing": [],
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


# ── freedom-fleet 執行端 stub ─────────────────────────────────────────────


@pytest.fixture
def ext(monkeypatch):
    """``ext.calls``：指令順序；``ext.payloads[cmd]``：最後一次輸入；``ext.fail[cmd]``：注入錯誤。"""
    calls: list[str] = []
    payloads: dict[str, dict] = {}
    fail: dict[str, Exception] = {}
    outputs = {
        "cover": {"ok": True, "media_id": 55, "url": COVER_URL, "note": ""},
        "zoom": {"ok": True, "id": 111, "join_url": ZOOM_URL},
        "post": {
            "ok": True,
            "feed_id": 900,
            "feed_slug": "p-1",
            "event_id": 7,
            "report_id": 8,
            "permalink": PERMALINK,
        },
    }

    def fake_run(command, payload):
        calls.append(command)
        payloads[command] = payload
        if command in fail:
            raise fail[command]
        return dict(outputs[command])

    monkeypatch.setattr(sanji_event.fleet_events, "run", fake_run)
    monkeypatch.setattr(sanji_event, "_now", lambda: FIXED_NOW)
    monkeypatch.setenv("SANJI_SLACK_BOT_TOKEN", "xoxb-sanji")
    monkeypatch.setenv("SANJI_EVENTS_SPACE", "test-dock")
    monkeypatch.setenv("SANJI_EVENTS_SPACE_LABEL", "測試船塢")
    gcal = sanji_fake_calendar.install(monkeypatch)
    return types.SimpleNamespace(
        calls=calls, payloads=payloads, fail=fail, outputs=outputs, gcal=gcal
    )


def _start(llm_replies, text=RAW):
    with patch.object(sanji_event, "ask", side_effect=list(llm_replies)) as m_ask:
        resp = SanjiHandler().handle("general", text, "U_XS")
    return resp, m_ask


def _cont(state, text, llm_replies=()):
    with patch.object(sanji_event, "ask", side_effect=list(llm_replies)) as m_ask:
        resp = SanjiHandler().continue_flow("event_draft", state, text, "U_XS")
    return resp, m_ask


def _assert_json_safe(state):
    assert json.loads(json.dumps(state)) == state


def _http_ok(content=b"JPEGDATA", ctype="image/jpeg"):
    r = MagicMock()
    r.content = content
    r.headers = {"content-type": ctype}
    r.raise_for_status.return_value = None
    return r


# ── 觸發 ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    ["幫我建活動", "建立活動：讀書會", "新增活動", "下週開活動", "活動草稿", "幫我開一場直播"],
)
def test_trigger_keywords(text):
    assert sanji_event.is_event_request(text)


@pytest.mark.parametrize("text", ["最近很累", "這是公開活動嗎", "活動好玩嗎"])
def test_non_trigger(text):
    assert not sanji_event.is_event_request(text)


def test_normal_chat_unchanged(ext):
    with (
        patch("gateway.handlers.sanji.ask", return_value="Sanji 的回覆") as chat_ask,
        patch.object(sanji_event, "ask") as event_ask,
    ):
        resp = SanjiHandler().handle("general", "最近很累", "U1")
    assert resp.text == "Sanji 的回覆"
    assert resp.continuation is None
    chat_ask.assert_called_once()
    event_ask.assert_not_called()
    assert ext.calls == []


# ── 確認卡 ──────────────────────────────────────────────────────────────


def test_trigger_starts_flow_with_card_and_cover_preview(ext):
    resp, m_ask = _start([_llm_json()])

    m_ask.assert_called_once()
    kwargs = m_ask.call_args.kwargs
    assert kwargs["call_class"] == "interactive"
    assert "2026-11-13" in kwargs["prompt"] and "星期五" in kwargs["prompt"]
    assert RAW in kwargs["prompt"]

    # 資料齊了 → 先備好封面預覽（只做封面，不開 Zoom、不發活動）
    assert ext.calls == ["cover"]
    cover_in = ext.payloads["cover"]
    assert cover_in["eyebrow"] == "Captain's Book Club"
    assert cover_in["headline_lines"] == ["六月選書"]
    assert cover_in["date_label"] == "11/28（六）20:00・線上"
    assert cover_in["main_kind"] == "none"  # 讀書會沒附書封
    assert cover_in["filename"] == "sanji-cover-20261128.jpg"

    assert resp.continuation is not None and resp.continuation.flow_name == "event_draft"
    card = resp.text
    assert "六月讀書會《原子習慣》" in card
    assert "2026/11/28（六） 20:00–22:00" in card
    assert "先抓 2 小時" in card
    assert "Zoom" in card
    assert "發在：測試船塢" in card
    assert "公告信：不寄" in card
    assert f"預覽：{COVER_URL}" in card
    assert COPY in card
    assert "待審核" in card and "Approve Post" in card
    assert "「確認」" in card and "「取消」" in card
    _assert_json_safe(resp.continuation.state)
    assert resp.continuation.state["event"]["description"] == COPY


def test_missing_start_asks_only_for_it(ext):
    resp, _ = _start([_llm_json(start=None, missing=["start"])])
    assert "活動時間" in resp.text and "「確認」" not in resp.text
    assert ext.calls == []  # 缺資料不做封面

    resp2, _ = _cont(resp.continuation.state, "確認")
    assert ext.calls == []
    assert "活動時間" in resp2.text and resp2.continuation is not None


def test_paraphrased_description_falls_back_to_raw_text(ext):
    resp, _ = _start([_llm_json(description="本月共讀原子習慣，讓好習慣自動化！")])
    event = resp.continuation.state["event"]
    assert event["description_fallback"] is True
    assert event["description"].startswith("活動名稱：六月讀書會")
    assert "幫我建活動" not in event["description"]
    assert COPY in event["description"]
    assert "本月共讀" not in resp.text


def test_json_retry_then_success(ext):
    resp, m_ask = _start(["抱歉我想一下", "```json\n" + _llm_json() + "\n```"])
    assert m_ask.call_count == 2
    assert "只回傳一個 JSON" in m_ask.call_args_list[1].kwargs["prompt"]
    assert resp.continuation is not None


def test_json_gives_up_with_friendly_message(ext):
    resp, m_ask = _start(["不是 JSON", "還是不是"])
    assert m_ask.call_count == 2
    assert resp.continuation is None
    assert "再傳一次" in resp.text


def test_cover_preview_failure_still_shows_card(ext):
    ext.fail["cover"] = FleetEventsError("字型不見了")
    resp, _ = _start([_llm_json()])
    assert "沒產生成功" in resp.text and "字型不見了" in resp.text
    assert resp.continuation is not None
    _assert_json_safe(resp.continuation.state)


# ── continue_flow ─────────────────────────────────────────────────────────


def test_cancel_ends_flow(ext):
    resp, _ = _start([_llm_json()])
    resp2, m_ask = _cont(resp.continuation.state, "取消")
    assert resp2.continuation is None
    m_ask.assert_not_called()
    assert ext.calls == ["cover"]  # 只有預覽，沒開 Zoom、沒發活動


def test_revision_updates_time_and_regenerates_cover(ext):
    resp, _ = _start([_llm_json()])
    resp2, m_ask = _cont(
        resp.continuation.state, "時間改成 9 點", [_llm_json(start="2026-11-28T21:00:00+08:00")]
    )
    prompt = m_ask.call_args.kwargs["prompt"]
    assert "時間改成 9 點" in prompt and "六月讀書會《原子習慣》" in prompt
    assert resp2.continuation.state["event"]["start"] == "2026-11-28T21:00:00+08:00"
    assert "21:00–23:00" in resp2.text
    assert resp2.continuation.state["event"]["description"] == COPY
    # 日期列變了 → 封面重做
    assert ext.calls == ["cover", "cover"]
    assert ext.payloads["cover"]["date_label"] == "11/28（六）21:00・線上"
    _assert_json_safe(resp2.continuation.state)


def test_revision_not_touching_cover_reuses_it(ext):
    resp, _ = _start([_llm_json()])
    _cont(resp.continuation.state, "要寄公告信", [_llm_json(send_announcement_email=True)])
    assert ext.calls == ["cover"]


def test_revision_cannot_paraphrase_existing_description(ext):
    resp, _ = _start([_llm_json()])
    resp2, _ = _cont(
        resp.continuation.state, "時間改成 9 點", [_llm_json(description="LLM 自己寫的新文案")]
    )
    assert resp2.continuation.state["event"]["description"] == COPY


def test_new_image_replaces_stored_image_and_redoes_cover(ext):
    with patch.object(sanji_event.httpx, "get", return_value=_http_ok()):
        resp, _ = _start([_llm_json()], text=RAW + "\n" + IMAGE_LINE)
        assert resp.continuation.state["image"]["name"] == "atomic habits.jpg"
        new_line = "[附件圖片] v2.png https://files.slack.com/files-pri/T1-F2/v2.png image/png"
        resp2, m_ask = _cont(resp.continuation.state, new_line)
    m_ask.assert_not_called()  # 只換圖不需要 LLM
    assert resp2.continuation.state["image"]["url"].endswith("v2.png")
    assert "v2.png" in resp2.text
    assert ext.calls == ["cover", "cover"]


# ── 執行 ────────────────────────────────────────────────────────────────


def test_confirm_online_live_runs_in_order(ext):
    resp, _ = _start(
        [
            _llm_json(
                title="五月主題直播：睡眠",
                description="一起聊聊怎麼讓好習慣自動發生！",
                kind="live",
                cover_eyebrow="Live Room・五月主題",
                cover_headline_lines=["睡眠直播"],
                illustration_topic="a cozy bedroom at night",
            )
        ],
        text=RAW.replace("六月讀書會《原子習慣》", "五月主題直播：睡眠"),
    )
    assert "AI 生成插畫" in resp.text
    cover_in = ext.payloads["cover"]
    assert cover_in["main_kind"] == "illustration"
    assert cover_in["illustration_topic"] == "a cozy bedroom at night"
    assert cover_in["date_label"] == "11/28（六）20:00・線上直播"

    resp2, _ = _cont(resp.continuation.state, "確認")

    assert ext.calls == ["cover", "zoom", "post"]  # 封面沿用預覽，不重做
    assert ext.payloads["zoom"] == {
        "title": "五月主題直播：睡眠",
        "start": "2026-11-28T20:00:00+08:00",
        "duration_min": 120,
    }
    post = ext.payloads["post"]
    assert post["space"] == "test-dock"
    assert post["message"] == "一起聊聊怎麼讓好習慣自動發生！"
    assert post["send_announcement_email"] is False
    fe = post["event"]
    assert fe["venue_type"] == "online" and fe["online_meeting_url"] == ZOOM_URL
    assert fe["start_at"] == "2026-11-28 20:00:00" and fe["end_at"] == "2026-11-28 22:00:00"
    assert fe["timezone"] == "Asia/Taipei"
    assert fe["cover_url"] == COVER_URL
    assert fe["description_md"] == "一起聊聊怎麼讓好習慣自動發生！"
    assert "venue" not in fe

    assert resp2.continuation is None
    assert PERMALINK in resp2.text and COVER_URL in resp2.text and ZOOM_URL in resp2.text
    assert "待審核" in resp2.text and "Approve Post" in resp2.text
    assert "公告信" not in resp2.text


def test_in_person_meetup_skips_zoom(ext):
    resp, _ = _start(
        [
            _llm_json(
                title="台北小聚",
                kind="meetup",
                is_online=False,
                start="2026-08-09T14:00:00+08:00",
                end="2026-08-09T17:00:00+08:00",
                location="某咖啡廳",
                address="台北市大安區某路 1 號",
                cover_location_label="台北場",
                cover_eyebrow="whatever",
                cover_headline_lines=["台北小聚"],
                illustration_topic="friends meeting in a cafe",
            )
        ],
        text=RAW.replace("六月讀書會《原子習慣》", "台北小聚"),
    )
    card = resp.text
    assert "實體・某咖啡廳（台北市大安區某路 1 號）" in card
    assert "Fleet Meetup" in card  # 小標由程式定案
    assert "先抓" not in card
    assert ext.payloads["cover"]["date_label"] == "台北場・8/9（日）14:00"

    _cont(resp.continuation.state, "OK")
    assert ext.calls == ["cover", "post"]
    fe = ext.payloads["post"]["event"]
    assert fe["venue_type"] == "offline"
    assert fe["venue"] == "某咖啡廳" and fe["address"] == "台北市大安區某路 1 號"
    assert "online_meeting_url" not in fe


def test_announcement_email_on(ext):
    resp, _ = _start([_llm_json(send_announcement_email=True)])
    assert "公告信：寄給這個 space 的全部成員" in resp.text
    resp2, _ = _cont(resp.continuation.state, "確認")
    assert ext.payloads["post"]["send_announcement_email"] is True
    assert "公告信會在核准後約 5 分鐘" in resp2.text


def test_book_club_with_image_sends_book_image(ext):
    with patch.object(sanji_event.httpx, "get", return_value=_http_ok()) as m_get:
        resp, _ = _start([_llm_json()], text=RAW + "\n" + IMAGE_LINE)
    assert "用你附的書封（atomic habits.jpg）" in resp.text
    assert m_get.call_args.args[0] == "https://files.slack.com/files-pri/T1-F1/atomic.jpg"
    assert m_get.call_args.kwargs["headers"] == {"Authorization": "Bearer xoxb-sanji"}
    assert m_get.call_args.kwargs["follow_redirects"] is True
    cover_in = ext.payloads["cover"]
    assert cover_in["main_kind"] == "book"
    assert base64.b64decode(cover_in["main_image_b64"]) == b"JPEGDATA"
    assert "illustration_topic" not in cover_in


def test_image_download_returns_html_continues_without_image(ext):
    html_resp = _http_ok(b"<!DOCTYPE html><html>login</html>", "text/html")
    with patch.object(sanji_event.httpx, "get", return_value=html_resp):
        resp, _ = _start([_llm_json()], text=RAW + "\n" + IMAGE_LINE)
    cover_in = ext.payloads["cover"]
    assert cover_in["main_kind"] == "none" and "main_image_b64" not in cover_in
    assert "下載不到" in resp.text


def test_cover_note_from_fleet_is_shown(ext):
    ext.outputs["cover"]["note"] = "AI 插畫這次沒生成成功，封面先不放主圖。"
    resp, _ = _start([_llm_json(kind="live", illustration_topic="x")])
    assert "沒生成成功" in resp.text


def test_cover_failure_at_preview_is_retried_on_confirm(ext):
    ext.fail["cover"] = FleetEventsError("timeout")
    resp, _ = _start([_llm_json()])
    del ext.fail["cover"]
    resp2, _ = _cont(resp.continuation.state, "確認")
    assert ext.calls == ["cover", "cover", "zoom", "post"]
    assert resp2.continuation is None


def test_post_failure_keeps_flow_and_retry_does_not_duplicate(ext):
    ext.fail["post"] = FleetEventsError("HTTP 403: forbidden")
    resp, _ = _start([_llm_json()])
    resp2, _ = _cont(resp.continuation.state, "確認")

    assert resp2.continuation is not None
    assert ZOOM_URL in resp2.text
    assert "發待審核活動" in resp2.text and "HTTP 403" in resp2.text
    assert "避免重複" not in resp2.text  # 明確被拒＝一定沒建
    state = resp2.continuation.state
    _assert_json_safe(state)
    assert state["progress"]["zoom"]["id"] == 111
    assert state["progress"]["cover"]["id"] == 55

    del ext.fail["post"]
    resp3, _ = _cont(state, "確認")
    assert ext.calls == ["cover", "zoom", "post", "post"]  # 不重開 Zoom、不重做封面
    assert resp3.continuation is None
    assert PERMALINK in resp3.text


def test_uncertain_post_failure_warns_about_duplicates(ext):
    ext.fail["post"] = FleetEventsError("逾時", maybe_done=True)
    resp, _ = _start([_llm_json()])
    resp2, _ = _cont(resp.continuation.state, "確認")
    assert "避免重複" in resp2.text and "測試船塢" in resp2.text
    assert resp2.continuation is not None


def test_zoom_failure_reports_and_keeps_flow(ext):
    ext.fail["zoom"] = FleetEventsError("ZoomError: 401 token")
    resp, _ = _start([_llm_json()])
    resp2, _ = _cont(resp.continuation.state, "確認")
    assert resp2.continuation is not None
    assert "Zoom" in resp2.text and "401 token" in resp2.text
    assert "post" not in ext.calls


def test_space_not_configured_refuses(ext, monkeypatch):
    monkeypatch.delenv("SANJI_EVENTS_SPACE")
    monkeypatch.delenv("SANJI_EVENTS_SPACE_LABEL")
    resp, _ = _start([_llm_json()])
    assert "未設定" in resp.text
    resp2, _ = _cont(resp.continuation.state, "確認")
    assert "SANJI_EVENTS_SPACE" in resp2.text
    assert ext.calls == ["cover"]
    assert resp2.continuation is not None


def test_unknown_flow_raises_not_implemented():
    with pytest.raises(NotImplementedError):
        SanjiHandler().continue_flow("other_flow", {}, "hi", "U1")


def test_state_json_roundtrip_through_conversation_store(ext):
    from gateway.conversation_state import ConversationStore

    with patch.object(sanji_event.httpx, "get", return_value=_http_ok()):
        resp, _ = _start([_llm_json()], text=RAW + "\n" + IMAGE_LINE)
    store = ConversationStore(db_path=":memory:")
    store.start(
        thread_ts="1.0",
        channel="C1",
        user_id="U_XS",
        agent_name="sanji",
        flow_name="event_draft",
        state=resp.continuation.state,
    )
    assert store.get("1.0").state == resp.continuation.state


# ── 2026-11-13 上線實測修正 ───────────────────────────────────────────────


def test_strip_ai_footer():
    f = sanji_event.strip_ai_footer
    assert f("確認\n*Sent using* Claude") == "確認"
    assert f("時間改成晚上八點半 *Sent using* Claude") == "時間改成晚上八點半"
    assert f("文案第一行\n\n文案第二行") == "文案第一行\n\n文案第二行"
    assert f("This was sent using a boat") == "This was sent using a boat"


def test_confirm_with_ai_footer_executes(ext):
    resp, _ = _start([_llm_json()])
    resp2, m_ask = _cont(resp.continuation.state, "確認\n*Sent using* Claude")
    m_ask.assert_not_called()
    assert ext.calls.count("post") == 1
    assert resp2.continuation is None


# ── 網址（2026-11-16 UAT：vendor 預設網址又長又難辨識）──────────────────────


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("book_club", "2026-11-28-book-club"),
        ("live", "2026-11-28-live"),
        ("meetup", "2026-11-28-meetup"),
    ],
)
def test_default_slug_is_date_and_kind(kind, expected):
    event = {"start": "2026-11-28T20:00:00+08:00", "kind": kind, "slug": ""}
    assert sanji_event.event_slug(event) == expected


def test_slug_shown_on_card_and_sent(ext):
    resp, _ = _start([_llm_json()])
    assert "網址：2026-11-28-book-club" in resp.text
    _cont(resp.continuation.state, "確認")
    assert ext.payloads["post"]["slug"] == "2026-11-28-book-club"


def test_default_slug_follows_date_change(ext):
    resp, _ = _start([_llm_json()])
    resp2, _ = _cont(
        resp.continuation.state, "改到 12/5", [_llm_json(start="2026-12-05T20:00:00+08:00")]
    )
    assert "網址：2026-12-05-book-club" in resp2.text


def test_custom_slug_is_cleaned_and_kept(ext):
    resp, _ = _start([_llm_json()])
    resp2, _ = _cont(
        resp.continuation.state, "網址改成 Atomic Habits!", [_llm_json(slug="Atomic Habits!")]
    )
    assert "網址：atomic-habits" in resp2.text
    resp3, _ = _cont(resp2.continuation.state, "確認")
    assert ext.payloads["post"]["slug"] == "atomic-habits"


@pytest.mark.parametrize(
    ("raw", "clean"), [("中文", ""), ("--a__b--", "a-b"), ("X" * 90, "x" * 80)]
)
def test_clean_slug(raw, clean):
    assert sanji_event.clean_slug(raw) == clean


# ── 行事曆（2026-11-16：跟 Nami 一樣先查衝突、確認後寫入）──────────────────────


def test_no_conflict_card_and_calendar_written(ext):
    resp, _ = _start([_llm_json()])
    assert "行事曆：這個時段沒有其他行程" in resp.text
    _cont(resp.continuation.state, "確認")
    created = ext.gcal.created[0]
    assert created["title"] == "六月讀書會《原子習慣》"
    assert created["start"] == "2026-11-28T20:00:00+08:00"
    assert created["end"] == "2026-11-28T22:00:00+08:00"
    assert created["check_conflict"] is False
    assert created["idempotency_key"] == "sanji-event-900@2026-11-28"
    assert PERMALINK in created["description"]


def test_conflict_blocks_confirm_until_force(ext):
    ext.gcal.conflicts = [
        sanji_fake_calendar.event(
            "看牙醫", "2026-11-28T19:30:00+08:00", "2026-11-28T21:00:00+08:00"
        )
    ]
    ext.gcal.free = [("2026-11-28T21:00:00+08:00", "2026-11-28T23:00:00+08:00")]
    resp, _ = _start([_llm_json()])
    assert "行事曆衝突：19:30–21:00「看牙醫」" in resp.text
    assert "21:00–23:00" in resp.text and "「照排」" in resp.text

    resp2, _ = _cont(resp.continuation.state, "確認")
    assert "先不建" in resp2.text and "post" not in ext.calls
    assert resp2.continuation is not None

    resp3, _ = _cont(resp2.continuation.state, "照排")
    assert ext.calls.count("post") == 1 and resp3.continuation is None
    assert ext.gcal.created


def test_revision_clears_force(ext):
    ext.gcal.conflicts = [
        sanji_fake_calendar.event("A", "2026-11-28T20:00:00+08:00", "2026-11-28T21:00:00+08:00")
    ]
    resp, _ = _start([_llm_json()])
    state = resp.continuation.state
    state["force_calendar"] = True
    resp2, _ = _cont(state, "時間改成 9 點", [_llm_json(start="2026-11-28T21:00:00+08:00")])
    assert resp2.continuation.state["force_calendar"] is False


def test_calendar_unavailable_does_not_block(ext):
    ext.gcal.fail_check = RuntimeError("token expired")
    ext.gcal.fail_add = RuntimeError("token expired")
    resp, _ = _start([_llm_json()])
    assert "沒查到你的 Google 行事曆" in resp.text
    resp2, _ = _cont(resp.continuation.state, "確認")
    assert ext.calls.count("post") == 1
    assert "行事曆沒寫進去" in resp2.text and "token expired" in resp2.text
