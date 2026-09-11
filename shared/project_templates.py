"""Project templates — 建立專案時一次開好該類型的所有任務。

修修 2026-09-10：「podcast 大約分成訪綱撰寫 / 節目錄製 / 後製上架；YouTube 影片
包括前期研究 / 拍攝 / 後製 / 上架。建立 project 的時候可以選是哪一種。」

樣板住在 ``config/project-templates.yaml``（修修可直接編輯，不需重新部署）。
每個 stage 產生一個任務，走既有的雙寫慣例（檔名前綴 + ``projects:``），另外在
任務 frontmatter 記 ``stage:``。

``stage:`` 現在只有一個用途：**決定任務在列表裡的順序**（:func:`stage_rank`）。
專案頁一度用它畫進度軌並把非樣板任務分到「其他任務」，兩者都已移除——修修
2026-09-11：「這一列我沒有跟你講說我要做」「已經重複了…我只需要乾淨的任務列表」。
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
        seen: set[str] = set()
        dupes = sorted({s.name for s in template.stages if s.name in seen or seen.add(s.name)})
        if dupes:
            # Two stages sharing a name map to ONE filename, so the second write
            # would fail after the stub and earlier tasks are already on disk —
            # exactly the half-built state this pre-flight exists to prevent.
            raise TemplateError(
                f"樣板「{kind}」有重複的階段名稱：{'、'.join(dupes)}。"
                f"請在 config/project-templates.yaml 改成不同名稱。"
            )
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


def stage_rank(entry: "ProjectEntry") -> dict[str, int]:
    """``{stage name: position}`` for ``entry``'s template — the order tasks are
    meant to be worked in.

    The list used to render a header per stage; 修修 2026-09-11 removed that
    (「已經重複了…我只需要乾淨的任務列表」) because the header just repeated the
    task's own name. The ORDER still matters though: without it a fresh podcast
    project lists 上架 before 前期研究, because the vault scan is alphabetical.
    Tasks with no (or an unknown) stage rank after every known one.
    """
    template = find_template(entry.kind)
    if template is None:
        return {}
    return {stage.name: i for i, stage in enumerate(template.stages)}
