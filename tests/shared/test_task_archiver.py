"""Tests for shared/task_archiver.py — done task 歸檔 sweep（修修 2026-07-29）。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from shared.task_archiver import ARCHIVE_DIR, TASKS_DIR, archive_done_tasks, find_integrity_issues

NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)


def _write_task(vault: Path, name: str, fm_lines: list[str], body: str = "內文") -> Path:
    d = vault / TASKS_DIR
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.md"
    p.write_text("---\n" + "\n".join(fm_lines) + "\n---\n\n" + body + "\n", encoding="utf-8")
    return p


def _write_archived(vault: Path, filename: str, fm_lines: list[str], body: str = "內文") -> Path:
    d = vault / ARCHIVE_DIR
    d.mkdir(parents=True, exist_ok=True)
    p = d / filename
    p.write_text("---\n" + "\n".join(fm_lines) + "\n---\n\n" + body + "\n", encoding="utf-8")
    return p


def test_old_done_task_moves_to_archive(tmp_path):
    _write_task(
        tmp_path,
        "寫電子報",
        ["title: 寫電子報", "status: done", "dateModified: '2026-06-17T03:41:33.278Z'"],
    )
    report = archive_done_tasks(tmp_path, now=NOW)
    assert [m[0] for m in report.moved] == ["寫電子報.md"]
    assert not (tmp_path / TASKS_DIR / "寫電子報.md").exists()
    archived = tmp_path / ARCHIVE_DIR / "寫電子報.md"
    assert archived.exists()
    assert "title: 寫電子報" in archived.read_text(encoding="utf-8")  # 內容原封不動


def test_recent_done_task_stays_in_retention_window(tmp_path):
    _write_task(
        tmp_path,
        "剛完成",
        ["title: 剛完成", "status: done", "dateModified: '2026-07-25T10:00:00Z'"],  # 4 天前
    )
    report = archive_done_tasks(tmp_path, now=NOW)
    assert report.moved == []
    assert report.kept_recent == ["剛完成.md"]
    assert (tmp_path / TASKS_DIR / "剛完成.md").exists()


def test_active_tasks_never_move(tmp_path):
    old_date = "dateModified: '2026-01-01T00:00:00Z'"
    _write_task(tmp_path, "進行中", ["title: 進行中", "status: doing", old_date])
    _write_task(tmp_path, "待辦", ["title: 待辦", "status: to-do", old_date])
    report = archive_done_tasks(tmp_path, now=NOW)
    assert report.moved == []
    assert (tmp_path / TASKS_DIR / "進行中.md").exists()
    assert (tmp_path / TASKS_DIR / "待辦.md").exists()


def test_time_entries_end_time_wins_over_date_modified(tmp_path):
    """完成時間取最晚：timeEntries 最後一筆比 dateModified 晚 → 用它判斷仍在窗內。"""
    _write_task(
        tmp_path,
        "有計時",
        [
            "title: 有計時",
            "status: done",
            "dateModified: '2026-06-01T00:00:00Z'",
            "timeEntries:",
            "- startTime: '2026-07-20T13:00:00+08:00'",
            "  endTime: '2026-07-20T14:00:00+08:00'",  # 9 天前 → 窗內
        ],
    )
    report = archive_done_tasks(tmp_path, now=NOW)
    assert report.moved == []
    assert report.kept_recent == ["有計時.md"]


def test_malformed_frontmatter_left_untouched(tmp_path):
    d = tmp_path / TASKS_DIR
    d.mkdir(parents=True)
    (d / "壞檔.md").write_text("沒有 frontmatter 的檔案", encoding="utf-8")
    report = archive_done_tasks(tmp_path, now=NOW)
    assert report.moved == []
    assert (d / "壞檔.md").exists()


def test_archive_name_conflict_gets_completion_date_suffix(tmp_path):
    (tmp_path / ARCHIVE_DIR).mkdir(parents=True)
    (tmp_path / ARCHIVE_DIR / "寫電子報.md").write_text("舊的", encoding="utf-8")
    _write_task(
        tmp_path,
        "寫電子報",
        ["title: 寫電子報", "status: done", "dateModified: '2026-06-17T03:41:33.278Z'"],
    )
    report = archive_done_tasks(tmp_path, now=NOW)
    assert [m[0] for m in report.moved] == ["寫電子報.md"]
    assert (tmp_path / ARCHIVE_DIR / "寫電子報 2026-06-17.md").exists()
    assert (tmp_path / ARCHIVE_DIR / "寫電子報.md").read_text(encoding="utf-8") == "舊的"  # 不覆蓋


def test_dry_run_reports_without_moving(tmp_path):
    _write_task(
        tmp_path,
        "寫電子報",
        ["title: 寫電子報", "status: done", "dateModified: '2026-06-17T03:41:33.278Z'"],
    )
    report = archive_done_tasks(tmp_path, now=NOW, dry_run=True)
    assert [m[0] for m in report.moved] == ["寫電子報.md"]
    assert report.dry_run is True
    assert (tmp_path / TASKS_DIR / "寫電子報.md").exists()
    assert not (tmp_path / ARCHIVE_DIR).exists()


def test_checkmark_emoji_counts_as_done(tmp_path):
    """✅: true 但 status 欄位缺失 —— 一樣視為完成（TaskNotes 舊檔形狀）。"""
    _write_task(
        tmp_path,
        "打勾檔",
        ["title: 打勾檔", "'✅': true", "dateModified: '2026-05-01T00:00:00Z'"],
    )
    report = archive_done_tasks(tmp_path, now=NOW)
    assert [m[0] for m in report.moved] == ["打勾檔.md"]


def test_find_integrity_issues_flags_stale_duplicate(tmp_path):
    """Tasks/ 跟 Archive/ 有同 title + 同 dateCreated 的檔案 —— 同一份筆記的殘留
    （2026-08-10 寫電子報事故：archive_done_tasks 只看 status，這份留在 Tasks/
    的複本 status 是 to-do，主 sweep 永遠不會碰它）。"""
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
    report = find_integrity_issues(tmp_path)
    assert report.stale_duplicates == [("寫電子報.md", "寫電子報.md")]


def test_find_integrity_issues_same_title_different_dateCreated_not_flagged(tmp_path):
    """同標題但 dateCreated 不同 —— 合法的不同輪次任務，不是殘留檔，不擋。"""
    _write_task(
        tmp_path,
        "寫電子報",
        ["title: 寫電子報", "status: to-do", "dateCreated: '2026-08-01T00:00:00.000Z'"],
    )
    _write_archived(
        tmp_path,
        "寫電子報 2026-06-17.md",
        ["title: 寫電子報", "status: done", "dateCreated: '2026-06-17T03:41:33.278Z'"],
    )
    report = find_integrity_issues(tmp_path)
    assert report.stale_duplicates == []


def test_find_integrity_issues_flags_stray_sync_conflict_files(tmp_path):
    d = tmp_path / TASKS_DIR
    d.mkdir(parents=True)
    (d / "寫電子報.sync-conflict-20260617-160416-YJZV5NL.md").write_text(
        "---\ntitle: 寫電子報\n---\n", encoding="utf-8"
    )
    report = find_integrity_issues(tmp_path)
    assert report.sync_conflicts == [
        "TaskNotes/Tasks/寫電子報.sync-conflict-20260617-160416-YJZV5NL.md"
    ]


def test_find_integrity_issues_clean_vault_reports_nothing(tmp_path):
    _write_task(
        tmp_path,
        "進行中",
        ["title: 進行中", "status: doing", "dateCreated: '2026-08-01T00:00:00.000Z'"],
    )
    _write_archived(
        tmp_path,
        "已完成.md",
        ["title: 已完成", "status: done", "dateCreated: '2026-05-01T00:00:00.000Z'"],
    )
    report = find_integrity_issues(tmp_path)
    assert report.stale_duplicates == []
    assert report.sync_conflicts == []
