"""Daily done-task 歸檔 sweep（修修 2026-07-29 裁決）。

Runs :func:`shared.task_archiver.archive_done_tasks`：完成超過保留視窗
（14 天）的 task 從 ``TaskNotes/Tasks/`` 搬進 ``TaskNotes/Archive/``，
對齊 TaskNotes plugin 的原生歸檔慣例。有搬移或錯誤時 Slack 一行摘要。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from shared.config import get_vault_path
from shared.log import get_logger
from shared.task_archiver import archive_done_tasks, find_integrity_issues

logger = get_logger("nakama.franky.task_archive")


@dataclass
class TaskArchiveResult:
    status: str  # ok | archived | error
    moved: int
    errors: int
    detail: str

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "moved": self.moved,
            "errors": self.errors,
            "detail": self.detail,
        }


def run_once(
    *,
    dry_run: bool = False,
    slack_bot: Optional[Any] = None,
    vault_root: Optional[Path] = None,
) -> TaskArchiveResult:
    """One sweep. ``dry_run`` 只盤點不搬檔、不發 Slack。"""
    vault = vault_root or get_vault_path()
    report = archive_done_tasks(vault, dry_run=dry_run)

    if report.errors:
        status = "error"
    elif report.moved:
        status = "archived"
    else:
        status = "ok"
    detail = (
        f"moved={len(report.moved)} kept_recent={len(report.kept_recent)} "
        f"errors={len(report.errors)}"
    )

    if (report.moved or report.errors) and slack_bot is not None and not dry_run:
        lines = [f"🗄️ Task 歸檔：{len(report.moved)} 個完成超過 14 天的 task 收進 Archive/"]
        lines += [f"  • {name}（完成 {done}）" for name, done in report.moved[:10]]
        if len(report.moved) > 10:
            lines.append(f"  …共 {len(report.moved)} 個")
        lines += [f"  ⚠️ {name}: {reason}" for name, reason in report.errors]
        try:
            slack_bot.post_plain("\n".join(lines), context="task_archive")
        except Exception as exc:  # noqa: BLE001 — a Slack hiccup mustn't fail the cron
            logger.warning("task_archive Slack post failed: %s", exc)

    logger.info("task_archive %s (%s)", status, detail)

    # 2026-08-10 事故的延遲偵測網：主 sweep 只搬「status: done」的檔案，一份
    # 被 reopen 或從沒關過的 Tasks/ 殘留檔會被它永遠跳過（見
    # shared.task_archiver.find_integrity_issues docstring）。這裡每天額外
    # 掃一次，異常不會馬上出現在 Obsidian 裡但至少 24 小時內會被 Slack 標出來。
    if not dry_run:
        try:
            integrity = find_integrity_issues(vault)
        except OSError as exc:
            logger.warning("task_archive integrity scan failed: %s", exc)
            integrity = None
        if integrity and (integrity.stale_duplicates or integrity.sync_conflicts) and slack_bot is not None:
            warn_lines = ["⚠️ Task vault 完整性檢查發現異常："]
            warn_lines += [
                f"  • Tasks/{name} 跟 Archive/{arch_name} 是同一份筆記（歸檔殘留，建議人工清理）"
                for name, arch_name in integrity.stale_duplicates[:10]
            ]
            warn_lines += [f"  • 孤兒 sync-conflict 檔：{p}" for p in integrity.sync_conflicts[:10]]
            try:
                slack_bot.post_plain("\n".join(warn_lines), context="task_archive_integrity")
            except Exception as exc:  # noqa: BLE001 — a Slack hiccup mustn't fail the cron
                logger.warning("task_archive integrity Slack post failed: %s", exc)

    return TaskArchiveResult(status, len(report.moved), len(report.errors), detail)
