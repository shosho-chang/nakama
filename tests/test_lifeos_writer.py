"""Unit tests for shared.lifeos_writer — Project + Task renderer/writer."""

from __future__ import annotations

import re

import pytest
import yaml

from shared.lifeos_writer import (
    CONTENT_TYPES,
    DEFAULT_TASKS,
    ProjectExistsError,
    create_project_with_tasks,
    default_task_names,
    render_project,
    render_task,
)


def _parse(content: str) -> tuple[dict, str]:
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", content, re.DOTALL)
    assert m, f"Missing frontmatter in: {content[:200]!r}"
    fm = yaml.safe_load(m.group(1)) or {}
    return fm, m.group(2)


class TestRenderProject:
    def test_youtube_frontmatter_matches_gold_standard(self):
        fm, body = render_project(
            "超加工食品",
            "youtube",
            area="work",
            priority="first",
            search_topic="超加工食品",
        )
        assert fm["type"] == "project"
        assert fm["content_type"] == "youtube"
        assert fm["status"] == "active"
        assert fm["priority"] == "first"
        assert fm["area"] == "work"
        assert fm["search_topic"] == "超加工食品"
        assert fm["publish_date"] is None
        assert fm["quarter"] is None
        assert fm["parent_kr"] is None
        assert fm["tags"] == ["project", "youtube"]

    def test_youtube_search_topic_defaults_to_title(self):
        fm, _ = render_project("肌酸的妙用", "youtube")
        assert fm["search_topic"] == "肌酸的妙用"

    def test_research_frontmatter(self):
        fm, _ = render_project("超加工食品", "research", area="health")
        assert fm["content_type"] == "research"
        assert fm["area"] == "health"
        assert fm["tags"] == ["project", "research"]
        assert "search_topic" not in fm
        assert fm["target_date"] is None

    def test_blog_frontmatter_has_search_topic(self):
        fm, _ = render_project("晨型人科學", "blog", search_topic="morning routine")
        assert fm["content_type"] == "blog"
        assert fm["search_topic"] == "morning routine"

    def test_body_substitutes_title_in_bases_filter(self):
        _, body = render_project("超加工食品", "youtube")
        assert 'link("超加工食品")' in body
        assert "__TITLE__" not in body

    def test_youtube_body_has_required_sections(self):
        _, body = render_project("X", "youtube")
        for section in [
            "## 🎯 對應 OKR",
            "## ✅ Tasks",
            "## 📊 番茄統計",
            "## 👄 One Sentence",
            "## 📚 KB Research",
            "## 🗝️ Keyword Research",
            "%%KW-START%%",
            "%%KW-END%%",
            "## Script / Outline",
            "## 專案筆記",
        ]:
            assert section in body, f"youtube body missing section: {section}"

    def test_research_body_has_required_sections(self):
        _, body = render_project("X", "research")
        for section in [
            "## 🎯 對應 OKR",
            "## ✅ Tasks",
            "## 專案描述",
            "## 預期成果",
            "## 📚 KB Research",
            "## Literature Notes",
            "## Synthesis",
            "## 專案筆記",
        ]:
            assert section in body, f"research body missing section: {section}"

    def test_unknown_content_type_raises(self):
        with pytest.raises(ValueError):
            render_project("X", "newsletter")  # type: ignore[arg-type]

    def test_extra_fm_overrides(self):
        fm, _ = render_project("X", "youtube", extra_fm={"priority": "first", "custom": "value"})
        assert fm["priority"] == "first"
        assert fm["custom"] == "value"

    @pytest.mark.parametrize("ct", CONTENT_TYPES)
    def test_all_content_types_render_without_error(self, ct):
        fm, body = render_project("Test Title", ct)
        assert fm["content_type"] == ct
        assert body.startswith("# Test Title")


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


class TestCreateProjectWithTasks:
    def test_creates_all_files(self, tmp_path):
        result = create_project_with_tasks(
            "超加工食品",
            "research",
            ["Literature Review", "Synthesis", "Write-up"],
            vault=tmp_path,
        )

        assert result.project_path.exists()
        assert result.project_path.name == "超加工食品.md"
        assert len(result.task_paths) == 3
        assert [p.name for p in result.task_paths] == [
            "超加工食品 - Literature Review.md",
            "超加工食品 - Synthesis.md",
            "超加工食品 - Write-up.md",
        ]

    def test_project_frontmatter_parseable(self, tmp_path):
        result = create_project_with_tasks(
            "肌酸的妙用",
            "youtube",
            ["Pre-production", "Filming", "Post-production"],
            vault=tmp_path,
            priority="first",
        )
        fm, body = _parse(result.project_path.read_text(encoding="utf-8"))
        assert fm["type"] == "project"
        assert fm["content_type"] == "youtube"
        assert fm["priority"] == "first"
        assert fm["tags"] == ["project", "youtube"]
        assert 'link("肌酸的妙用")' in body

    def test_task_files_have_wikilink_to_project(self, tmp_path):
        result = create_project_with_tasks(
            "超加工食品",
            "blog",
            ["Research", "Draft", "Publish"],
            vault=tmp_path,
        )
        for tp in result.task_paths:
            fm, _ = _parse(tp.read_text(encoding="utf-8"))
            assert fm["projects"] == ["[[超加工食品]]"]
            assert fm["tags"] == ["task"]
            assert fm["status"] == "to-do"

    def test_conflict_raises_and_writes_nothing(self, tmp_path):
        projects_dir = tmp_path / "Projects"
        projects_dir.mkdir()
        (projects_dir / "Existing.md").write_text("preexisting", encoding="utf-8")

        with pytest.raises(ProjectExistsError):
            create_project_with_tasks(
                "Existing",
                "research",
                ["A", "B", "C"],
                vault=tmp_path,
            )

        tasks_dir = tmp_path / "TaskNotes" / "Tasks"
        assert not tasks_dir.exists() or not any(tasks_dir.iterdir())

    def test_task_conflict_raises_before_any_write(self, tmp_path):
        tasks_dir = tmp_path / "TaskNotes" / "Tasks"
        tasks_dir.mkdir(parents=True)
        (tasks_dir / "超加工食品 - Research.md").write_text("existing", encoding="utf-8")

        with pytest.raises(ProjectExistsError):
            create_project_with_tasks(
                "超加工食品",
                "blog",
                ["Research", "Draft", "Publish"],
                vault=tmp_path,
            )

        assert not (tmp_path / "Projects" / "超加工食品.md").exists()

    def test_blank_none_renders_as_empty_in_frontmatter(self, tmp_path):
        result = create_project_with_tasks(
            "X",
            "youtube",
            ["A", "B", "C"],
            vault=tmp_path,
        )
        raw = result.project_path.read_text(encoding="utf-8")
        assert "quarter:\n" in raw
        assert "parent_kr:\n" in raw
        assert "publish_date:\n" in raw
        assert "quarter: null" not in raw


class TestDefaultTaskNames:
    @pytest.mark.parametrize("ct", CONTENT_TYPES)
    def test_returns_three_tasks(self, ct):
        names = default_task_names(ct)
        assert len(names) == 3
        assert all(isinstance(n, str) and n for n in names)

    def test_unknown_raises(self):
        with pytest.raises(ValueError):
            default_task_names("newsletter")  # type: ignore[arg-type]

    def test_matches_spec(self):
        assert default_task_names("youtube") == ["Pre-production", "Filming", "Post-production"]
        assert default_task_names("blog") == ["Research", "Draft", "Publish"]
        assert default_task_names("research") == ["Literature Review", "Synthesis", "Write-up"]
        assert default_task_names("podcast") == ["Prep & Booking", "Recording", "Edit & Publish"]


def test_default_tasks_cover_all_content_types():
    assert set(DEFAULT_TASKS.keys()) == set(CONTENT_TYPES)
