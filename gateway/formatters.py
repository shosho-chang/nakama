"""Slack Block Kit 訊息格式化。"""

from __future__ import annotations

AGENT_EMOJI: dict[str, str] = {
    "nami": ":tangerine:",
    "zoro": ":crossed_swords:",
    "robin": ":books:",
    "franky": ":wrench:",
    "brook": ":musical_note:",
    "usopp": ":dart:",
    "sanji": ":cook:",
}


def format_agent_response(agent: str, text: str, intent: str) -> tuple[str, list[dict]]:
    """將 agent 回應格式化為 Block Kit。回傳 (fallback_text, blocks)。"""
    emoji = AGENT_EMOJI.get(agent, ":robot_face:")

    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{emoji} *{agent.capitalize()}*\n{text}",
            },
        },
    ]
    return (f"[{agent}] {text}", blocks)


def format_event_message(source: str, event_type: str, payload: dict) -> tuple[str, list[dict]]:
    """將 event bus 事件格式化為 Block Kit。回傳 (fallback_text, blocks)。"""
    emoji = AGENT_EMOJI.get(source, ":robot_face:")
    title = payload.get("title", event_type)

    text = f"{emoji} *{source.capitalize()}* — {event_type}\n{title}"
    if payload.get("path"):
        text += f"\n:page_facing_up: `{payload['path']}`"

    blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": text},
        },
    ]

    # Handoff 提示
    if payload.get("suggest_handoff"):
        handoff = payload["suggest_handoff"]
        target = handoff.get("target", "?")
        reason = handoff.get("reason", "")
        target_emoji = AGENT_EMOJI.get(target, ":robot_face:")
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (f":arrow_right: {target_emoji} *{target.capitalize()}*: {reason}"),
                },
            }
        )

    fallback = f"[{source}] {event_type}: {title}"
    return (fallback, blocks)
