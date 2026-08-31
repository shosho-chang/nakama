"""Tests for shared.project_writer (Tier C mutations)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from shared.project_writer import (
    ProjectWriteError,
    create_task,
    now_iso_taipei,
    reassign_task_project,
)

SEED = """---
type: project
content_type: youtube
status: active
priority: first
area: work
search_topic: x
tags:
  - project
  - youtube
---

# t

<!-- vault:human-only-section -->
## 專案描述
原始描述

## Script / Outline
原始正文
"""


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    proj_dir = tmp_path / "Projects"
    proj_dir.mkdir(parents=True)
    (proj_dir / "t.md").write_text(SEED, encoding="utf-8")
    (tmp_path / "TaskNotes" / "Tasks").mkdir(parents=True)
    (tmp_path / "TaskNotes" / "Tasks" / "t - Pre-production.md").write_text(
        "---\ntitle: t - Pre-production\nstatus: to-do\n預估🍅: 4\ntimeEntries: []\n---\n",
        encoding="utf-8",
    )
    return tmp_path


class TestCreateTask:
    def test_writes_plugin_compatible_frontmatter(self, vault: Path):
        path = create_task(
            vault_root=vault,
            project_slug="t",
            task_name="Filming",
            estimated_pomodoros=6,
            priority="high",
        )
        assert path.exists()
        assert path.name == "t - Filming.md"

        fm = yaml.safe_load(path.read_text(encoding="utf-8").split("---")[1])
        assert fm["title"] == "t - Filming"
        assert fm["status"] == "to-do"
        assert fm["priority"] == "high"
        assert fm["category"] == "work"  # default
        assert fm["projects"] == ["[[t]]"]
        assert fm["tags"] == ["task"]
        assert fm["預估🍅"] == 6
        assert fm["✅"] is False
        assert "dateCreated" in fm
        assert "dateModified" in fm

    def test_standalone_task_no_project(self, vault: Path):
        # v3-G: project_slug=None → bare {title}.md, no filename prefix, no projects key.
        path = create_task(vault_root=vault, project_slug=None, task_name="獨立任務")
        assert path.name == "獨立任務.md"
        fm = yaml.safe_load(path.read_text(encoding="utf-8").split("---")[1])
        assert fm["title"] == "獨立任務"
        assert "projects" not in fm
        assert fm["category"] == "work"

    def test_blank_project_is_standalone(self, vault: Path):
        path = create_task(vault_root=vault, project_slug="", task_name="也是獨立")
        assert path.name == "也是獨立.md"
        fm = yaml.safe_load(path.read_text(encoding="utf-8").split("---")[1])
        assert "projects" not in fm

    def test_category_written(self, vault: Path):
        path = create_task(vault_root=vault, project_slug="t", task_name="冥想", category="health")
        fm = yaml.safe_load(path.read_text(encoding="utf-8").split("---")[1])
        assert fm["category"] == "health"

    def test_default_priority_and_pomodoros(self, vault: Path):
        path = create_task(vault_root=vault, project_slug="t", task_name="後製校色")
        fm = yaml.safe_load(path.read_text(encoding="utf-8").split("---")[1])
        assert fm["priority"] == "normal"
        assert fm["預估🍅"] == 4

    def test_scheduled_optional(self, vault: Path):
        path = create_task(
            vault_root=vault,
            project_slug="t",
            task_name="第二輪剪輯",
            scheduled="2026-06-01T10:00:00",
        )
        fm = yaml.safe_load(path.read_text(encoding="utf-8").split("---")[1])
        assert fm["scheduled"] == "2026-06-01T10:00:00"

    def test_rejects_duplicate(self, vault: Path):
        create_task(vault_root=vault, project_slug="t", task_name="dup")
        with pytest.raises(ProjectWriteError, match="already exists"):
            create_task(vault_root=vault, project_slug="t", task_name="dup")

    def test_rejects_empty_name(self, vault: Path):
        with pytest.raises(ProjectWriteError, match="empty"):
            create_task(vault_root=vault, project_slug="t", task_name="  ")

    def test_rejects_path_separator(self, vault: Path):
        with pytest.raises(ProjectWriteError, match="path separators"):
            create_task(vault_root=vault, project_slug="t", task_name="bad/name")
        with pytest.raises(ProjectWriteError, match="path separators"):
            create_task(vault_root=vault, project_slug="t", task_name="bad\\name")

    def test_cjk_task_name(self, vault: Path):
        path = create_task(vault_root=vault, project_slug="t", task_name="腳本初稿")
        assert path.exists()
        assert path.name == "t - 腳本初稿.md"


class TestRenameTask:
    """v3-I.3: a true rename — frontmatter title + filename (slug) + linked Google
    event summaries — preserving the project prefix."""

    def _fm(self, path: Path) -> dict:
        return yaml.safe_load(path.read_text(encoding="utf-8").split("---")[1])

    def test_rename_standalone(self, vault: Path):
        from shared.project_writer import rename_task

        old = create_task(vault_root=vault, project_slug=None, task_name="打錯字")
        new_path, errs = rename_task(vault_root=vault, old_slug="打錯字", new_title="正確標題")
        assert errs == 0
        assert new_path.name == "正確標題.md"
        assert not old.exists()
        fm = self._fm(new_path)
        assert fm["title"] == "正確標題"
        assert "projects" not in fm

    def test_rename_keeps_project_prefix(self, vault: Path):
        from shared.project_writer import rename_task

        create_task(vault_root=vault, project_slug="t", task_name="Filming")
        new_path, _ = rename_task(vault_root=vault, old_slug="t - Filming", new_title="Filming v2")
        assert new_path.name == "t - Filming v2.md"
        fm = self._fm(new_path)
        assert fm["title"] == "t - Filming v2"
        assert fm["projects"] == ["[[t]]"]

    def test_rename_legacy_filename_prefix_no_frontmatter(self, vault: Path):
        # Pre-v3-H tasks: filename has "{project} - " but projects: is absent — the
        # prefix must survive a rename (it's the only project link they have).
        from shared.project_writer import rename_task

        legacy = vault / "TaskNotes" / "Tasks" / "t - Pre-production.md"  # from the fixture
        new_path, _ = rename_task(
            vault_root=vault, old_slug="t - Pre-production", new_title="前期製作"
        )
        assert new_path.name == "t - 前期製作.md"
        assert not legacy.exists()

    def test_rename_duplicate_raises(self, vault: Path):
        from shared.project_writer import rename_task

        create_task(vault_root=vault, project_slug="t", task_name="A")
        create_task(vault_root=vault, project_slug="t", task_name="B")
        with pytest.raises(ProjectWriteError, match="already exists"):
            rename_task(vault_root=vault, old_slug="t - A", new_title="B")

    def test_rename_empty_raises(self, vault: Path):
        from shared.project_writer import rename_task

        create_task(vault_root=vault, project_slug="t", task_name="X")
        with pytest.raises(ProjectWriteError, match="empty"):
            rename_task(vault_root=vault, old_slug="t - X", new_title="   ")

    def test_rename_retitles_linked_events(self, vault: Path, monkeypatch):
        from shared.project_writer import rename_task

        path = create_task(vault_root=vault, project_slug="t", task_name="老名字")
        # inject a linked plan entry (as the 排入 flow would write)
        text = path.read_text(encoding="utf-8")
        fm, body = text.split("---")[1], "---".join(text.split("---")[2:])
        data = yaml.safe_load(fm)
        data["plan"] = [{"date": "2026-06-09", "pomodoros": 2, "calendar_event_id": "evt_1"}]
        path.write_text(
            "---\n" + yaml.dump(data, allow_unicode=True) + "---" + body, encoding="utf-8"
        )

        calls = []
        monkeypatch.setattr(
            "shared.google_calendar.update_event",
            lambda eid, **kw: calls.append((eid, kw)),
        )
        new_path, errs = rename_task(vault_root=vault, old_slug="t - 老名字", new_title="新名字")
        assert errs == 0
        assert new_path.name == "t - 新名字.md"
        assert calls == [
            ("evt_1", {"title": "t - 新名字", "idempotency_key": "t - 新名字@2026-06-09"})
        ]

    def test_rename_calendar_failure_is_soft(self, vault: Path, monkeypatch):
        from shared.project_writer import rename_task

        path = create_task(vault_root=vault, project_slug="t", task_name="會掛")
        text = path.read_text(encoding="utf-8")
        fm, body = text.split("---")[1], "---".join(text.split("---")[2:])
        data = yaml.safe_load(fm)
        data["plan"] = [{"date": "2026-06-09", "pomodoros": 2, "calendar_event_id": "evt_x"}]
        path.write_text(
            "---\n" + yaml.dump(data, allow_unicode=True) + "---" + body, encoding="utf-8"
        )

        def _boom(eid, **kw):
            raise RuntimeError("calendar down")

        monkeypatch.setattr("shared.google_calendar.update_event", _boom)
        new_path, errs = rename_task(vault_root=vault, old_slug="t - 會掛", new_title="改好了")
        # vault rename is authoritative — it proceeds despite the calendar failure
        assert new_path.name == "t - 改好了.md"
        assert errs == 1


def test_now_iso_taipei_has_offset():
    assert "+08:00" in now_iso_taipei()


class TestReassignTaskProject:
    """修修 2026-08-29 稽核 B：專案歸屬過去只能在建立當下決定，事後無處可改。

    歸屬是雙寫的（檔名前綴 + projects:），所以改派＝搬檔＝換 slug，並要同步
    已連動的行事曆事件標題。"""

    def _fm(self, path: Path) -> dict:
        return yaml.safe_load(path.read_text(encoding="utf-8").split("---")[1])

    def test_attach_standalone_task_to_project(self, vault: Path):
        create_task(vault_root=vault, project_slug=None, task_name="孤兒任務")
        new_path, cal_errors = reassign_task_project(
            vault_root=vault, task_slug="孤兒任務", project_slug="t"
        )
        assert cal_errors == 0
        assert new_path.name == "t - 孤兒任務.md"
        assert not (vault / "TaskNotes" / "Tasks" / "孤兒任務.md").exists()
        fm = self._fm(new_path)
        assert fm["projects"] == ["[[t]]"]
        assert fm["title"] == "t - 孤兒任務"

    def test_detach_task_from_project(self, vault: Path):
        create_task(vault_root=vault, project_slug="t", task_name="要獨立")
        new_path, _ = reassign_task_project(
            vault_root=vault, task_slug="t - 要獨立", project_slug=None
        )
        assert new_path.name == "要獨立.md"
        fm = self._fm(new_path)
        assert "projects" not in fm
        assert fm["title"] == "要獨立"

    def test_move_between_projects_without_stacking_prefix(self, vault: Path):
        (vault / "Projects" / "u.md").write_text(SEED, encoding="utf-8")
        create_task(vault_root=vault, project_slug="t", task_name="腳本")
        new_path, _ = reassign_task_project(
            vault_root=vault, task_slug="t - 腳本", project_slug="u"
        )
        # 前綴是換掉、不是疊上去（不可以變成 "u - t - 腳本"）
        assert new_path.name == "u - 腳本.md"
        fm = self._fm(new_path)
        assert fm["title"] == "u - 腳本"
        assert fm["projects"] == ["[[u]]"]

    def test_noop_when_already_in_target(self, vault: Path):
        path = create_task(vault_root=vault, project_slug="t", task_name="不動")
        before = path.read_text(encoding="utf-8")
        same, cal_errors = reassign_task_project(
            vault_root=vault, task_slug="t - 不動", project_slug="t"
        )
        assert same == path and cal_errors == 0
        assert path.read_text(encoding="utf-8") == before  # 不重寫、不動行事曆

    def test_collision_raises(self, vault: Path):
        create_task(vault_root=vault, project_slug="t", task_name="撞名")
        create_task(vault_root=vault, project_slug=None, task_name="撞名")
        with pytest.raises(ProjectWriteError):
            reassign_task_project(vault_root=vault, task_slug="撞名", project_slug="t")

    def test_path_separator_rejected(self, vault: Path):
        create_task(vault_root=vault, project_slug=None, task_name="安全")
        with pytest.raises(ProjectWriteError):
            reassign_task_project(vault_root=vault, task_slug="安全", project_slug="a/b")

    def test_missing_task_raises(self, vault: Path):
        with pytest.raises(ProjectWriteError):
            reassign_task_project(vault_root=vault, task_slug="不存在", project_slug="t")
