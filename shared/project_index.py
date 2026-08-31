"""Project index — ADR-068 thin read/write layer over vault ``Projects/``.

A Project is a **long-running thread** (長期戰線: 自由艦隊社群、電子報、課程…) —
the grouping key that binds cross-week tasks together. It is NOT a per-deliverable
workspace; that ADR-031 semantic (7-stage YouTube pipeline) was retired by ADR-068.

Vault schema (``Projects/{name}.md``)::

    ---
    type: project
    status: active        # active | archived
    created: 2026-08-31T…Z
    ---
    自由書寫的戰線目標／備註（Obsidian 端編輯）。

No stats snapshots are ever stored — every number is computed on read from the
member tasks (the "🍅 3/11 ghost snapshot" failure mode is designed out).

Membership: a task belongs to a project via its ``projects:`` frontmatter
(canonical), with the legacy ``{project} - {task}`` filename prefix as a
read-side fallback for files that predate the dual-write convention.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import yaml

if TYPE_CHECKING:  # avoid import cycle at runtime; only for type hints
    from shared.weekly_indexer import WeeklyTask

PROJECTS_DIR = "Projects"

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)
_FILENAME_UNSAFE = re.compile(r'[\\/:*?"<>|\r\n\t]')


class ProjectError(RuntimeError):
    """Raised on invalid create/status operations (message is user-facing)."""


@dataclass(frozen=True)
class ProjectEntry:
    name: str  # file stem, NFC — display name AND URL slug
    status: str  # "active" | "archived"
    created: str  # ISO string as stored ("" when absent)
    body: str  # free-form markdown notes (may be "")

    @property
    def archived(self) -> bool:
        return self.status == "archived"


def normalize_name(raw: str) -> str:
    """NFC + trimmed project name; raises :class:`ProjectError` when unusable."""
    name = unicodedata.normalize("NFC", str(raw or "")).strip()
    name = re.sub(r"\s+", " ", name)
    if not name or _FILENAME_UNSAFE.search(name) or name.startswith("."):
        raise ProjectError('戰線名稱不可為空，且不可含 \\ / : * ? " < > | 等字元。')
    if len(name) > 80:
        raise ProjectError("戰線名稱請在 80 字內。")
    return name


def _entry_from_path(path: Path) -> Optional[ProjectEntry]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        return None
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(fm, dict) or fm.get("type") != "project":
        return None  # agent-workspace 等非 project 檔（如 Brook 風格訓練）自然略過
    status = str(fm.get("status") or "active")
    if status not in ("active", "archived"):
        status = "active"
    created = fm.get("created")
    if isinstance(created, datetime):
        created = created.isoformat()
    return ProjectEntry(
        name=unicodedata.normalize("NFC", path.stem),
        status=status,
        created=str(created or ""),
        body=m.group(2).strip(),
    )


def list_projects(vault_root: Path) -> list[ProjectEntry]:
    """All ``type: project`` files under ``Projects/`` (active + archived)."""
    d = Path(vault_root) / PROJECTS_DIR
    if not d.is_dir():
        return []
    out: list[ProjectEntry] = []
    for p in sorted(d.iterdir()):
        if not p.is_file() or p.suffix != ".md" or ".sync-conflict" in p.name:
            continue
        e = _entry_from_path(p)
        if e is not None:
            out.append(e)
    return out


def find_project(vault_root: Path, name: str) -> Optional[ProjectEntry]:
    name = unicodedata.normalize("NFC", str(name or "")).strip()
    if not name or _FILENAME_UNSAFE.search(name):
        return None
    p = Path(vault_root) / PROJECTS_DIR / f"{name}.md"
    if not p.is_file():
        return None
    return _entry_from_path(p)


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8", newline="\n")
    tmp.replace(path)


def _render(fm: dict, body: str) -> str:
    fm_yaml = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False, default_flow_style=False)
    body = body.strip()
    return f"---\n{fm_yaml}---\n\n{body}\n" if body else f"---\n{fm_yaml}---\n"


def create_project(vault_root: Path, raw_name: str) -> ProjectEntry:
    """Create the minimal project stub; :class:`ProjectError` on invalid/duplicate."""
    name = normalize_name(raw_name)
    d = Path(vault_root) / PROJECTS_DIR
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{name}.md"
    if path.exists():
        raise ProjectError(f"戰線「{name}」已存在。")
    created = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    fm = {"type": "project", "status": "active", "created": created}
    _atomic_write(path, _render(fm, ""))
    return ProjectEntry(name=name, status="active", created=created, body="")


def set_project_status(vault_root: Path, name: str, status: str) -> None:
    """Flip ``status`` (archive/restore) in place, preserving body + extra keys."""
    if status not in ("active", "archived"):
        raise ProjectError("status 只能是 active 或 archived。")
    name = unicodedata.normalize("NFC", str(name or "")).strip()
    path = Path(vault_root) / PROJECTS_DIR / f"{name}.md"
    if _FILENAME_UNSAFE.search(name) or not path.is_file():
        raise ProjectError(f"找不到戰線「{name}」。")
    raw = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        raise ProjectError(f"「{name}」缺 frontmatter，請在 Obsidian 檢查。")
    fm = yaml.safe_load(m.group(1)) or {}
    if not isinstance(fm, dict) or fm.get("type") != "project":
        raise ProjectError(f"「{name}」不是 project 檔（type != project）。")
    fm["status"] = status
    _atomic_write(path, _render(fm, m.group(2)))


def tasks_for(name: str, tasks: list["WeeklyTask"]) -> list["WeeklyTask"]:
    """Member tasks of one project — frontmatter canonical, prefix legacy fallback."""
    name = unicodedata.normalize("NFC", str(name or "")).strip()
    prefix = f"{name} - "
    return [t for t in tasks if t.project == name or (not t.project and t.slug.startswith(prefix))]
