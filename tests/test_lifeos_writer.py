"""Unit tests for shared.lifeos_writer — Task renderer (ADR-068 trimmed scope)."""

from __future__ import annotations

import re

from shared.lifeos_writer import render_task


class TestRenderTask:
    def test_task_frontmatter_gold_standard(self):
        fm, body = render_task("超加工食品", "Research")
        assert fm["title"] == "超加工食品 - Research"
        assert fm["status"] == "to-do"
        assert fm["priority"] == "normal"
        assert fm["projects"] == ["[[超加工食品]]"]
        assert fm["tags"] == ["task"]
        assert fm["預估🍅"] == 4
        assert fm["✅"] is False
        assert body == ""

    def test_task_datetime_is_iso_z(self):
        fm, _ = render_task("X", "Y")
        assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$", fm["dateCreated"])
        assert fm["dateCreated"] == fm["dateModified"]

    def test_standalone_task_omits_projects_key(self):
        fm, _ = render_task(None, "獨立任務")
        assert fm["title"] == "獨立任務"
        assert "projects" not in fm

    def test_blank_project_treated_as_standalone(self):
        fm, _ = render_task("", "獨立任務")
        assert fm["title"] == "獨立任務"
        assert "projects" not in fm
