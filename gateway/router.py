"""訊息路由：slash command → regex alias → keyword intent → Haiku fallback。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from shared.log import get_logger

logger = get_logger("nakama.gateway.router")

# ── Agent 名稱別名（零 LLM 成本）──────────────────────────────────────

AGENT_ALIASES: dict[str, str] = {
    # English
    "nami": "nami",
    "zoro": "zoro",
    "robin": "robin",
    "franky": "franky",
    "brook": "brook",
    "usopp": "usopp",
    "sanji": "sanji",
    # 中文名
    "娜美": "nami",
    "索隆": "nami",
    "羅賓": "robin",
    "佛朗基": "franky",
    "布魯克": "brook",
    "騙人布": "usopp",
    "香吉士": "sanji",
    # 職稱
    "航海士": "nami",
    "秘書": "nami",
    "劍士": "zoro",
    "考古學家": "robin",
    "船匠": "franky",
    "音樂家": "brook",
    "狙擊手": "usopp",
    "廚師": "sanji",
}

# ── Intent 關鍵字（零 LLM 成本）────────────────────────────────────────

INTENT_KEYWORDS: dict[str, list[str]] = {
    "create_task": ["任務", "提醒", "排程", "待辦", "to-do", "task", "remind"],
    "list_tasks": ["任務清單", "list tasks", "列出任務", "今天有什麼"],
    "keyword_research": ["關鍵字", "keyword", "研究", "趨勢", "trend"],
    "kb_search": ["知識庫", "KB", "搜尋", "找", "wiki"],
    "system_status": ["系統", "狀態", "health", "status", "花費", "cost"],
    "compose": ["寫文", "文章", "article", "compose", "大綱"],
}

INTENT_TO_AGENT: dict[str, str] = {
    "create_task": "nami",
    "list_tasks": "nami",
    "keyword_research": "zoro",
    "kb_search": "robin",
    "system_status": "franky",
    "compose": "brook",
}


@dataclass
class RouteResult:
    """路由結果。"""

    agent: str
    intent: str
    text: str  # 清理後的使用者文字
    confidence: str  # "exact" | "keyword" | "haiku"


def route_slash_command(command: str, text: str) -> RouteResult:
    """路由 slash command — agent 由 command 名稱決定。"""
    agent = command.lstrip("/")
    if agent == "nakama":
        return route_natural_language(text)
    intent = _match_intent_keywords(text) or "general"
    return RouteResult(agent=agent, intent=intent, text=text, confidence="exact")


def route_mention(text: str) -> RouteResult:
    """路由 @mention 訊息 — 去掉 bot mention 後走自然語言路由。"""
    # Slack 的 @mention 格式：<@U12345> some text
    cleaned = re.sub(r"<@[A-Z0-9]+>\s*", "", text).strip()
    return route_natural_language(cleaned)


def route_natural_language(text: str) -> RouteResult:
    """三層路由：regex alias → keyword intent → Haiku fallback。"""
    # Tier 2a: Agent 名稱 regex
    agent = _match_agent_name(text)
    clean_text = _strip_agent_name(text, agent) if agent else text

    # Tier 2b: Intent 關鍵字
    intent = _match_intent_keywords(clean_text)

    if agent and intent:
        return RouteResult(agent=agent, intent=intent, text=clean_text, confidence="keyword")

    if agent:
        return RouteResult(agent=agent, intent="general", text=clean_text, confidence="keyword")

    if intent:
        resolved_agent = INTENT_TO_AGENT.get(intent, "nami")
        return RouteResult(
            agent=resolved_agent, intent=intent, text=clean_text, confidence="keyword"
        )

    # Tier 3: Claude Haiku fallback
    return _haiku_classify(text)


def _match_agent_name(text: str) -> str | None:
    """在文字中 regex 匹配 agent 別名。"""
    text_lower = text.lower()
    # 先試較長的別名（避免短名誤配）
    for alias, agent in sorted(AGENT_ALIASES.items(), key=lambda x: -len(x[0])):
        if alias in text_lower:
            return agent
    return None


def _strip_agent_name(text: str, agent: str | None) -> str:
    """從文字中移除 agent 名稱，留下指令內容。"""
    if not agent:
        return text
    result = text
    for alias, a in AGENT_ALIASES.items():
        if a == agent:
            # 大小寫不敏感移除
            result = re.sub(
                rf"[,，]?\s*{re.escape(alias)}\s*[,，]?\s*",
                " ",
                result,
                flags=re.IGNORECASE,
            )
    return result.strip()


def _match_intent_keywords(text: str) -> str | None:
    """關鍵字匹配 intent。先試長 keyword 再試短 keyword。"""
    text_lower = text.lower()
    # 先試多字 keyword（如「任務清單」優先於「任務」）
    all_pairs = []
    for intent, keywords in INTENT_KEYWORDS.items():
        for kw in keywords:
            all_pairs.append((kw, intent))
    all_pairs.sort(key=lambda x: -len(x[0]))

    for kw, intent in all_pairs:
        if kw.lower() in text_lower:
            return intent
    return None


def _haiku_classify(text: str) -> RouteResult:
    """用 Claude Haiku 分類 agent + intent（Tier 3 fallback）。"""
    try:
        from shared.anthropic_client import ask_claude, set_current_agent

        set_current_agent("gateway")
        prompt = (
            "你是 Nakama 路由系統。根據使用者訊息，判斷應該由哪個 agent 處理。\n\n"
            "可用 agents:\n"
            "- nami: 任務管理、排程、待辦\n"
            "- zoro: 關鍵字研究、趨勢分析\n"
            "- robin: 知識庫搜尋、資料查詢\n"
            "- franky: 系統狀態、成本查詢\n"
            "- brook: 文章撰寫、內容創作\n\n"
            f"使用者訊息：{text}\n\n"
            '回覆 JSON：{{"agent": "agent_name", "intent": "intent_name"}}\n'
            "只回覆 JSON，不要其他文字。"
        )
        raw = ask_claude(
            prompt,
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            temperature=0.0,
        )
        result = json.loads(raw.strip())
        return RouteResult(
            agent=result.get("agent", "nami"),
            intent=result.get("intent", "general"),
            text=text,
            confidence="haiku",
        )
    except Exception as e:
        logger.warning(f"Haiku routing failed: {e}, defaulting to nami")
        return RouteResult(agent="nami", intent="general", text=text, confidence="haiku")
