"""Project writer — frontmatter + body section + TaskNotes timeentry mutations.

Tier C write-path. Vault is canonical SoT (ADR-030 D1); writes go directly
to ``Projects/{slug}.md`` (frontmatter + body) and to
``TaskNotes/Tasks/{slug} - {task}.md`` (timeEntries only). The TaskNotes
plugin owns task lifecycle; this module only appends to ``timeEntries[]``
so the plugin's ``formula.實際🍅`` continues to compute.

All writes are **atomic** via tmp-file + rename. CJK filenames work on
Win/Mac/Linux because ``Path.rename`` uses POSIX semantics (or the
Windows equivalent) under Python ≥ 3.3.
"""

from __future__ import annotations

import os
import re
import tempfile
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from shared.log import get_logger

logger = get_logger(__name__)

PROJECTS_DIR = "Projects"
TASKS_DIR = "TaskNotes/Tasks"

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)


class ProjectWriteError(RuntimeError):
    pass


class _BlankNoneDumper(yaml.SafeDumper):
    """YAML dumper that renders None as blank, matching LifeOS handwritten files."""


def _none_as_blank(dumper: yaml.SafeDumper, _value: None) -> yaml.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:null", "")


_BlankNoneDumper.add_representer(type(None), _none_as_blank)


def _dump_frontmatter(fm: dict[str, Any]) -> str:
    return yaml.dump(
        fm,
        Dumper=_BlankNoneDumper,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        indent=2,
        width=10**9,
    ).rstrip()


def _atomic_write(path: Path, content: str) -> None:
    """Write content to path atomically via tmp + rename.

    Tmp file sits in the same directory so the rename is intra-FS (atomic).

    Windows retry: ``os.replace`` fails with WinError 5 when another process
    (Obsidian / Dropbox / antivirus) briefly holds a read handle on the
    destination. Three attempts with linear backoff cover the typical
    50-300ms contention window. Final failure raises ``ProjectWriteError``
    with a 繁中 message naming the suspect culprits.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(
        prefix=f".{path.stem}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        for attempt in range(3):
            try:
                os.replace(tmp_path_str, path)
                return
            except PermissionError as e:
                if attempt == 2:
                    raise ProjectWriteError(
                        f"無法寫入 {path.name}：被其他程式鎖定"
                        f"（可能是 Obsidian / Dropbox / 防毒在 sync）。"
                        f"關掉該檔或暫停 sync 再重試。Original: {e}"
                    ) from e
                time.sleep(0.15 * (attempt + 1))
    except Exception:
        try:
            os.unlink(tmp_path_str)
        except OSError:
            pass
        raise


def _read_split(path: Path) -> tuple[dict[str, Any], str]:
    """Read the file and return (frontmatter dict, body str)."""
    if not path.exists():
        raise ProjectWriteError(f"file not found: {path}")
    raw = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        # No frontmatter — treat as empty fm + full body
        return {}, raw
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as exc:
        raise ProjectWriteError(f"invalid frontmatter in {path}: {exc}") from exc
    if not isinstance(fm, dict):
        raise ProjectWriteError(f"frontmatter is not a mapping in {path}")
    return fm, m.group(2)


def _write_split(path: Path, fm: dict[str, Any], body: str) -> None:
    fm_str = _dump_frontmatter(fm)
    if body and not body.startswith("\n"):
        body = "\n" + body
    content = f"---\n{fm_str}\n---{body}"
    if not content.endswith("\n"):
        content += "\n"
    _atomic_write(path, content)


# ── Public API ─────────────────────────────────────────────────────────────


def create_task(
    *,
    vault_root: Path,
    project_slug: str | None,
    task_name: str,
    estimated_pomodoros: int = 4,
    priority: str = "normal",
    category: str = "work",
    scheduled: str | None = None,
    notes: str = "",
) -> Path:
    """Create a new TaskNotes-plugin-compatible task .md.

    With a ``project_slug``: file ``TaskNotes/Tasks/{project_slug} - {task_name}.md``,
    title prefixed, ``projects: [[project_slug]]`` — the dual-write convention the
    Project Brief tab (filename prefix) and Nami (frontmatter) both read.
    ``project_slug=None``/blank ⇒ a STANDALONE task: file ``TaskNotes/Tasks/{task_name}.md``,
    bare title, no ``projects`` key.

    Builds frontmatter via :func:`shared.lifeos_writer.render_task` so the TaskNotes
    plugin's auto-index picks it up identically to bootstrap-time tasks. Raises
    :class:`ProjectWriteError` if a task with the same basename already exists —
    callers decide whether to surface as 409 Conflict or auto-rename.
    """
    from shared.lifeos_writer import render_task

    project_slug = unicodedata.normalize("NFC", project_slug) if project_slug else None
    project_slug = project_slug or None  # treat "" as standalone
    task_name = unicodedata.normalize("NFC", task_name).strip()
    if not task_name:
        raise ProjectWriteError("task_name cannot be empty")

    # TaskNotes plugin builds the dashboard label from the filename, so
    # forbid path separators that would break out of TaskNotes/Tasks/.
    if "/" in task_name or "\\" in task_name:
        raise ProjectWriteError(f"task_name must not contain path separators: {task_name!r}")

    basename = f"{project_slug} - {task_name}" if project_slug else task_name
    task_path = vault_root / TASKS_DIR / f"{basename}.md"
    if task_path.exists():
        raise ProjectWriteError(f"Task already exists: {task_path.name}")

    fm, body = render_task(
        project_slug,
        task_name,
        estimated_pomodoros=estimated_pomodoros,
        priority=priority,
        category=category,
    )
    if scheduled:
        fm["scheduled"] = scheduled
    if notes:
        body = notes

    # Atomic tmp + rename (matches the other writer fns in this module).
    task_path.parent.mkdir(parents=True, exist_ok=True)
    fm_str = yaml.dump(
        fm,
        Dumper=_BlankNoneDumper,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        indent=2,
        width=10**9,
    ).rstrip()
    content = f"---\n{fm_str}\n---\n" if not body else f"---\n{fm_str}\n---\n\n{body.lstrip()}\n"

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        delete=False,
        dir=str(task_path.parent),
        suffix=".tmp",
    ) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, task_path)

    return task_path


def task_project(fm: dict[str, Any]) -> str | None:
    """The project a task belongs to, from its ``projects:`` frontmatter.

    Handles the three shapes seen in the vault: a list of wikilinks, a bare string,
    and path-style / aliased links (``[[Projects/肌酸的妙用|肌酸]]``). Returns None
    for a standalone task."""
    raw = fm.get("projects")
    if isinstance(raw, list) and raw:
        raw = raw[0]
    if not isinstance(raw, str) or not raw.strip():
        return None
    name = raw.strip().lstrip("[").rstrip("]").split("|")[0].strip()
    if "/" in name:
        name = name.rsplit("/", 1)[-1]
    return unicodedata.normalize("NFC", name) or None


def reassign_task_project(
    *, vault_root: Path, task_slug: str, project_slug: str | None
) -> tuple[Path, int]:
    """Move a task to another project — or detach it (``project_slug=None``).

    修修 2026-08-29 稽核：專案歸屬過去只能在「建立任務」當下決定，之後無論在
    任務頁或任務列都沒有任何改派入口（``set_task_meta`` 只碰 category/priority/
    預估🍅），選錯就只能進 Obsidian 手改檔名 + frontmatter。

    歸屬是雙寫的（檔名前綴 ``{專案} - `` + ``projects: [[…]]``），所以改派必須同時
    搬檔；檔名就是 slug，plan 鍵與行事曆 idempotency key 都由它衍生。這裡先改寫
    ``projects``，再交給 :func:`rename_task` 重算前綴、搬檔並同步每個已連動的
    Google 事件標題 —— 一條路徑、一套規則，不另造第二套改名邏輯。

    Returns ``(new_path, calendar_errors)``. Raises :class:`ProjectWriteError` if the
    task is missing or the destination name is taken.
    """
    task_slug = unicodedata.normalize("NFC", task_slug)
    path = vault_root / TASKS_DIR / f"{task_slug}.md"
    fm, body = _read_split(path)  # ProjectWriteError if missing

    current = task_project(fm)
    target = unicodedata.normalize("NFC", project_slug).strip() if project_slug else None
    if target and ("/" in target or "\\" in target):
        raise ProjectWriteError(f"project must not contain path separators: {target!r}")
    if (current or None) == (target or None):
        return path, 0  # already there — no write, no calendar churn

    # The bare task name: strip the CURRENT project prefix off the title so the
    # new prefix isn't stacked on top of the old one.
    title = str(fm.get("title") or task_slug)
    bare = title
    if current and title.startswith(f"{current} - "):
        bare = title[len(current) + 3 :]
    elif current and title.startswith(current):
        bare = title[len(current) :].lstrip(" -—–") or title

    if target:
        fm["projects"] = [f"[[{target}]]"]
    else:
        fm.pop("projects", None)
    _write_split(path, fm, body)

    # rename_task applies the prefix to both the title and the filename, then re-titles
    # the linked calendar events. The target is passed EXPLICITLY (an empty one means
    # detach) so its legacy filename-prefix fallback can't resurrect the old project.
    return rename_task(vault_root=vault_root, old_slug=task_slug, new_title=bare, project=target)


_KEEP_PROJECT = object()  # sentinel: "infer the project", vs an explicit None = detach


def rename_task(
    *,
    vault_root: Path,
    old_slug: str,
    new_title: str,
    project: str | None | object = _KEEP_PROJECT,
) -> tuple[Path, int]:
    """Rename a task — update the frontmatter ``title`` AND the filename (the slug),
    preserving any ``{project} - `` prefix, then re-push each linked Google event's
    summary and re-stamp its ``{slug}@{date}`` idempotency key.

    The filename IS the slug (plan keys + calendar idempotency derive from it), so a
    true rename must move the file and re-title the events — not just edit a display
    field (修修's locked choice). ``new_title`` is the bare display name; the project
    prefix is re-applied. Returns ``(new_path, calendar_errors)`` — the vault rename
    is authoritative and always proceeds even if some events fail to re-title (the
    count lets the caller surface a soft warning). Raises :class:`ProjectWriteError`
    on an empty/invalid title, a missing source, or a destination-name collision.
    """
    from shared import google_calendar

    new_title = unicodedata.normalize("NFC", new_title).strip()
    if not new_title:
        raise ProjectWriteError("new title cannot be empty")
    if "/" in new_title or "\\" in new_title:
        raise ProjectWriteError(f"title must not contain path separators: {new_title!r}")

    old_slug = unicodedata.normalize("NFC", old_slug)
    old_path = vault_root / TASKS_DIR / f"{old_slug}.md"
    fm, body = _read_split(old_path)  # ProjectWriteError if missing

    # Preserve the project prefix: prefer the frontmatter `projects:` link (the
    # dual-write convention create_task writes), else fall back to a legacy filename
    # prefix ("{project} - {title}.md" with an empty projects: — pre-v3-H tasks).
    # An explicit ``project=`` (including None) overrides the inference — that is how
    # ``reassign_task_project`` detaches a task: without it the legacy filename-prefix
    # fallback below would re-derive "t" from "t - 要獨立.md" and put the prefix straight
    # back on (修修 2026-08-29 稽核時實測到).
    if project is _KEEP_PROJECT:
        project = task_project(fm)
        if not project and " - " in old_slug:
            project = old_slug.split(" - ", 1)[0]
    elif project:
        project = unicodedata.normalize("NFC", str(project)).strip() or None

    new_basename = f"{project} - {new_title}" if project else new_title
    new_path = vault_root / TASKS_DIR / f"{new_basename}.md"
    if new_path != old_path and new_path.exists():
        raise ProjectWriteError(f"Task already exists: {new_path.name}")

    new_slug = unicodedata.normalize("NFC", new_path.stem)
    fm["title"] = new_basename
    fm["dateModified"] = _now_iso_z()

    # Re-title each linked Google event (the summary was the prefixed title) and
    # re-stamp its date-scoped idempotency key so {slug}@{date} tracks the new slug.
    # Best-effort: a calendar hiccup must never block the local rename.
    cal_errors = 0
    plan = fm.get("plan")
    if isinstance(plan, list):
        for e in plan:
            if not isinstance(e, dict):
                continue
            event_id = e.get("calendar_event_id")
            day = e.get("date")
            if not event_id or not day:
                continue
            try:
                google_calendar.update_event(
                    str(event_id), title=new_basename, idempotency_key=f"{new_slug}@{day}"
                )
            except Exception as exc:  # noqa: BLE001 — vault rename is authoritative
                cal_errors += 1
                logger.warning("rename_task event re-title failed (%s): %s", event_id, exc)

    _write_split(new_path, fm, body)
    if new_path != old_path:
        old_path.unlink()
    return new_path, cal_errors


def now_iso_taipei() -> str:
    """Return Asia/Taipei ISO 8601 with explicit +08:00 offset.

    Used for review ``run_at`` and timer endpoints.
    """
    return datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds")


def _now_iso_z() -> str:
    """UTC ISO with millisecond precision and 'Z' suffix.

    Matches the TaskNotes plugin convention written by ``shared.lifeos_writer``.
    """
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
