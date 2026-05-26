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
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from shared.project_indexer import _FRONTMATTER_RE  # canonical regex

PROJECTS_DIR = "Projects"
TASKS_DIR = "TaskNotes/Tasks"

_NULL = object()  # sentinel for "field present but value should be cleared/None"


class ProjectWriteError(RuntimeError):
    pass


class ProjectConcurrentEditError(ProjectWriteError):
    """File was modified between this writer's read and its write.

    ADR-031 v2 panel #13 guard. Single-user vault has near-zero contention
    probability (per `user_vault_edit_pattern_no_concurrent`), but Syncthing
    + Obsidian-side edits create a non-zero window. Self-contained mtime
    check inside the writer narrows the race to the writer's own read→write
    span (~milliseconds).
    """


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
        os.replace(tmp_path_str, path)
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


def update_frontmatter(
    *,
    vault_root: Path,
    slug: str,
    patch: dict[str, Any],
) -> None:
    """Merge ``patch`` into the project's frontmatter and write back atomically.

    ``patch`` values may be:
        - any YAML-serializable value (replaces existing)
        - ``None`` (sets the field to YAML null / blank)
        - dict (deep-merges into existing dict at that key; pass ``{}`` to clear)
    """
    slug = unicodedata.normalize("NFC", slug)
    path = vault_root / PROJECTS_DIR / f"{slug}.md"
    fm, body = _read_split(path)
    _deep_merge(fm, patch)
    _write_split(path, fm, body)


def update_body_section(
    *,
    vault_root: Path,
    slug: str,
    heading_text: str,
    content: str,
) -> None:
    """Replace the body of an H2 section with ``content``.

    Matches ``## <heading_text>`` (heading line, ignoring trailing whitespace
    and trailing HTML-comment markers like ``<!-- vault:human-only-section -->``)
    and replaces everything until the next H2 (``\\n## ``) or EOF.

    Heading line itself is preserved.
    """
    slug = unicodedata.normalize("NFC", slug)
    path = vault_root / PROJECTS_DIR / f"{slug}.md"
    fm, body = _read_split(path)

    # Match a "## heading_text" possibly followed by HTML comment marker
    heading_pat = re.compile(
        r"(^##\s+" + re.escape(heading_text) + r"\s*(?:<!--[^\n]*-->)?\s*\n)",
        re.MULTILINE,
    )
    m = heading_pat.search(body)
    if not m:
        # Section doesn't exist — append a new one
        new_section = f"\n## {heading_text}\n\n{content.rstrip()}\n"
        new_body = body.rstrip() + new_section
    else:
        start = m.end()
        # Find next H2 or EOF
        next_h2 = re.search(r"\n##\s+", body[start:], re.MULTILINE)
        end = start + next_h2.start() if next_h2 else len(body)
        new_body = body[: m.end()] + "\n" + content.rstrip() + "\n" + body[end:]

    _write_split(path, fm, new_body)


def append_review(
    *,
    vault_root: Path,
    slug: str,
    persona: str,
    review: dict[str, Any],
) -> None:
    """Append a review entry to ``reviews.{persona}`` list (v2 schema).

    Reviews are stored as a list-of-versioned-objects per persona
    (ADR-031 v2 panel push) so prompt iteration history is preserved
    in-frontmatter. UI shows latest by default with a "歷史" toggle.

    Tolerates the v1 dict-shape on read for forward compatibility: if
    the existing value is a dict, it is wrapped to ``[dict, new_review]``
    before append. After this call the field is always a list.
    """
    if persona not in ("storyteller", "coach"):
        raise ValueError(f"unknown persona: {persona!r}")

    path = vault_root / PROJECTS_DIR / f"{slug}.md"
    fm, body = _read_split(path)

    reviews = fm.get("reviews")
    if not isinstance(reviews, dict):
        reviews = {}

    existing = reviews.get(persona)
    if existing is None:
        history: list[Any] = []
    elif isinstance(existing, list):
        history = list(existing)
    elif isinstance(existing, dict):
        # v1 dict-shape — migrate inline by wrapping the previous entry.
        history = [existing]
    else:
        raise ProjectWriteError(
            f"reviews.{persona} has unexpected shape: {type(existing).__name__}"
        )

    history.append(review)
    reviews[persona] = history
    fm["reviews"] = reviews
    _write_split(path, fm, body)


# Backwards-compatible alias for any in-flight PR1 callers (none in current
# codebase — kept as a soft shim during the PR1→PR2 transition window).
def write_review(
    *,
    vault_root: Path,
    slug: str,
    persona: str,
    review: dict[str, Any],
) -> None:
    """Deprecated v1 overwrite-shape — delegates to :func:`append_review`."""
    append_review(vault_root=vault_root, slug=slug, persona=persona, review=review)


def append_timeentry(
    *,
    vault_root: Path,
    project_slug: str,
    task_name: str,
    start_iso: str,
    end_iso: str,
) -> None:
    """Append a ``timeEntries`` entry to a TaskNotes Task md.

    File path: ``TaskNotes/Tasks/{project_slug} - {task_name}.md``.

    Shape matches the TaskNotes plugin contract:
        timeEntries:
          - startTime: 2026-05-24T20:00:00+08:00
            endTime:   2026-05-24T20:25:00+08:00

    Plugin's ``formula.實際🍅`` reads ``timeEntries[].endTime - startTime``,
    sums in minutes, divides by 25, floors.

    Concurrent-edit guard (ADR-031 v2 panel #13): records mtime before
    reading; re-checks before writing. Raises
    :class:`ProjectConcurrentEditError` if Obsidian / Syncthing / another
    writer touched the file in between. Caller decides whether to retry
    or surface to the user as 409.
    """
    project_slug = unicodedata.normalize("NFC", project_slug)
    task_basename = f"{project_slug} - {task_name}"
    path = vault_root / TASKS_DIR / f"{task_basename}.md"
    mtime_before = path.stat().st_mtime if path.exists() else None
    fm, body = _read_split(path)
    entries = fm.get("timeEntries")
    if not isinstance(entries, list):
        entries = []
    entries.append({"startTime": start_iso, "endTime": end_iso})
    fm["timeEntries"] = entries
    fm["dateModified"] = _now_iso_z()
    _check_unchanged(path, mtime_before)
    _write_split(path, fm, body)


VALID_TASK_STATUSES: tuple[str, ...] = ("to-do", "doing", "done", "paused")


def read_task_status(
    *,
    vault_root: Path,
    project_slug: str,
    task_name: str,
) -> str | None:
    """Return the current ``status`` field of a TaskNote, or None if missing."""
    project_slug = unicodedata.normalize("NFC", project_slug)
    task_name = unicodedata.normalize("NFC", task_name)
    path = vault_root / TASKS_DIR / f"{project_slug} - {task_name}.md"
    if not path.exists():
        return None
    fm, _ = _read_split(path)
    val = fm.get("status")
    return str(val) if val is not None else None


def update_task_status(
    *,
    vault_root: Path,
    project_slug: str,
    task_name: str,
    status: str,
) -> None:
    """Set the ``status`` field of a TaskNote.

    ADR-031 §F1: 4-state workflow (`to-do` / `doing` / `done` / `paused`).
    Caller validates the value — this fn raises ``ProjectWriteError`` on
    unknown values so callers don't accidentally write garbage.
    """
    if status not in VALID_TASK_STATUSES:
        raise ProjectWriteError(f"unknown task status {status!r}; valid: {VALID_TASK_STATUSES}")

    project_slug = unicodedata.normalize("NFC", project_slug)
    task_name = unicodedata.normalize("NFC", task_name)
    path = vault_root / TASKS_DIR / f"{project_slug} - {task_name}.md"
    if not path.exists():
        raise ProjectWriteError(f"Task not found: {path.name}")

    fm, body = _read_split(path)
    fm["status"] = status
    fm["dateModified"] = _now_iso_z()
    _write_split(path, fm, body)


def delete_task(
    *,
    vault_root: Path,
    project_slug: str,
    task_name: str,
    recycle_bin_fn=None,
) -> bool:
    """Send a TaskNote .md to recycle bin (Windows) or unlink (POSIX).

    Returns True if the file existed and was removed; False if it
    didn't exist (caller decides 404 vs 204).

    ``recycle_bin_fn`` is dependency-injected so tests can substitute a
    plain ``Path.unlink`` without spawning PowerShell. Default at runtime
    uses ``shared.discard_service._send_to_recycle_bin`` (the canonical
    PowerShell recycle-bin prefix matched by ``.claude/settings.json``).
    """
    if recycle_bin_fn is None:
        from shared.discard_service import _send_to_recycle_bin

        recycle_bin_fn = _send_to_recycle_bin

    project_slug = unicodedata.normalize("NFC", project_slug)
    task_name = unicodedata.normalize("NFC", task_name)
    path = vault_root / TASKS_DIR / f"{project_slug} - {task_name}.md"
    if not path.exists():
        return False
    recycle_bin_fn(path)
    return True


def pop_last_timeentry(
    *,
    vault_root: Path,
    project_slug: str,
    task_name: str,
) -> bool:
    """Remove the last ``timeEntries`` entry from a TaskNotes Task md.

    Returns True if an entry was popped, False if the list was already empty
    (caller decides whether to surface as 409 Conflict). Atomic via the
    underlying ``_write_split`` tmp+rename path.

    Same mtime guard as :func:`append_timeentry` — raises
    :class:`ProjectConcurrentEditError` if the file changed between read
    and write.
    """
    project_slug = unicodedata.normalize("NFC", project_slug)
    task_basename = f"{project_slug} - {task_name}"
    path = vault_root / TASKS_DIR / f"{task_basename}.md"
    mtime_before = path.stat().st_mtime if path.exists() else None
    fm, body = _read_split(path)
    entries = fm.get("timeEntries")
    if not isinstance(entries, list) or not entries:
        return False
    entries.pop()
    fm["timeEntries"] = entries
    fm["dateModified"] = _now_iso_z()
    _check_unchanged(path, mtime_before)
    _write_split(path, fm, body)
    return True


def create_task(
    *,
    vault_root: Path,
    project_slug: str,
    task_name: str,
    estimated_pomodoros: int = 4,
    priority: str = "normal",
    scheduled: str | None = None,
) -> Path:
    """Create a new TaskNotes-plugin-compatible task .md for a project.

    File path: ``TaskNotes/Tasks/{project_slug} - {task_name}.md``. Builds
    the same frontmatter shape as :func:`shared.lifeos_writer.render_task`
    so the TaskNotes plugin's auto-index picks it up identically to
    bootstrap-time tasks.

    Raises :class:`ProjectWriteError` if a task with the same basename
    already exists — callers decide whether to surface as 409 Conflict
    or auto-rename.
    """
    from shared.lifeos_writer import render_task

    project_slug = unicodedata.normalize("NFC", project_slug)
    task_name = unicodedata.normalize("NFC", task_name).strip()
    if not task_name:
        raise ProjectWriteError("task_name cannot be empty")

    # TaskNotes plugin builds the dashboard label from the filename, so
    # forbid path separators that would break out of TaskNotes/Tasks/.
    if "/" in task_name or "\\" in task_name:
        raise ProjectWriteError(f"task_name must not contain path separators: {task_name!r}")

    task_path = vault_root / TASKS_DIR / f"{project_slug} - {task_name}.md"
    if task_path.exists():
        raise ProjectWriteError(f"Task already exists: {task_path.name}")

    fm, body = render_task(
        project_slug,
        task_name,
        estimated_pomodoros=estimated_pomodoros,
        priority=priority,
    )
    if scheduled:
        fm["scheduled"] = scheduled

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


def _check_unchanged(path: Path, expected_mtime: float | None) -> None:
    """Raise :class:`ProjectConcurrentEditError` if the file's mtime has
    drifted since ``expected_mtime`` was recorded.

    ``expected_mtime is None`` is the "file didn't exist when we started"
    case — skip the check (no race surface to defend against).

    Filesystem mtime granularity varies (FAT32 = 2s, NTFS = 100ns, ext4 = 1ns).
    Bridge runs on Win + Mac + Linux; in all three the granularity is fine
    enough that a write within a writer's read→write span (~ms) reliably
    bumps the mtime. False negatives (mtime didn't bump despite a write)
    are vanishingly rare at this granularity.
    """
    if expected_mtime is None:
        return
    if not path.exists():
        # File was deleted between our read and our write — definitely changed.
        raise ProjectConcurrentEditError(f"file vanished mid-write: {path.name}")
    if path.stat().st_mtime != expected_mtime:
        raise ProjectConcurrentEditError(
            f"檔案 {path.name} 在 Bridge 讀寫之間被其他來源修改"
            "（Obsidian / Syncthing / 其他 process）。"
            "請重新整理頁面再試。"
        )


def _deep_merge(dst: dict[str, Any], patch: dict[str, Any]) -> None:
    """Recursive in-place merge of ``patch`` into ``dst``.

    Dict values merge recursively; everything else replaces.
    """
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v
