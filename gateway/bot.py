"""Nakama Slack Gateway — Bolt Socket Mode application。"""

from __future__ import annotations

import os

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from gateway.formatters import format_agent_response
from gateway.handlers import get_handler
from gateway.router import route_mention, route_slash_command
from shared.config import load_config
from shared.log import get_logger

logger = get_logger("nakama.gateway")


def _get_allowed_channels() -> set[str]:
    """從 config 取得允許的頻道 ID 集合。"""
    cfg = load_config()
    channels = cfg.get("gateway", {}).get("slack", {}).get("channels", {})
    return {v for v in channels.values() if v}


def _handle_command(ack, command, respond):
    """處理所有 slash commands（/nami, /zoro, /robin 等）。"""
    ack()

    cmd_name = command["command"]
    text = command.get("text", "").strip()
    user_id = command["user_id"]

    logger.info(f"Slash command: {cmd_name} '{text}' from {user_id}")

    route = route_slash_command(cmd_name, text)
    handler = get_handler(route.agent)

    if not handler:
        respond(f"Agent `{route.agent}` 尚未上線。")
        return

    # 檢查 handler 是否支援此 intent，不支援則轉介
    if not handler.can_handle(route.intent):
        redirect = handler.suggest_redirect(route.intent)
        if redirect:
            redirect_handler = get_handler(redirect)
            if redirect_handler:
                # 轉介到正確的 agent
                result = redirect_handler.handle(route.intent, route.text, user_id)
                fallback, blocks = format_agent_response(redirect, result.text, route.intent)
                respond(text=fallback, blocks=blocks)
                return

    result = handler.handle(route.intent, route.text, user_id)
    fallback, blocks = format_agent_response(route.agent, result.text, route.intent)
    respond(text=fallback, blocks=blocks)


def _handle_mention(event, say):
    """處理 @mention 訊息。"""
    text = event.get("text", "")
    user_id = event.get("user", "")
    channel = event.get("channel", "")

    logger.info(f"Mention in {channel}: '{text}' from {user_id}")

    route = route_mention(text)
    handler = get_handler(route.agent)

    if not handler:
        say(f"Agent `{route.agent}` 尚未上線。", thread_ts=event.get("ts"))
        return

    result = handler.handle(route.intent, route.text, user_id)
    fallback, blocks = format_agent_response(route.agent, result.text, route.intent)
    say(text=fallback, blocks=blocks, thread_ts=event.get("ts"))


def create_app() -> App:
    """建立並設定 Slack Bolt App。"""
    app = App(token=os.environ["SLACK_BOT_TOKEN"])

    # 註冊 slash commands
    for cmd in ["/nami", "/zoro", "/robin", "/franky", "/brook", "/nakama"]:
        app.command(cmd)(_handle_command)

    # @mention handler
    app.event("app_mention")(_handle_mention)

    return app


def run() -> None:
    """啟動 Gateway（Socket Mode + Event Bridge）。"""
    load_config()

    app = create_app()

    # 背景啟動 Event Bridge（Phase 2）
    # from gateway.event_bridge import EventBridge
    # bridge = EventBridge(app.client)
    # bridge_thread = threading.Thread(target=bridge.run, daemon=True)
    # bridge_thread.start()

    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    logger.info("Nakama Slack Gateway started (Socket Mode)")
    handler.start()
