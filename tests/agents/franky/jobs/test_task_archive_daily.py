"""Tests for agents/franky/jobs/task_archive_daily.py — cron entrypoint.

Covers the 2026-08-10 事故的延遲偵測網 addition: run_once now also runs
shared.task_archiver.find_integrity_issues and posts a separate Slack
warning when it finds a stale Tasks/Archive duplicate or a stray
sync-conflict file — on top of the pre-existing archive-moved summary.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from agents.franky.jobs.task_archive_daily import run_once

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)


def _write_task(vault: Path, name: str, fm_lines: list[str]) -> None:
    d = vault / "TaskNotes" / "Tasks"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(
        "---\n" + "\n".join(fm_lines) + "\n---\n\n內文\n", encoding="utf-8"
    )


def _write_archived(vault: Path, filename: str, fm_lines: list[str]) -> None:
    d = vault / "TaskNotes" / "Archive"
    d.mkdir(parents=True, exist_ok=True)
    (d / filename).write_text("---\n" + "\n".join(fm_lines) + "\n---\n\n內文\n", encoding="utf-8")


def _integrity_calls(slack_bot: MagicMock) -> list:
    return [
        c
        for c in slack_bot.post_plain.call_args_list
        if c.kwargs.get("context") == "task_archive_integrity"
    ]


def test_run_once_posts_integrity_warning_for_stale_duplicate(tmp_path):
    _write_task(
        tmp_path,
        "寫電子報",
        ["title: 寫電子報", "status: to-do", "dateCreated: '2026-06-17T03:41:33.278Z'"],
    )
    _write_archived(
        tmp_path,
        "寫電子報.md",
        ["title: 寫電子報", "status: done", "dateCreated: '2026-06-17T03:41:33.278Z'"],
    )
    slack_bot = MagicMock()

    run_once(slack_bot=slack_bot, vault_root=tmp_path)

    integrity_calls = _integrity_calls(slack_bot)
    assert len(integrity_calls) == 1
    msg = integrity_calls[0].args[0]
    assert "寫電子報" in msg
    assert "同一份筆記" in msg or "歸檔殘留" in msg


def test_run_once_posts_integrity_warning_for_sync_conflict(tmp_path):
    d = tmp_path / "TaskNotes" / "Tasks"
    d.mkdir(parents=True)
    (d / "寫電子報.sync-conflict-20260617-160416-YJZV5NL.md").write_text(
        "---\ntitle: 寫電子報\n---\n", encoding="utf-8"
    )
    slack_bot = MagicMock()

    run_once(slack_bot=slack_bot, vault_root=tmp_path)

    integrity_calls = _integrity_calls(slack_bot)
    assert len(integrity_calls) == 1
    assert "sync-conflict" in integrity_calls[0].args[0]


def test_run_once_clean_vault_no_integrity_warning(tmp_path):
    _write_task(
        tmp_path,
        "進行中",
        ["title: 進行中", "status: doing", "dateCreated: '2026-08-01T00:00:00.000Z'"],
    )
    slack_bot = MagicMock()

    result = run_once(slack_bot=slack_bot, vault_root=tmp_path)

    integrity_calls = _integrity_calls(slack_bot)
    assert integrity_calls == []
    assert result.status == "ok"


def test_run_once_dry_run_skips_integrity_slack_post(tmp_path):
    _write_task(
        tmp_path,
        "寫電子報",
        ["title: 寫電子報", "status: to-do", "dateCreated: '2026-06-17T03:41:33.278Z'"],
    )
    _write_archived(
        tmp_path,
        "寫電子報.md",
        ["title: 寫電子報", "status: done", "dateCreated: '2026-06-17T03:41:33.278Z'"],
    )
    slack_bot = MagicMock()

    run_once(dry_run=True, slack_bot=slack_bot, vault_root=tmp_path)

    slack_bot.post_plain.assert_not_called()
