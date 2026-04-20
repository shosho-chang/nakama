"""Nakama Slack Gateway — multi-bot Socket Mode application。

每個 agent 一個獨立 Slack app（各自 bot/app token），共用同一個 python process，
每個 bot 跑一條 `SocketModeHandler` daemon thread。

`.env` 讀 `<AGENT>_SLACK_BOT_TOKEN` + `<AGENT>_SLACK_APP_TOKEN` 組合，兩個都有才啟動
該 agent 的 bot。留空跳過（未上線的 agent 不會炸）。

mention 來自哪個 bot 就 dispatch 到該 agent 的 handler — 不再用 keyword routing。
slash command 一樣綁在 bot.app 上（每個 agent 的 slash command 在自己的 Slack app
那邊設；runbook 有寫）。
"""

from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING, Callable

from gateway.conversation_state import get_store
from gateway.formatters import format_agent_response
from gateway.handlers import get_handler
from gateway.handlers.base import HandlerResponse
from gateway.router import route_mention, route_slash_command
from shared.config import load_config
from shared.log import get_logger

if TYPE_CHECKING:
    from slack_bolt import App

logger = get_logger("nakama.gateway")

# 所有 agent 都走 <AGENT>_SLACK_BOT_TOKEN / <AGENT>_SLACK_APP_TOKEN 格式。
# order 決定啟動順序、log 順序；Nami 放第一因為她是主 bot（所有 slash commands
# 目前還都掛她那邊，搬到對應 agent bot 是漸進的事）。
_SUPPORTED_AGENTS: tuple[str, ...] = ("nami", "sanji", "zoro", "brook", "robin", "chopper")


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


def _make_slash_command_handler() -> Callable:
    """slash command 通用 handler（`/nami`, `/zoro` 等走 router）。

    slash command 的 routing 不依賴 bot 身份 — `/nami` 在 Nami app 設、`/sanji` 在
    Sanji app 設。command 名字決定要去哪個 handler。保留 router 的 slash routing
    邏輯（跟 mention 不同，這邊 agent 是從 command text 解的）。
    """

    def _handle(ack, command, respond):
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

        if not handler.can_handle(route.intent):
            redirect = handler.suggest_redirect(route.intent)
            if redirect:
                redirect_handler = get_handler(redirect)
                if redirect_handler:
                    result = redirect_handler.handle(route.intent, route.text, user_id)
                    fallback, blocks = format_agent_response(redirect, result.text, route.intent)
                    respond(text=fallback, blocks=blocks)
                    return

        result = handler.handle(route.intent, route.text, user_id)
        fallback, blocks = format_agent_response(route.agent, result.text, route.intent)
        if result.continuation is not None:
            fallback += "\n_（此流程需要多輪回覆；請用 @mention 或 DM 啟動以開 thread）_"
        respond(text=fallback, blocks=blocks)

    return _handle


def _make_mention_handler(agent_name: str) -> Callable:
    """每個 bot 各自的 @mention handler。agent_name 由 closure 綁死 — 訊息到這條
    connection 就等於該 agent 被 mention，不再靠 keyword routing。"""

    def _handle(event, say):
        text = event.get("text", "")
        user_id = event.get("user", "")
        channel = event.get("channel", "")
        event_ts = event.get("ts")
        msg_thread_ts = event.get("thread_ts")

        logger.info(f"[{agent_name}] Mention in {channel}: '{text}' from {user_id}")

        handler = get_handler(agent_name)
        if not handler:
            say(f"Agent `{agent_name}` 尚未上線。", thread_ts=msg_thread_ts)
            return

        # agent 已由 bot 身份決定，但仍用 router 做 intent 分類（例如 Nami 靠
        # intent="create_project" 進 bootstrap flow）；忽略 route.agent
        route = route_mention(text)
        result = handler.handle(route.intent, route.text, user_id)
        fallback, blocks = format_agent_response(agent_name, result.text, route.intent)

        if msg_thread_ts:
            say(text=fallback, blocks=blocks, thread_ts=msg_thread_ts)
            cont_thread_ts = msg_thread_ts
        else:
            say_resp = say(text=fallback, blocks=blocks)
            cont_thread_ts = (say_resp or {}).get("ts") or event_ts

        _register_continuation(
            result,
            thread_ts=cont_thread_ts,
            channel=channel,
            user_id=user_id,
            agent_name=agent_name,
        )

    return _handle


def _make_thread_message_handler(agent_name: str) -> Callable:
    """每個 bot 的 thread reply handler。接續多輪 flow 用 ConversationStore 找
    對應 conversation — thread_ts 是 primary key，跨 bot 可共用。"""

    def _handle(event, say, client):
        if event.get("bot_id") or event.get("subtype") in {"bot_message", "message_changed"}:
            return
        thread_ts = event.get("thread_ts")
        msg_ts = event.get("ts")
        user_id = event.get("user", "")
        channel = event.get("channel", "")

        logger.debug(
            f"[{agent_name}] Message event: ts={msg_ts} thread_ts={thread_ts} "
            f"user={user_id} channel={channel}"
        )

        conv = get_store().get(thread_ts) if thread_ts else None
        # DM 時 thread_ts 可能沒有；嘗試從 user_id + 本 bot 的 agent_name 找最新
        if not conv and user_id:
            conv = get_store().get_latest_for_user_and_agent(user_id, agent_name)
            if conv:
                logger.info(
                    f"[{agent_name}] DM fallback: conversation via user+agent lookup: "
                    f"thread={conv.thread_ts} flow={conv.flow_name}"
                )
                thread_ts = conv.thread_ts

        is_dm = event.get("channel_type") == "im"
        if (not thread_ts or thread_ts == msg_ts or conv is None) and not is_dm:
            return

        if conv is None:
            if not is_dm:
                return
            # DM 第一則訊息：無 active conversation → 當成新請求，路由到**本 bot 對應的 agent**
            text = event.get("text", "").strip()
            if not text:
                return
            logger.info(f"[{agent_name}] DM new conversation: user={user_id} text='{text[:50]}'")

            handler = get_handler(agent_name)
            if handler is None:
                return
            route = route_mention(text)  # intent 分類用；agent 鎖本 bot
            result = handler.handle(route.intent, route.text, user_id)
            fallback, blocks = format_agent_response(agent_name, result.text, route.intent)
            say(text=fallback, blocks=blocks)
            _register_continuation(
                result,
                thread_ts=msg_ts,
                channel=channel,
                user_id=user_id,
                agent_name=agent_name,
            )
            return

        # 活躍 conversation 存在 — 只認原發起人、且只處理屬於本 bot 的 thread
        if user_id != conv.user_id:
            return
        if conv.agent_name != agent_name:
            # 其他 bot 的 thread，不要搶
            return

        handler = get_handler(conv.agent_name)
        if handler is None:
            return

        text = event.get("text", "").strip()
        logger.info(
            f"[{agent_name}] Thread continuation: thread={thread_ts} flow={conv.flow_name} "
            f"user={user_id} text='{text[:50]}...'"
        )

        try:
            result = handler.continue_flow(conv.flow_name, conv.state, text, user_id)
        except NotImplementedError:
            logger.warning(
                f"[{agent_name}] Handler does not implement continue_flow for flow {conv.flow_name}"
            )
            get_store().end(thread_ts)
            return

        fallback, blocks = format_agent_response(conv.agent_name, result.text, conv.flow_name)
        reply_thread_ts = None if channel.startswith("D") else thread_ts
        say(text=fallback, blocks=blocks, thread_ts=reply_thread_ts)

        if result.continuation is None:
            get_store().end(thread_ts)
        else:
            get_store().update(thread_ts, result.continuation.state)

    return _handle


def _create_bot_app(agent_name: str, bot_token: str) -> App:
    """建立單一 agent 的 Slack Bolt App，綁定對應 event handler。"""
    from slack_bolt import App

    app = App(token=bot_token)

    # 每個 bot 註冊自己的 `/<agent>` slash command handler。Slack app 有設
    # slash command 才會真收到 event；沒設就是 dead registration，不影響。
    # Nami bot 額外繼承早期全部掛她的 commands（`/zoro` `/robin` 等），之後
    # 這些 command 搬到對應 agent 的 Slack app 時，自然改由該 bot 收到。
    bot_commands = [f"/{agent_name}"]
    if agent_name == "nami":
        bot_commands += ["/zoro", "/robin", "/franky", "/brook", "/nakama"]
    for cmd in bot_commands:
        app.command(cmd)(_make_slash_command_handler())

    app.event("app_mention")(_make_mention_handler(agent_name))
    app.event("message")(_make_thread_message_handler(agent_name))

    return app


def _discover_bots() -> list[tuple[str, str, str]]:
    """掃 .env 找出所有有設 token 的 agent bot。

    回傳 [(agent_name, bot_token, app_token), ...]。兩個 token 都要有才算數；缺一
    視為該 agent 尚未上線，跳過不報錯。
    """
    found: list[tuple[str, str, str]] = []
    for agent in _SUPPORTED_AGENTS:
        bot_token = os.environ.get(f"{agent.upper()}_SLACK_BOT_TOKEN", "").strip()
        app_token = os.environ.get(f"{agent.upper()}_SLACK_APP_TOKEN", "").strip()
        if bot_token and app_token:
            found.append((agent, bot_token, app_token))
    return found


def run() -> None:
    """啟動所有設好 token 的 agent bot（各自 thread）。"""
    load_config()

    bots = _discover_bots()
    if not bots:
        raise RuntimeError(
            "沒有任何 agent 的 Slack token 設定（預期 <AGENT>_SLACK_BOT_TOKEN + "
            "<AGENT>_SLACK_APP_TOKEN 至少一組）。見 docs/runbooks/add-agent-slack-bot.md"
        )

    logger.info(f"啟動 {len(bots)} 個 Slack bot：{[a for a, _, _ in bots]}")

    from slack_bolt.adapter.socket_mode import SocketModeHandler

    threads: list[threading.Thread] = []
    for agent_name, bot_token, app_token in bots:
        app = _create_bot_app(agent_name, bot_token)
        handler = SocketModeHandler(app, app_token)
        thread = threading.Thread(
            target=handler.start,
            name=f"slack-{agent_name}",
            daemon=True,
        )
        thread.start()
        threads.append(thread)
        logger.info(f"[{agent_name}] Socket Mode connection started")

    # main thread 不能退 — daemon=True 表示 process 收到 SIGTERM 時一併收工
    for t in threads:
        t.join()
