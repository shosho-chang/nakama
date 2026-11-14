"""Zoom REST API 客戶端 — 建立 / 刪除會議（Server-to-Server OAuth）。

Nami 排會議時用：``create_meeting`` 回 join URL，由呼叫端寫進 Google Calendar 事件。
憑證從 .env 讀（建立 Server-to-Server OAuth app 時取得）::

    ZOOM_ACCOUNT_ID=
    ZOOM_CLIENT_ID=
    ZOOM_CLIENT_SECRET=
    ZOOM_USER_ID=me        # 選填，會議掛在哪個 Zoom 使用者底下；預設 me

所需 scope：``meeting:write:meeting:admin``（建立 / 刪除會議）。

刻意**不**保存、回傳 ``start_url``：那條連結帶主持人權杖，寫進行事曆等於把主持權發給
所有看得到事件的人。
"""

from __future__ import annotations

import math
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from shared.log import get_logger

logger = get_logger("nakama.zoom")

_TOKEN_URL = "https://zoom.us/oauth/token"
_API_BASE = "https://api.zoom.us/v2"
TIMEZONE = "Asia/Taipei"
_TIMEOUT = 15.0
# token 有效 1 小時；提前 60 秒視為過期，避免剛好在邊界送出失效 token
_TOKEN_SKEW_SEC = 60


class ZoomError(Exception):
    """Zoom 呼叫失敗（未設定憑證、授權失敗、API 回錯、網路問題）。訊息可直接給使用者看。"""


@dataclass
class ZoomMeeting:
    """Zoom 會議的精簡表示（不含主持人 start_url，見模組 docstring）。"""

    id: str
    join_url: str
    passcode: str = ""


# ── 憑證 / token ──────────────────────────────────────────────────

_token_lock = threading.Lock()
_token_cache: dict = {"value": None, "expires_at": 0.0}


def _make_client() -> httpx.Client:
    return httpx.Client(timeout=_TIMEOUT)


def _credentials() -> tuple[str, str, str]:
    account_id = os.environ.get("ZOOM_ACCOUNT_ID", "").strip()
    client_id = os.environ.get("ZOOM_CLIENT_ID", "").strip()
    client_secret = os.environ.get("ZOOM_CLIENT_SECRET", "").strip()
    missing = [
        name
        for name, val in (
            ("ZOOM_ACCOUNT_ID", account_id),
            ("ZOOM_CLIENT_ID", client_id),
            ("ZOOM_CLIENT_SECRET", client_secret),
        )
        if not val
    ]
    if missing:
        raise ZoomError(f"Zoom 憑證未設定（.env 缺 {', '.join(missing)}）")
    return account_id, client_id, client_secret


def is_configured() -> bool:
    """三個憑證都有填才算可用。"""
    try:
        _credentials()
    except ZoomError:
        return False
    return True


def _error_text(resp: httpx.Response) -> str:
    """Zoom 錯誤回應通常是 ``{"code":..., "message":...}``；解析失敗就回原文片段。"""
    try:
        data = resp.json()
        msg = data.get("message") or data.get("reason") or data.get("error_description")
        if msg:
            code = data.get("code") or data.get("error") or resp.status_code
            return f"{msg}（{code}）"
    except Exception:
        pass
    return resp.text[:200] or f"HTTP {resp.status_code}"


def _get_token(*, force_refresh: bool = False) -> str:
    account_id, client_id, client_secret = _credentials()
    with _token_lock:
        now = time.time()
        if (
            not force_refresh
            and _token_cache["value"]
            and now < _token_cache["expires_at"] - _TOKEN_SKEW_SEC
        ):
            return _token_cache["value"]
        try:
            with _make_client() as client:
                resp = client.post(
                    _TOKEN_URL,
                    params={"grant_type": "account_credentials", "account_id": account_id},
                    auth=(client_id, client_secret),
                )
        except httpx.HTTPError as exc:
            raise ZoomError(f"連不上 Zoom 授權服務：{exc}") from exc
        if resp.status_code != 200:
            raise ZoomError(
                f"Zoom 授權失敗：{_error_text(resp)}。"
                "請確認 app 已 Activate、Account ID / Client ID / Client Secret 沒貼錯。"
            )
        data = resp.json()
        token = data.get("access_token")
        if not token:
            raise ZoomError("Zoom 授權回應沒有 access_token")
        _token_cache["value"] = token
        _token_cache["expires_at"] = now + float(data.get("expires_in", 3600))
        return token


def _api(method: str, path: str, **kwargs) -> httpx.Response:
    """帶 Bearer token 呼叫 Zoom API；token 被拒（401）時換一張重試一次。"""
    for attempt in (1, 2):
        token = _get_token(force_refresh=attempt == 2)
        try:
            with _make_client() as client:
                resp = client.request(
                    method,
                    f"{_API_BASE}{path}",
                    headers={"Authorization": f"Bearer {token}"},
                    **kwargs,
                )
        except httpx.HTTPError as exc:
            raise ZoomError(f"連不上 Zoom：{exc}") from exc
        if resp.status_code == 401 and attempt == 1:
            continue
        return resp
    return resp  # pragma: no cover — loop 一定在第 2 圈 return


# ── 時間轉換 ──────────────────────────────────────────────────────


def _to_local(value: str) -> datetime:
    """ISO 8601 → Asia/Taipei 的 aware datetime；沒帶時區視為台北時間。"""
    dt = datetime.fromisoformat(value)
    tz = ZoneInfo(TIMEZONE)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


# ── Public API ────────────────────────────────────────────────────


def create_meeting(*, topic: str, start: str, end: str, agenda: str = "") -> ZoomMeeting:
    """建立一場排程會議（type=2）。``start`` / ``end`` 為 ISO 8601，時長由兩者相減。

    Raises:
        ZoomError: 憑證缺、授權失敗、時間格式 / 區間錯、或 Zoom API 回錯。
    """
    try:
        start_dt = _to_local(start)
        end_dt = _to_local(end)
    except ValueError as exc:
        raise ZoomError(f"Zoom 會議時間格式無效：{exc}") from exc
    seconds = (end_dt - start_dt).total_seconds()
    if seconds <= 0:
        raise ZoomError("Zoom 會議結束時間必須晚於開始時間")

    body = {
        "topic": topic,
        "type": 2,
        "start_time": start_dt.strftime("%Y-%m-%dT%H:%M:%S"),
        "timezone": TIMEZONE,
        "duration": max(1, math.ceil(seconds / 60)),
    }
    if agenda:
        body["agenda"] = agenda

    user_id = os.environ.get("ZOOM_USER_ID", "").strip() or "me"
    resp = _api("POST", f"/users/{user_id}/meetings", json=body)
    if resp.status_code not in (200, 201):
        raise ZoomError(f"Zoom 建立會議失敗：{_error_text(resp)}")
    data = resp.json()
    join_url = data.get("join_url")
    if not join_url:
        raise ZoomError("Zoom 回應沒有 join_url")
    logger.info("Zoom meeting created: id=%s", data.get("id"))
    return ZoomMeeting(
        id=str(data.get("id", "")),
        join_url=join_url,
        passcode=str(data.get("password", "") or ""),
    )


def delete_meeting(meeting_id: str) -> None:
    """刪除會議。**idempotent**：已不存在（404）視為成功，供 rollback 安全重送。"""
    resp = _api("DELETE", f"/meetings/{meeting_id}")
    if resp.status_code in (200, 204, 404):
        return
    raise ZoomError(f"Zoom 刪除會議失敗：{_error_text(resp)}")
