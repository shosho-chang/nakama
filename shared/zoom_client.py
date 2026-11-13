"""Zoom 會議 client（同步）——讓 Sanji 幫社群活動開一般排程會議、拿入會連結。

- Server-to-Server OAuth：環境變數 `ZOOM_ACCOUNT_ID` / `ZOOM_CLIENT_ID` /
  `ZOOM_CLIENT_SECRET`；access token 在 process 內快取到過期前約 60 秒。
- 只用 `users/me/meetings`（建立）與 `meetings/{id}`（刪除）；這組 token 沒有
  user:read scope，不要呼叫 user 端點。
- 建立會議**不自動重試**（避免重複開會）；取 token 遇 5xx／逾時可重試一次。
- 任何錯誤一律包成 `ZoomError`；log 不寫任何 secret / token。

Usage::

    from shared.zoom_client import create_meeting

    meeting = create_meeting("讀書會", start=aware_dt, duration_min=90)
    print(meeting.join_url)
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from shared.log import get_logger

logger = get_logger("nakama.zoom_client")

_TOKEN_URL = "https://zoom.us/oauth/token"
_API_BASE = "https://api.zoom.us/v2"
_TIMEOUT_S = 30.0
_TOKEN_EXPIRY_MARGIN_S = 60.0
_TOKEN_RETRY_DELAY_S = 1.0
_TOPIC_MAX_CHARS = 200
_ERROR_BODY_CHARS = 300
_TIMEZONE_NAME = "Asia/Taipei"
_TAIPEI = ZoneInfo(_TIMEZONE_NAME)
_USER_AGENT = "nakama-zoom-client/1.0"

_ENV_VARS = ("ZOOM_ACCOUNT_ID", "ZOOM_CLIENT_ID", "ZOOM_CLIENT_SECRET")

# Tests swap this for an ``httpx.MockTransport``; production leaves it None.
_transport: httpx.BaseTransport | None = None

# (account_id, client_id) -> (access_token, monotonic expiry)
_token_cache: dict[tuple[str, str], tuple[str, float]] = {}
_token_lock = threading.Lock()


@dataclass(frozen=True)
class ZoomMeeting:
    id: int
    join_url: str
    start_url: str
    password: str


class ZoomError(RuntimeError):
    """Zoom API / 設定錯誤。"""


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _credentials() -> tuple[str, str, str]:
    values = []
    for name in _ENV_VARS:
        value = os.environ.get(name, "").strip()
        if not value:
            raise ZoomError(f"Zoom 設定缺少環境變數 {name}")
        values.append(value)
    return values[0], values[1], values[2]


def _client() -> httpx.Client:
    return httpx.Client(
        timeout=_TIMEOUT_S,
        transport=_transport,
        headers={"User-Agent": _USER_AGENT},
    )


def _http_error(action: str, resp: httpx.Response) -> ZoomError:
    body = resp.text[:_ERROR_BODY_CHARS]
    return ZoomError(f"Zoom {action} 失敗：HTTP {resp.status_code} {body}")


def _fetch_token(account_id: str, client_id: str, client_secret: str) -> tuple[str, float]:
    """POST /oauth/token；5xx / 逾時 / 連線錯誤重試一次。回傳 (token, expires_in 秒)。"""
    last_error: ZoomError | None = None
    for attempt in (1, 2):
        try:
            with _client() as client:
                resp = client.post(
                    _TOKEN_URL,
                    params={"grant_type": "account_credentials", "account_id": account_id},
                    auth=(client_id, client_secret),
                )
        except httpx.TimeoutException as exc:
            last_error = ZoomError(f"Zoom 取 token 逾時（{_TIMEOUT_S:.0f}s）")
            last_error.__cause__ = exc
        except httpx.HTTPError as exc:
            last_error = ZoomError(f"Zoom 取 token 連線失敗：{type(exc).__name__}")
            last_error.__cause__ = exc
        else:
            if resp.status_code < 400:
                try:
                    data = resp.json()
                    token = str(data["access_token"])
                    expires_in = float(data.get("expires_in", 3600))
                except (ValueError, KeyError, TypeError) as exc:
                    raise ZoomError("Zoom 取 token 回應格式不符（缺 access_token）") from exc
                return token, expires_in
            if resp.status_code < 500:
                raise _http_error("取 token", resp)
            last_error = _http_error("取 token", resp)

        if attempt == 1:
            logger.warning("Zoom token fetch failed, retrying once: %s", last_error)
            time.sleep(_TOKEN_RETRY_DELAY_S)

    assert last_error is not None
    raise last_error


def _get_token() -> str:
    account_id, client_id, client_secret = _credentials()
    key = (account_id, client_id)
    with _token_lock:
        cached = _token_cache.get(key)
        now = time.monotonic()
        if cached and now < cached[1]:
            return cached[0]
        token, expires_in = _fetch_token(account_id, client_id, client_secret)
        _token_cache[key] = (token, now + max(expires_in - _TOKEN_EXPIRY_MARGIN_S, 0.0))
        logger.info("Zoom access token refreshed (expires_in=%.0fs)", expires_in)
        return token


def _invalidate_token() -> None:
    with _token_lock:
        _token_cache.clear()


def _api_request(method: str, path: str, action: str, **kwargs) -> httpx.Response:
    token = _get_token()
    try:
        with _client() as client:
            resp = client.request(
                method,
                f"{_API_BASE}{path}",
                headers={"Authorization": f"Bearer {token}"},
                **kwargs,
            )
    except httpx.TimeoutException as exc:
        raise ZoomError(f"Zoom {action} 逾時（{_TIMEOUT_S:.0f}s）") from exc
    except httpx.HTTPError as exc:
        raise ZoomError(f"Zoom {action} 連線失敗：{type(exc).__name__}") from exc
    if resp.status_code == 401:
        # Token revoked / rotated — drop it so the next call refetches.
        _invalidate_token()
    if resp.status_code >= 400:
        raise _http_error(action, resp)
    return resp


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_meeting(topic: str, start: datetime, duration_min: int) -> ZoomMeeting:
    """建立一般排程會議（type 2，Asia/Taipei）。`start` 必須帶時區。"""
    topic = (topic or "").strip()
    if not topic:
        raise ValueError("topic 不可為空")
    topic = topic[:_TOPIC_MAX_CHARS]
    if start.tzinfo is None or start.utcoffset() is None:
        raise ValueError("start 必須是帶時區的 datetime")
    if isinstance(duration_min, bool) or not isinstance(duration_min, int):
        raise ValueError("duration_min 必須是整數分鐘")
    if not 1 <= duration_min <= 1440:
        raise ValueError("duration_min 必須介於 1 到 1440 分鐘")

    start_local = start.astimezone(_TAIPEI).strftime("%Y-%m-%dT%H:%M:%S")
    body = {
        "topic": topic,
        "type": 2,
        "start_time": start_local,
        "timezone": _TIMEZONE_NAME,
        "duration": duration_min,
    }
    resp = _api_request("POST", "/users/me/meetings", "建立會議", json=body)
    try:
        data = resp.json()
        meeting = ZoomMeeting(
            id=int(data["id"]),
            join_url=str(data["join_url"]),
            start_url=str(data.get("start_url", "")),
            password=str(data.get("password", "")),
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise ZoomError("Zoom 建立會議回應格式不符（缺 id / join_url）") from exc
    logger.info("Zoom meeting created id=%s start=%s %s", meeting.id, start_local, _TIMEZONE_NAME)
    return meeting


def delete_meeting(meeting_id: int) -> None:
    """刪除會議；Zoom 回 204 即成功，其餘（含 404）丟 ZoomError。"""
    _api_request("DELETE", f"/meetings/{int(meeting_id)}", "刪除會議")
    logger.info("Zoom meeting deleted id=%s", meeting_id)
