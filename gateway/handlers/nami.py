"""Nami handler — 任務管理（建立、列表）+ LifeOS Project Bootstrap。"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from gateway.handlers.base import BaseHandler, Continuation, HandlerResponse
from shared.anthropic_client import ask_claude, set_current_agent
from shared.events import emit
from shared.lifeos_writer import CONTENT_TYPES, default_task_names
from shared.log import get_logger, kb_log
from shared.obsidian_writer import list_files, read_page, write_page
from shared.prompt_loader import load_prompt

logger = get_logger("nakama.gateway.nami")

TASK_DIR = "TaskNotes/Tasks"

PROJECT_BOOTSTRAP_FLOW = "project_bootstrap"
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_PROJECT_BOOTSTRAP_SCRIPT = _REPO_ROOT / "scripts" / "run_project_bootstrap.py"


class NamiHandler(BaseHandler):
    """Nami：任務 CRUD + Project Bootstrap。"""

    agent_name = "nami"
    supported_intents = ["create_task", "list_tasks", "create_project", "general"]

    def handle(self, intent: str, text: str, user_id: str) -> HandlerResponse:
        set_current_agent("nami")

        if intent == "create_task":
            return self._create_task(text)
        if intent == "list_tasks":
            return self._list_tasks()
        if intent == "create_project":
            return self._start_project_bootstrap(text)
        # general — 嘗試從文字推斷
        return self._dispatch_general(text)

    def continue_flow(
        self,
        flow_name: str,
        state: dict,
        text: str,
        user_id: str,
    ) -> HandlerResponse:
        set_current_agent("nami")
        if flow_name == PROJECT_BOOTSTRAP_FLOW:
            return self._continue_project_bootstrap(state, text)
        return super().continue_flow(flow_name, state, text, user_id)

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

    # ── Project Bootstrap flow ──────────────────────────────────────────

    def _start_project_bootstrap(self, text: str) -> HandlerResponse:
        """收到 create_project intent 時的入口。解析 → 若缺 content_type 問，否則直接秀 plan。"""
        parsed = self._parse_project_text(text)
        title = (parsed.get("title") or text).strip()
        if not title:
            return HandlerResponse(
                text="請告訴我專案的主題（例如：「幫我開個超加工食品的 project」）。"
            )

        content_type = parsed.get("content_type")
        area = parsed.get("area") or "work"
        priority = parsed.get("priority") or "medium"
        search_topic = parsed.get("search_topic")

        if content_type not in CONTENT_TYPES:
            state = {
                "step": "awaiting_content_type",
                "title": title,
                "area": area,
                "priority": priority,
                "search_topic": search_topic,
            }
            return HandlerResponse(
                text=(
                    f"好的，要建立「{title}」的 project。\n\n"
                    "請選 content type：\n"
                    "  • youtube（拍影片）\n"
                    "  • blog（寫文章 / SEO）\n"
                    "  • research（深度研究）\n"
                    "  • podcast（錄音 / 訪談）"
                ),
                continuation=Continuation(flow_name=PROJECT_BOOTSTRAP_FLOW, state=state),
            )

        return self._prompt_plan_confirm(
            title=title,
            content_type=content_type,
            area=area,
            priority=priority,
            search_topic=search_topic,
        )

    def _continue_project_bootstrap(self, state: dict, text: str) -> HandlerResponse:
        """Thread 內接續 project_bootstrap flow。"""
        step = state.get("step")
        if step == "awaiting_content_type":
            return self._handle_content_type_reply(state, text)
        if step == "awaiting_confirm":
            return self._handle_confirm_reply(state, text)
        # 不明狀態，保險起見結束
        return HandlerResponse(text="流程狀態異常，已中止。請重新開始。")

    def _handle_content_type_reply(self, state: dict, text: str) -> HandlerResponse:
        ct = self._detect_content_type(text)
        if ct is None:
            return HandlerResponse(
                text="沒看懂，請回覆 youtube / blog / research / podcast 其一。",
                continuation=Continuation(flow_name=PROJECT_BOOTSTRAP_FLOW, state=state),
            )
        return self._prompt_plan_confirm(
            title=state["title"],
            content_type=ct,
            area=state.get("area", "work"),
            priority=state.get("priority", "medium"),
            search_topic=state.get("search_topic"),
        )

    def _handle_confirm_reply(self, state: dict, text: str) -> HandlerResponse:
        lower = text.strip().lower()
        confirm_words = {"ok", "yes", "go", "confirm", "確認", "好", "對", "建立", "建立吧", "跑"}
        cancel_words = {"no", "cancel", "取消", "算了", "不用"}
        if any(w in lower for w in cancel_words):
            return HandlerResponse(text="已取消，沒有建立任何檔案。")
        if not any(w == lower or w in lower for w in confirm_words):
            return HandlerResponse(
                text="請回覆「確認 / ok / go」啟動，或「取消」結束。",
                continuation=Continuation(flow_name=PROJECT_BOOTSTRAP_FLOW, state=state),
            )
        return self._execute_project_bootstrap(state)

    def _prompt_plan_confirm(
        self,
        *,
        title: str,
        content_type: str,
        area: str,
        priority: str,
        search_topic: str | None,
    ) -> HandlerResponse:
        tasks = default_task_names(content_type)
        lines = [f"  {i + 1}. {title} - {t}" for i, t in enumerate(tasks)]
        search_line = f"\n  搜尋關鍵字：{search_topic}" if search_topic else ""
        text = (
            "要建立的 Project：\n"
            f"  標題：{title}\n"
            f"  類型：{content_type}\n"
            f"  領域：{area}\n"
            f"  優先級：{priority}"
            f"{search_line}\n\n"
            f"預設 3 個 Task：\n"
            + "\n".join(lines)
            + "\n\n確認建立？（回「確認」執行，「取消」中止）"
        )
        state = {
            "step": "awaiting_confirm",
            "title": title,
            "content_type": content_type,
            "area": area,
            "priority": priority,
            "search_topic": search_topic,
            "tasks": tasks,
        }
        return HandlerResponse(
            text=text,
            continuation=Continuation(flow_name=PROJECT_BOOTSTRAP_FLOW, state=state),
        )

    def _execute_project_bootstrap(self, state: dict) -> HandlerResponse:
        cmd = [
            sys.executable,
            str(_PROJECT_BOOTSTRAP_SCRIPT),
            "--title",
            state["title"],
            "--content-type",
            state["content_type"],
            "--tasks",
            *state["tasks"],
            "--area",
            state["area"],
            "--priority",
            state["priority"],
        ]
        if state.get("search_topic"):
            cmd += ["--search-topic", state["search_topic"]]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            return HandlerResponse(text="⚠️ 建立 project 超時，請稍後再試。")

        if proc.returncode == 2:
            return HandlerResponse(
                text="⚠️ 已有同名 project 或 task。要改標題（例如加日期後綴）再試一次嗎？"
            )
        if proc.returncode != 0:
            logger.error(f"project bootstrap failed: {proc.stderr}")
            return HandlerResponse(text=f"❌ 建立失敗：{proc.stderr[:300] or '未知錯誤'}")

        try:
            payload = json.loads(proc.stdout.strip())
        except json.JSONDecodeError:
            return HandlerResponse(text=f"⚠️ 建立完成但輸出解析失敗：\n{proc.stdout[:300]}")

        project_path = payload.get("project_path", "?")
        task_paths = payload.get("task_paths", [])
        uri = payload.get("obsidian_uri", "")
        content_type = payload.get("content_type", state["content_type"])

        emit(
            "nami",
            "project_created",
            {
                "title": state["title"],
                "content_type": content_type,
                "project_path": project_path,
                "task_paths": task_paths,
            },
        )
        kb_log("nami", "project_created", state["title"])

        next_step = self._next_step_hint(content_type)
        return HandlerResponse(
            text=(
                "✅ Project 建好了\n"
                f"  📄 {project_path}\n"
                f"  ✅ {len(task_paths)} 個 Tasks 已建立\n\n"
                f"直接開啟：{uri}\n\n"
                f"下一步建議：\n{next_step}"
            )
        )

    def _next_step_hint(self, content_type: str) -> str:
        if content_type in {"youtube", "blog"}:
            field = "👄 One Sentence" if content_type == "youtube" else "專案描述"
            label = "🗝️ 關鍵字研究" if content_type == "youtube" else "🗝️ Keyword Research & SEO"
            return (
                f"  → 打開 Project 檔填「{field}」讓 KB Research 有查詢依據\n"
                f"  → 按「{label}」讓 Zoro 抓搜尋潛力 + 標題建議"
            )
        return (
            "  → 打開 Project 檔填「專案描述 / 預期成果」\n"
            "  → 按「📚 KB Research」按鈕讓 Robin 從 KB 抓相關已知素材"
        )

    def _parse_project_text(self, text: str) -> dict:
        try:
            prompt = load_prompt("nami", "parse_project", user_message=text)
        except FileNotFoundError:
            logger.warning("parse_project prompt missing; falling back to minimal parse")
            return {"title": text.strip()}
        raw = ask_claude(
            prompt,
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            temperature=0.0,
        )
        try:
            data = json.loads(raw.strip())
        except json.JSONDecodeError:
            logger.warning(f"parse_project JSON decode failed: {raw[:200]}")
            return {"title": text.strip()}
        return {k: v for k, v in data.items() if v is not None}

    def _detect_content_type(self, text: str) -> str | None:
        lower = text.strip().lower()
        if not lower:
            return None
        for ct in CONTENT_TYPES:
            if ct in lower:
                return ct
        zh_map = {
            "影片": "youtube",
            "youtube": "youtube",
            "部落格": "blog",
            "文章": "blog",
            "研究": "research",
            "深度": "research",
            "錄音": "podcast",
            "訪談": "podcast",
        }
        for zh, ct in zh_map.items():
            if zh in lower:
                return ct
        return None


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
