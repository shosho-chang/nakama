"""Unit tests for shared.project_index — ADR-068 thin Project layer."""

from __future__ import annotations

from pathlib import Path

import pytest

from shared.project_index import (
    ProjectError,
    create_project,
    find_project,
    list_projects,
    normalize_name,
    set_project_status,
    tasks_for,
)
from shared.weekly_indexer import WeeklyIndexer


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    (tmp_path / "Projects").mkdir()
    (tmp_path / "TaskNotes" / "Tasks").mkdir(parents=True)
    return tmp_path


def _write_project(vault: Path, name: str, status: str = "active", extra: str = "") -> Path:
    p = vault / "Projects" / f"{name}.md"
    p.write_text(
        f"---\ntype: project\nstatus: {status}\ncreated: 2026-08-31T00:00:00Z\n---\n{extra}",
        encoding="utf-8",
    )
    return p


def _write_task(vault: Path, basename: str, fm_extra: str = "") -> Path:
    p = vault / "TaskNotes" / "Tasks" / f"{basename}.md"
    p.write_text(
        f"---\ntitle: {basename}\nstatus: to-do\n預估🍅: 4\ntimeEntries: []\n{fm_extra}---\n",
        encoding="utf-8",
    )
    return p


class TestListProjects:
    def test_lists_only_type_project(self, vault: Path):
        _write_project(vault, "自由艦隊")
        # agent-workspace files (e.g. Brook 風格訓練) must be ignored
        (vault / "Projects" / "Brook 風格訓練.md").write_text(
            "---\ntype: agent-workspace\n---\n", encoding="utf-8"
        )
        # files without frontmatter must be ignored, not crash
        (vault / "Projects" / "隨手筆記.md").write_text("no frontmatter", encoding="utf-8")
        entries = list_projects(vault)
        assert [e.name for e in entries] == ["自由艦隊"]

    def test_includes_archived_with_flag(self, vault: Path):
        _write_project(vault, "舊戰線", status="archived")
        (e,) = list_projects(vault)
        assert e.archived is True

    def test_missing_dir_is_empty(self, tmp_path: Path):
        assert list_projects(tmp_path) == []

    def test_skips_sync_conflict_copies(self, vault: Path):
        _write_project(vault, "自由艦隊")
        (vault / "Projects" / "自由艦隊.sync-conflict-20260831-ABC.md").write_text(
            "---\ntype: project\nstatus: active\n---\n", encoding="utf-8"
        )
        assert len(list_projects(vault)) == 1


class TestCreateProject:
    def test_creates_minimal_stub(self, vault: Path):
        entry = create_project(vault, "  電子報 ")
        assert entry.name == "電子報"
        raw = (vault / "Projects" / "電子報.md").read_text(encoding="utf-8")
        assert "type: project" in raw
        assert "status: active" in raw
        assert "created:" in raw
        # ADR-068: no stats snapshot keys, ever
        assert "pomodoro" not in raw
        assert find_project(vault, "電子報") is not None

    def test_duplicate_raises(self, vault: Path):
        create_project(vault, "電子報")
        with pytest.raises(ProjectError):
            create_project(vault, "電子報")

    @pytest.mark.parametrize("bad", ["", "   ", "a/b", "a\\b", "a:b", ".hidden", "x" * 81])
    def test_invalid_names_raise(self, vault: Path, bad: str):
        with pytest.raises(ProjectError):
            normalize_name(bad)


class TestSetProjectStatus:
    def test_archive_and_restore_preserve_body(self, vault: Path):
        _write_project(vault, "自由艦隊", extra="\n戰線目標：百人社群。\n")
        set_project_status(vault, "自由艦隊", "archived")
        e = find_project(vault, "自由艦隊")
        assert e.archived is True
        assert "百人社群" in e.body
        set_project_status(vault, "自由艦隊", "active")
        assert find_project(vault, "自由艦隊").archived is False

    def test_missing_project_raises(self, vault: Path):
        with pytest.raises(ProjectError):
            set_project_status(vault, "不存在", "archived")

    def test_invalid_status_raises(self, vault: Path):
        _write_project(vault, "自由艦隊")
        with pytest.raises(ProjectError):
            set_project_status(vault, "自由艦隊", "paused")


class TestTasksFor:
    def test_frontmatter_is_canonical(self, vault: Path):
        _write_task(vault, "自由艦隊 - 社群文章", fm_extra='projects: ["[[自由艦隊]]"]\n')
        _write_task(vault, "獨立任務")
        tasks = WeeklyIndexer(vault).read_tasks()
        got = tasks_for("自由艦隊", tasks)
        assert [t.slug for t in got] == ["自由艦隊 - 社群文章"]

    def test_legacy_prefix_fallback(self, vault: Path):
        # pre-dual-write file: prefix only, no projects: frontmatter
        _write_task(vault, "自由艦隊 - 舊任務")
        tasks = WeeklyIndexer(vault).read_tasks()
        got = tasks_for("自由艦隊", tasks)
        assert [t.slug for t in got] == ["自由艦隊 - 舊任務"]

    def test_frontmatter_beats_prefix(self, vault: Path):
        # prefix says A, frontmatter says B → belongs to B only
        _write_task(vault, "自由艦隊 - 轉隊任務", fm_extra='projects: ["[[電子報]]"]\n')
        tasks = WeeklyIndexer(vault).read_tasks()
        assert tasks_for("自由艦隊", tasks) == []
        assert [t.slug for t in tasks_for("電子報", tasks)] == ["自由艦隊 - 轉隊任務"]
