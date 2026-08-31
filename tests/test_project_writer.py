"""Tests for shared.project_writer (Tier C mutations)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from shared.project_writer import (
    VALID_TASK_STATUSES,
    ProjectConcurrentEditError,
    ProjectWriteError,
    append_timeentry,
    create_task,
    delete_task,
    now_iso_taipei,
    pop_last_timeentry,
    read_task_status,
    reassign_task_project,
    update_body_section,
    update_frontmatter,
    update_marked_section,
    update_task_status,
    write_review,
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


class TestUpdateFrontmatter:
    def test_simple_field(self, vault: Path):
        update_frontmatter(vault_root=vault, slug="t", patch={"one_sentence": "新的一句話"})
        content = (vault / "Projects" / "t.md").read_text(encoding="utf-8")
        fm = yaml.safe_load(content.split("---")[1])
        assert fm["one_sentence"] == "新的一句話"

    def test_preserves_body(self, vault: Path):
        update_frontmatter(vault_root=vault, slug="t", patch={"hook_text": "hi"})
        content = (vault / "Projects" / "t.md").read_text(encoding="utf-8")
        assert "原始描述" in content
        assert "原始正文" in content

    def test_deep_merge_dict(self, vault: Path):
        update_frontmatter(
            vault_root=vault,
            slug="t",
            patch={"pomodoro": {"est_total": 5, "actual_total": 2}},
        )
        update_frontmatter(
            vault_root=vault,
            slug="t",
            patch={"pomodoro": {"actual_total": 4}},
        )
        content = (vault / "Projects" / "t.md").read_text(encoding="utf-8")
        fm = yaml.safe_load(content.split("---")[1])
        # est_total survives the second patch
        assert fm["pomodoro"]["est_total"] == 5
        assert fm["pomodoro"]["actual_total"] == 4

    def test_status_change_no_silent_publish_date(self, vault: Path):
        # Writer does not auto-set publish_date; router does. Writer is dumb.
        update_frontmatter(vault_root=vault, slug="t", patch={"status": "paused"})
        content = (vault / "Projects" / "t.md").read_text(encoding="utf-8")
        fm = yaml.safe_load(content.split("---")[1])
        assert fm["status"] == "paused"
        assert fm.get("publish_date") is None


class TestUpdateBodySection:
    def test_replaces_existing_section_body(self, vault: Path):
        update_body_section(
            vault_root=vault,
            slug="t",
            heading_text="Script / Outline",
            content="新的正文內容",
        )
        body = (vault / "Projects" / "t.md").read_text(encoding="utf-8")
        assert "原始正文" not in body
        assert "新的正文內容" in body
        # Other sections preserved
        assert "原始描述" in body

    def test_human_only_marker_preserved_on_section_match(self, vault: Path):
        update_body_section(
            vault_root=vault,
            slug="t",
            heading_text="專案描述",
            content="新描述",
        )
        body = (vault / "Projects" / "t.md").read_text(encoding="utf-8")
        assert "<!-- vault:human-only-section -->" in body

    def test_creates_new_section_if_absent(self, vault: Path):
        update_body_section(
            vault_root=vault,
            slug="t",
            heading_text="YouTube Description",
            content="影片描述",
        )
        body = (vault / "Projects" / "t.md").read_text(encoding="utf-8")
        assert "## YouTube Description" in body
        assert "影片描述" in body


class TestAppendReview:
    def test_writes_persona_list_first_run(self, vault: Path):
        from shared.project_writer import append_review

        append_review(
            vault_root=vault,
            slug="t",
            persona="storyteller",
            review={
                "run_at": "2026-05-24T22:00:00+08:00",
                "prompt_version": "v1.0",
                "score": 4,
                "summary": "Hook is decent",
                "suggestions": ["改第 2 段"],
            },
        )
        content = (vault / "Projects" / "t.md").read_text(encoding="utf-8")
        fm = yaml.safe_load(content.split("---")[1])
        assert isinstance(fm["reviews"]["storyteller"], list)
        assert len(fm["reviews"]["storyteller"]) == 1
        assert fm["reviews"]["storyteller"][0]["score"] == 4
        assert fm["reviews"]["storyteller"][0]["prompt_version"] == "v1.0"

    def test_appends_second_run_preserves_first(self, vault: Path):
        from shared.project_writer import append_review

        append_review(
            vault_root=vault,
            slug="t",
            persona="coach",
            review={"run_at": "t1", "prompt_version": "v1", "score": 2, "summary": "draft 1"},
        )
        append_review(
            vault_root=vault,
            slug="t",
            persona="coach",
            review={"run_at": "t2", "prompt_version": "v1", "score": 4, "summary": "draft 2"},
        )
        content = (vault / "Projects" / "t.md").read_text(encoding="utf-8")
        fm = yaml.safe_load(content.split("---")[1])
        assert len(fm["reviews"]["coach"]) == 2
        assert fm["reviews"]["coach"][0]["score"] == 2
        assert fm["reviews"]["coach"][1]["score"] == 4

    def test_migrates_legacy_dict_shape_on_append(self, vault: Path):
        """v1 dict-shape gets wrapped to list when a new entry appended."""
        from shared.project_writer import append_review, update_frontmatter

        # Seed v1 dict-shape directly
        update_frontmatter(
            vault_root=vault,
            slug="t",
            patch={"reviews": {"storyteller": {"run_at": "old", "score": 3, "summary": "old"}}},
        )
        append_review(
            vault_root=vault,
            slug="t",
            persona="storyteller",
            review={"run_at": "new", "prompt_version": "v1", "score": 5, "summary": "new"},
        )
        content = (vault / "Projects" / "t.md").read_text(encoding="utf-8")
        fm = yaml.safe_load(content.split("---")[1])
        assert isinstance(fm["reviews"]["storyteller"], list)
        assert len(fm["reviews"]["storyteller"]) == 2
        assert fm["reviews"]["storyteller"][0]["score"] == 3  # legacy
        assert fm["reviews"]["storyteller"][1]["score"] == 5  # new

    def test_unknown_persona_raises(self, vault: Path):
        from shared.project_writer import append_review

        with pytest.raises(ValueError):
            append_review(vault_root=vault, slug="t", persona="seo", review={})

    def test_write_review_alias_still_works(self, vault: Path):
        """Soft shim — old name delegates to append_review (PR1→PR2 window)."""
        write_review(
            vault_root=vault,
            slug="t",
            persona="storyteller",
            review={"run_at": "t1", "score": 4, "summary": "ok"},
        )
        content = (vault / "Projects" / "t.md").read_text(encoding="utf-8")
        fm = yaml.safe_load(content.split("---")[1])
        # alias path = list-shape too (no longer dict)
        assert isinstance(fm["reviews"]["storyteller"], list)
        assert fm["reviews"]["storyteller"][0]["score"] == 4


class TestAppendTimeentry:
    def test_appends_entry_and_updates_modified(self, vault: Path):
        task_path = vault / "TaskNotes" / "Tasks" / "t - Pre-production.md"
        before_fm = yaml.safe_load(task_path.read_text(encoding="utf-8").split("---")[1])
        assert before_fm["timeEntries"] == []

        append_timeentry(
            vault_root=vault,
            project_slug="t",
            task_name="Pre-production",
            start_iso="2026-05-24T20:00:00+08:00",
            end_iso="2026-05-24T20:25:00+08:00",
        )
        fm = yaml.safe_load(task_path.read_text(encoding="utf-8").split("---")[1])
        assert fm["timeEntries"] == [
            {"startTime": "2026-05-24T20:00:00+08:00", "endTime": "2026-05-24T20:25:00+08:00"}
        ]
        assert "dateModified" in fm

    def test_appends_to_existing_entries(self, vault: Path):
        task_path = vault / "TaskNotes" / "Tasks" / "t - Pre-production.md"
        append_timeentry(
            vault_root=vault,
            project_slug="t",
            task_name="Pre-production",
            start_iso="2026-05-24T20:00:00+08:00",
            end_iso="2026-05-24T20:25:00+08:00",
        )
        append_timeentry(
            vault_root=vault,
            project_slug="t",
            task_name="Pre-production",
            start_iso="2026-05-24T20:30:00+08:00",
            end_iso="2026-05-24T20:55:00+08:00",
        )
        fm = yaml.safe_load(task_path.read_text(encoding="utf-8").split("---")[1])
        assert len(fm["timeEntries"]) == 2

    def test_mtime_guard_detects_concurrent_edit(
        self, vault: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Simulate Obsidian/Syncthing touching the file between read + write
        by monkey-patching :func:`_read_split` to mutate the file after
        recording mtime. The guard must raise ProjectConcurrentEditError."""
        from shared import project_writer

        original_read_split = project_writer._read_split

        def racing_read_split(path):
            result = original_read_split(path)
            # Simulate an external write: touch the file with new mtime
            # by re-writing existing content.
            current = path.read_text(encoding="utf-8")
            # Sleep enough to bump mtime granularity on slow filesystems (FAT32 = 2s);
            # then re-write with trailing newline to actually change mtime on all FS.
            import time

            time.sleep(0.05)
            path.write_text(current + "\n", encoding="utf-8")
            return result

        monkeypatch.setattr(project_writer, "_read_split", racing_read_split)

        with pytest.raises(ProjectConcurrentEditError, match="其他來源修改"):
            append_timeentry(
                vault_root=vault,
                project_slug="t",
                task_name="Pre-production",
                start_iso="2026-05-24T20:00:00+08:00",
                end_iso="2026-05-24T20:25:00+08:00",
            )

    def test_quiet_path_no_guard_trip(self, vault: Path):
        """Sanity: normal back-to-back calls don't trip the mtime guard."""
        for hour in (20, 21, 22):
            append_timeentry(
                vault_root=vault,
                project_slug="t",
                task_name="Pre-production",
                start_iso=f"2026-05-24T{hour:02d}:00:00+08:00",
                end_iso=f"2026-05-24T{hour:02d}:25:00+08:00",
            )
        task_path = vault / "TaskNotes" / "Tasks" / "t - Pre-production.md"
        fm = yaml.safe_load(task_path.read_text(encoding="utf-8").split("---")[1])
        assert len(fm["timeEntries"]) == 3


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


class TestPopLastTimeentry:
    def test_pops_last_entry_returns_true(self, vault: Path):
        append_timeentry(
            vault_root=vault,
            project_slug="t",
            task_name="Pre-production",
            start_iso="2026-05-25T10:00:00+08:00",
            end_iso="2026-05-25T10:25:00+08:00",
        )
        append_timeentry(
            vault_root=vault,
            project_slug="t",
            task_name="Pre-production",
            start_iso="2026-05-25T11:00:00+08:00",
            end_iso="2026-05-25T11:25:00+08:00",
        )

        popped = pop_last_timeentry(vault_root=vault, project_slug="t", task_name="Pre-production")
        assert popped is True

        path = vault / "TaskNotes" / "Tasks" / "t - Pre-production.md"
        fm = yaml.safe_load(path.read_text(encoding="utf-8").split("---")[1])
        assert len(fm["timeEntries"]) == 1
        # Last entry removed — only the first remains
        assert fm["timeEntries"][0]["startTime"] == "2026-05-25T10:00:00+08:00"

    def test_returns_false_when_empty(self, vault: Path):
        # Fixture seeds timeEntries: [] for the task
        popped = pop_last_timeentry(vault_root=vault, project_slug="t", task_name="Pre-production")
        assert popped is False

    def test_updates_dateModified(self, vault: Path):
        append_timeentry(
            vault_root=vault,
            project_slug="t",
            task_name="Pre-production",
            start_iso="2026-05-25T10:00:00+08:00",
            end_iso="2026-05-25T10:25:00+08:00",
        )
        path = vault / "TaskNotes" / "Tasks" / "t - Pre-production.md"
        before_modified = yaml.safe_load(path.read_text(encoding="utf-8").split("---")[1])[
            "dateModified"
        ]
        pop_last_timeentry(vault_root=vault, project_slug="t", task_name="Pre-production")
        after_modified = yaml.safe_load(path.read_text(encoding="utf-8").split("---")[1])[
            "dateModified"
        ]
        assert after_modified >= before_modified


class TestTaskStatus:
    def test_valid_statuses_constant(self):
        assert VALID_TASK_STATUSES == ("to-do", "doing", "done", "paused")

    def test_read_task_status_returns_value(self, vault: Path):
        assert (
            read_task_status(vault_root=vault, project_slug="t", task_name="Pre-production")
            == "to-do"
        )

    def test_read_task_status_returns_none_when_missing(self, vault: Path):
        assert read_task_status(vault_root=vault, project_slug="t", task_name="nonexistent") is None

    def test_update_task_status_writes_value(self, vault: Path):
        update_task_status(
            vault_root=vault, project_slug="t", task_name="Pre-production", status="doing"
        )
        assert (
            read_task_status(vault_root=vault, project_slug="t", task_name="Pre-production")
            == "doing"
        )

    def test_update_task_status_rejects_unknown(self, vault: Path):
        with pytest.raises(ProjectWriteError, match="unknown task status"):
            update_task_status(
                vault_root=vault, project_slug="t", task_name="Pre-production", status="finished"
            )

    def test_update_task_status_404_when_missing(self, vault: Path):
        with pytest.raises(ProjectWriteError, match="not found"):
            update_task_status(vault_root=vault, project_slug="t", task_name="ghost", status="done")


class TestDeleteTask:
    def test_delete_existing_task_returns_true(self, vault: Path):
        path = vault / "TaskNotes" / "Tasks" / "t - Pre-production.md"
        assert path.exists()
        called = []
        result = delete_task(
            vault_root=vault,
            project_slug="t",
            task_name="Pre-production",
            recycle_bin_fn=lambda p: (called.append(p), p.unlink())[-1],
        )
        assert result is True
        assert not path.exists()
        assert called == [path]

    def test_delete_missing_task_returns_false(self, vault: Path):
        result = delete_task(
            vault_root=vault,
            project_slug="t",
            task_name="ghost",
            recycle_bin_fn=lambda p: None,
        )
        assert result is False


class TestUpdateMarkedSection:
    """ADR-031 PR3 — marker-pair body section (research write-back)."""

    def test_appends_block_when_marker_absent(self, vault: Path):
        update_marked_section(
            vault_root=vault,
            slug="t",
            marker="nakama:research:zoro",
            content="## 🗝 Zoro 2026-05-25\n\n- keyword 1",
        )
        body = (vault / "Projects" / "t.md").read_text(encoding="utf-8")
        assert "<!-- nakama:research:zoro:start -->" in body
        assert "<!-- nakama:research:zoro:end -->" in body
        assert "## 🗝 Zoro 2026-05-25" in body
        # Pre-existing body preserved
        assert "原始描述" in body
        assert "原始正文" in body

    def test_replaces_block_idempotently(self, vault: Path):
        update_marked_section(
            vault_root=vault,
            slug="t",
            marker="nakama:research:zoro",
            content="## old\n\n- a",
        )
        update_marked_section(
            vault_root=vault,
            slug="t",
            marker="nakama:research:zoro",
            content="## new\n\n- b",
        )
        body = (vault / "Projects" / "t.md").read_text(encoding="utf-8")
        assert "## old" not in body
        assert "- a" not in body
        assert "## new" in body
        assert "- b" in body
        # Exactly one start/end pair survives
        assert body.count("<!-- nakama:research:zoro:start -->") == 1
        assert body.count("<!-- nakama:research:zoro:end -->") == 1

    def test_two_markers_coexist(self, vault: Path):
        update_marked_section(
            vault_root=vault,
            slug="t",
            marker="nakama:research:zoro",
            content="Zoro block",
        )
        update_marked_section(
            vault_root=vault,
            slug="t",
            marker="nakama:research:kb",
            content="KB block",
        )
        body = (vault / "Projects" / "t.md").read_text(encoding="utf-8")
        assert "Zoro block" in body
        assert "KB block" in body
        # Re-running Zoro doesn't touch KB block
        update_marked_section(
            vault_root=vault,
            slug="t",
            marker="nakama:research:zoro",
            content="Zoro v2",
        )
        body = (vault / "Projects" / "t.md").read_text(encoding="utf-8")
        assert "Zoro v2" in body
        assert "Zoro block" not in body
        assert "KB block" in body

    def test_invalid_marker_raises(self, vault: Path):
        with pytest.raises(ProjectWriteError, match="invalid marker"):
            update_marked_section(
                vault_root=vault,
                slug="t",
                marker="nakama research zoro",  # spaces forbidden
                content="x",
            )
        with pytest.raises(ProjectWriteError, match="invalid marker"):
            update_marked_section(
                vault_root=vault,
                slug="t",
                marker="bad<marker>",
                content="x",
            )


def test_now_iso_taipei_has_offset():
    iso = now_iso_taipei()
    assert "+08:00" in iso


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
