"""Nakama Slack Gateway — Bolt Socket Mode application。"""

from __future__ import annotations

import os

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from gateway.conversation_state import get_store
from gateway.formatters import format_agent_response
from gateway.handlers import get_handler
from gateway.handlers.base import HandlerResponse
from gateway.router import route_mention, route_slash_command
from shared.config import load_config
from shared.log import get_logger

logger = get_logger("nakama.gateway")


def _get_allowed_channels() -> set[str]:
    """從 config 取得允許的頻道 ID 集合。"""
    cfg = load_config()
    channels = cfg.get("gateway", {}).get("slack", {}).get("channels", {})
    return {v for v in channels.values() if v}


def _register_continuation(
    result: HandlerResponse,
    *,
    thread_ts: str | None,
    channel: str,
    user_id: str,
    agent_name: str,
) -> None:
    """若 handler 要求接續，註冊到 ConversationStore。"""
    if result.continuation is None or not thread_ts:
        return
    store = get_store()
    if store.get(thread_ts) is not None:
        # 已有活躍 conversation（如 end_turn 後用戶再次 @mention），只更新 state
        store.update(thread_ts, result.continuation.state)
        logger.info(
            f"Continuation updated: thread={thread_ts} agent={agent_name} "
            f"flow={result.continuation.flow_name}"
        )
    else:
        store.start(
            thread_ts=thread_ts,
            channel=channel,
            user_id=user_id,
            agent_name=agent_name,
            flow_name=result.continuation.flow_name,
            state=result.continuation.state,
        )
        logger.info(
            f"Continuation registered: thread={thread_ts} agent={agent_name} "
            f"flow={result.continuation.flow_name}"
        )


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
    # Slash command 有 continuation 時無法開 thread（respond 沒 ts），以 note 提示
    if result.continuation is not None:
        fallback += "\n_（此流程需要多輪回覆；請用 @mention 或 DM 啟動以開 thread）_"
    respond(text=fallback, blocks=blocks)


def _handle_mention(event, say):
    """處理 @mention 訊息。"""
    text = event.get("text", "")
    user_id = event.get("user", "")
    channel = event.get("channel", "")
    event_ts = event.get("ts")

    logger.info(f"Mention in {channel}: '{text}' from {user_id}")

    route = route_mention(text)
    handler = get_handler(route.agent)

    if not handler:
        say(f"Agent `{route.agent}` 尚未上線。", thread_ts=event_ts)
        return

    result = handler.handle(route.intent, route.text, user_id)
    fallback, blocks = format_agent_response(route.agent, result.text, route.intent)
    say(text=fallback, blocks=blocks, thread_ts=event_ts)

    _register_continuation(
        result,
        thread_ts=event_ts,
        channel=channel,
        user_id=user_id,
        agent_name=route.agent,
    )


def _handle_thread_message(event, say, client):
    """接續 thread 內對話：若 thread_ts 有註冊中的 flow，路由回 handler.continue_flow。"""
    # 只處理 user 訊息（bot subtype 跳過，不然會自己回自己）
    if event.get("bot_id") or event.get("subtype") in {"bot_message", "message_changed"}:
        return
    thread_ts = event.get("thread_ts")
    msg_ts = event.get("ts")
    user_id = event.get("user", "")
    channel = event.get("channel", "")

    # Debug: 記錄收到的 message event
    logger.debug(
        f"Message event: ts={msg_ts} thread_ts={thread_ts} user={user_id} channel={channel}"
    )

    conv = get_store().get(thread_ts) if thread_ts else None
    # 在 DM 中，thread_ts 可能沒有；嘗試從 user_id + agent_name 找最新 conversation
    if not conv and user_id:
        conv = get_store().get_latest_for_user_and_agent(user_id, "nami")
        if conv:
            logger.info(
                f"DM fallback: found conversation via user+agent lookup: "
                f"thread={conv.thread_ts} flow={conv.flow_name}"
            )
            thread_ts = conv.thread_ts

    if not thread_ts or thread_ts == msg_ts or conv is None:
        return  # 非 thread reply 或沒有活躍 flow

    if user_id != conv.user_id:
        return  # 只認原發起人

    handler = get_handler(conv.agent_name)
    if handler is None:
        return

    text = event.get("text", "").strip()
    logger.info(
        f"Thread continuation: thread={thread_ts} flow={conv.flow_name} "
        f"user={user_id} text='{text[:50]}...'"
    )

    try:
        result = handler.continue_flow(conv.flow_name, conv.state, text, user_id)
    except NotImplementedError:
        logger.warning(
            f"Handler {conv.agent_name} does not implement continue_flow for flow {conv.flow_name}"
        )
        get_store().end(thread_ts)
        return

    fallback, blocks = format_agent_response(conv.agent_name, result.text, conv.flow_name)
    say(text=fallback, blocks=blocks, thread_ts=thread_ts)

    if result.continuation is None:
        get_store().end(thread_ts)
    else:
        get_store().update(thread_ts, result.continuation.state)


def create_app() -> App:
    """建立並設定 Slack Bolt App。"""
    app = App(token=os.environ["SLACK_BOT_TOKEN"])

    # 註冊 slash commands
    for cmd in ["/nami", "/zoro", "/robin", "/franky", "/brook", "/nakama"]:
        app.command(cmd)(_handle_command)

    # @mention handler
    app.event("app_mention")(_handle_mention)

    # thread reply handler — 接續多輪 flow
    app.event("message")(_handle_thread_message)

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
