"""FluentCommunity「FCA Events Pro」REST client（fleet.shosho.tw）。

給 Slack agent Sanji 用：上傳活動封面圖、以**草稿**（draft）建立活動。
本 client 永遠送 ``status="draft"``——絕不直接發布，發布由人工在前台完成。
同時永遠送 ``hide_from_global="no"``，確保人工發布後活動會出現在全站活動列表
（只掛在 space 底下的活動會在全站列表隱形，這正是本專案的起因）。

API：``{base}/wp-json/fca-events/v1``（FCA Events Pro 1.3.1，``can_manage_events``
權限；WP 帳號需具備 Event Manager 社群角色）。認證走 WordPress application
password（HTTP Basic）。

環境變數：
    WP_FLEET_BASE_URL       例 https://fleet.shosho.tw（結尾的 /wp-json 會被剝掉）
    WP_FLEET_USERNAME       WP 使用者名稱（缺時 fallback 到 WP_FLEET_USER）
    WP_FLEET_APP_PASSWORD   application password（空白會被剝掉）

建立活動**不重試**（避免重複建立）；密碼永不寫進 log。
"""

from __future__ import annotations

import base64
import os
import secrets
import string
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx

from shared.log import get_logger
from shared.wordpress_client import WPAuthError, WPClientError, WPServerError

logger = get_logger("nakama.fca_events_client")

# 刻意沿用 WordPressClient 的 UA：Cloudflare 的 SBFM 會擋 VPS IP，只有這個 UA
# （＋VPS IP＋/wp-json/ 路徑）有 skip rule。自訂新 UA 會被「Just a moment...」擋成 403
# （2026-11-13 上線實測踩到）。見 docs/runbooks/cf-waf-skip-rules.md。
_USER_AGENT = "nakama-wordpress-client/1.0"
_NAMESPACE = "fca-events/v1"
_UPLOAD_TIMEOUT_S = 60.0
_DEFAULT_TIMEOUT_S = 30.0
_MAX_UPLOAD_BYTES = 5 * 1024 * 1024
_BODY_SNIPPET_CHARS = 300
_SLUG_ALPHABET = string.ascii_lowercase + string.digits

_IMAGE_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

_AUTH_HINT = (
    "check WP_FLEET_USERNAME / WP_FLEET_APP_PASSWORD; the WP user needs the "
    "FluentCommunity 'Event Manager' community role (can_manage_events)"
)

__all__ = [
    "CreatedEvent",
    "EventDraft",
    "FcaEventsClient",
    "FcaEventsResponseError",
    "UploadedImage",
    "WPAuthError",
    "WPClientError",
    "WPServerError",
    "normalize_base_url",
]


class FcaEventsResponseError(RuntimeError):
    """The API answered 2xx but the response is unusable or unsafe.

    Raised e.g. when a created event does not come back as ``draft``.
    """


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class EventDraft:
    title: str
    content_html: str
    start: datetime  # tz-aware
    end: datetime  # tz-aware, > start
    event_type: Literal["online", "in_person"]
    event_url: str = ""  # Zoom join link for online
    meeting_provider: str = ""  # "zoom" when event_url is a Zoom link
    location: str = ""  # for in_person
    featured_image_id: int | None = None
    timezone: str = "Asia/Taipei"


@dataclass(frozen=True)
class UploadedImage:
    id: int
    url: str


@dataclass(frozen=True)
class CreatedEvent:
    id: int
    slug: str
    status: str
    edit_url: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def normalize_base_url(raw: str) -> str:
    """Strip whitespace, trailing ``/`` and a trailing ``/wp-json`` segment."""
    base = raw.strip().rstrip("/")
    if base.endswith("/wp-json"):
        base = base[: -len("/wp-json")].rstrip("/")
    if not base:
        raise ValueError("WP_FLEET_BASE_URL is empty")
    return base


def _is_aware(dt: datetime) -> bool:
    return dt.tzinfo is not None and dt.tzinfo.utcoffset(dt) is not None


def _validate_draft(draft: EventDraft) -> None:
    if not draft.title or not draft.title.strip():
        raise ValueError("EventDraft.title must not be empty")
    if not _is_aware(draft.start) or not _is_aware(draft.end):
        raise ValueError("EventDraft.start / end must be timezone-aware datetimes")
    if draft.end <= draft.start:
        raise ValueError("EventDraft.end must be after start")
    if draft.event_type not in ("online", "in_person"):
        raise ValueError(
            f"EventDraft.event_type must be 'online' or 'in_person', got {draft.event_type!r}"
        )
    if draft.event_type == "online" and not draft.event_url.strip():
        raise ValueError("online event requires event_url (e.g. the Zoom join link)")


def _make_slug(start_local: datetime) -> str:
    suffix = "".join(secrets.choice(_SLUG_ALPHABET) for _ in range(6))
    return f"sanji-{start_local.strftime('%Y%m%d')}-{suffix}"


def _unwrap_event(body: Any) -> dict[str, Any]:
    """Return the event dict whether bare or wrapped in ``event`` / ``data``."""
    if not isinstance(body, dict):
        raise FcaEventsResponseError(
            f"unexpected create-event response type: {type(body).__name__}"
        )
    for key in ("event", "data"):
        inner = body.get(key)
        if isinstance(inner, dict) and "id" in inner:
            return inner
    return body


def _snippet(resp: httpx.Response) -> str:
    return resp.text[:_BODY_SNIPPET_CHARS]


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class FcaEventsClient:
    """Synchronous client for FCA Events Pro (``fca-events/v1``)."""

    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        app_password: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = normalize_base_url(base_url)
        parts = urlsplit(self._base_url)
        self._origin = (
            f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else self._base_url
        )
        cleaned = app_password.replace(" ", "")
        self._b64 = base64.b64encode(f"{username}:{cleaned}".encode()).decode()
        # Test hook: lets tests inject httpx.MockTransport (no network).
        self._transport = transport
        logger.info("FcaEventsClient init base_url=%s user=%s", self._base_url, username)

    @classmethod
    def from_env(cls) -> "FcaEventsClient":
        base_url = os.environ.get("WP_FLEET_BASE_URL", "")
        username = os.environ.get("WP_FLEET_USERNAME") or os.environ.get("WP_FLEET_USER") or ""
        app_password = os.environ.get("WP_FLEET_APP_PASSWORD", "")
        missing = [
            name
            for name, value in (
                ("WP_FLEET_BASE_URL", base_url),
                ("WP_FLEET_USERNAME (or WP_FLEET_USER)", username),
                ("WP_FLEET_APP_PASSWORD", app_password),
            )
            if not value.strip()
        ]
        if missing:
            raise KeyError(f"missing env: {', '.join(missing)}")
        return cls(base_url=base_url, username=username.strip(), app_password=app_password)

    # ------------------------------------------------------------------
    # URLs
    # ------------------------------------------------------------------

    @property
    def base_url(self) -> str:
        return self._base_url

    def edit_url(self, slug: str) -> str:
        return f"{self._origin}/deck/events/edit/{slug}"

    @property
    def manage_url(self) -> str:
        return f"{self._origin}/deck/events/manage"

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------

    def _request(self, method: str, path: str, *, timeout: float, **kwargs: Any) -> Any:
        url = f"{self._base_url}/wp-json/{_NAMESPACE}/{path.lstrip('/')}"
        headers = {
            "Authorization": f"Basic {self._b64}",
            "Accept": "application/json",
            "User-Agent": _USER_AGENT,
        }
        try:
            with httpx.Client(timeout=timeout, transport=self._transport) as client:
                resp = client.request(method, url, headers=headers, **kwargs)
        except httpx.TimeoutException as exc:
            raise WPServerError(
                f"FCA Events timeout on {method} {path} after {timeout:.0f}s"
            ) from exc
        except httpx.TransportError as exc:
            raise WPServerError(f"FCA Events network error on {method} {path}: {exc}") from exc

        logger.debug(
            "fca_events_request method=%s path=%s status=%s", method, path, resp.status_code
        )
        status = resp.status_code
        if status in (401, 403):
            raise WPAuthError(
                f"FCA Events auth error {status} on {method} {path} "
                f"({_AUTH_HINT}): {_snippet(resp)}"
            )
        if 400 <= status < 500:
            raise WPClientError(
                f"FCA Events client error {status} on {method} {path}: {_snippet(resp)}"
            )
        if status >= 500:
            raise WPServerError(
                f"FCA Events server error {status} on {method} {path}: {_snippet(resp)}"
            )
        try:
            return resp.json()
        except ValueError as exc:
            raise FcaEventsResponseError(
                f"FCA Events non-JSON response {status} on {method} {path}: {_snippet(resp)}"
            ) from exc

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def upload_image(self, data: bytes, filename: str) -> UploadedImage:
        """Upload a cover image (png/jpg/webp, ≤ 5 MB) → media id + url."""
        if not data:
            raise ValueError("image data is empty")
        if len(data) > _MAX_UPLOAD_BYTES:
            raise ValueError(f"image is {len(data)} bytes; FCA Events upload limit is 5 MB")
        ext = os.path.splitext(filename)[1].lower()
        content_type = _IMAGE_CONTENT_TYPES.get(ext)
        if content_type is None:
            raise ValueError(f"filename must end with .png/.jpg/.jpeg/.webp, got {filename!r}")

        body = self._request(
            "POST",
            "upload",
            timeout=_UPLOAD_TIMEOUT_S,
            files={"file": (filename, data, content_type)},
        )
        if not isinstance(body, dict) or "id" not in body:
            raise FcaEventsResponseError(
                f"unexpected upload response: {str(body)[:_BODY_SNIPPET_CHARS]}"
            )
        try:
            image = UploadedImage(id=int(body["id"]), url=str(body.get("url") or ""))
        except (TypeError, ValueError) as exc:
            raise FcaEventsResponseError(
                f"upload response has non-integer id: {body.get('id')!r}"
            ) from exc
        logger.info("fca_events upload ok media_id=%s", image.id)
        return image

    def build_create_payload(self, draft: EventDraft) -> dict[str, Any]:
        """Validate ``draft`` and build the ``POST /events`` JSON body."""
        _validate_draft(draft)
        tz = ZoneInfo(draft.timezone)
        start_local = draft.start.astimezone(tz)
        end_local = draft.end.astimezone(tz)

        payload: dict[str, Any] = {
            "title": draft.title.strip(),
            "content": draft.content_html,
            "slug": _make_slug(start_local),
            "status": "draft",  # never publish — a human publishes later
            "hide_from_global": "no",  # visible in global list once published
            "date": start_local.strftime("%Y-%m-%d"),
            "time": start_local.strftime("%H:%M"),
            "end_date": end_local.strftime("%Y-%m-%d"),
            "end_time": end_local.strftime("%H:%M"),
            "timezone": draft.timezone,
            "event_type": draft.event_type,
        }
        if draft.event_url:
            payload["event_url"] = draft.event_url.strip()
        if draft.meeting_provider:
            payload["meeting_provider"] = draft.meeting_provider
        if draft.location:
            payload["location"] = draft.location
        if draft.featured_image_id is not None:
            payload["featured_image_id"] = int(draft.featured_image_id)
        return payload

    def create_draft_event(self, draft: EventDraft) -> CreatedEvent:
        """Create the event as a DRAFT. Not retried (avoid duplicate events)."""
        payload = self.build_create_payload(draft)
        body = self._request("POST", "events", timeout=_DEFAULT_TIMEOUT_S, json=payload)
        event = _unwrap_event(body)

        raw_id = event.get("id")
        try:
            event_id = int(raw_id)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise FcaEventsResponseError(
                f"create-event response has no usable id: {str(body)[:_BODY_SNIPPET_CHARS]}"
            ) from exc

        status = str(event.get("status") or event.get("post_status") or "")
        if status != "draft":
            raise FcaEventsResponseError(
                f"event {event_id} came back with status {status!r}, expected 'draft' — "
                f"check it immediately at {self.manage_url}"
            )

        slug = str(event.get("slug") or event.get("post_name") or payload["slug"])
        created = CreatedEvent(id=event_id, slug=slug, status=status, edit_url=self.edit_url(slug))
        logger.info("fca_events draft created id=%s slug=%s", created.id, created.slug)
        return created
