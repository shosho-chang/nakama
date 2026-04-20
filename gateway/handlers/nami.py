"""Nami handler — LLM tool-use agent loop。

取代舊 state-machine 架構：讓 Claude 自己用 tool_use 決定下一步動作
（create_project / create_task / list_tasks / ask_user）。

關鍵設計：
- ``ask_user`` 是特殊 tool — LLM 呼叫時，我們**不執行**，而是把問題回 Slack
  thread 並存住 messages，等使用者下一條訊息進來時，把它當成 tool_result
  丟回去繼續 loop。
- 其他 tool 直接執行、把結果包成 tool_result、繼續 loop。
- 單次呼叫最多 ``_MAX_ITERS`` 輪，避免 LLM 卡在無限 tool 循環。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from gateway.handlers.base import BaseHandler, Continuation, HandlerResponse
from shared import agent_memory, google_calendar, google_gmail
from shared.anthropic_client import call_claude_with_tools, set_current_agent
from shared.events import emit
from shared.google_calendar import CalendarEvent, GoogleCalendarAuthError
from shared.google_gmail import GoogleGmailAuthError
from shared.lifeos_writer import (
    CONTENT_TYPES,
    ProjectExistsError,
    create_project_with_tasks,
    default_task_names,
)
from shared.log import get_logger, kb_log
from shared.memory_extractor import extract_in_background
from shared.obsidian_writer import delete_page, list_files, read_page, write_page
from shared.prompt_loader import load_prompt

logger = get_logger("nakama.gateway.nami")

NAMI_AGENT_FLOW = "nami_agent"
TASK_DIR = "TaskNotes/Tasks"
PROJECT_DIR = "Projects"

_MAX_ITERS = 6
_MODEL = "claude-sonnet-4-6"

# ── Tool definitions（stable, will be prompt-cached） ──────────────────

NAMI_TOOLS: list[dict] = [
    {
        "name": "create_project",
        "description": (
            "建立新的 LifeOS Project（含三個預設 task）。"
            "只有當你確定 topic 與 content_type 時才呼叫。"
            "若缺這兩項，先用 ask_user 問。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "專案主題（繁體中文，去掉「建立」「幫我」等指令詞）",
                },
                "content_type": {
                    "type": "string",
                    "enum": list(CONTENT_TYPES),
                    "description": "專案類型",
                },
                "area": {
                    "type": "string",
                    "enum": ["work", "health", "family", "self-growth", "play", "visibility"],
                    "description": "領域，沒提就填 work",
                },
                "priority": {
                    "type": "string",
                    "enum": ["first", "high", "medium", "low"],
                    "description": "優先級，沒提就填 medium",
                },
                "search_topic": {
                    "type": "string",
                    "description": "SEO 關鍵字（只有 youtube/blog 才適用）",
                },
            },
            "required": ["topic", "content_type"],
        },
    },
    {
        "name": "create_task",
        "description": (
            "建立 Task 檔案。可以獨立存在，也可以 linked 到某個 project。"
            "當使用者說「提醒我」「下週要」「加個 task」等時使用。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "任務標題"},
                "scheduled": {
                    "type": "string",
                    "description": (
                        "排程日期時間 ISO 8601。"
                        "有時間就填 datetime（例：2026-04-23T15:00:00）；"
                        "只有日期就填 date（例：2026-04-23）。沒講就不填。"
                    ),
                },
                "priority": {
                    "type": "string",
                    "enum": ["high", "normal", "low"],
                    "description": "沒講就填 normal",
                },
                "project": {
                    "type": "string",
                    "description": "掛在哪個 project 的名稱（若有）",
                },
                "notes": {"type": "string", "description": "備註"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "list_tasks",
        "description": "列出所有待辦 task（status=to-do / in-progress）。",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "update_task",
        "description": (
            "修改現有 Task 的欄位（排程日期、優先級、狀態、預估番茄數）。"
            "當使用者說「改」「設」「調整日期」「把...改成」「完成了」「番茄設成」等時使用。"
            "若找不到 task，回傳錯誤讓 LLM 告知使用者。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "要修改的任務標題（用來搜尋，不需完全符合）",
                },
                "scheduled": {
                    "type": "string",
                    "description": (
                        "新的排程日期時間 ISO 8601。"
                        "有時間就填 datetime（例：2026-04-23T15:00:00）；"
                        "只有日期就填 date（例：2026-04-23）。"
                        "要清除排程就填空字串。"
                    ),
                },
                "priority": {
                    "type": "string",
                    "enum": ["high", "normal", "low"],
                    "description": "新的優先級",
                },
                "status": {
                    "type": "string",
                    "enum": ["to-do", "in-progress", "done"],
                    "description": "新的狀態",
                },
                "pomodoros": {
                    "type": "integer",
                    "description": "預估番茄數（pomodoro 數量，例如 4）",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "delete_task",
        "description": (
            "刪除現有 Task 檔案。呼叫前必須先用 ask_user 告知使用者將刪除哪個 task 並請確認。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "要刪除的任務標題（用來搜尋，不需完全符合）",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "delete_project",
        "description": (
            "刪除 Project 檔案，可選擇一併刪除該 project 下的所有 tasks。"
            "呼叫前必須先用 ask_user 列出將刪除的所有檔案並請使用者確認。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "要刪除的 project 標題（用來搜尋）",
                },
                "include_tasks": {
                    "type": "boolean",
                    "description": "是否一併刪除該 project 下的所有 tasks（預設 true）",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "create_calendar_event",
        "description": (
            "建立 Google Calendar 事件，預設同時建立對應的 Obsidian Task"
            "（方便在 Tasks view 看到）。預設會先檢查時段衝突，若有重疊事件"
            "會回傳衝突資訊（不建立）— 此時用 ask_user 問使用者要改時段還是"
            "覆蓋。使用者確認要覆蓋時用 force=true 再呼叫一次。"
            "純事件（婚禮、生日、紀念日）不需要 task 的話用 also_create_task=false。"
            "適用於「排會議」「排行程」「XX 點跟 XX 開會」等需求。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "事件標題"},
                "start": {
                    "type": "string",
                    "description": (
                        "開始時間 ISO 8601 本地時間（例：2026-04-25T15:00:00），"
                        "時區會自動套用 Asia/Taipei。"
                    ),
                },
                "end": {
                    "type": "string",
                    "description": "結束時間 ISO 8601（同 start 格式）",
                },
                "description": {"type": "string", "description": "事件描述（可選）"},
                "force": {
                    "type": "boolean",
                    "description": "跳過衝突偵測強制建立，預設 false",
                },
                "also_create_task": {
                    "type": "boolean",
                    "description": (
                        "是否同時建立對應 Task（預設 true）。純事件（婚禮、生日、紀念日）設 false。"
                    ),
                },
            },
            "required": ["title", "start", "end"],
        },
    },
    {
        "name": "list_calendar_events",
        "description": ("列出 Google Calendar 事件。用於「查今天行程」「這週有什麼」等需求。"),
        "input_schema": {
            "type": "object",
            "properties": {
                "range": {
                    "type": "string",
                    "enum": ["today", "tomorrow", "this_week", "next_week", "custom"],
                    "description": "時段範圍",
                },
                "time_min": {
                    "type": "string",
                    "description": "range=custom 時的起始日期（ISO 8601，含時間）",
                },
                "time_max": {
                    "type": "string",
                    "description": "range=custom 時的結束日期（ISO 8601）",
                },
            },
            "required": ["range"],
        },
    },
    {
        "name": "update_calendar_event",
        "description": (
            "修改現有 Calendar 事件。by title 模糊搜尋最近 30 天的事件。"
            "若改動時段，會再次檢查衝突（同 create 行為）。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "要修改的事件現有標題（模糊搜尋）",
                },
                "new_title": {"type": "string", "description": "新標題（可選）"},
                "start": {"type": "string", "description": "新開始時間（可選）"},
                "end": {"type": "string", "description": "新結束時間（可選）"},
                "description": {"type": "string", "description": "新描述（可選）"},
                "force": {
                    "type": "boolean",
                    "description": "改時段時跳過衝突偵測，預設 false",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "delete_calendar_event",
        "description": (
            "刪除 Calendar 事件。**呼叫前必須先用 ask_user 列出要刪的事件請使用者確認。**"
            "by title 模糊搜尋最近 30 天的事件。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "要刪除的事件標題（模糊搜尋）"},
            },
            "required": ["title"],
        },
    },
    # ── Gmail tools ───────────────────────────────────────────────
    {
        "name": "list_gmail_unread",
        "description": (
            "列出 Gmail 收件匣的未讀（或符合 query 的）信件。"
            "用於「Gmail 有什麼新信」「幫我掃信箱」「有沒有重要信」等需求。"
            "回傳每封信的編號、寄件人、主旨、日期、摘要。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Gmail search syntax，預設 'is:unread'。"
                        "例：'is:unread from:brand@example.com'、'is:unread newer_than:3d'"
                    ),
                },
                "max_results": {
                    "type": "integer",
                    "description": "最多回傳幾封，預設 10，最多 20",
                },
            },
        },
    },
    {
        "name": "get_gmail_message",
        "description": (
            "取得單封 Gmail 信件的完整內容（含 body）。"
            "在 list_gmail_unread 後，使用者要求看某封信的完整內容，或你需要讀內文才能回覆時使用。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "string",
                    "description": "信件 ID（從 list_gmail_unread 取得）",
                },
            },
            "required": ["message_id"],
        },
    },
    {
        "name": "search_gmail_history",
        "description": (
            "搜尋 Gmail 全域歷史（含寄件備份）。"
            "報價時用來找自己過去寄過的類似報價信、合作信。"
            "建議 query 帶 'in:sent' 搜已寄出的信。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Gmail search syntax。例：'in:sent 報價 YouTube'、"
                        "'in:sent subject:合作邀約'"
                    ),
                },
                "max_results": {
                    "type": "integer",
                    "description": "最多回傳幾封，預設 5",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "create_gmail_draft",
        "description": (
            "把撰寫好的信件存成 Gmail 草稿。"
            "草稿建立後，**在 Slack 貼出完整預覽（收件人 / 主旨 / 信件內容），"
            "附 Gmail 連結，告訴使用者確認後說「發」才發出**。"
            "若是回覆某封信，請傳入 thread_id 與 in_reply_to_message_id。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "收件人 email 列表",
                },
                "subject": {"type": "string", "description": "信件主旨"},
                "body": {"type": "string", "description": "信件正文（plain text）"},
                "thread_id": {
                    "type": "string",
                    "description": "若為回覆，帶入原信件的 thread_id",
                },
                "in_reply_to_message_id": {
                    "type": "string",
                    "description": "若為回覆，帶入原信件的 message_id（設 In-Reply-To header）",
                },
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "update_gmail_draft",
        "description": (
            "修改既有 Gmail 草稿（未提供的欄位保留原值）。"
            "使用者說「改第二段」「收件人換成 X」等時使用。"
            "修改後同樣在 Slack 貼出新版完整預覽。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "draft_id": {
                    "type": "string",
                    "description": "要修改的草稿 ID（從 create_gmail_draft 取得）",
                },
                "to": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "新的收件人列表（可選）",
                },
                "subject": {"type": "string", "description": "新的主旨（可選）"},
                "body": {"type": "string", "description": "新的信件正文（可選）"},
            },
            "required": ["draft_id"],
        },
    },
    {
        "name": "send_gmail_draft",
        "description": (
            "發送既有 Gmail 草稿。"
            "**只有在使用者明確說「發」「發出去」「確認」「OK 發」之後才呼叫。**"
            "發送後回報已寄出。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "draft_id": {
                    "type": "string",
                    "description": "要發送的草稿 ID",
                },
            },
            "required": ["draft_id"],
        },
    },
    # ── / Gmail tools ─────────────────────────────────────────────
    {
        "name": "ask_user",
        "description": (
            "當必要資訊缺失時向使用者問一個澄清問題。"
            "使用者回覆後你會繼續接力完成任務。一次只問一個最關鍵的缺項。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "給使用者的簡潔問題（繁中）",
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": ("可選項（若適用），例如 ['youtube', 'blog', 'research']"),
                },
            },
            "required": ["question"],
        },
    },
]


# ── Helper: message / content block serialization ─────────────────────


def _content_blocks_to_dicts(blocks: list[Any]) -> list[dict]:
    """將 Claude response content blocks（SDK 物件）轉為可存入 state 的 dict。"""
    result: list[dict] = []
    for block in blocks:
        btype = getattr(block, "type", None)
        if btype == "text":
            result.append({"type": "text", "text": block.text})
        elif btype == "tool_use":
            result.append(
                {
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                }
            )
        elif btype == "thinking":
            # Thinking blocks 在 tool-use 續輪時必須保留（否則 Claude 會困惑）
            result.append({"type": "thinking", "thinking": block.thinking})
    return result


def _extract_text(content_dicts: list[dict]) -> str:
    """從 content dicts 抽出可顯示給使用者的純文字。"""
    parts = [b["text"] for b in content_dicts if b.get("type") == "text"]
    return "\n".join(p.strip() for p in parts if p and p.strip())


# ── Handler ──────────────────────────────────────────────────────────


@dataclass
class _ToolOutcome:
    """Tool 執行的結果（給 LLM 看的字串 + 可選的事件 payload）。"""

    content: str
    is_error: bool = False
    event: dict | None = None


class NamiHandler(BaseHandler):
    """Nami：LLM agent loop handler。"""

    agent_name = "nami"
    supported_intents = ["create_task", "list_tasks", "create_project", "general"]

    def handle(self, intent: str, text: str, user_id: str) -> HandlerResponse:
        set_current_agent("nami")
        date_context = _build_date_context()
        memory_context = agent_memory.format_as_context("nami", user_id)
        parts = [date_context]
        if memory_context:
            parts.append(memory_context)
        parts.append(text)
        messages: list[dict] = [{"role": "user", "content": "\n\n".join(parts)}]
        return self._run_loop(messages, user_id)

    def continue_flow(
        self,
        flow_name: str,
        state: dict,
        text: str,
        user_id: str,
    ) -> HandlerResponse:
        set_current_agent("nami")
        if flow_name != NAMI_AGENT_FLOW:
            return super().continue_flow(flow_name, state, text, user_id)

        messages = state.get("messages", [])
        pending_id = state.get("pending_tool_use_id")
        if not messages:
            return HandlerResponse(text="流程狀態異常，已重置。請重新開始。")

        messages = list(messages)
        if pending_id:
            # 有 pending ask_user：把使用者回覆當成 tool_result 塞回 loop
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": pending_id,
                            "content": text,
                        }
                    ],
                }
            )
        else:
            # 後續問題（task 建完後繼續問）：直接 append 新 user message
            messages.append({"role": "user", "content": text})
        return self._run_loop(messages, user_id)

    # ── Agent loop ────────────────────────────────────────────────

    def _run_loop(self, messages: list[dict], user_id: str) -> HandlerResponse:
        try:
            system_prompt = load_prompt("nami", "agent_system")
        except FileNotFoundError:
            logger.error("agent_system prompt missing — fallback to minimal system")
            system_prompt = "你是 Nami，修修的 LifeOS 任務助手。用繁體中文。"

        for _ in range(_MAX_ITERS):
            response = call_claude_with_tools(
                messages=messages,
                tools=NAMI_TOOLS,
                system=system_prompt,
                model=_MODEL,
            )

            stop_reason = response.stop_reason
            content_dicts = _content_blocks_to_dicts(response.content)

            if stop_reason == "end_turn":
                text = _extract_text(content_dicts) or "完成。"
                # 把 assistant 回覆存進 messages，讓 thread 保持存活接受後續問題
                messages.append({"role": "assistant", "content": content_dicts})
                # 背景抽取記憶（Phase 2）。失敗不影響主流程。
                try:
                    extract_in_background(agent="nami", user_id=user_id, messages=messages)
                except Exception as e:
                    logger.warning(f"Failed to spawn memory extractor: {e}")
                return HandlerResponse(
                    text=text,
                    continuation=Continuation(
                        flow_name=NAMI_AGENT_FLOW,
                        state={"messages": messages, "pending_tool_use_id": None},
                    ),
                )

            if stop_reason != "tool_use":
                logger.warning(f"Unexpected stop_reason: {stop_reason}")
                text = _extract_text(content_dicts) or "流程異常，已中止。"
                return HandlerResponse(text=text)

            # 把 assistant 的完整回覆（含 tool_use blocks）append
            messages.append({"role": "assistant", "content": content_dicts})

            tool_uses = [b for b in content_dicts if b.get("type") == "tool_use"]
            if not tool_uses:
                # stop_reason=tool_use 但沒 tool_use block — 保底結束
                text = _extract_text(content_dicts) or "完成。"
                return HandlerResponse(text=text)

            tool_results: list[dict] = []
            for tu in tool_uses:
                name = tu["name"]
                tool_id = tu["id"]
                tool_input = tu["input"]

                if name == "ask_user":
                    # 特殊：pause loop，丟問題回使用者，等下一輪
                    question = str(tool_input.get("question", "")).strip()
                    options = tool_input.get("options") or []
                    text = question
                    if options:
                        text += "\n\n" + "\n".join(f"  • {o}" for o in options)

                    return HandlerResponse(
                        text=text,
                        continuation=Continuation(
                            flow_name=NAMI_AGENT_FLOW,
                            state={
                                "messages": messages,
                                "pending_tool_use_id": tool_id,
                            },
                        ),
                    )

                outcome = self._execute_tool(name, tool_input)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": outcome.content,
                        "is_error": outcome.is_error,
                    }
                )
                if outcome.event and not outcome.is_error:
                    emit("nami", outcome.event["name"], outcome.event["payload"])
                    kb_log("nami", outcome.event["name"], outcome.event.get("log", ""))

            messages.append({"role": "user", "content": tool_results})

        logger.warning(f"Agent loop hit max iters ({_MAX_ITERS}) without end_turn")
        return HandlerResponse(text="已達最大迴圈次數，請重新下指令。")

    # ── Tool executors ───────────────────────────────────────────

    def _execute_tool(self, name: str, tool_input: dict) -> _ToolOutcome:
        try:
            if name == "create_project":
                return self._tool_create_project(tool_input)
            if name == "create_task":
                return self._tool_create_task(tool_input)
            if name == "update_task":
                return self._tool_update_task(tool_input)
            if name == "delete_task":
                return self._tool_delete_task(tool_input)
            if name == "delete_project":
                return self._tool_delete_project(tool_input)
            if name == "list_tasks":
                return self._tool_list_tasks()
            if name == "create_calendar_event":
                return self._tool_create_calendar_event(tool_input)
            if name == "list_calendar_events":
                return self._tool_list_calendar_events(tool_input)
            if name == "update_calendar_event":
                return self._tool_update_calendar_event(tool_input)
            if name == "delete_calendar_event":
                return self._tool_delete_calendar_event(tool_input)
            if name == "list_gmail_unread":
                return self._tool_list_gmail_unread(tool_input)
            if name == "get_gmail_message":
                return self._tool_get_gmail_message(tool_input)
            if name == "search_gmail_history":
                return self._tool_search_gmail_history(tool_input)
            if name == "create_gmail_draft":
                return self._tool_create_gmail_draft(tool_input)
            if name == "update_gmail_draft":
                return self._tool_update_gmail_draft(tool_input)
            if name == "send_gmail_draft":
                return self._tool_send_gmail_draft(tool_input)
            return _ToolOutcome(content=f"Unknown tool: {name}", is_error=True)
        except GoogleCalendarAuthError as e:
            return _ToolOutcome(
                content=f"Google Calendar 授權失效：{e}",
                is_error=True,
            )
        except GoogleGmailAuthError as e:
            return _ToolOutcome(
                content=f"Gmail 授權失效：{e}",
                is_error=True,
            )
        except Exception as e:
            logger.exception(f"Tool {name} failed")
            return _ToolOutcome(content=f"Tool {name} error: {e}", is_error=True)

    def _tool_create_project(self, input_: dict) -> _ToolOutcome:
        topic = str(input_.get("topic", "")).strip()
        content_type = input_.get("content_type")
        if not topic or content_type not in CONTENT_TYPES:
            return _ToolOutcome(
                content="Missing required fields: topic and/or content_type",
                is_error=True,
            )

        area = input_.get("area") or "work"
        priority = input_.get("priority") or "medium"
        search_topic = input_.get("search_topic")
        tasks = default_task_names(content_type)

        try:
            result = create_project_with_tasks(
                title=topic,
                content_type=content_type,
                task_names=tasks,
                area=area,
                priority=priority,
                search_topic=search_topic,
            )
        except ProjectExistsError as e:
            return _ToolOutcome(
                content=f"Project 或 Task 已存在：{e}。請改用不同標題。",
                is_error=True,
            )

        project_rel = _to_vault_relative(result.project_path)
        task_rels = [_to_vault_relative(p) for p in result.task_paths]

        payload = {
            "title": topic,
            "content_type": content_type,
            "project_path": project_rel,
            "task_paths": task_rels,
        }
        summary = (
            f"✅ Project 建立成功\n"
            f"  📄 {project_rel}\n"
            f"  ✅ {len(task_rels)} 個 Task：{', '.join(tasks)}"
        )
        return _ToolOutcome(
            content=summary,
            event={"name": "project_created", "payload": payload, "log": topic},
        )

    def _tool_create_task(self, input_: dict) -> _ToolOutcome:
        title = str(input_.get("title", "")).strip()
        if not title:
            return _ToolOutcome(content="Missing task title", is_error=True)

        scheduled = input_.get("scheduled")
        priority = input_.get("priority") or "normal"
        project = input_.get("project")
        notes = input_.get("notes", "")

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        frontmatter: dict = {
            "title": title,
            "status": "to-do",
            "priority": priority,
            "tags": ["task"],
            "dateCreated": now_iso,
            "dateModified": now_iso,
        }
        if scheduled:
            frontmatter["scheduled"] = scheduled
        if project:
            frontmatter["projects"] = [f"[[{project}]]"]

        slug = _slugify(title)
        path = f"{TASK_DIR}/{slug}.md"
        write_page(path, frontmatter, notes)

        scheduled_info = f"（排程：{scheduled}）" if scheduled else ""
        project_info = f"（掛在 {project}）" if project else ""
        summary = f"✅ 已建立 task：{title}{scheduled_info}{project_info}"
        return _ToolOutcome(
            content=summary,
            event={
                "name": "task_created",
                "payload": {"title": title, "path": path, "scheduled": scheduled},
                "log": title,
            },
        )

    def _tool_list_tasks(self) -> _ToolOutcome:
        files = list_files(TASK_DIR)
        tasks = []
        for f in files:
            content = read_page(f"{TASK_DIR}/{f.name}")
            if not content:
                continue
            fm = _extract_frontmatter(content)
            if fm.get("status") in ("to-do", "todo", "in-progress"):
                tasks.append(
                    {
                        "title": fm.get("title", f.stem),
                        "scheduled": fm.get("scheduled", ""),
                        "priority": fm.get("priority", "normal"),
                        "status": fm.get("status", "to-do"),
                    }
                )

        if not tasks:
            return _ToolOutcome(content="目前沒有待辦任務。")

        lines = []
        for t in tasks[:20]:
            icon = "🔴" if t["priority"] == "high" else "⚪"
            line = f"- {icon} {t['title']}"
            if t["scheduled"]:
                line += f" ({t['scheduled']})"
            if t["status"] == "in-progress":
                line += " [進行中]"
            lines.append(line)

        return _ToolOutcome(content=f"*待辦任務（{len(tasks)} 項）*\n" + "\n".join(lines))

    def _tool_delete_task(self, input_: dict) -> _ToolOutcome:
        title = str(input_.get("title", "")).strip()
        if not title:
            return _ToolOutcome(content="Missing task title", is_error=True)

        found = self._find_task_by_title(title)
        if not found:
            return _ToolOutcome(
                content=f"找不到標題含「{title}」的 task。請用 list_tasks 確認標題。",
                is_error=True,
            )

        rel_path, fm, _ = found
        matched_title = str(fm.get("title", title))
        deleted = delete_page(rel_path)
        if not deleted:
            return _ToolOutcome(content=f"刪除失敗：檔案不存在（{rel_path}）", is_error=True)

        return _ToolOutcome(
            content=f"🗑️ 已刪除 task：{matched_title}",
            event={
                "name": "task_deleted",
                "payload": {"title": matched_title, "path": rel_path},
                "log": matched_title,
            },
        )

    def _tool_delete_project(self, input_: dict) -> _ToolOutcome:
        title = str(input_.get("title", "")).strip()
        if not title:
            return _ToolOutcome(content="Missing project title", is_error=True)

        include_tasks = input_.get("include_tasks", True)

        found = self._find_project_by_title(title)
        if not found:
            return _ToolOutcome(
                content=f"找不到標題含「{title}」的 project。",
                is_error=True,
            )

        proj_rel, proj_fm = found
        matched_title = str(proj_fm.get("title", title))

        deleted_tasks: list[str] = []
        if include_tasks:
            for task_rel, task_fm in self._find_tasks_by_project(matched_title):
                if delete_page(task_rel):
                    deleted_tasks.append(str(task_fm.get("title", task_rel)))

        delete_page(proj_rel)

        task_summary = f"，含 {len(deleted_tasks)} 個 task" if deleted_tasks else ""
        summary = f"🗑️ 已刪除 project：{matched_title}{task_summary}"
        return _ToolOutcome(
            content=summary,
            event={
                "name": "project_deleted",
                "payload": {
                    "title": matched_title,
                    "path": proj_rel,
                    "deleted_tasks": deleted_tasks,
                },
                "log": matched_title,
            },
        )

    def _find_project_by_title(self, title: str) -> tuple[str, dict] | None:
        """以 title 搜尋 project 檔案，回傳 (relative_path, frontmatter) 或 None。"""
        title_lower = title.lower()
        for f in list_files(PROJECT_DIR):
            rel = f"{PROJECT_DIR}/{f.name}"
            content = read_page(rel)
            if not content:
                continue
            fm = _extract_frontmatter(content)
            fm_title = str(fm.get("title", f.stem)).lower()
            if fm_title == title_lower or title_lower in fm_title or fm_title in title_lower:
                return rel, fm
        return None

    def _find_tasks_by_project(self, project_title: str) -> list[tuple[str, dict]]:
        """找出所有 linked 到 project_title 的 task，回傳 [(rel_path, frontmatter)]。"""
        results: list[tuple[str, dict]] = []
        link = f"[[{project_title}]]"
        for f in list_files(TASK_DIR):
            rel = f"{TASK_DIR}/{f.name}"
            content = read_page(rel)
            if not content:
                continue
            fm = _extract_frontmatter(content)
            projects_field = fm.get("projects") or []
            if isinstance(projects_field, list) and link in projects_field:
                results.append((rel, fm))
            elif isinstance(projects_field, str) and link in projects_field:
                results.append((rel, fm))
        return results

    def _find_task_by_title(self, title: str) -> tuple[str, dict, str] | None:
        """以 title 搜尋 task 檔案，回傳 (relative_path, frontmatter, body) 或 None。"""
        title_lower = title.lower()
        for f in list_files(TASK_DIR):
            rel = f"{TASK_DIR}/{f.name}"
            content = read_page(rel)
            if not content:
                continue
            fm = _extract_frontmatter(content)
            fm_title = str(fm.get("title", "")).lower()
            if fm_title == title_lower or title_lower in fm_title or fm_title in title_lower:
                parts = content.split("---", 2)
                body = parts[2].strip() if len(parts) >= 3 else ""
                return rel, fm, body
        return None

    def _find_task_by_calendar_id(self, event_id: str) -> tuple[str, dict, str] | None:
        """以 calendar_event_id 搜尋 task 檔案，回傳 (relative_path, frontmatter, body) 或 None。"""
        for f in list_files(TASK_DIR):
            rel = f"{TASK_DIR}/{f.name}"
            content = read_page(rel)
            if not content:
                continue
            fm = _extract_frontmatter(content)
            if fm.get("calendar_event_id") == event_id:
                parts = content.split("---", 2)
                body = parts[2].strip() if len(parts) >= 3 else ""
                return rel, fm, body
        return None

    def _tool_update_task(self, input_: dict) -> _ToolOutcome:
        title = str(input_.get("title", "")).strip()
        if not title:
            return _ToolOutcome(content="Missing task title", is_error=True)

        found = self._find_task_by_title(title)
        if not found:
            return _ToolOutcome(
                content=f"找不到標題含「{title}」的 task。請用 list_tasks 確認標題。",
                is_error=True,
            )

        rel_path, fm, body = found
        matched_title = str(fm.get("title", title))

        updated_fields: list[str] = []
        if input_.get("scheduled") is not None:
            scheduled_val = input_["scheduled"]
            if scheduled_val == "":
                fm.pop("scheduled", None)
                updated_fields.append("scheduled 已清除")
            else:
                fm["scheduled"] = scheduled_val
                updated_fields.append(f"scheduled={scheduled_val}")
        if input_.get("priority") is not None:
            fm["priority"] = input_["priority"]
            updated_fields.append(f"priority={input_['priority']}")
        if input_.get("status") is not None:
            fm["status"] = input_["status"]
            updated_fields.append(f"status={input_['status']}")
        if input_.get("pomodoros") is not None:
            fm["pomodoros"] = int(input_["pomodoros"])
            updated_fields.append(f"pomodoros={input_['pomodoros']}")

        if not updated_fields:
            return _ToolOutcome(content="沒有指定要更新的欄位。", is_error=True)

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        fm["dateModified"] = now_iso
        fm = _stringify_fm_dates(fm)

        write_page(rel_path, fm, body)

        summary = f"✅ 已更新 task：{matched_title}（{', '.join(updated_fields)}）"
        return _ToolOutcome(
            content=summary,
            event={
                "name": "task_updated",
                "payload": {
                    "title": matched_title,
                    "path": rel_path,
                    "updated_fields": updated_fields,
                },
                "log": matched_title,
            },
        )

    # ── Calendar tool executors ──────────────────────────────────

    def _tool_create_calendar_event(self, input_: dict) -> _ToolOutcome:
        title = str(input_.get("title", "")).strip()
        start = str(input_.get("start", "")).strip()
        end = str(input_.get("end", "")).strip()
        if not title or not start or not end:
            return _ToolOutcome(content="Missing required fields: title, start, end", is_error=True)

        description = input_.get("description", "") or ""
        force = bool(input_.get("force", False))
        also_create_task = bool(input_.get("also_create_task", True))

        # Pre-check task 檔案不存在，避免 calendar 建完後 task 撞名產生孤兒 event
        task_rel_path: str | None = None
        if also_create_task:
            slug = _slugify(title)
            task_rel_path = f"{TASK_DIR}/{slug}.md"
            existing = self._find_task_by_title(title)
            if existing is not None:
                return _ToolOutcome(
                    content=(
                        f"Task 標題撞名：vault 內已有「{existing[1].get('title', title)}」。"
                        "請改 event 標題，或用 also_create_task=false 只建 calendar 不建 task。"
                    ),
                    is_error=True,
                )

        result = google_calendar.create_event(
            title=title,
            start=start,
            end=end,
            description=description,
            check_conflict=not force,
        )

        # 衝突 → result 是 list[CalendarEvent]，沒有建立
        if isinstance(result, list):
            conflicts_desc = "、".join(
                f"{_fmt_event_time(e.start, e.end)}「{e.title}」" for e in result[:3]
            )
            return _ToolOutcome(
                content=(
                    f"時段衝突：{conflicts_desc}。"
                    " 要改時段還是強制建立（ask_user 問使用者，若同意覆蓋再用 force=true 重試）？"
                ),
                is_error=True,
            )

        event = result
        task_path_display = ""
        if also_create_task and task_rel_path is not None:
            try:
                self._write_calendar_linked_task(task_rel_path, event)
            except Exception as e:
                # Task 寫入失敗 → rollback calendar 避免孤兒事件
                logger.exception(
                    f"Task write failed after calendar create; rolling back event {event.id}"
                )
                try:
                    google_calendar.delete_event(event.id)
                except Exception:
                    logger.exception(
                        f"Rollback delete_event({event.id}) also failed — orphan event remains"
                    )
                    return _ToolOutcome(
                        content=(
                            f"Calendar 已建立但 task 寫入失敗（{e}），"
                            f"自動 rollback 也失敗。請手動刪除 Calendar 事件「{event.title}」。"
                        ),
                        is_error=True,
                    )
                return _ToolOutcome(
                    content=(
                        f"Task 寫入失敗（{e}），Calendar 事件已 rollback。"
                        " 請檢查 vault 狀態後再試。"
                    ),
                    is_error=True,
                )
            task_path_display = f"\n   📝 Task：{task_rel_path}"

        summary = (
            f"📅 Calendar 事件已建立：{event.title}\n"
            f"   時間：{_fmt_event_time(event.start, event.end)}\n"
            f"   連結：{event.html_link}"
            f"{task_path_display}"
        )
        return _ToolOutcome(
            content=summary,
            event={
                "name": "calendar_event_created",
                "payload": {
                    "id": event.id,
                    "title": event.title,
                    "start": event.start,
                    "end": event.end,
                    "html_link": event.html_link,
                    "task_path": task_rel_path if also_create_task else None,
                },
                "log": event.title,
            },
        )

    def _write_calendar_linked_task(self, rel_path: str, event: CalendarEvent) -> None:
        """建立 calendar-linked task；scheduled/scheduled_end 剝 tz 對齊 Obsidian 格式。"""
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        frontmatter = {
            "title": event.title,
            "status": "to-do",
            "priority": "normal",
            "tags": ["task"],
            "dateCreated": now_iso,
            "dateModified": now_iso,
            "scheduled": _strip_tz(event.start),
            "scheduled_end": _strip_tz(event.end),
            "calendar_event_id": event.id,
        }
        write_page(rel_path, frontmatter, "")

    def _tool_list_calendar_events(self, input_: dict) -> _ToolOutcome:
        range_ = input_.get("range", "today")
        tz = ZoneInfo("Asia/Taipei")
        now = datetime.now(tz)

        if range_ == "today":
            time_min = now.replace(hour=0, minute=0, second=0, microsecond=0)
            time_max = time_min + timedelta(days=1)
        elif range_ == "tomorrow":
            time_min = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            time_max = time_min + timedelta(days=1)
        elif range_ == "this_week":
            start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
            days_to_sunday = 6 - now.weekday()  # 週一=0, 週日=6
            time_min = start_of_today
            time_max = start_of_today + timedelta(days=days_to_sunday + 1)
        elif range_ == "next_week":
            start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
            days_to_next_monday = 7 - now.weekday()
            time_min = start_of_today + timedelta(days=days_to_next_monday)
            time_max = time_min + timedelta(days=7)
        elif range_ == "custom":
            t_min_str = input_.get("time_min")
            t_max_str = input_.get("time_max")
            if not t_min_str or not t_max_str:
                return _ToolOutcome(
                    content="range=custom 時必須同時提供 time_min 和 time_max",
                    is_error=True,
                )
            time_min = _parse_iso_local(t_min_str, tz)
            time_max = _parse_iso_local(t_max_str, tz)
        else:
            return _ToolOutcome(content=f"Unknown range: {range_}", is_error=True)

        events = google_calendar.list_events(time_min=time_min, time_max=time_max, max_results=30)

        if not events:
            return _ToolOutcome(content=f"{range_} 時段沒有 Calendar 事件。")

        lines = [f"*Calendar 事件（{range_}，共 {len(events)} 項）*"]
        for e in events[:20]:
            lines.append(f"  • {_fmt_event_time(e.start, e.end)} — {e.title}")
        return _ToolOutcome(content="\n".join(lines))

    def _tool_update_calendar_event(self, input_: dict) -> _ToolOutcome:
        title = str(input_.get("title", "")).strip()
        if not title:
            return _ToolOutcome(content="Missing title", is_error=True)

        found = self._find_calendar_event_by_title(title)
        if not found:
            return _ToolOutcome(
                content=f"找不到標題含「{title}」的 Calendar 事件（最近 30 天）。",
                is_error=True,
            )

        new_title = input_.get("new_title")
        start = input_.get("start")
        end = input_.get("end")
        description = input_.get("description")
        force = bool(input_.get("force", False))

        if not any([new_title, start, end, description]):
            return _ToolOutcome(content="沒有指定要更新的欄位。", is_error=True)

        # 若改時段，先做衝突檢查（排除當前這筆事件本身）
        if (start or end) and not force:
            effective_start = start or found.start
            effective_end = end or found.end
            conflicts = [
                c
                for c in google_calendar.find_conflicts(effective_start, effective_end)
                if c.id != found.id
            ]
            if conflicts:
                conflicts_desc = "、".join(
                    f"{_fmt_event_time(c.start, c.end)}「{c.title}」" for c in conflicts[:3]
                )
                return _ToolOutcome(
                    content=(
                        f"更新時段衝突：{conflicts_desc}。"
                        " 要改到別的時段還是強制覆蓋（force=true）？"
                    ),
                    is_error=True,
                )

        updated = google_calendar.update_event(
            found.id,
            title=new_title,
            start=start,
            end=end,
            description=description,
        )

        changes = []
        if new_title:
            changes.append(f"標題→{new_title}")
        if start:
            changes.append(f"start={start}")
        if end:
            changes.append(f"end={end}")
        if description is not None:
            changes.append("描述已更新")

        task_sync_note = self._sync_task_from_calendar_update(
            updated, title_changed=bool(new_title)
        )

        summary = f"📝 Calendar 事件已更新：{updated.title}（{', '.join(changes)}）{task_sync_note}"
        return _ToolOutcome(
            content=summary,
            event={
                "name": "calendar_event_updated",
                "payload": {
                    "id": updated.id,
                    "title": updated.title,
                    "start": updated.start,
                    "end": updated.end,
                    "changes": changes,
                },
                "log": updated.title,
            },
        )

    def _sync_task_from_calendar_update(self, event: CalendarEvent, *, title_changed: bool) -> str:
        """更新 calendar 後同步對應 task。回傳要附在 summary 後的備註（可為空字串）。"""
        linked = self._find_task_by_calendar_id(event.id)
        if linked is None:
            return ""

        rel_path, fm, body = linked
        fm["scheduled"] = _strip_tz(event.start)
        fm["scheduled_end"] = _strip_tz(event.end)
        if title_changed:
            fm["title"] = event.title
            new_rel = f"{TASK_DIR}/{_slugify(event.title)}.md"
        else:
            new_rel = rel_path

        fm["dateModified"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        fm = _stringify_fm_dates(fm)

        # Write-then-delete 順序：先寫新檔，成功後才刪舊檔。若 write 拋例外，舊檔還在，
        # task 不會遺失。
        write_page(new_rel, fm, body)
        if new_rel != rel_path:
            delete_page(rel_path)
        return f"\n   📝 Task 同步更新：{new_rel}"

    def _tool_delete_calendar_event(self, input_: dict) -> _ToolOutcome:
        title = str(input_.get("title", "")).strip()
        if not title:
            return _ToolOutcome(content="Missing title", is_error=True)

        found = self._find_calendar_event_by_title(title)
        if not found:
            return _ToolOutcome(
                content=f"找不到標題含「{title}」的 Calendar 事件（最近 30 天）。",
                is_error=True,
            )

        google_calendar.delete_event(found.id)

        # 靜默刪除對應 task（找不到不視為錯誤，PRD 規格）
        task_note = ""
        linked = self._find_task_by_calendar_id(found.id)
        if linked is not None:
            task_rel, _, _ = linked
            if delete_page(task_rel):
                task_note = f"\n   📝 Task 一併刪除：{task_rel}"

        summary = f"🗑️ 已刪除 Calendar 事件：{found.title}{task_note}"
        return _ToolOutcome(
            content=summary,
            event={
                "name": "calendar_event_deleted",
                "payload": {"id": found.id, "title": found.title},
                "log": found.title,
            },
        )

    def _find_calendar_event_by_title(self, title: str) -> CalendarEvent | None:
        """在最近 30 天（past 7 + future 23）內 by title 模糊搜尋，回第一個匹配。"""
        tz = ZoneInfo("Asia/Taipei")
        now = datetime.now(tz)
        time_min = now - timedelta(days=7)
        time_max = now + timedelta(days=23)
        title_lower = title.lower()
        events = google_calendar.find_events_by_title(title, time_min=time_min, time_max=time_max)
        # Google q 搜尋已做過濾；這裡再確認以防萬一
        for e in events:
            if title_lower in e.title.lower():
                return e
        return None

    # ── Gmail tool executors ─────────────────────────────────────

    def _tool_list_gmail_unread(self, input_: dict) -> _ToolOutcome:
        query = str(input_.get("query") or "is:unread")
        max_results = min(int(input_.get("max_results") or 10), 20)

        messages = google_gmail.list_messages(query=query, max_results=max_results)
        if not messages:
            return _ToolOutcome(content=f"沒有符合條件的信件（query: {query}）。")

        lines = [f"*Gmail 信件（{query}，共 {len(messages)} 封）*"]
        for i, m in enumerate(messages, 1):
            lines.append(
                f"{i}. [{m['date'][:16]}] {m['from']}\n"
                f"   主旨：{m['subject']}\n"
                f"   id: {m['id']} | thread: {m['thread_id']}\n"
                f"   摘要：{m['snippet'][:100]}"
            )
        return _ToolOutcome(content="\n\n".join(lines))

    def _tool_get_gmail_message(self, input_: dict) -> _ToolOutcome:
        message_id = str(input_.get("message_id", "")).strip()
        if not message_id:
            return _ToolOutcome(content="Missing message_id", is_error=True)

        msg = google_gmail.get_message(message_id)
        content = (
            f"*信件詳情*\n"
            f"From: {msg['from']}\n"
            f"To: {msg['to']}\n"
            f"CC: {msg['cc']}\n"
            f"Subject: {msg['subject']}\n"
            f"Date: {msg['date']}\n"
            f"Thread ID: {msg['thread_id']}\n"
            f"Message ID: {msg['id']}\n\n"
            f"---\n{msg['body'] or '（無純文字內容）'}"
        )
        return _ToolOutcome(content=content)

    def _tool_search_gmail_history(self, input_: dict) -> _ToolOutcome:
        query = str(input_.get("query", "")).strip()
        if not query:
            return _ToolOutcome(content="Missing query", is_error=True)
        max_results = min(int(input_.get("max_results") or 5), 10)

        messages = google_gmail.list_messages(query=query, max_results=max_results)
        if not messages:
            return _ToolOutcome(content=f"沒有找到符合的歷史信件（query: {query}）。")

        lines = [f"*Gmail 歷史搜尋（{query}，共 {len(messages)} 封）*"]
        for i, m in enumerate(messages, 1):
            lines.append(
                f"{i}. [{m['date'][:16]}] to:{m['to']}\n"
                f"   主旨：{m['subject']}\n"
                f"   id: {m['id']}\n"
                f"   摘要：{m['snippet'][:120]}"
            )
        return _ToolOutcome(content="\n\n".join(lines))

    def _tool_create_gmail_draft(self, input_: dict) -> _ToolOutcome:
        to = input_.get("to")
        subject = str(input_.get("subject", "")).strip()
        body = str(input_.get("body", "")).strip()
        if not to or not subject or not body:
            return _ToolOutcome(content="Missing required fields: to, subject, body", is_error=True)
        if isinstance(to, str):
            to = [to]

        thread_id = input_.get("thread_id") or None
        in_reply_to = input_.get("in_reply_to_message_id") or None

        result = google_gmail.create_draft(
            to=to,
            subject=subject,
            body=body,
            thread_id=thread_id,
            in_reply_to_message_id=in_reply_to,
        )

        content = (
            f"✉️ 草稿已存 Gmail Drafts\n"
            f"draft_id: {result['draft_id']}\n"
            f"Gmail 連結：{result['gmail_web_link']}\n\n"
            f"---\n"
            f"To: {', '.join(to)}\n"
            f"Subject: {subject}\n\n"
            f"{body}\n"
            f"---\n\n"
            f"確認 OK 後說「發」，Nami 會呼叫 send_gmail_draft 發出。\n"
            f"要修改就告訴我哪裡要改。"
        )
        return _ToolOutcome(
            content=content,
            event={
                "name": "gmail_draft_created",
                "payload": {
                    "draft_id": result["draft_id"],
                    "to": to,
                    "subject": subject,
                    "gmail_web_link": result["gmail_web_link"],
                },
                "log": subject,
            },
        )

    def _tool_update_gmail_draft(self, input_: dict) -> _ToolOutcome:
        draft_id = str(input_.get("draft_id", "")).strip()
        if not draft_id:
            return _ToolOutcome(content="Missing draft_id", is_error=True)

        to = input_.get("to") or None
        if isinstance(to, str):
            to = [to]
        subject = input_.get("subject") or None
        body = input_.get("body") or None

        result = google_gmail.update_draft(draft_id, to=to, subject=subject, body=body)

        content = (
            f"✏️ 草稿已更新\n"
            f"draft_id: {result['draft_id']}\n"
            f"Gmail 連結：{result['gmail_web_link']}\n\n"
            f"---\n"
            f"To: {', '.join(result['to'])}\n"
            f"Subject: {result['subject']}\n\n"
            f"{result['body']}\n"
            f"---\n\n"
            f"確認 OK 後說「發」。"
        )
        return _ToolOutcome(
            content=content,
            event={
                "name": "gmail_draft_updated",
                "payload": {
                    "draft_id": draft_id,
                    "subject": result["subject"],
                    "gmail_web_link": result["gmail_web_link"],
                },
                "log": result["subject"],
            },
        )

    def _tool_send_gmail_draft(self, input_: dict) -> _ToolOutcome:
        draft_id = str(input_.get("draft_id", "")).strip()
        if not draft_id:
            return _ToolOutcome(content="Missing draft_id", is_error=True)

        result = google_gmail.send_draft(draft_id)

        content = (
            f"📬 信件已發出\n"
            f"message_id: {result['message_id']}\n"
            f"thread_id: {result['thread_id']}"
        )
        return _ToolOutcome(
            content=content,
            event={
                "name": "gmail_sent",
                "payload": {
                    "message_id": result["message_id"],
                    "thread_id": result["thread_id"],
                },
                "log": f"draft {draft_id}",
            },
        )


# ── Utilities ────────────────────────────────────────────────────────


def _build_date_context() -> str:
    """今日日期資訊，注入到 user message（而非 system）以保持 system prompt 可快取。"""
    _weekday_zh = {0: "週一", 1: "週二", 2: "週三", 3: "週四", 4: "週五", 5: "週六", 6: "週日"}
    now = datetime.now(ZoneInfo("Asia/Taipei"))
    today_str = now.strftime("%Y-%m-%d")
    today_zh = _weekday_zh[now.weekday()]
    date_lines = []
    for i in range(14):
        d = now + timedelta(days=i)
        label = "今天" if i == 0 else ("明天" if i == 1 else "")
        suffix = f"（{label}）" if label else ""
        date_lines.append(f"  {d.strftime('%Y-%m-%d')} {_weekday_zh[d.weekday()]}{suffix}")
    date_table = "\n".join(date_lines)
    return (
        f"## 今日資訊\n"
        f"今天是 {today_str}（{today_zh}）。\n\n"
        f"未來 14 天日期對照表（直接查，不要自行推算）：\n{date_table}"
    )


def _slugify(title: str) -> str:
    slug = re.sub(r"[^\w\u4e00-\u9fff\-]", " ", title)
    slug = re.sub(r"\s+", "-", slug.strip())
    return slug[:60] or "untitled"


def _stringify_fm_dates(fm: dict) -> dict:
    """yaml.safe_load 會把 2026-04-23 解析成 date 物件，寫回前先轉字串。"""
    import datetime as _dt

    return {
        k: v.isoformat() if isinstance(v, (_dt.date, _dt.datetime)) else v for k, v in fm.items()
    }


def _extract_frontmatter(content: str) -> dict:
    if not content.startswith("---"):
        return {}
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        import yaml

        return yaml.safe_load(parts[1]) or {}
    except Exception:
        return {}


def _to_vault_relative(path: Path) -> str:
    """把 absolute Path 轉成 vault-relative 字串（供訊息顯示）。"""
    parts = path.parts
    for marker in ("Projects", "TaskNotes"):
        if marker in parts:
            idx = parts.index(marker)
            return "/".join(parts[idx:])
    return path.name


def _fmt_event_time(start: str, end: str) -> str:
    """格式化事件時間給使用者看（Asia/Taipei，精簡）。"""
    try:
        s = datetime.fromisoformat(start.replace("Z", "+00:00"))
        e = datetime.fromisoformat(end.replace("Z", "+00:00"))
        if s.tzinfo is not None:
            tz = ZoneInfo("Asia/Taipei")
            s = s.astimezone(tz)
            e = e.astimezone(tz)
        if s.date() == e.date():
            return f"{s.strftime('%m/%d %H:%M')}-{e.strftime('%H:%M')}"
        return f"{s.strftime('%m/%d %H:%M')} 至 {e.strftime('%m/%d %H:%M')}"
    except Exception:
        # 全日事件是 YYYY-MM-DD 格式，無時間
        return f"{start} 至 {end}"


def _parse_iso_local(s: str, tz: ZoneInfo) -> datetime:
    """ISO 字串轉 datetime，無時區時假設為 ``tz``。"""
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt


def _strip_tz(iso_str: str) -> str:
    """剝掉 ISO 字串的時區，對齊 Obsidian task scheduled 格式。

    ``2026-04-25T15:00:00+08:00`` → ``2026-04-25T15:00:00``
    ``2026-04-25T15:00:00Z``      → ``2026-04-25T15:00:00``
    無 tz 原樣回。全日事件 (``2026-04-25``) 原樣回。
    """
    if not iso_str:
        return iso_str
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except ValueError:
        return iso_str
    if dt.tzinfo is None:
        return iso_str
    local = dt.astimezone(ZoneInfo("Asia/Taipei"))
    return local.strftime("%Y-%m-%dT%H:%M:%S")


# ── Deprecated alias（for backward-compat with old tests/imports） ─────

PROJECT_BOOTSTRAP_FLOW = NAMI_AGENT_FLOW  # 舊 flow name；保留以免 break import
