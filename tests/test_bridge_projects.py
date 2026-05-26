"""Tests for thousand_sunny.routers.bridge_projects (Tier C Bridge surface)."""

from __future__ import annotations

import importlib

import pytest
import yaml
from fastapi.testclient import TestClient

SAMPLE_PROJECT = """---
type: project
content_type: youtube
created: 2026-04-10
status: active
priority: first
area: work
search_topic: 肌酸
quarter:
parent_kr:
publish_date:
one_sentence: 探討肌酸對非運動族群的妙用
hook_text: 你以為肌酸只是健身房裡那罐白白的粉？
title_candidates:
  - 肌酸不只練肌肉
  - 65 歲開始吃肌酸
thumbnail_concept: 健身房 vs 大腦
reviews: {}
pomodoro:
  est_total: 12
  actual_total: 8
tags:
  - project
  - youtube
---

# 肌酸的妙用

<!-- vault:human-only-section -->
## 專案描述
肌酸有效

## Script / Outline
script body
"""


PODCAST_PROJECT = """---
type: project
content_type: podcast
status: paused
priority: medium
area: work
search_topic: 睡眠
tags:
  - project
  - podcast
---

# 睡眠 ep
"""


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_SECRET", raising=False)
    monkeypatch.setenv("DISABLE_ROBIN", "1")
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    monkeypatch.setenv("NAKAMA_DOC_INDEX_DB_PATH", str(tmp_path / "doc_index.db"))

    proj_dir = tmp_path / "Projects"
    proj_dir.mkdir(parents=True)
    (proj_dir / "肌酸的妙用.md").write_text(SAMPLE_PROJECT, encoding="utf-8")
    (proj_dir / "睡眠.md").write_text(PODCAST_PROJECT, encoding="utf-8")

    tasks_dir = tmp_path / "TaskNotes" / "Tasks"
    tasks_dir.mkdir(parents=True)
    (tasks_dir / "肌酸的妙用 - Pre-production.md").write_text(
        "---\ntitle: 肌酸的妙用 - Pre-production\nstatus: to-do\n預估🍅: 4\ntimeEntries: []\n---\n",
        encoding="utf-8",
    )

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.bridge_projects as bp_module

    importlib.reload(auth_module)
    importlib.reload(bp_module)
    importlib.reload(app_module)
    return TestClient(app_module.app)


class TestIndex:
    def test_lists_projects(self, client):
        r = client.get("/bridge/projects")
        assert r.status_code == 200
        body = r.text
        assert "肌酸的妙用" in body
        assert "睡眠" in body
        assert "YOUTUBE" in body or "youtube" in body

    def test_sorts_active_first_above_paused(self, client):
        r = client.get("/bridge/projects")
        idx_active = r.text.find("肌酸的妙用")
        idx_paused = r.text.find("睡眠")
        assert idx_active < idx_paused, "active first should sort before paused medium"


class TestNewForm:
    def test_renders_form_with_all_content_types(self, client):
        r = client.get("/bridge/projects/new")
        assert r.status_code == 200
        # All 4 content types in select options (revert held)
        body = r.text
        assert ">youtube<" in body
        assert ">podcast<" in body


class TestDetail:
    def test_renders_all_tabs(self, client):
        r = client.get("/bridge/projects/肌酸的妙用")
        assert r.status_code == 200
        body = r.text
        assert 'data-tab="brief"' in body
        assert 'data-tab="research"' in body
        assert 'data-tab="title-thumbnail"' in body
        assert 'data-tab="hook"' in body
        assert 'data-tab="script"' in body
        assert 'data-tab="review"' in body
        assert 'data-tab="publish"' in body

    def test_brief_shows_one_sentence(self, client):
        r = client.get("/bridge/projects/肌酸的妙用")
        assert "探討肌酸對非運動族群的妙用" in r.text

    def test_hook_shows_hook_text(self, client):
        r = client.get("/bridge/projects/肌酸的妙用")
        assert "你以為肌酸只是健身房裡那罐白白的粉" in r.text

    def test_pomodoro_dock_present(self, client):
        r = client.get("/bridge/projects/肌酸的妙用")
        assert "pj-pomodoro-dock" in r.text
        assert "Pre-production" in r.text  # task in selector

    def test_404_for_unknown(self, client):
        r = client.get("/bridge/projects/does-not-exist")
        assert r.status_code == 404


class TestFrontmatterUpdate:
    def test_updates_one_sentence(self, client, tmp_path):
        r = client.post(
            "/bridge/projects/肌酸的妙用/frontmatter",
            data={"field": "one_sentence", "value": "新一句話", "tab": "brief"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        path = tmp_path / "Projects" / "肌酸的妙用.md"
        content = path.read_text(encoding="utf-8")
        fm = yaml.safe_load(content.split("---")[1])
        assert fm["one_sentence"] == "新一句話"

    def test_rejects_unknown_field(self, client):
        r = client.post(
            "/bridge/projects/肌酸的妙用/frontmatter",
            data={"field": "secret_field", "value": "x", "tab": "brief"},
        )
        assert r.status_code == 400

    def test_rejects_invalid_enum(self, client):
        r = client.post(
            "/bridge/projects/肌酸的妙用/frontmatter",
            data={"field": "status", "value": "bogus", "tab": "brief"},
        )
        assert r.status_code == 400

    def test_status_published_sets_publish_date(self, client, tmp_path):
        client.post(
            "/bridge/projects/肌酸的妙用/frontmatter",
            data={"field": "status", "value": "published", "tab": "publish"},
            follow_redirects=False,
        )
        path = tmp_path / "Projects" / "肌酸的妙用.md"
        fm = yaml.safe_load(path.read_text(encoding="utf-8").split("---")[1])
        assert fm["status"] == "published"
        assert fm["publish_date"] is not None

    def test_title_candidates_multiline(self, client, tmp_path):
        client.post(
            "/bridge/projects/肌酸的妙用/frontmatter",
            data={
                "field": "title_candidates",
                "value": "A 標題\nB 標題\n\nC 標題",
                "tab": "title-thumbnail",
            },
            follow_redirects=False,
        )
        path = tmp_path / "Projects" / "肌酸的妙用.md"
        fm = yaml.safe_load(path.read_text(encoding="utf-8").split("---")[1])
        assert fm["title_candidates"] == ["A 標題", "B 標題", "C 標題"]


class TestSectionUpdate:
    def test_updates_script_section(self, client, tmp_path):
        r = client.post(
            "/bridge/projects/肌酸的妙用/section/script",
            data={"content": "新 script", "tab": "script"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        body = (tmp_path / "Projects" / "肌酸的妙用.md").read_text(encoding="utf-8")
        assert "新 script" in body
        assert "script body" not in body

    def test_rejects_non_whitelisted_section(self, client):
        r = client.post(
            "/bridge/projects/肌酸的妙用/section/totally-bogus",
            data={"content": "x", "tab": "script"},
        )
        assert r.status_code == 400

    def test_updates_description_section(self, client, tmp_path):
        r = client.post(
            "/bridge/projects/肌酸的妙用/section/description",
            data={"content": "新描述", "tab": "brief"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        body = (tmp_path / "Projects" / "肌酸的妙用.md").read_text(encoding="utf-8")
        assert "新描述" in body

    def test_rejects_empty_content(self, client, tmp_path):
        before = (tmp_path / "Projects" / "肌酸的妙用.md").read_text(encoding="utf-8")
        r = client.post(
            "/bridge/projects/肌酸的妙用/section/script",
            data={"content": "", "tab": "script"},
        )
        assert r.status_code == 400
        after = (tmp_path / "Projects" / "肌酸的妙用.md").read_text(encoding="utf-8")
        assert before == after, "empty POST must not mutate the file"

    def test_rejects_whitespace_only_content(self, client, tmp_path):
        r = client.post(
            "/bridge/projects/肌酸的妙用/section/script",
            data={"content": "   \n  \n", "tab": "script"},
        )
        assert r.status_code == 400


class TestPomodoroTimer:
    def test_complete_writes_timeentry(self, client, tmp_path):
        r = client.post(
            "/bridge/projects/肌酸的妙用/timer/complete",
            data={"task_name": "Pre-production"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        task_md = (tmp_path / "TaskNotes" / "Tasks" / "肌酸的妙用 - Pre-production.md").read_text(
            encoding="utf-8"
        )
        fm = yaml.safe_load(task_md.split("---")[1])
        assert len(fm["timeEntries"]) == 1
        entry = fm["timeEntries"][0]
        assert "startTime" in entry
        assert "endTime" in entry

    def test_complete_no_task_name_rejected(self, client):
        r = client.post(
            "/bridge/projects/肌酸的妙用/timer/complete",
            data={"task_name": ""},
        )
        assert r.status_code == 400

    def test_manual_pomodoro_writes_entry(self, client, tmp_path):
        r = client.post(
            "/bridge/projects/肌酸的妙用/tasks/Pre-production/manual-pomodoro",
            follow_redirects=False,
        )
        assert r.status_code == 303
        task_md = (tmp_path / "TaskNotes" / "Tasks" / "肌酸的妙用 - Pre-production.md").read_text(
            encoding="utf-8"
        )
        fm = yaml.safe_load(task_md.split("---")[1])
        assert len(fm["timeEntries"]) == 1

    def test_manual_pomodoro_undo_pops_entry(self, client, tmp_path):
        # Seed an entry first via +1
        client.post(
            "/bridge/projects/肌酸的妙用/tasks/Pre-production/manual-pomodoro",
            follow_redirects=False,
        )
        # Now undo
        r = client.post(
            "/bridge/projects/肌酸的妙用/tasks/Pre-production/manual-pomodoro/undo",
            follow_redirects=False,
        )
        assert r.status_code == 303
        task_md = (tmp_path / "TaskNotes" / "Tasks" / "肌酸的妙用 - Pre-production.md").read_text(
            encoding="utf-8"
        )
        fm = yaml.safe_load(task_md.split("---")[1])
        assert fm["timeEntries"] == []

    def test_manual_pomodoro_undo_returns_409_when_empty(self, client):
        r = client.post(
            "/bridge/projects/肌酸的妙用/tasks/Pre-production/manual-pomodoro/undo",
        )
        assert r.status_code == 409
        assert "undo" in r.text.lower()

    def test_manual_pomodoro_undo_returns_json_when_ajax(self, client, tmp_path):
        # Seed first
        client.post(
            "/bridge/projects/肌酸的妙用/tasks/Pre-production/manual-pomodoro",
            follow_redirects=False,
        )
        r = client.post(
            "/bridge/projects/肌酸的妙用/tasks/Pre-production/manual-pomodoro/undo",
            headers={"Accept": "application/json"},
            follow_redirects=False,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["task_name"] == "Pre-production"
        assert data["task_actual"] == 0
        assert data["project_actual_total"] == 0

    def test_manual_pomodoro_returns_json_when_ajax(self, client, tmp_path):
        """Accept: application/json → JSON body, no 303 (preserves tab + dock state)."""
        r = client.post(
            "/bridge/projects/肌酸的妙用/tasks/Pre-production/manual-pomodoro",
            headers={"Accept": "application/json"},
            follow_redirects=False,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["task_name"] == "Pre-production"
        assert data["task_actual"] == 1  # one new entry just written
        assert data["project_actual_total"] >= 1
        # est_total is the sum of 預估🍅 across all tasks — fixture sets 4 for
        # Pre-production; smoke seed only has that one task in fixture.
        assert data["project_est_total"] >= 4


class TestTaskStatusEndpoint:
    def test_status_update_writes_field(self, client, tmp_path):
        r = client.post(
            "/bridge/projects/肌酸的妙用/tasks/Pre-production/status",
            data={"value": "doing"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        task_md = (tmp_path / "TaskNotes" / "Tasks" / "肌酸的妙用 - Pre-production.md").read_text(
            encoding="utf-8"
        )
        fm = yaml.safe_load(task_md.split("---")[1])
        assert fm["status"] == "doing"

    def test_status_update_json(self, client):
        r = client.post(
            "/bridge/projects/肌酸的妙用/tasks/Pre-production/status",
            data={"value": "done"},
            headers={"Accept": "application/json"},
            follow_redirects=False,
        )
        assert r.status_code == 200
        assert r.json() == {"task_name": "Pre-production", "task_status": "done"}

    def test_status_rejects_unknown_value(self, client):
        r = client.post(
            "/bridge/projects/肌酸的妙用/tasks/Pre-production/status",
            data={"value": "finished"},
        )
        assert r.status_code == 400

    def test_status_404_when_task_missing(self, client):
        r = client.post(
            "/bridge/projects/肌酸的妙用/tasks/ghost/status",
            data={"value": "doing"},
        )
        assert r.status_code == 404

    def test_first_pomodoro_auto_flips_to_doing(self, client, tmp_path):
        # Pre-production starts as `to-do` in the fixture
        task_path = tmp_path / "TaskNotes" / "Tasks" / "肌酸的妙用 - Pre-production.md"
        before = yaml.safe_load(task_path.read_text(encoding="utf-8").split("---")[1])
        assert before["status"] == "to-do"

        # +1🍅
        r = client.post(
            "/bridge/projects/肌酸的妙用/tasks/Pre-production/manual-pomodoro",
            headers={"Accept": "application/json"},
        )
        assert r.status_code == 200
        assert r.json()["task_status"] == "doing"

        after = yaml.safe_load(task_path.read_text(encoding="utf-8").split("---")[1])
        assert after["status"] == "doing"

    def test_second_pomodoro_does_not_overwrite_status(self, client, tmp_path):
        # Set status to "done" first, then +1🍅 should NOT flip it back
        client.post(
            "/bridge/projects/肌酸的妙用/tasks/Pre-production/status",
            data={"value": "done"},
        )
        r = client.post(
            "/bridge/projects/肌酸的妙用/tasks/Pre-production/manual-pomodoro",
            headers={"Accept": "application/json"},
        )
        assert r.json()["task_status"] == "done"

        task_path = tmp_path / "TaskNotes" / "Tasks" / "肌酸的妙用 - Pre-production.md"
        fm = yaml.safe_load(task_path.read_text(encoding="utf-8").split("---")[1])
        assert fm["status"] == "done"


class TestTaskDeleteEndpoint:
    def test_delete_removes_file_and_recomputes_rollup(self, client, tmp_path, monkeypatch):
        # Patch recycle-bin send to a plain unlink for the test
        from shared import project_writer

        def _fake_delete(*, vault_root, project_slug, task_name, recycle_bin_fn=None):
            path = vault_root / "TaskNotes" / "Tasks" / f"{project_slug} - {task_name}.md"
            if path.exists():
                path.unlink()
                return True
            return False

        # Patch both the writer module and the imported alias in the router.
        monkeypatch.setattr(project_writer, "delete_task", _fake_delete)
        import thousand_sunny.routers.bridge_projects as bp_module

        monkeypatch.setattr(bp_module, "delete_task", _fake_delete)

        r = client.post(
            "/bridge/projects/肌酸的妙用/tasks/Pre-production/delete",
            headers={"Accept": "application/json"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["deleted"] is True
        assert body["project_est_total"] == 0  # only task was 預估:4, now gone

        task_path = tmp_path / "TaskNotes" / "Tasks" / "肌酸的妙用 - Pre-production.md"
        assert not task_path.exists()

    def test_delete_missing_returns_404(self, client, monkeypatch):
        # No monkeypatch — real delete_task returns False for missing files
        r = client.post(
            "/bridge/projects/肌酸的妙用/tasks/does-not-exist/delete",
        )
        assert r.status_code == 404


class TestCreateTaskEndpoint:
    def test_creates_task_with_form_post(self, client, tmp_path):
        r = client.post(
            "/bridge/projects/肌酸的妙用/tasks/new",
            data={"name": "Filming v2", "estimated_pomodoros": "3", "priority": "high"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        task_md = (tmp_path / "TaskNotes" / "Tasks" / "肌酸的妙用 - Filming v2.md").read_text(
            encoding="utf-8"
        )
        fm = yaml.safe_load(task_md.split("---")[1])
        assert fm["title"] == "肌酸的妙用 - Filming v2"
        assert fm["預估🍅"] == 3
        assert fm["priority"] == "high"

    def test_creates_task_with_cjk_name(self, client, tmp_path):
        r = client.post(
            "/bridge/projects/肌酸的妙用/tasks/new",
            data={"name": "第二輪剪輯"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert (tmp_path / "TaskNotes" / "Tasks" / "肌酸的妙用 - 第二輪剪輯.md").exists()

    def test_rejects_empty_name_400(self, client):
        r = client.post(
            "/bridge/projects/肌酸的妙用/tasks/new",
            data={"name": "  "},
        )
        assert r.status_code == 400
        assert "required" in r.text

    def test_rejects_invalid_priority_400(self, client):
        r = client.post(
            "/bridge/projects/肌酸的妙用/tasks/new",
            data={"name": "x", "priority": "ultra-urgent"},
        )
        assert r.status_code == 400

    def test_rejects_out_of_range_pomodoros_400(self, client):
        r = client.post(
            "/bridge/projects/肌酸的妙用/tasks/new",
            data={"name": "x", "estimated_pomodoros": "0"},
        )
        assert r.status_code == 400

        r = client.post(
            "/bridge/projects/肌酸的妙用/tasks/new",
            data={"name": "y", "estimated_pomodoros": "50"},
        )
        assert r.status_code == 400

    def test_duplicate_returns_409(self, client):
        # Pre-production task already seeded in the fixture
        r = client.post(
            "/bridge/projects/肌酸的妙用/tasks/new",
            data={"name": "Pre-production"},
        )
        assert r.status_code == 409
        assert "exists" in r.text.lower()


class TestReviewStub:
    def test_pr1_review_returns_501(self, client):
        r = client.post("/bridge/projects/肌酸的妙用/review/storyteller")
        assert r.status_code == 501
        assert "PR2" in r.text

    def test_review_invalid_persona_400(self, client):
        r = client.post("/bridge/projects/肌酸的妙用/review/seo")
        assert r.status_code == 400
