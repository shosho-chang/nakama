"""LifeOS Task renderer.

寫入 Obsidian LifeOS vault ``TaskNotes/Tasks/`` 用的 frontmatter renderer，格式對齊
``TaskNotes/Tasks/肌酸的妙用 - Pre-production.md`` gold standard。

不使用 ``shared.obsidian_writer.write_page()``——它會自動注入 ``updated`` 欄位，
與 LifeOS 實際 frontmatter schema 不相容（Task 用 dateModified）。

ADR-068：Project 側的 ``render_project`` / ``create_project_with_tasks`` /
content-type 模板已隨 ADR-031 workspace 退役——project 檔現在是極簡 stub，
由 :mod:`shared.project_index` 建立。
"""

from __future__ import annotations

from datetime import datetime, timezone

TASKS_DIR = "TaskNotes/Tasks"


def _iso_z(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def render_task(
    project_name: str | None,
    task_name: str,
    *,
    estimated_pomodoros: int = 4,
    priority: str = "normal",
    category: str = "work",
) -> tuple[dict, str]:
    """Return (frontmatter_dict, body_md) for a LifeOS Task file.

    ``project_name=None`` (or blank) ⇒ a standalone task: the title is the bare
    ``task_name`` and the ``projects`` key is omitted entirely (not ``[]``). With
    a project, the title is prefixed ``{project} - {task}`` and ``projects`` holds
    the wikilink — the dual-write convention Weekly + Nami both read.
    ``category`` defaults to ``work`` because only work-category tasks track 🍅
    (see ``weekly_indexer`` CATEGORY model)."""
    now_iso = _iso_z(datetime.now(timezone.utc))
    title = f"{project_name} - {task_name}" if project_name else task_name

    fm: dict = {
        "title": title,
        "status": "to-do",
        "priority": priority,
        "category": category,
    }
    if project_name:
        fm["projects"] = [f"[[{project_name}]]"]
    fm.update(
        {
            "tags": ["task"],
            "dateCreated": now_iso,
            "dateModified": now_iso,
            "預估🍅": estimated_pomodoros,
            "✅": False,
        }
    )
    return fm, ""
