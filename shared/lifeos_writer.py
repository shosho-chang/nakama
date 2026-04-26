"""LifeOS Project + Task renderer and writer.

寫入 Obsidian LifeOS vault 的 Projects/ 與 TaskNotes/Tasks/，格式對齊
`Projects/肌酸的妙用.md` 與 `TaskNotes/Tasks/肌酸的妙用 - Pre-production.md` gold standard。

不使用 `shared.obsidian_writer.write_page()`——它會自動注入 `updated` 欄位，
與 LifeOS 實際 frontmatter schema 不相容（Project 沒有 updated，Task 用 dateModified）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

import yaml

from shared.config import get_vault_path

ContentType = Literal["youtube", "blog", "research", "podcast"]
CONTENT_TYPES: tuple[ContentType, ...] = ("youtube", "blog", "research", "podcast")

PROJECTS_DIR = "Projects"
TASKS_DIR = "TaskNotes/Tasks"
TEMPLATES_DIR = Path(__file__).resolve().parent / "lifeos_templates"

DEFAULT_TASKS: dict[str, tuple[str, str, str]] = {
    "youtube": ("Pre-production", "Filming", "Post-production"),
    "blog": ("Research", "Draft", "Publish"),
    "research": ("Literature Review", "Synthesis", "Write-up"),
    "podcast": ("Prep & Booking", "Recording", "Edit & Publish"),
}


class ProjectExistsError(Exception):
    """Raised when project file already exists — do not overwrite."""


class _BlankNoneDumper(yaml.SafeDumper):
    """YAML dumper that renders None as blank (matches LifeOS hand-edited files)."""


def _none_as_blank(dumper: yaml.SafeDumper, _value: None) -> yaml.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:null", "")


_BlankNoneDumper.add_representer(type(None), _none_as_blank)


@dataclass
class CreateResult:
    project_path: Path
    task_paths: list[Path] = field(default_factory=list)


def render_project(
    title: str,
    content_type: ContentType,
    *,
    area: str = "work",
    priority: str = "medium",
    status: str = "active",
    search_topic: str | None = None,
    extra_fm: dict | None = None,
) -> tuple[dict, str]:
    """Return (frontmatter_dict, body_md) for a LifeOS Project file."""
    if content_type not in CONTENT_TYPES:
        raise ValueError(f"Unknown content_type: {content_type!r}")

    tags: list[str] = ["project", content_type]

    fm: dict = {
        "type": "project",
        "content_type": content_type,
        "created": date.today(),
        "status": status,
        "priority": priority,
        "area": area,
        "quarter": None,
        "parent_kr": None,
    }

    if content_type == "youtube":
        fm["search_topic"] = search_topic or title
        fm["publish_date"] = None
    elif content_type == "blog":
        fm["search_topic"] = search_topic or title
        fm["publish_date"] = None
    else:
        fm["target_date"] = None

    fm["tags"] = tags

    if extra_fm:
        fm.update(extra_fm)

    body = _load_body(content_type, title)
    return fm, body


def render_task(
    project_name: str,
    task_name: str,
    *,
    estimated_pomodoros: int = 4,
    priority: str = "normal",
) -> tuple[dict, str]:
    """Return (frontmatter_dict, body_md) for a LifeOS Task file."""
    now_iso = _iso_z(datetime.now(timezone.utc))
    title = f"{project_name} - {task_name}"

    fm: dict = {
        "title": title,
        "status": "to-do",
        "priority": priority,
        "projects": [f"[[{project_name}]]"],
        "tags": ["task"],
        "dateCreated": now_iso,
        "dateModified": now_iso,
        "預估🍅": estimated_pomodoros,
        "✅": False,
    }
    return fm, ""


def create_project_with_tasks(
    title: str,
    content_type: ContentType,
    task_names: list[str],
    *,
    vault: Path | None = None,
    extra_fields: dict | None = None,
    estimated_pomodoros: int = 4,
    **project_kwargs,
) -> CreateResult:
    """Create one Project file + N Task files. Raises ProjectExistsError on conflict."""
    if vault is None:
        vault = get_vault_path()

    project_rel = f"{PROJECTS_DIR}/{title}.md"
    project_abs = vault / project_rel
    if project_abs.exists():
        raise ProjectExistsError(f"Project already exists: {project_rel}")

    task_paths: list[Path] = []
    for tname in task_names:
        task_abs = vault / TASKS_DIR / f"{title} - {tname}.md"
        if task_abs.exists():
            raise ProjectExistsError(f"Task already exists: {task_abs.name}")
        task_paths.append(task_abs)

    fm, body = render_project(
        title,
        content_type,
        extra_fm=extra_fields,
        **project_kwargs,
    )
    _write_markdown(project_abs, fm, body)

    written_tasks: list[Path] = []
    for task_abs, tname in zip(task_paths, task_names):
        t_fm, t_body = render_task(
            title,
            tname,
            estimated_pomodoros=estimated_pomodoros,
        )
        _write_markdown(task_abs, t_fm, t_body)
        written_tasks.append(task_abs)

    return CreateResult(project_path=project_abs, task_paths=written_tasks)


def default_task_names(content_type: ContentType) -> list[str]:
    """Return the hardcoded 3-task default for a content_type."""
    if content_type not in DEFAULT_TASKS:
        raise ValueError(f"Unknown content_type: {content_type!r}")
    return list(DEFAULT_TASKS[content_type])


def _load_body(content_type: ContentType, title: str) -> str:
    tpl_path = TEMPLATES_DIR / f"project_{content_type}.md.tpl"
    raw = tpl_path.read_text(encoding="utf-8")
    return raw.replace("__TITLE__", title)


def _iso_z(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _write_markdown(target: Path, frontmatter: dict, body: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fm_str = yaml.dump(
        frontmatter,
        Dumper=_BlankNoneDumper,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        indent=2,
        width=10**9,
    ).rstrip()
    if body:
        content = f"---\n{fm_str}\n---\n\n{body.lstrip()}"
        if not content.endswith("\n"):
            content += "\n"
    else:
        content = f"---\n{fm_str}\n---\n"
    target.write_text(content, encoding="utf-8")
