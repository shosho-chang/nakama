"""Franky Slack DM sender — Critical alerts + weekly digest (ADR-007 §9).

Uses slack_sdk.WebClient (sync). Only chat_postMessage + conversations_open — does NOT
subscribe to events or handle interactivity (降低 attack surface per ADR-007 §9).

Env:
    SLACK_FRANKY_BOT_TOKEN   — bot token (xoxb-...)
    SLACK_SHOSHO_USER_ID     — target user ID (U07XXXXXXX) for DMs

Factory returns a no-op stub when either env is missing — dev machines and CI can run
without creds, alert_router still exercises dedup logic, no Slack API calls made.
"""

from __future__ import annotations

import os
from typing import Protocol

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from shared.log import get_logger
from shared.schemas.franky import AlertV1

logger = get_logger("nakama.franky.slack_bot")

# Emoji prefixes for visual scanability in Slack DM (observability.md §6 Alert 三層)
_SEVERITY_EMOJI = {
    "critical": ":rotating_light:",
    "warning": ":warning:",
    "info": ":information_source:",
}


class SlackPoster(Protocol):
    """Structural protocol for alert_router dependency injection — any object with
    a `post_alert(AlertV1) -> str | None` method satisfies this."""

    def post_alert(self, alert: AlertV1) -> str | None: ...


class FrankySlackBot:
    """Thin WebClient wrapper that DMs `SLACK_SHOSHO_USER_ID`."""

    def __init__(self, *, bot_token: str, user_id: str, client: WebClient | None = None) -> None:
        self._client = client or WebClient(token=bot_token)
        self._user_id = user_id
        self._dm_channel: str | None = None  # cached after first conversations_open

    @classmethod
    def from_env(cls) -> SlackPoster:
        """Return FrankySlackBot if env is present, otherwise a log-only stub."""
        token = os.getenv("SLACK_FRANKY_BOT_TOKEN")
        user = os.getenv("SLACK_SHOSHO_USER_ID")
        if not token or not user:
            logger.info(
                "Franky Slack bot disabled — missing %s",
                "SLACK_FRANKY_BOT_TOKEN" if not token else "SLACK_SHOSHO_USER_ID",
            )
            return _NoopSlackStub()
        return cls(bot_token=token, user_id=user)

    def _ensure_dm_channel(self) -> str:
        if self._dm_channel:
            return self._dm_channel
        resp = self._client.conversations_open(users=self._user_id)
        ch = resp["channel"]["id"]  # raises KeyError on malformed response — surface loudly
        self._dm_channel = ch
        return ch

    def post_alert(self, alert: AlertV1) -> str | None:
        """Send DM; returns Slack `ts` (thread id) on success, None on error."""
        emoji = _SEVERITY_EMOJI.get(alert.severity, "")
        text = (
            f"{emoji} *{alert.title}*\n"
            f"{alert.message}\n"
            f"_rule=`{alert.rule_id}`  op=`{alert.operation_id}`_"
        )
        return self._post_text(text, context=f"alert rule={alert.rule_id}")

    def post_plain(self, text: str, *, context: str = "plain") -> str | None:
        """Send plain markdown DM (weekly digest / startup notice). Returns Slack ts or None."""
        return self._post_text(text, context=context)

    def _post_text(self, text: str, *, context: str) -> str | None:
        try:
            channel = self._ensure_dm_channel()
            resp = self._client.chat_postMessage(
                channel=channel,
                text=text,
                unfurl_links=False,
                unfurl_media=False,
            )
            ts = resp.get("ts")
            logger.info("slack dm sent context=%s ts=%s", context, ts)
            return str(ts) if ts else None
        except SlackApiError as exc:
            logger.error(
                "slack dm failed context=%s err=%s",
                context,
                exc.response.get("error") if exc.response else str(exc),
            )
            return None


class _NoopSlackStub:
    """Drop-in replacement used when Slack env is missing; logs instead of posting."""

    def post_alert(self, alert: AlertV1) -> str | None:
        logger.warning(
            "[slack stub] severity=%s rule=%s title=%s msg=%s",
            alert.severity,
            alert.rule_id,
            alert.title,
            alert.message,
        )
        return None

    def post_plain(self, text: str, *, context: str = "plain") -> str | None:
        # Log only the first 240 chars to avoid dumping whole digest to cron log
        preview = text if len(text) <= 240 else text[:240] + "…"
        logger.warning("[slack stub] context=%s preview=%s", context, preview)
        return None
