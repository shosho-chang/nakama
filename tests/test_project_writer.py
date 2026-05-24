"""Tests for shared.project_writer (Tier C mutations)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from shared.project_writer import (
    ProjectWriteError,
    append_timeentry,
    create_task,
    now_iso_taipei,
    update_body_section,
    update_frontmatter,
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


class TestWriteReview:
    def test_writes_persona_block(self, vault: Path):
        write_review(
            vault_root=vault,
            slug="t",
            persona="storyteller",
            review={
                "run_at": "2026-05-24T22:00:00+08:00",
                "score": 4,
                "summary": "Hook is decent",
                "suggestions": ["改第 2 段"],
            },
        )
        content = (vault / "Projects" / "t.md").read_text(encoding="utf-8")
        fm = yaml.safe_load(content.split("---")[1])
        assert fm["reviews"]["storyteller"]["score"] == 4
        assert fm["reviews"]["storyteller"]["suggestions"] == ["改第 2 段"]

    def test_unknown_persona_raises(self, vault: Path):
        with pytest.raises(ValueError):
            write_review(vault_root=vault, slug="t", persona="seo", review={})


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
        assert fm["projects"] == ["[[t]]"]
        assert fm["tags"] == ["task"]
        assert fm["預估🍅"] == 6
        assert fm["✅"] is False
        assert "dateCreated" in fm
        assert "dateModified" in fm

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


def test_now_iso_taipei_has_offset():
    iso = now_iso_taipei()
    assert "+08:00" in iso
