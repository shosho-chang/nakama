"""Nami handler — 任務管理（建立、列表）via Obsidian vault。"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from gateway.handlers.base import BaseHandler, HandlerResponse
from shared.anthropic_client import ask_claude, set_current_agent
from shared.events import emit
from shared.log import get_logger, kb_log
from shared.obsidian_writer import list_files, read_page, write_page
from shared.prompt_loader import load_prompt

logger = get_logger("nakama.gateway.nami")

TASK_DIR = "TaskNotes/Tasks"


class NamiHandler(BaseHandler):
    """Nami：任務 CRUD。"""

    agent_name = "nami"
    supported_intents = ["create_task", "list_tasks", "general"]

    def handle(self, intent: str, text: str, user_id: str) -> HandlerResponse:
        set_current_agent("nami")

        if intent == "create_task":
            return self._create_task(text)
        if intent == "list_tasks":
            return self._list_tasks()
        # general — 嘗試從文字推斷
        return self._dispatch_general(text)

    def _dispatch_general(self, text: str) -> HandlerResponse:
        """General intent — 依文字內容決定是建任務還是列任務。"""
        lower = text.lower()
        if any(kw in lower for kw in ["清單", "列出", "list", "有什麼"]):
            return self._list_tasks()
        # 預設當作建立任務
        return self._create_task(text)

    def _create_task(self, text: str) -> HandlerResponse:
        """用 Claude Haiku 解析自然語言，建立 TaskNote。"""
        try:
            prompt = load_prompt("nami", "parse_task", user_message=text)
        except FileNotFoundError:
            # Prompt 檔不存在時用 inline fallback
            prompt = (
                "從以下使用者訊息中提取任務資訊，回傳 JSON：\n"
                '{"title": "任務標題", "scheduled": "YYYY-MM-DD 或 null",'
                ' "priority": "normal 或 high 或 low",'
                ' "notes": "額外備註或空字串"}\n\n'
                f"使用者訊息：{text}\n\n"
                "只回覆 JSON，不要其他文字。"
            )

        raw = ask_claude(
            prompt,
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            temperature=0.0,
        )

        try:
            task = json.loads(raw.strip())
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse task JSON: {raw}")
            return HandlerResponse(text="抱歉，我無法理解這個任務。請再說清楚一點？")

        title = task.get("title", text[:30])
        slug = _slugify(title)
        path = f"{TASK_DIR}/{slug}.md"

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        frontmatter = {
            "title": title,
            "status": "to-do",
            "priority": task.get("priority", "normal"),
            "tags": ["task"],
            "scheduled": task.get("scheduled"),
            "dateCreated": now_iso,
            "dateModified": now_iso,
        }
        # 移除 None 值
        frontmatter = {k: v for k, v in frontmatter.items() if v is not None}

        write_page(path, frontmatter, task.get("notes", ""))

        emit(
            "nami",
            "task_created",
            {"title": title, "path": path, "scheduled": task.get("scheduled")},
        )
        kb_log("nami", "task_created", title)

        scheduled_info = ""
        if task.get("scheduled"):
            scheduled_info = f"（排程：{task['scheduled']}）"

        return HandlerResponse(text=f"已建立任務：{title}{scheduled_info}")

    def _list_tasks(self) -> HandlerResponse:
        """列出所有 status=to-do 的任務。"""
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
            return HandlerResponse(text="目前沒有待辦任務。")

        lines = []
        for t in tasks[:20]:
            icon = {"high": ":red_circle:", "normal": ":white_circle:"}.get(
                t["priority"], ":white_circle:"
            )
            line = f"- {icon} {t['title']}"
            if t["scheduled"]:
                line += f" ({t['scheduled']})"
            if t["status"] == "in-progress":
                line += " [進行中]"
            lines.append(line)

        return HandlerResponse(text=f"*待辦任務（{len(tasks)} 項）*\n" + "\n".join(lines))


def _slugify(title: str) -> str:
    """將標題轉為安全的檔名。"""
    # 移除不安全字元，保留中文、英文、數字、連字號
    slug = re.sub(r"[^\w\u4e00-\u9fff\-]", " ", title)
    slug = re.sub(r"\s+", "-", slug.strip())
    return slug[:60] or "untitled"


def _extract_frontmatter(content: str) -> dict:
    """從 Markdown 內容提取 YAML frontmatter。"""
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
