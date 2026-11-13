"""gateway/handlers/sanji_event.py — Sanji 建活動草稿流程測試。

外部模組（shared.fleet_cover / zoom_client / fca_events_client）由其他 PR 實作，
這裡一律用 ``sys.modules`` stub 換掉；LLM 一律 patch ``sanji_event.ask``。
不打任何真網路 / 真模型。
"""

from __future__ import annotations

import json
import sys
import types
from dataclasses import dataclass
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from gateway.handlers import sanji_event
from gateway.handlers.sanji import SanjiHandler

TZ = sanji_event.TZ
FIXED_NOW = datetime(2026, 11, 13, 10, 0, tzinfo=TZ)  # 星期五

COPY = "這個月我們一起讀《原子習慣》。\n\n一起聊聊怎麼讓好習慣自動發生！"
RAW = f"幫我建活動\n活動名稱：六月讀書會《原子習慣》\n時間：11/28 晚上八點\n文案：\n{COPY}"
IMAGE_LINE = (
    "[附件圖片] atomic habits.jpg https://files.slack.com/files-pri/T1-F1/atomic.jpg image/jpeg"
)


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
        "cover_location_label": "",
        "cover_eyebrow": "Captain's Book Club",
        "cover_headline_lines": ["六月選書"],
        "illustration_topic": "",
        "missing": [],
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


# ── 外部模組 stub ──────────────────────────────────────────────────────────


class _IllustrationError(RuntimeError):
    pass


@dataclass
class _CoverSpec:
    eyebrow: str
    headline_lines: list
    date_label: str = ""
    main_image: bytes | None = None
    main_kind: str = "none"


@dataclass
class _EventDraft:
    title: str
    content_html: str
    start: datetime
    end: datetime
    event_type: str
    event_url: str = ""
    meeting_provider: str = ""
    location: str = ""
    featured_image_id: int | None = None
    timezone: str = "Asia/Taipei"


@pytest.fixture
def ext(monkeypatch):
    """三個外部模組的 stub；``ext.calls`` 記錄呼叫順序。"""
    calls: list[str] = []

    def _rec(name, ret=None, side_effect=None):
        m = MagicMock(name=name)

        def _effect(*a, **kw):
            calls.append(name)
            if side_effect is not None:
                return side_effect(*a, **kw)
            return ret

        m.side_effect = _effect
        return m

    zoom = types.ModuleType("shared.zoom_client")
    zoom.ZoomError = type("ZoomError", (RuntimeError,), {})
    zoom.create_meeting = _rec(
        "zoom",
        types.SimpleNamespace(
            id=111, join_url="https://zoom.us/j/111?pwd=x", start_url="s", password="p"
        ),
    )

    cover = types.ModuleType("shared.fleet_cover")
    cover.CoverSpec = _CoverSpec
    cover.IllustrationError = _IllustrationError
    cover.generate_illustration = _rec("illustration", b"ILLU")
    cover.render_cover = _rec("render", b"PNG")

    client = MagicMock(name="FcaEventsClient")
    client.upload_image = _rec(
        "upload", types.SimpleNamespace(id=55, url="https://fleet.shosho.tw/c.png")
    )
    client.create_draft_event = _rec(
        "create",
        types.SimpleNamespace(
            id=900,
            slug="june-book-club",
            status="draft",
            edit_url="https://fleet.shosho.tw/wp-admin/post.php?post=900&action=edit",
        ),
    )
    fca = types.ModuleType("shared.fca_events_client")
    fca.EventDraft = _EventDraft
    fca.FcaEventsClient = MagicMock()
    fca.FcaEventsClient.from_env.return_value = client

    monkeypatch.setitem(sys.modules, "shared.zoom_client", zoom)
    monkeypatch.setitem(sys.modules, "shared.fleet_cover", cover)
    monkeypatch.setitem(sys.modules, "shared.fca_events_client", fca)
    monkeypatch.setattr(sanji_event, "_now", lambda: FIXED_NOW)
    monkeypatch.setenv("SANJI_SLACK_BOT_TOKEN", "xoxb-sanji")
    return types.SimpleNamespace(calls=calls, zoom=zoom, cover=cover, fca=fca, client=client)


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


def test_trigger_starts_flow_with_card(ext):
    resp, m_ask = _start([_llm_json()])

    m_ask.assert_called_once()
    kwargs = m_ask.call_args.kwargs
    assert kwargs["call_class"] == "interactive"
    # 今天日期與星期要給 LLM，相對日期才能換算
    assert "2026-11-13" in kwargs["prompt"] and "星期五" in kwargs["prompt"]
    assert RAW in kwargs["prompt"]

    assert resp.continuation is not None
    assert resp.continuation.flow_name == "event_draft"
    card = resp.text
    assert "六月讀書會《原子習慣》" in card
    assert "2026/11/28（六） 20:00–22:00" in card
    assert "先抓 2 小時" in card  # 預設長度要讓她看得到
    assert "Zoom" in card
    assert "Captain's Book Club" in card and "六月選書" in card
    assert "11/28（六）20:00・線上" in card
    assert COPY in card  # 全文、保留原本的空行
    assert "「確認」" in card and "「取消」" in card
    _assert_json_safe(resp.continuation.state)
    assert resp.continuation.state["event"]["description"] == COPY


def test_missing_start_asks_only_for_it(ext):
    resp, _ = _start([_llm_json(start=None, missing=["start"])])
    assert resp.continuation is not None
    assert "活動時間" in resp.text
    assert "「確認」" not in resp.text
    assert resp.continuation.state["event"]["missing"] == ["start"]

    # 缺東西時回「確認」不能執行
    resp2, _ = _cont(resp.continuation.state, "確認")
    assert ext.calls == []
    assert "活動時間" in resp2.text
    assert resp2.continuation is not None


def test_paraphrased_description_falls_back_to_raw_text(ext):
    resp, _ = _start([_llm_json(description="本月共讀原子習慣，讓好習慣自動化！")])
    event = resp.continuation.state["event"]
    assert event["description_fallback"] is True
    # 退回使用者原文（去掉觸發那一行）
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


# ── continue_flow ─────────────────────────────────────────────────────────


def test_cancel_ends_flow(ext):
    resp, _ = _start([_llm_json()])
    resp2, m_ask = _cont(resp.continuation.state, "取消")
    assert resp2.continuation is None
    m_ask.assert_not_called()
    assert ext.calls == []


def test_revision_updates_time_and_keeps_flow(ext):
    resp, _ = _start([_llm_json()])
    resp2, m_ask = _cont(
        resp.continuation.state,
        "時間改成 9 點",
        [_llm_json(start="2026-11-28T21:00:00+08:00")],
    )
    prompt = m_ask.call_args.kwargs["prompt"]
    assert "時間改成 9 點" in prompt
    assert "六月讀書會《原子習慣》" in prompt  # 目前 JSON 有帶給 LLM
    assert resp2.continuation is not None
    assert resp2.continuation.state["event"]["start"] == "2026-11-28T21:00:00+08:00"
    assert "21:00–23:00" in resp2.text
    assert resp2.continuation.state["event"]["description"] == COPY
    _assert_json_safe(resp2.continuation.state)


def test_revision_cannot_paraphrase_existing_description(ext):
    resp, _ = _start([_llm_json()])
    resp2, _ = _cont(
        resp.continuation.state, "時間改成 9 點", [_llm_json(description="LLM 自己寫的新文案")]
    )
    assert resp2.continuation.state["event"]["description"] == COPY


def test_revision_with_new_image_replaces_stored_image(ext):
    resp, _ = _start([_llm_json()], text=RAW + "\n" + IMAGE_LINE)
    assert resp.continuation.state["image"]["name"] == "atomic habits.jpg"
    new_line = "[附件圖片] v2.png https://files.slack.com/files-pri/T1-F2/v2.png image/png"
    resp2, m_ask = _cont(resp.continuation.state, new_line)
    m_ask.assert_not_called()  # 只換圖不需要 LLM
    assert resp2.continuation.state["image"]["url"].endswith("v2.png")
    assert "v2.png" in resp2.text


# ── 執行 ────────────────────────────────────────────────────────────────


def _http_ok(content=b"JPEGDATA", ctype="image/jpeg"):
    r = MagicMock()
    r.content = content
    r.headers = {"content-type": ctype}
    r.raise_for_status.return_value = None
    return r


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
    resp2, _ = _cont(resp.continuation.state, "確認")

    assert ext.calls == ["zoom", "illustration", "render", "upload", "create"]
    title, start, duration = ext.zoom.create_meeting.call_args.args
    assert title == "五月主題直播：睡眠"
    assert start == datetime(2026, 11, 28, 20, 0, tzinfo=TZ)
    assert duration == 120
    ext.cover.generate_illustration.assert_called_once_with("a cozy bedroom at night")

    spec = ext.cover.render_cover.call_args.args[0]
    assert spec.eyebrow == "Live Room・五月主題"
    assert spec.headline_lines == ["睡眠直播"]
    assert spec.date_label == "11/28（六）20:00・線上直播"
    assert spec.main_image == b"ILLU" and spec.main_kind == "illustration"

    ext.client.upload_image.assert_called_once_with(b"PNG", "sanji-cover-20261128.png")
    draft = ext.client.create_draft_event.call_args.args[0]
    assert draft.event_type == "online"
    assert draft.event_url == "https://zoom.us/j/111?pwd=x"
    assert draft.meeting_provider == "zoom"
    assert draft.featured_image_id == 55
    assert draft.end == datetime(2026, 11, 28, 22, 0, tzinfo=TZ)

    assert resp2.continuation is None
    assert "https://fleet.shosho.tw/wp-admin/post.php?post=900&action=edit" in resp2.text
    assert "https://fleet.shosho.tw/c.png" in resp2.text
    assert "https://zoom.us/j/111?pwd=x" in resp2.text
    assert "草稿" in resp2.text
    assert "已發布" not in resp2.text and "已經發布" not in resp2.text
    assert "published" not in resp2.text.lower()


def test_content_html_is_escaped_paragraphs(ext):
    copy = "第一段 <b>粗</b> & 好\n第二行\n\n第二段"
    resp, _ = _start(
        [_llm_json(description=copy)], text=f"幫我建活動\n六月讀書會《原子習慣》 11/28 八點\n{copy}"
    )
    _cont(resp.continuation.state, "確認")
    draft = ext.client.create_draft_event.call_args.args[0]
    assert draft.content_html == (
        "<p>第一段 &lt;b&gt;粗&lt;/b&gt; &amp; 好<br>\n第二行</p>\n<p>第二段</p>"
    )


def test_in_person_meetup_skips_zoom(ext):
    resp, _ = _start(
        [
            _llm_json(
                title="台北小聚",
                kind="meetup",
                is_online=False,
                start="2026-08-09T14:00:00+08:00",
                end="2026-08-09T17:00:00+08:00",
                location="台北市大安區某咖啡廳",
                cover_location_label="台北場",
                cover_eyebrow="whatever",
                cover_headline_lines=["台北小聚"],
                illustration_topic="friends meeting in a cafe",
            )
        ],
        text=RAW.replace("六月讀書會《原子習慣》", "台北小聚"),
    )
    card = resp.text
    assert "實體・台北市大安區某咖啡廳" in card
    assert "Fleet Meetup" in card  # 小標由程式定案
    assert "先抓" not in card  # 有給結束時間

    _cont(resp.continuation.state, "OK")
    assert ext.calls == ["illustration", "render", "upload", "create"]
    ext.zoom.create_meeting.assert_not_called()
    spec = ext.cover.render_cover.call_args.args[0]
    assert spec.date_label == "台北場・8/9（日）14:00"
    draft = ext.client.create_draft_event.call_args.args[0]
    assert draft.event_type == "in_person"
    assert draft.event_url == "" and draft.meeting_provider == ""
    assert draft.location == "台北市大安區某咖啡廳"


def test_book_club_with_image_uses_book_and_no_illustration(ext):
    resp, _ = _start([_llm_json()], text=RAW + "\n" + IMAGE_LINE)
    assert "用你附的書封（atomic habits.jpg）" in resp.text
    with patch.object(sanji_event.httpx, "get", return_value=_http_ok()) as m_get:
        resp2, _ = _cont(resp.continuation.state, "確認")

    m_get.assert_called_once()
    assert m_get.call_args.args[0] == "https://files.slack.com/files-pri/T1-F1/atomic.jpg"
    assert m_get.call_args.kwargs["headers"] == {"Authorization": "Bearer xoxb-sanji"}
    assert m_get.call_args.kwargs["follow_redirects"] is True
    ext.cover.generate_illustration.assert_not_called()
    spec = ext.cover.render_cover.call_args.args[0]
    assert spec.main_kind == "book" and spec.main_image == b"JPEGDATA"
    assert ext.calls == ["zoom", "render", "upload", "create"]
    assert resp2.continuation is None


def test_image_download_returns_html_continues_without_image(ext):
    resp, _ = _start([_llm_json()], text=RAW + "\n" + IMAGE_LINE)
    html_resp = _http_ok(b"<!DOCTYPE html><html>login</html>", "text/html")
    with patch.object(sanji_event.httpx, "get", return_value=html_resp):
        resp2, _ = _cont(resp.continuation.state, "確認")
    spec = ext.cover.render_cover.call_args.args[0]
    assert spec.main_kind == "none" and spec.main_image is None
    assert "下載不到" in resp2.text
    assert resp2.continuation is None


def test_illustration_error_continues_with_none(ext):
    ext.cover.generate_illustration.side_effect = _IllustrationError("timeout")
    resp, _ = _start([_llm_json(kind="live", illustration_topic="x")])
    resp2, _ = _cont(resp.continuation.state, "確認")
    spec = ext.cover.render_cover.call_args.args[0]
    assert spec.main_kind == "none" and spec.main_image is None
    assert "插畫" in resp2.text and "沒生成成功" in resp2.text
    assert resp2.continuation is None
    ext.client.create_draft_event.assert_called_once()


def test_create_failure_keeps_flow_and_retry_does_not_duplicate(ext):
    ext.client.create_draft_event.side_effect = RuntimeError("WP 500")
    resp, _ = _start([_llm_json()])
    resp2, _ = _cont(resp.continuation.state, "確認")

    assert resp2.continuation is not None
    assert "https://zoom.us/j/111?pwd=x" in resp2.text
    assert "建立活動草稿" in resp2.text and "WP 500" in resp2.text
    state = resp2.continuation.state
    _assert_json_safe(state)
    assert state["progress"]["zoom"]["id"] == 111
    assert state["progress"]["cover"]["id"] == 55

    # 修好之後再「確認」：不重開 Zoom、不重傳封面
    ext.client.create_draft_event.side_effect = lambda d: types.SimpleNamespace(
        id=901, slug="s", status="draft", edit_url="https://fleet.shosho.tw/edit/901"
    )
    resp3, _ = _cont(state, "確認")
    assert ext.zoom.create_meeting.call_count == 1
    assert ext.client.upload_image.call_count == 1
    assert ext.cover.render_cover.call_count == 1
    assert resp3.continuation is None
    assert "https://fleet.shosho.tw/edit/901" in resp3.text
    draft = ext.client.create_draft_event.call_args.args[0]
    assert draft.featured_image_id == 55


def test_uncertain_create_failure_warns_about_duplicates(ext):
    """逾時／5xx 時站上可能已經建好 → 提醒先去管理頁確認再重試。"""
    ext.client.create_draft_event.side_effect = RuntimeError("timeout")
    resp, _ = _start([_llm_json()])
    resp2, _ = _cont(resp.continuation.state, "確認")
    assert "https://fleet.shosho.tw/deck/events/manage" in resp2.text
    assert "避免重複" in resp2.text


def test_rejected_create_failure_has_no_duplicate_warning(ext):
    """站上明確拒絕（4xx）＝ 一定沒建，不需要嚇人。"""
    from shared.wordpress_client import WPClientError

    ext.client.create_draft_event.side_effect = WPClientError("400 bad date")
    resp, _ = _start([_llm_json()])
    resp2, _ = _cont(resp.continuation.state, "確認")
    assert "避免重複" not in resp2.text
    assert resp2.continuation is not None


def test_zoom_failure_reports_and_keeps_flow(ext):
    ext.zoom.create_meeting.side_effect = ext.zoom.ZoomError("401 token")
    resp, _ = _start([_llm_json()])
    resp2, _ = _cont(resp.continuation.state, "確認")
    assert resp2.continuation is not None
    assert "Zoom" in resp2.text and "401 token" in resp2.text
    ext.client.create_draft_event.assert_not_called()


def test_unknown_flow_raises_not_implemented():
    with pytest.raises(NotImplementedError):
        SanjiHandler().continue_flow("other_flow", {}, "hi", "U1")


def test_state_json_roundtrip_through_conversation_store(ext):
    """state 真的能進 SQLite ConversationStore（JSON 序列化）再拿出來接著跑。"""
    from gateway.conversation_state import ConversationStore

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
    conv = store.get("1.0")
    assert conv.state == resp.continuation.state
