"""Project templates — 建立專案時一次開好該類型的所有任務，並推導進度軌。

修修 2026-09-10：「podcast 大約分成訪綱撰寫 / 節目錄製 / 後製上架；YouTube 影片
包括前期研究 / 拍攝 / 後製 / 上架。建立 project 的時候可以選是哪一種。」

樣板住在 ``config/project-templates.yaml``（修修可直接編輯，不需重新部署）。
每個 stage 產生一個任務，走既有的雙寫慣例（檔名前綴 + ``projects:``），另外在
任務 frontmatter 記 ``stage:`` 供 dashboard 分組。

**進度軌的狀態完全由任務完成度推導**——沒有任何可以手動勾的階段。ADR-031 的
七道工序就是死在「手動勾、跟現實脫節」，這裡不重蹈：一個階段是不是完成，只看
它底下的任務有沒有做完。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import yaml

from shared.log import get_logger

if TYPE_CHECKING:  # type-only; avoids an import cycle at runtime
    from shared.project_index import ProjectEntry
    from shared.weekly_indexer import WeeklyTask

logger = get_logger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_PATH = _ROOT / "config" / "project-templates.yaml"


class TemplateError(RuntimeError):
    """Raised when a template cannot be applied (message is user-facing)."""


@dataclass(frozen=True)
class Stage:
    name: str
    pomodoros: int = 4


@dataclass(frozen=True)
class ProjectTemplate:
    key: str
    label: str
    stages: tuple[Stage, ...]


@dataclass(frozen=True)
class StageState:
    """One rail segment — every number derived from the member tasks."""

    name: str
    order: int  # 1-based, for the ① ② ③ labels
    total: int
    done: int
    est: int
    actual: int
    is_now: bool

    @property
    def is_done(self) -> bool:
        """Complete only when it actually has tasks and they are all done —
        an empty stage is 'nothing here yet', not 'finished'."""
        return self.total > 0 and self.done == self.total


def _templates_path() -> Path:
    override = os.environ.get("NAKAMA_PROJECT_TEMPLATES")
    return Path(override) if override else _DEFAULT_PATH


def load_templates() -> dict[str, ProjectTemplate]:
    """Parse the YAML. A malformed or missing file degrades to "no templates"
    (the 建立 form falls back to 空專案) rather than breaking the page."""
    path = _templates_path()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("project templates unreadable at %s: %s", path, exc)
        return {}
    entries = raw.get("templates")
    if not isinstance(entries, dict):
        return {}

    out: dict[str, ProjectTemplate] = {}
    for key, body in entries.items():
        if not isinstance(body, dict):
            continue
        stages: list[Stage] = []
        for s in body.get("stages") or []:
            if isinstance(s, dict) and str(s.get("name") or "").strip():
                try:
                    pom = int(s.get("pomodoros") or 4)
                except (TypeError, ValueError):
                    pom = 4
                stages.append(Stage(name=str(s["name"]).strip(), pomodoros=max(1, min(20, pom))))
            elif isinstance(s, str) and s.strip():
                stages.append(Stage(name=s.strip()))
        if not stages:
            continue
        out[str(key)] = ProjectTemplate(
            key=str(key),
            label=str(body.get("label") or key),
            stages=tuple(stages),
        )
    return out


def find_template(kind: str) -> Optional[ProjectTemplate]:
    return load_templates().get(kind) if kind else None


def kind_label(kind: str) -> str:
    """Display label for a project's ``kind`` — the raw key when the template
    has since been removed from the YAML (the project itself stays valid)."""
    tpl = find_template(kind)
    return tpl.label if tpl else kind


def create_project_with_template(vault_root: Path, raw_name: str, kind: str = ""):
    """Create the project stub and, when ``kind`` names a template, its tasks.

    Task basenames are **all checked before anything is written** — a collision
    on stage 3 must not leave a half-built project behind. Returns
    ``(entry, [task_path, …])``.
    """
    from shared.project_index import create_project, normalize_name
    from shared.project_writer import TASKS_DIR, create_task

    kind = (kind or "").strip()
    template = None
    if kind:
        template = find_template(kind)
        if template is None:
            raise TemplateError(f"找不到專案類型「{kind}」，請確認 config/project-templates.yaml。")

    name = normalize_name(raw_name)  # ProjectError propagates with its own code
    if template is not None:
        clashes = [
            s.name
            for s in template.stages
            if (Path(vault_root) / TASKS_DIR / f"{name} - {s.name}.md").exists()
        ]
        if clashes:
            raise TemplateError(
                f"這些任務檔已存在，請先處理再建立：{'、'.join(f'{name} - {c}' for c in clashes)}"
            )

    entry = create_project(vault_root, name, kind=kind)
    paths: list[Path] = []
    if template is not None:
        for stage in template.stages:
            paths.append(
                create_task(
                    vault_root=vault_root,
                    project_slug=name,
                    task_name=stage.name,
                    estimated_pomodoros=stage.pomodoros,
                    stage=stage.name,
                )
            )
    return entry, paths


def stage_states(entry: "ProjectEntry", tasks: list["WeeklyTask"], actual: dict[str, int]):
    """Rail segments for ``entry`` — ``[]`` when it has no (or an unknown) kind.

    ``is_now`` marks the first stage that still has an open task; everything is
    read off the tasks, so the rail cannot drift from reality.
    """
    template = find_template(entry.kind)
    if template is None:
        return []

    by_stage: dict[str, list[WeeklyTask]] = {}
    for t in tasks:
        if t.stage:
            by_stage.setdefault(t.stage, []).append(t)

    now_marked = False
    out: list[StageState] = []
    for i, stage in enumerate(template.stages, start=1):
        members = by_stage.get(stage.name, [])
        done = sum(1 for t in members if t.done)
        has_open = done < len(members)
        is_now = has_open and not now_marked
        if is_now:
            now_marked = True
        out.append(
            StageState(
                name=stage.name,
                order=i,
                total=len(members),
                done=done,
                est=sum(t.est_pomodoros for t in members) or stage.pomodoros * len(members),
                actual=sum(actual.get(t.slug, 0) for t in members),
                is_now=is_now,
            )
        )
    return out


def unstaged(entry: "ProjectEntry", tasks: list["WeeklyTask"]) -> list["WeeklyTask"]:
    """Member tasks that no rail segment claims — either the project has no
    template, or the task carries a ``stage:`` the template no longer defines."""
    template = find_template(entry.kind)
    if template is None:
        return list(tasks)
    known = {s.name for s in template.stages}
    return [t for t in tasks if t.stage not in known]
