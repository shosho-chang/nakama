"""Sanji 建社群活動流程 — 曉宣／修修在 Slack 叫 Sanji 在 fleet.shosho.tw 發一場「待審核」活動。

流程（多輪，flow_name = ``event_draft``）：

1. 觸發：訊息含建活動關鍵字（見 :data:`TRIGGER_KEYWORDS`）→ :func:`start_flow`
2. LLM 把訊息整理成嚴格 JSON（``prompts/sanji/event_parse.md``），程式端再驗一次：
   - 文案（description）必須是使用者原文的子字串（空白正規化後比對）；LLM 改寫了就
     退回使用者原文（去掉觸發那一行），並記 log
   - 封面小標由程式依活動類型定案，不讓 LLM 自由發揮
3. 資料齊了就先產生封面（上傳到網站媒體庫拿預覽連結），再回確認卡；缺必填就只問缺的東西。
   **內容審核在這張確認卡**（2026-11-16 修修裁決：社群上的待審核活動內頁看不到，
   FluentEvent 寫死只顯示已發布貼文）。
4. :func:`continue_flow`：取消 → 結束；確認 → 執行；其他 → 當修改指示，讓 LLM 套用
5. 執行順序：封面（內容沒變就沿用）→ Zoom（線上）→ 發待審核活動。每一步的成果都寫進
   state，失敗時回報哪些完成、哪些失敗，流程保留；再回「確認」會從失敗那步接著做，
   不會重開 Zoom。

邊界：Sanji 只發「待審核」。社群上按 Review → Approve Post 才上線、才通知會員
（FluentCommunity Pro 內建審核；公告信也是核准後才排程寄出）。

執行端（開 Zoom、合成與上傳封面、發活動）在 freedom-fleet repo（ADR-071 分工 B），
透過 :mod:`shared.fleet_events` 呼叫。

state 會被 ConversationStore JSON 序列化 → 只放 str / int / bool / None / list / dict；
時間一律存 ISO 字串，圖片只存 Slack 下載網址，bytes 不進 state。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from gateway.handlers import sanji_calendar as cal
from gateway.handlers import slack_markdown
from gateway.handlers.base import Continuation, HandlerResponse
from shared import fleet_events
from shared.llm import ask
from shared.llm_json import extract_json_object
from shared.log import get_logger
from shared.prompt_loader import load_prompt

logger = get_logger("nakama.gateway.sanji_event")

FLOW_NAME = "event_draft"
TZ = ZoneInfo("Asia/Taipei")
DEFAULT_DURATION_MIN = 120
PARSE_MAX_TOKENS = 2048
_IMAGE_DOWNLOAD_TIMEOUT_S = 30
_STATE_VERSION = 2
# 活動要發到哪個 space（slug）；UAT 期間設 test-dock。沒設就不准建（避免發錯地方）。
ENV_SPACE = "SANJI_EVENTS_SPACE"
# 確認卡上顯示的 space 名稱（選填；沒設就顯示 slug）
ENV_SPACE_LABEL = "SANJI_EVENTS_SPACE_LABEL"

# ── 觸發 ─────────────────────────────────────────────────────────────────
#
# 刻意保持「單純關鍵字比對」：誤觸的代價很低（Sanji 回確認卡 / 問缺什麼，回「取消」即可），
# 漏觸的代價也低（換個說法再講一次）。不用 LLM 分類，免得一般聊天多打一次模型。
# 「開活動」前面若是「公」（公開活動）不算，避免一般聊天被搶走。
TRIGGER_KEYWORDS = ("建活動", "建立活動", "新增活動", "開活動", "活動草稿", "幫我開一場")
_TRIGGER_RE = re.compile(
    "|".join(r"(?<!公)開活動" if kw == "開活動" else re.escape(kw) for kw in TRIGGER_KEYWORDS),
    re.IGNORECASE,
)

# 回覆詞比對前會去掉頭尾空白與標點、轉小寫
CONFIRM_WORDS = {"確認", "好", "好的", "ok", "okay", "建立", "沒問題", "確定"}
CANCEL_WORDS = {"取消", "算了", "cancel", "不用了"}
_REPLY_STRIP = " \t\r\n。．.！!～~，,、？?…"

# ── 附件圖片（gateway/slack_files.image_attachment_lines 產生的行）──────────
_IMAGE_LINE_RE = re.compile(
    r"^\[附件圖片\]\s+(?P<name>.*?)\s*(?P<url>https?://\S+)\s+(?P<mime>image/\S+)\s*$"
)

_KINDS = ("book_club", "live", "meetup")
# 活動網址（社群貼文＋活動內頁共用）預設「日期＋類型」，修修 2026-11-16 選定
_KIND_SLUGS = {"book_club": "book-club", "live": "live", "meetup": "meetup"}
_SLUG_MAX = 80
_REQUIRED = ("title", "start", "description")
_MISSING_LABELS = {
    "title": "活動名稱",
    "start": "活動時間（哪一天、幾點開始）",
    "description": "活動文案（要放在活動頁上的介紹文字）",
}
_WEEKDAYS = "一二三四五六日"


class EventParseError(RuntimeError):
    """LLM 兩次都沒給出可用的 JSON。"""


# ═════════════════════════════════════════════════════════════════════════
# 小工具
# ═════════════════════════════════════════════════════════════════════════


def _now() -> datetime:
    """現在時間（Asia/Taipei）。獨立成函式方便測試固定日期。"""
    return datetime.now(TZ)


def is_event_request(text: str) -> bool:
    """訊息是不是在叫 Sanji 建活動（關鍵字比對，見 :data:`TRIGGER_KEYWORDS`）。"""
    body, _ = extract_image_lines(text or "")
    return bool(_TRIGGER_RE.search(body))


# Slack 對「透過 AI 工具代發」的訊息會在最後加一行「*Sent using* <工具名>」，
# 不是使用者打的字；留著的話「確認」比對不到、也會混進文案。
_AI_FOOTER_RE = re.compile(r"\s*\*Sent using\*\s+\S[^\n]*\Z")


def strip_ai_footer(text: str) -> str:
    """移除 Slack 代發訊息結尾的「*Sent using* …」那一行。"""
    return _AI_FOOTER_RE.sub("", text or "").rstrip()


def extract_image_lines(text: str) -> tuple[str, dict | None]:
    """把 ``[附件圖片] ...`` 行從文字中拿掉，回 (乾淨文字, 最後一張圖片 or None)。

    多張圖只留最後一張（封面只用得到一張主圖）。
    """
    kept: list[str] = []
    image: dict | None = None
    for line in (text or "").splitlines():
        m = _IMAGE_LINE_RE.match(line.strip())
        if m:
            image = {
                "name": m.group("name").strip() or "image",
                "url": m.group("url"),
                "mimetype": m.group("mime"),
            }
        else:
            kept.append(line)
    return "\n".join(kept).strip(), image


def _normalize_reply(text: str) -> str:
    return (text or "").strip(_REPLY_STRIP).lower()


def _strip_trigger_line(text: str) -> str:
    """去掉第一行含觸發關鍵字的那一行（文案 fallback 用）。"""
    lines = (text or "").splitlines()
    for i, line in enumerate(lines):
        if _TRIGGER_RE.search(line):
            return "\n".join(lines[:i] + lines[i + 1 :]).strip()
    return (text or "").strip()


def _collapse_ws(s: str) -> tuple[str, list[int]]:
    """空白正規化：連續空白縮成一個空格、去頭尾。回 (正規化字串, 每個字元在原字串的 index)。"""
    out: list[str] = []
    idx: list[int] = []
    prev_space = True  # 去掉開頭空白
    for i, ch in enumerate(s):
        if ch.isspace():
            if not prev_space:
                out.append(" ")
                idx.append(i)
            prev_space = True
        else:
            out.append(ch)
            idx.append(i)
            prev_space = False
    if out and out[-1] == " ":
        out.pop()
        idx.pop()
    return "".join(out), idx


def find_verbatim_span(candidate: str, source: str) -> str | None:
    """candidate 在空白正規化後若是 source 的子字串，回 source 中對應的**原文片段**
    （保留原本的換行／空白）；否則回 None。"""
    cand_norm, _ = _collapse_ws(candidate or "")
    if not cand_norm:
        return None
    src_norm, idx = _collapse_ws(source or "")
    pos = src_norm.find(cand_norm)
    if pos == -1:
        return None
    start = idx[pos]
    end = idx[pos + len(cand_norm) - 1] + 1
    return source[start:end]


def _parse_dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(TZ)


def _to_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value > 0:
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip()) or None
    return None


def _md(dt: datetime) -> str:
    """6/28（日）"""
    return f"{dt.month}/{dt.day}（{_WEEKDAYS[dt.weekday()]}）"


def _hm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def format_when(event: dict) -> str:
    """確認卡上的時間：2026/6/28（日）20:00–22:00。"""
    start = _parse_dt(event.get("start"))
    end = _parse_dt(event.get("end"))
    if start is None:
        return "（未定）"
    text = f"{start.year}/{_md(start)} {_hm(start)}"
    if end is not None:
        if end.date() == start.date():
            text += f"–{_hm(end)}"
        else:
            text += f" – {end.year}/{_md(end)} {_hm(end)}"
    return text


def cover_date_label(event: dict) -> str:
    """封面日期列。

    - 線上：``6/28（日）20:00・線上``；直播：``6/28（日）20:00・線上直播``
    - 實體：地點在前 ``台北場・8/9（六）14:00``
    """
    start = _parse_dt(event.get("start"))
    if start is None:
        return ""
    when = f"{_md(start)}{_hm(start)}"
    if event.get("is_online"):
        suffix = "線上直播" if event.get("kind") == "live" else "線上"
        return f"{when}・{suffix}"
    place = (event.get("cover_location_label") or event.get("location") or "").strip()
    return f"{place}・{when}" if place else when


def clean_slug(value: Any) -> str:
    """使用者指定的網址 → 小寫英數與 -；不合格回空字串（改用預設）。"""
    s = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return s[:_SLUG_MAX].strip("-")


def event_slug(event: dict) -> str:
    """實際要用的網址：使用者指定的優先，否則「YYYY-MM-DD-類型」。日期沒定就回空字串。"""
    custom = clean_slug(event.get("slug"))
    if custom:
        return custom
    start = _parse_dt(event.get("start"))
    if start is None:
        return ""
    return f"{start.strftime('%Y-%m-%d')}-{_KIND_SLUGS.get(event.get('kind'), 'event')}"


def _duration_min(event: dict) -> int:
    start = _parse_dt(event.get("start"))
    end = _parse_dt(event.get("end"))
    if start and end and end > start:
        return int((end - start).total_seconds() // 60)
    return int(event.get("duration_min") or DEFAULT_DURATION_MIN)


# ═════════════════════════════════════════════════════════════════════════
# LLM 解析 + 程式端驗證
# ═════════════════════════════════════════════════════════════════════════


def _ask_json(prompt: str) -> dict:
    """叫 LLM 回 JSON；第一次撈不到就補一句「只回 JSON」再試一次，還是不行就丟錯。"""
    reminder = "\n\n（提醒：只回傳一個 JSON 物件，不要任何其他文字或 code fence。）"
    attempts = [prompt, prompt + reminder]
    last_raw = ""
    for i, p in enumerate(attempts, start=1):
        try:
            last_raw = ask(prompt=p, max_tokens=PARSE_MAX_TOKENS, call_class="interactive")
        except Exception as e:  # provider 錯誤也算一次失敗
            logger.warning(f"event_parse ask failed (attempt {i}): {e}")
            continue
        data = extract_json_object(last_raw or "")
        if data:
            return data
        logger.warning(f"event_parse got non-JSON (attempt {i}): {str(last_raw)[:200]!r}")
    raise EventParseError(f"LLM 沒給出可解析的 JSON：{str(last_raw)[:200]!r}")


def _build_prompt(user_message: str, current: dict | None) -> str:
    now = _now()
    current_json = "（無）"
    if current:
        visible = {k: v for k, v in current.items() if not k.startswith("_")}
        current_json = json.dumps(visible, ensure_ascii=False, indent=2)
    return load_prompt(
        "sanji",
        "event_parse",
        today=now.strftime("%Y-%m-%d"),
        weekday=f"星期{_WEEKDAYS[now.weekday()]}",
        now_time=now.strftime("%H:%M"),
        current_json=current_json,
        user_message=user_message,
    )


def _resolve_description(
    llm_desc: str,
    sources: list[str],
    *,
    previous: str,
    fallback: str,
) -> tuple[str, bool]:
    """文案逐字守門。回 (最終文案, 是否走了 fallback)。

    LLM 給的文案必須是某則使用者原文的子字串（空白正規化後）；是 → 用原文片段（保留換行）。
    不是 → 有舊文案就保留舊的；沒有就用 fallback（使用者原文去掉觸發行）。
    """
    llm_desc = (llm_desc or "").strip()
    if not llm_desc:
        return previous, False
    for src in reversed(sources):
        span = find_verbatim_span(llm_desc, src)
        if span is not None:
            return span.strip(), False
    if previous:
        logger.warning("event_parse: LLM 文案不是使用者原文，保留上一版文案")
        return previous, True
    logger.warning("event_parse: LLM 文案不是使用者原文，退回使用者原始訊息（去掉觸發行）")
    return fallback, True


def normalize_event(
    data: dict,
    *,
    sources: list[str],
    previous: dict | None = None,
    fallback_description: str = "",
) -> dict:
    """LLM JSON → 驗證過的活動 dict（JSON-safe）。"""
    previous = previous or {}
    title = str(data.get("title") or "").strip()
    if title and not any(find_verbatim_span(title, s) for s in sources):
        logger.warning(f"event_parse: 活動名稱不在使用者原文中：{title!r}")

    description, desc_fallback = _resolve_description(
        str(data.get("description") or ""),
        sources,
        previous=str(previous.get("description") or ""),
        fallback=fallback_description,
    )

    kind = data.get("kind") if data.get("kind") in _KINDS else None
    raw_online = data.get("is_online")
    if kind is None:
        kind = "live" if raw_online is not False else "meetup"
    if isinstance(raw_online, bool):
        is_online = raw_online
    else:
        is_online = kind != "meetup"

    start = _parse_dt(data.get("start"))
    end = _parse_dt(data.get("end"))
    duration = _to_int(data.get("duration_min"))
    duration_defaulted = False
    if start is not None:
        if end is None or end <= start:
            if duration is None:
                duration = DEFAULT_DURATION_MIN
                duration_defaulted = True
            end = start + timedelta(minutes=duration)
        else:
            duration = int((end - start).total_seconds() // 60)
    else:
        end = None

    eyebrow_llm = str(data.get("cover_eyebrow") or "").strip()
    if kind == "book_club":
        eyebrow = "Captain's Book Club"
    elif kind == "meetup":
        eyebrow = "Fleet Meetup"
    else:
        eyebrow = eyebrow_llm if eyebrow_llm.startswith("Live Room") else "Live Room"

    lines = [str(x).strip() for x in (data.get("cover_headline_lines") or []) if str(x).strip()]
    if not lines and title:
        lines = [title]
    lines = lines[:2]
    for line in lines:
        if len(line) > 7:
            logger.warning(f"event_parse: 封面大字超過 7 字：{line!r}")

    missing = {m for m in (data.get("missing") or []) if m in _REQUIRED}
    if not title:
        missing.add("title")
    if start is None:
        missing.add("start")
    if not description:
        missing.add("description")
    else:
        missing.discard("description")
    if title:
        missing.discard("title")
    if start is not None:
        missing.discard("start")

    return {
        "title": title,
        "description": description,
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "duration_min": duration,
        "duration_defaulted": duration_defaulted,
        "kind": kind,
        "is_online": is_online,
        "location": "" if is_online else str(data.get("location") or "").strip(),
        "address": "" if is_online else str(data.get("address") or "").strip(),
        "cover_location_label": ""
        if is_online
        else str(data.get("cover_location_label") or "").strip(),
        "cover_eyebrow": eyebrow,
        "cover_headline_lines": lines,
        "illustration_topic": ""
        if kind == "book_club"
        else str(data.get("illustration_topic") or "").strip(),
        "send_announcement_email": data.get("send_announcement_email") is True,
        # 只存使用者「明確指定」的網址；空字串＝用預設（日期＋類型，改日期會跟著變）
        "slug": clean_slug(data.get("slug")),
        "missing": [m for m in _REQUIRED if m in missing],
        "description_fallback": desc_fallback,
    }


def parse_event(text: str) -> dict:
    """第一次解析：使用者原始訊息（已去掉圖片行）→ 活動 dict。"""
    data = _ask_json(_build_prompt(text, None))
    return normalize_event(data, sources=[text], fallback_description=_strip_trigger_line(text))


def revise_event(current: dict, instruction: str, sources: list[str]) -> dict:
    """修改：LLM 把修改指示套到目前的活動 JSON 上，再跑一次同樣的驗證。

    文案守門：新文案必須出自任一則使用者訊息；不是 → 保留舊文案（舊文案是空的才退回
    這次訊息原文）。
    """
    llm_view = {
        k: v
        for k, v in current.items()
        if k not in {"missing", "description_fallback", "duration_defaulted"}
    }
    data = _ask_json(_build_prompt(instruction, llm_view))
    return normalize_event(
        data, sources=sources, previous=current, fallback_description=instruction.strip()
    )


# ═════════════════════════════════════════════════════════════════════════
# 確認卡
# ═════════════════════════════════════════════════════════════════════════


def _space() -> tuple[str, str]:
    """(slug, 顯示名稱)。slug 沒設回空字串。"""
    slug = os.environ.get(ENV_SPACE, "").strip()
    label = os.environ.get(ENV_SPACE_LABEL, "").strip() or slug
    return slug, label


def _cover_image_plan(event: dict, image: dict | None) -> str:
    if image:
        what = "書封" if event.get("kind") == "book_club" else "圖片"
        return f"用你附的{what}（{image.get('name') or 'image'}）"
    if event.get("kind") != "book_club":
        topic = event.get("illustration_topic") or "依活動主題"
        return f"AI 生成插畫（場景：{topic}）"
    return "不放主圖（讀書會沒附書封；要放的話直接把書封圖貼在這串）"


def _duration_label(minutes: int) -> str:
    return f"{minutes // 60} 小時" if minutes % 60 == 0 else f"{minutes} 分鐘"


def build_missing_prompt(event: dict) -> str:
    items = "\n".join(f"・{_MISSING_LABELS[m]}" for m in event.get("missing") or [])
    return f"要建活動，我這邊還缺這幾樣：\n{items}\n直接在這串回我就好；不做了就回「取消」。"


def build_card(state: dict) -> str:
    """確認卡（純文字）。缺必填時只問缺的東西。內容審核就在這張卡上。"""
    event = state["event"]
    if event.get("missing"):
        return build_missing_prompt(event)

    when = format_when(event)
    if event.get("duration_defaulted"):
        when += f"（你沒給結束時間，先抓 {_duration_label(event['duration_min'])}，要改直接說）"

    if event.get("is_online"):
        where = "線上（確認後我會開一個 Zoom 會議）"
    else:
        where = f"實體・{event.get('location') or '（地點未填）'}"
        if event.get("address"):
            where += f"（{event['address']}）"

    _slug, space_label = _space()
    announce = (
        "寄給這個 space 的全部成員（社群上核准後約 5 分鐘寄出）"
        if event.get("send_announcement_email")
        else "不寄（要寄就說「要寄公告信」）"
    )
    headline = " / ".join(event.get("cover_headline_lines") or []) or "（無）"
    image = state.get("image")
    progress = state.get("progress") or {}
    cover = progress.get("cover") or {}
    cover_fresh = cover.get("key") == _cover_key(event, image)
    lines = [
        "收到，活動內容我整理好了，先對一下（社群上看不到待審核活動的內頁，內容請在這裡確認）：",
        "",
        f"活動名稱：{event.get('title')}",
        f"時間：{when}",
        f"形式：{where}",
        f"發在：{space_label or '（未設定，建不了）'}",
        f"網址：{event_slug(event)}（要改就說「網址改成 xxx」，只能用英數和 -）",
        f"公告信：{announce}",
        "封面：",
        f"・小標：{event.get('cover_eyebrow')}",
        f"・大字：{headline}",
        f"・日期列：{cover_date_label(event)}",
        f"・主圖：{_cover_image_plan(event, image)}",
    ]
    if cover_fresh and cover.get("url"):
        lines.append(f"・預覽：{cover['url']}")
    elif state.get("cover_error"):
        lines.append(f"・預覽：這次沒產生成功（{state['cover_error']}），確認時我會再試一次")
    if cover_fresh and cover.get("note"):
        lines.append(f"・備註：{cover['note']}")
    lines += cal.card_lines(state.get("calendar"), state.get("force_calendar", False))

    zoom = progress.get("zoom")
    if zoom and zoom.get("key") != _zoom_key(event):
        lines += [
            "",
            f"注意：Zoom 會議之前已經用舊的設定開好了（{zoom.get('join_url')}），"
            "我不會再開一個；時間或名稱改了的話，記得到 Zoom 後台手動調整。",
        ]

    lines += ["", "活動文案（照你的原文，一字不改）："]
    if event.get("description_fallback"):
        lines.append("（文案我直接放你訊息的全文，範圍不對的話直接貼正確的文案給我）")
    lines += [
        event.get("description") or "",
        "",
        "沒問題就回「確認」，我會發到社群，狀態是「待審核」：會員看不到，"
        "要到社群上按 Review → Approve Post 才會上線、通知會員。",
        "要改就直接說，例如「時間改成 8 點」；不做了就回「取消」。",
    ]
    return "\n".join(lines)


def _respond(state: dict, text: str) -> HandlerResponse:
    return HandlerResponse(text=text, continuation=Continuation(flow_name=FLOW_NAME, state=state))


def _card_response(state: dict, user_id: str) -> HandlerResponse:
    """資料齊了就先備好封面預覽，再出確認卡。"""
    state.pop("cover_error", None)
    if not state["event"].get("missing"):
        try:
            _ensure_cover(state, user_id)
        except Exception as e:  # 預覽失敗不擋確認卡；確認時會再試
            logger.warning(f"event_draft: cover preview failed user={user_id}: {e}")
            state["cover_error"] = str(e)[:200]
    # 跟 Nami 同一套：先查修修的 Google 行事曆有沒有衝突（2026-11-16 修修要求）
    span = None if state["event"].get("missing") else _event_span(state["event"])
    state["calendar"] = cal.check(*span) if span else None
    return _respond(state, build_card(state))


def _event_span(event: dict) -> tuple[datetime, datetime] | None:
    start = _parse_dt(event.get("start"))
    if start is None:
        return None
    end = _parse_dt(event.get("end")) or start + timedelta(minutes=_duration_min(event))
    return start, end


# ═════════════════════════════════════════════════════════════════════════
# 流程入口
# ═════════════════════════════════════════════════════════════════════════


def start_flow(text: str, user_id: str) -> HandlerResponse:
    """handle() 偵測到建活動請求時呼叫。"""
    body, image = extract_image_lines(text)
    body = strip_ai_footer(body)
    try:
        event = parse_event(body)
    except EventParseError as e:
        logger.error(f"event_draft start: parse failed user={user_id}: {e}")
        return HandlerResponse(
            text="抱歉，這則活動資訊我沒整理成功。可以再傳一次嗎？"
            "請把活動名稱、日期時間、活動文案寫在同一則訊息裡。"
        )
    state = {
        "version": _STATE_VERSION,
        "event": event,
        "sources": [body],
        "image": image,
        "progress": {},
    }
    logger.info(
        f"event_draft start: user={user_id} title={event.get('title')!r} "
        f"missing={event.get('missing')} image={'yes' if image else 'no'}"
    )
    return _card_response(state, user_id)


def continue_flow(state: dict, text: str, user_id: str) -> HandlerResponse:
    """thread 裡的後續訊息：取消 / 確認 / 修改。"""
    state = json.loads(json.dumps(state))  # 不就地改 store 傳進來的 dict
    state.setdefault("progress", {})
    state.setdefault("sources", [])
    body, new_image = extract_image_lines(text)
    body = strip_ai_footer(body)
    if new_image:
        state["image"] = new_image

    reply = _normalize_reply(body)
    if reply in CANCEL_WORDS:
        logger.info(f"event_draft cancelled: user={user_id}")
        zoom = state["progress"].get("zoom")
        extra = (
            f"\n提醒：Zoom 會議之前已經開好了（{zoom.get('join_url')}），"
            "不用的話記得到 Zoom 後台刪掉。"
            if zoom
            else ""
        )
        return HandlerResponse(text="好，這場活動先不建了。" + extra)

    if not body:  # 只補一張圖（沒打字）→ 換圖後重出確認卡
        return _card_response(state, user_id)

    if reply in cal.FORCE_WORDS and not state["event"].get("missing"):
        state["force_calendar"] = True
        return execute(state, user_id)

    if reply in CONFIRM_WORDS:
        if state["event"].get("missing"):
            return _respond(state, build_missing_prompt(state["event"]))
        if cal.blocked(state.get("calendar"), state.get("force_calendar", False)):
            return _respond(
                state,
                "這個時段修修的 Google 行事曆已經有行程，我先不建。\n"
                + "\n".join(cal.card_lines(state["calendar"], False)),
            )
        return execute(state, user_id)

    state["sources"].append(body)
    try:
        state["event"] = revise_event(state["event"], body, state["sources"])
    except EventParseError as e:
        logger.error(f"event_draft revise failed user={user_id}: {e}")
        return _respond(
            state,
            "抱歉，這個修改我沒看懂。可以換個說法再講一次嗎？"
            "（例如「時間改成 8 點」「地點改成台北」）不做了就回「取消」。",
        )
    state["force_calendar"] = False  # 改了內容要重新看衝突
    return _card_response(state, user_id)


# ═════════════════════════════════════════════════════════════════════════
# 執行
# ═════════════════════════════════════════════════════════════════════════


def _zoom_key(event: dict) -> str:
    return f"{event.get('title')}|{event.get('start')}|{_duration_min(event)}"


def _cover_key(event: dict, image: dict | None) -> str:
    payload = json.dumps(
        [
            event.get("kind"),
            event.get("cover_eyebrow"),
            event.get("cover_headline_lines"),
            cover_date_label(event),
            event.get("illustration_topic"),
            (image or {}).get("url"),
        ],
        ensure_ascii=False,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _download_image(url: str) -> bytes:
    """用 Sanji bot token 下載 Slack 附圖；拿到 HTML（缺 files:read scope 時 Slack 回登入頁）
    或非圖片內容就丟錯。"""
    token = os.environ.get("SANJI_SLACK_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("沒有 SANJI_SLACK_BOT_TOKEN")
    resp = httpx.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        follow_redirects=True,
        timeout=_IMAGE_DOWNLOAD_TIMEOUT_S,
    )
    resp.raise_for_status()
    ctype = (resp.headers.get("content-type") or "").lower()
    data = resp.content or b""
    if "text/html" in ctype or data[:64].lstrip().lower().startswith((b"<!doctype", b"<html")):
        raise RuntimeError("Slack 回了 HTML 而不是圖片（可能缺 files:read 權限）")
    if not data:
        raise RuntimeError("下載到空檔案")
    return data


def _cover_payload(event: dict, image: dict | None) -> tuple[dict, str]:
    """組 freedom-fleet ``cover`` 指令的輸入。回 (payload, 給使用者的備註)。

    使用者附圖由這裡下載（要用 Sanji 的 Slack token）；AI 插畫交給 freedom-fleet 生。
    """
    start = _parse_dt(event.get("start"))
    payload: dict[str, Any] = {
        "eyebrow": event["cover_eyebrow"],
        "headline_lines": list(event["cover_headline_lines"]),
        "date_label": cover_date_label(event),
        "filename": f"sanji-cover-{start.strftime('%Y%m%d') if start else 'event'}.jpg",
        "main_kind": "none",
    }
    note = ""
    is_book = event.get("kind") == "book_club"
    if image and image.get("url"):
        try:
            data = _download_image(image["url"])
            payload["main_kind"] = "book" if is_book else "illustration"
            payload["main_image_b64"] = base64.b64encode(data).decode("ascii")
            return payload, ""
        except Exception as e:
            logger.warning(f"event_draft: 附圖下載失敗 {image.get('name')}: {e}")
            note = "你附的圖片我下載不到（Slack app 可能缺 files:read 權限），封面先不放這張圖。"
    if not is_book:
        payload["main_kind"] = "illustration"
        payload["illustration_topic"] = event.get("illustration_topic") or event.get("title") or ""
    return payload, note


def _ensure_cover(state: dict, user_id: str) -> dict:
    """封面內容沒變就沿用；變了（或還沒有）就請 freedom-fleet 重新合成並上傳。"""
    event = state["event"]
    image = state.get("image")
    progress = state.setdefault("progress", {})
    key = _cover_key(event, image)
    cover = progress.get("cover")
    if cover and cover.get("key") == key:
        return cover
    payload, note = _cover_payload(event, image)
    out = fleet_events.run("cover", payload)
    note = " ".join(x for x in (note, str(out.get("note") or "")) if x)
    cover = {"id": int(out["media_id"]), "url": str(out["url"]), "key": key, "note": note}
    progress["cover"] = cover
    logger.info(f"event_draft: cover uploaded id={cover['id']} user={user_id}")
    return cover


def _local(dt: datetime) -> str:
    """FluentEvent EventRequest 的時間格式：當地時間 ``Y-m-d H:i:s``。"""
    return dt.astimezone(TZ).strftime("%Y-%m-%d %H:%M:%S")


def _post_payload(state: dict, space: str) -> dict:
    event = state["event"]
    progress = state["progress"]
    start = _parse_dt(event["start"])
    end = _parse_dt(event.get("end")) or start + timedelta(minutes=_duration_min(event))
    fe: dict[str, Any] = {
        "title": slack_markdown.emojize(event["title"]),
        # Slack 的表情代碼、<網址|文字>、*粗體* 網站看不懂，送出前轉成 Markdown
        "description_md": slack_markdown.to_markdown(event["description"]),
        "start_at": _local(start),
        "end_at": _local(end),
        "timezone": "Asia/Taipei",
        "cover_url": progress["cover"]["url"],
        "entry_type": "rsvp",
        "show_guest_count": "yes",
    }
    if event.get("is_online"):
        fe["venue_type"] = "online"
        fe["online_meeting_url"] = (progress.get("zoom") or {}).get("join_url", "")
    else:
        fe["venue_type"] = "offline"
        fe["venue"] = event.get("location", "")
        fe["address"] = event.get("address", "")
    return {
        "space": space,
        "slug": event_slug(event),
        # 貼文本文 = 活動文案：待審核時管理者在動態上就讀得到內容
        "message": slack_markdown.to_markdown(event["description"]),
        "send_announcement_email": bool(event.get("send_announcement_email")),
        "event": fe,
    }


def _success_text(state: dict, created: dict) -> str:
    progress = state["progress"]
    cover = progress.get("cover") or {}
    zoom = progress.get("zoom")
    event = state["event"]
    lines = [
        f"活動「{event.get('title')}」已經發到社群，狀態是「待審核」（會員還看不到）。",
        "",
        f"・待審核貼文：{created.get('permalink') or '（站上沒回連結，請到 space 動態找）'}",
        f"・封面圖：{cover.get('url') or '（無）'}",
    ]
    if zoom:
        lines.append(f"・Zoom 會議連結：{zoom.get('join_url')}")
    if cover.get("note"):
        lines += ["", cover["note"]]
    lines += [
        "",
        "下一步：到社群打開這篇，按 Review → Approve Post，活動才會上線、通知會員。",
    ]
    if event.get("send_announcement_email"):
        lines.append("公告信會在核准後約 5 分鐘寄給這個 space 的全部成員。")
    added = progress.get("calendar") or {}
    if added.get("ok"):
        lines.append("已寫進修修的 Google 行事曆。")
    elif added:
        lines.append(f"行事曆沒寫進去（{added.get('error')}），請手動加。")
    return "\n".join(lines)


def _failure_text(state: dict, step: str, err: Exception) -> str:
    progress = state["progress"]
    done: list[str] = []
    if progress.get("cover"):
        done.append(f"・封面已經上傳：{progress['cover'].get('url')}")
    if progress.get("zoom"):
        done.append(f"・Zoom 會議已經開好：{progress['zoom'].get('join_url')}")
    lines = [f"活動還沒發出去，卡在「{step}」這一步：{err}"]
    if done:
        lines += ["", "已經完成的部分（不會重做）：", *done]
    lines += [
        "",
        "處理好之後回「確認」，我會從失敗的那一步接著做；不做了就回「取消」。",
    ]
    return "\n".join(lines)


def execute(state: dict, user_id: str) -> HandlerResponse:
    """依序執行：封面 → Zoom（線上）→ 發待審核活動。每步成果寫回 state。"""
    event = state["event"]
    progress = state.setdefault("progress", {})
    space, space_label = _space()
    if not space:
        return _respond(
            state,
            f"還沒設定活動要發到哪個 space（env {ENV_SPACE}），我先不建。"
            "請修修設定好再回「確認」。",
        )

    step = "準備"
    try:
        step = "產生封面"
        _ensure_cover(state, user_id)

        if event.get("is_online") and not progress.get("zoom"):
            step = "開 Zoom 會議"
            start = _parse_dt(event["start"])
            out = fleet_events.run(
                "zoom",
                {
                    "title": event["title"],
                    "start": start.isoformat(),
                    "duration_min": _duration_min(event),
                },
            )
            progress["zoom"] = {
                "id": out["id"],
                "join_url": out["join_url"],
                "key": _zoom_key(event),
            }
            logger.info(f"event_draft: zoom created id={out['id']} user={user_id}")

        step = "發待審核活動"
        created = fleet_events.run("post", _post_payload(state, space))
        logger.info(
            f"event_draft: pending event feed={created.get('feed_id')} "
            f"event={created.get('event_id')} space={space} user={user_id}"
        )
    except Exception as e:
        logger.error(f"event_draft execute failed at {step!r} user={user_id}: {e}", exc_info=True)
        text = _failure_text(state, step, e)
        if getattr(e, "maybe_done", False):
            # 逾時／5xx／回應異常：站上可能已經建了，盲目重試會變成兩篇（或兩場 Zoom）
            text += (
                "\n\n⚠️ 這種錯誤有可能是其實已經做完、只是回應出問題。"
                f"回「確認」之前，請先到社群「{space_label}」看有沒有這篇待審核貼文"
                "（或到 Zoom 後台看有沒有這場會議），有的話就不要再確認，避免重複。"
            )
        return _respond(state, text)

    # 寫進修修的 Google 行事曆（失敗不擋，成功訊息裡提醒手動加）
    span = _event_span(event)
    if span:
        desc = [f"社群貼文（待審核）：{created.get('permalink') or ''}"]
        if progress.get("zoom"):
            desc.append(f"Zoom：{progress['zoom']['join_url']}")
        progress["calendar"] = cal.add(
            title=event["title"],
            start=span[0],
            end=span[1],
            description="\n".join(desc) + "\n（Sanji 建的社群活動）",
            key=f"sanji-event-{created.get('feed_id')}",
        )
    return HandlerResponse(text=_success_text(state, created))
