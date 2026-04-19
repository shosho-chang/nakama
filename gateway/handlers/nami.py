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
from shared.anthropic_client import call_claude_with_tools, set_current_agent
from shared.events import emit
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
        messages: list[dict] = [{"role": "user", "content": f"{date_context}\n\n{text}"}]
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
            return _ToolOutcome(content=f"Unknown tool: {name}", is_error=True)
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


# ── Deprecated alias（for backward-compat with old tests/imports） ─────

PROJECT_BOOTSTRAP_FLOW = NAMI_AGENT_FLOW  # 舊 flow name；保留以免 break import
