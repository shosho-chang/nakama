"""Done task 歸檔 sweep：``TaskNotes/Tasks/`` → ``TaskNotes/Archive/``。

修修 2026-07-29 裁決：已完成的 task 留在 Tasks/ 會無限堆積，完成超過
``RETENTION_DAYS`` 天的收進 Archive/。設計對齊 TaskNotes plugin 的原生歸檔
慣例（vault plugin 設定實測：``moveArchivedTasks=true``、``archiveFolder=
"TaskNotes/Archive"``、``taskIdentificationMethod="tag"`` —— plugin 靠 tag
認 task，搬資料夾不影響 plugin 追蹤，wikilink 認檔名也不會斷）。

保留視窗的理由：weekly report（``shared/weekly_indexer`` 只讀 Tasks/）要
看到本週完成的 task；近期完成的留在原地方便回顧；剛完成就撞名的機率極低。
plugin 自己的 auto-archive 上限只有 1440 分鐘，蓋不住這個窗口，所以走
server-side cron（``agents/franky/jobs/task_archive_daily``）。

完成時間的認定（取最晚者）：``timeEntries[]`` 最後一筆 ``endTime`` >
``dateModified`` > 檔案 mtime。frontmatter 壞掉的檔案一律跳過不動。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from shared.log import get_logger

logger = get_logger("nakama.task_archiver")

TASKS_DIR = "TaskNotes/Tasks"
ARCHIVE_DIR = "TaskNotes/Archive"
RETENTION_DAYS = 14

_FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass
class IntegrityReport:
    # (Tasks/ name, Archive/ name)
    stale_duplicates: list[tuple[str, str]] = field(default_factory=list)
    sync_conflicts: list[str] = field(default_factory=list)  # vault-relative paths

    def to_summary_dict(self) -> dict:
        return {
            "stale_duplicates": len(self.stale_duplicates),
            "sync_conflicts": len(self.sync_conflicts),
        }


@dataclass
class ArchiveReport:
    moved: list[tuple[str, str]] = field(default_factory=list)  # (filename, completed_iso)
    kept_recent: list[str] = field(default_factory=list)  # done 但還在保留視窗內
    errors: list[tuple[str, str]] = field(default_factory=list)  # (filename, reason)
    dry_run: bool = False

    def to_summary_dict(self) -> dict:
        return {
            "moved": len(self.moved),
            "kept_recent": len(self.kept_recent),
            "errors": len(self.errors),
            "dry_run": self.dry_run,
        }


def _parse_frontmatter(text: str) -> dict | None:
    m = _FM_RE.match(text)
    if not m:
        return None
    try:
        fm = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return None
    return fm if isinstance(fm, dict) else None


def _is_done(fm: dict) -> bool:
    return str(fm.get("status", "")).lower() == "done" or bool(fm.get("✅"))


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    # naive 值視為 UTC（dateModified 是 TaskNotes plugin 寫的 Z 結尾 ISO；
    # fromisoformat 在 3.11+ 吃 Z 會回 aware）
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _completed_at(fm: dict, path: Path) -> datetime:
    candidates: list[datetime] = []
    entries = fm.get("timeEntries")
    if isinstance(entries, list):
        for e in entries:
            if isinstance(e, dict):
                dt = _parse_dt(e.get("endTime"))
                if dt:
                    candidates.append(dt)
    dt = _parse_dt(fm.get("dateModified"))
    if dt:
        candidates.append(dt)
    if candidates:
        return max(candidates)
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def archive_done_tasks(
    vault_root: Path,
    *,
    retention_days: int = RETENTION_DAYS,
    now: datetime | None = None,
    dry_run: bool = False,
) -> ArchiveReport:
    """把完成超過 ``retention_days`` 天的 task 檔搬進 Archive/。

    只動「確定已完成且過窗」的檔案；frontmatter 缺失或解析失敗一律跳過
    （寧可留在 Tasks/ 也不誤搬）。搬移不改檔名；Archive/ 內已有同名檔時
    改用「{stem} {完成日}.md」，再撞就記 error 跳過。
    """
    report = ArchiveReport(dry_run=dry_run)
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=retention_days)
    tasks_dir = vault_root / TASKS_DIR
    archive_dir = vault_root / ARCHIVE_DIR
    if not tasks_dir.is_dir():
        return report

    for path in sorted(tasks_dir.glob("*.md")):
        try:
            fm = _parse_frontmatter(path.read_text(encoding="utf-8"))
        except OSError as exc:
            report.errors.append((path.name, f"read failed: {exc}"))
            continue
        if fm is None or not _is_done(fm):
            continue
        completed = _completed_at(fm, path)
        if completed > cutoff:
            report.kept_recent.append(path.name)
            continue

        target = archive_dir / path.name
        if target.exists():
            target = archive_dir / f"{path.stem} {completed:%Y-%m-%d}{path.suffix}"
        if target.exists():
            report.errors.append((path.name, f"archive 內同名衝突：{target.name}"))
            continue

        if not dry_run:
            try:
                archive_dir.mkdir(parents=True, exist_ok=True)
                path.rename(target)
            except OSError as exc:
                report.errors.append((path.name, f"move failed: {exc}"))
                continue
        report.moved.append((path.name, completed.date().isoformat()))
        logger.info("archived task %s (completed %s)", path.name, completed.date())

    return report


def find_integrity_issues(vault_root: Path) -> IntegrityReport:
    """Vault-hygiene check for the 2026-08-10 寫電子報事故 failure mode: a
    ``Tasks/`` file that is actually a stale duplicate of an already-archived
    note (same ``title`` + ``dateCreated`` — the fingerprint of "same note,
    never cleaned out of Tasks/") silently skips ``archive_done_tasks`` above
    forever if its own ``status`` isn't ``done`` (e.g. it got reopened), and
    schedule_task_entry has no collision guard against it — it just gets
    written into, races Obsidian Sync, and the write lands as a
    ``*.sync-conflict-*.md`` sibling instead of canonical.

    Also flags any stray ``*.sync-conflict-*.md`` anywhere under
    ``TaskNotes/`` regardless of cause. Read-only — reports for a human/Slack
    to act on, never moves or deletes anything."""
    report = IntegrityReport()
    tasks_dir = vault_root / TASKS_DIR
    archive_dir = vault_root / ARCHIVE_DIR

    archived_by_identity: dict[tuple[str, str], str] = {}
    if archive_dir.is_dir():
        for path in sorted(archive_dir.glob("*.md")):
            try:
                fm = _parse_frontmatter(path.read_text(encoding="utf-8"))
            except OSError:
                continue
            if fm is None:
                continue
            title = str(fm.get("title") or "").strip()
            created = str(fm.get("dateCreated") or "").strip()
            if title and created:
                archived_by_identity[(title, created)] = path.name

    if tasks_dir.is_dir():
        for path in sorted(tasks_dir.glob("*.md")):
            try:
                fm = _parse_frontmatter(path.read_text(encoding="utf-8"))
            except OSError:
                continue
            if fm is None:
                continue
            title = str(fm.get("title") or "").strip()
            created = str(fm.get("dateCreated") or "").strip()
            if not title or not created:
                continue
            archive_name = archived_by_identity.get((title, created))
            if archive_name:
                report.stale_duplicates.append((path.name, archive_name))

    notes_root = vault_root / "TaskNotes"
    if notes_root.is_dir():
        for path in sorted(notes_root.rglob("*.sync-conflict-*.md")):
            report.sync_conflicts.append(str(path.relative_to(vault_root)).replace("\\", "/"))

    return report
