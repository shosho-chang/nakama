"""Tests for thousand_sunny.routers.bridge_projects (Tier C Bridge surface)."""

from __future__ import annotations

import importlib
from datetime import datetime

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
    monkeypatch.setenv("NAKAMA_RESEARCH_CACHE_DIR", str(tmp_path / "research_cache"))

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

    def test_brief_lists_linked_tasks(self, client):
        # 修修: every linked Task surfaced on the Brief tab (not just the dock)
        body = client.get("/bridge/projects/肌酸的妙用").text
        assert "pj-brief-tasks" in body
        assert "關聯任務" in body
        # each task links to its weekly task page (full file-stem slug)
        assert "/bridge/weekly/task/" in body
        assert "Pre-production" in body

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

    def test_publish_with_incomplete_writes_audit_scope_json(self, client, tmp_path, monkeypatch):
        """Panel #10: status → published while tabs are incomplete writes
        an audit entry to state.db api_calls.scope_json. The fixture project
        has hook_text empty / title_candidates empty / no review → multiple
        incomplete tabs, so the decision should land as
        ``published_with_incomplete``."""
        import json as _json

        captured = {}

        def fake_record_api_call(**kwargs):
            captured.update(kwargs)

        monkeypatch.setattr("shared.state.record_api_call", fake_record_api_call)

        r = client.post(
            "/bridge/projects/肌酸的妙用/frontmatter",
            data={"field": "status", "value": "published", "tab": "publish"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert captured.get("agent") == "bridge"
        assert captured.get("model") == "(audit-publish-decision)"
        scope = _json.loads(captured["scope_json"])
        assert scope["decision"] == "published_with_incomplete"
        assert scope["project"] == "肌酸的妙用"
        # Sanity: at least one incomplete tab; should NOT include "publish" itself.
        assert len(scope["incomplete_tabs"]) >= 1
        assert "publish" not in scope["incomplete_tabs"]

    def test_publish_audit_skipped_for_non_publish_field(self, client, tmp_path, monkeypatch):
        """Other frontmatter updates must NOT trigger the audit log."""
        called = []
        monkeypatch.setattr("shared.state.record_api_call", lambda **kw: called.append(kw))
        client.post(
            "/bridge/projects/肌酸的妙用/frontmatter",
            data={"field": "one_sentence", "value": "新", "tab": "brief"},
            follow_redirects=False,
        )
        assert called == []

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

    def test_complete_deep_mode_writes_75min_block(self, client, tmp_path):
        # Unified dock 25/75 toggle: mode=deep → a 75-min span (≈3🍅 by duration).
        r = client.post(
            "/bridge/projects/肌酸的妙用/timer/complete",
            data={"task_name": "Pre-production", "mode": "deep"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        task_md = (tmp_path / "TaskNotes" / "Tasks" / "肌酸的妙用 - Pre-production.md").read_text(
            encoding="utf-8"
        )
        fm = yaml.safe_load(task_md.split("---")[1])
        entry = fm["timeEntries"][0]
        span_min = (
            datetime.fromisoformat(entry["endTime"]) - datetime.fromisoformat(entry["startTime"])
        ).total_seconds() / 60
        assert round(span_min) == 75  # 75-min super-focus block, not 25

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


class TestBriefTaskParity:
    """v3-H Slice 3: the Brief tab's 關聯任務 section renders the shared dashboard
    task_row (per-entry 排入 chips), scoped to the project, with from_project wired."""

    def test_brief_renders_task_rows_for_project(self, client):
        html = client.get("/bridge/projects/肌酸的妙用?tab=brief").text
        # the seeded "肌酸的妙用 - Pre-production" task renders as a unified wk-task row
        assert 'class="wk-task-d"' in html
        assert 'data-slug="肌酸的妙用 - Pre-production"' in html
        # the 排入 / chip forms carry from_project so actions land back on the project
        assert 'name="from_project" value="肌酸的妙用"' in html
        # the 新增關聯任務 form is present
        assert "新增關聯任務" in html

    def test_brief_excludes_other_projects_tasks(self, client, tmp_path):
        # a standalone (no-prefix, no projects) task must NOT appear under the project
        (tmp_path / "TaskNotes" / "Tasks" / "獨立任務.md").write_text(
            "---\ntitle: 獨立任務\nstatus: to-do\n預估🍅: 2\n---\n", encoding="utf-8"
        )
        html = client.get("/bridge/projects/肌酸的妙用?tab=brief").text
        assert 'data-slug="獨立任務"' not in html


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

    def test_creates_task_with_category(self, client, tmp_path):
        # v3-H: the Brief add-task form carries a category (default work); scheduled dropped.
        r = client.post(
            "/bridge/projects/肌酸的妙用/tasks/new",
            data={"name": "校色", "category": "growth"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        fm = yaml.safe_load(
            (tmp_path / "TaskNotes" / "Tasks" / "肌酸的妙用 - 校色.md")
            .read_text(encoding="utf-8")
            .split("---")[1]
        )
        assert fm["category"] == "growth"
        assert "scheduled" not in fm

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


class TestReviewDispatch:
    def test_storyteller_dispatch_appends_to_list(self, client, tmp_path, monkeypatch):
        import json

        from shared import project_reviews

        canned = json.dumps(
            {
                "score": 4,
                "summary": "Hook 抓得不錯，數字具體。",
                "suggestions": ["第二段加情境"],
            },
            ensure_ascii=False,
        )
        monkeypatch.setattr(project_reviews, "ask_claude", lambda *a, **kw: canned)

        r = client.post(
            "/bridge/projects/肌酸的妙用/review/storyteller",
            follow_redirects=False,
        )
        assert r.status_code == 303
        project_md = (tmp_path / "Projects" / "肌酸的妙用.md").read_text(encoding="utf-8")
        fm = yaml.safe_load(project_md.split("---")[1])
        # v2 list-shape
        assert isinstance(fm["reviews"]["storyteller"], list)
        assert len(fm["reviews"]["storyteller"]) == 1
        assert fm["reviews"]["storyteller"][0]["score"] == 4
        assert "Hook" in fm["reviews"]["storyteller"][0]["summary"]
        assert "prompt_version" in fm["reviews"]["storyteller"][0]

    def test_second_run_appends_preserving_first(self, client, tmp_path, monkeypatch):
        import json

        from shared import project_reviews

        responses = iter(
            [
                json.dumps(
                    {"score": 2, "summary": "first", "suggestions": []},
                    ensure_ascii=False,
                ),
                json.dumps(
                    {"score": 4, "summary": "second", "suggestions": []},
                    ensure_ascii=False,
                ),
            ]
        )
        monkeypatch.setattr(project_reviews, "ask_claude", lambda *a, **kw: next(responses))

        client.post("/bridge/projects/肌酸的妙用/review/storyteller", follow_redirects=False)
        client.post("/bridge/projects/肌酸的妙用/review/storyteller", follow_redirects=False)

        project_md = (tmp_path / "Projects" / "肌酸的妙用.md").read_text(encoding="utf-8")
        fm = yaml.safe_load(project_md.split("---")[1])
        assert len(fm["reviews"]["storyteller"]) == 2
        assert fm["reviews"]["storyteller"][0]["score"] == 2
        assert fm["reviews"]["storyteller"][1]["score"] == 4

    def test_coach_dispatch_reads_body(self, client, tmp_path, monkeypatch):
        import json

        from shared import project_reviews

        captured: dict = {}

        def _fake_ask(prompt, **kw):
            captured["prompt"] = prompt
            return json.dumps(
                {"score": 3, "summary": "script ok", "suggestions": []},
                ensure_ascii=False,
            )

        monkeypatch.setattr(project_reviews, "ask_claude", _fake_ask)

        r = client.post(
            "/bridge/projects/肌酸的妙用/review/coach",
            follow_redirects=False,
        )
        assert r.status_code == 303
        # Coach prompt should contain the project body (script body line from SAMPLE_PROJECT)
        assert "script body" in captured["prompt"]

    def test_invalid_persona_returns_400(self, client):
        r = client.post("/bridge/projects/肌酸的妙用/review/seo")
        assert r.status_code == 400

    def test_unknown_project_returns_404(self, client, monkeypatch):
        # Stub ask_claude so the dispatch doesn't try to call a real LLM
        # before the indexer lookup fails.
        from shared import project_reviews

        monkeypatch.setattr(project_reviews, "ask_claude", lambda *a, **kw: "{}")
        r = client.post("/bridge/projects/does-not-exist/review/storyteller")
        assert r.status_code == 404

    def test_storyteller_empty_hook_returns_422(self, client, tmp_path, monkeypatch):
        """Storyteller review on a project with empty hook_text is rejected at 422."""
        # Clear hook_text via frontmatter update
        client.post(
            "/bridge/projects/肌酸的妙用/frontmatter",
            data={"field": "hook_text", "value": "", "tab": "hook"},
            follow_redirects=False,
        )

        from shared import project_reviews

        monkeypatch.setattr(project_reviews, "ask_claude", lambda *a, **kw: "{}")
        r = client.post("/bridge/projects/肌酸的妙用/review/storyteller")
        assert r.status_code == 422
        assert "empty" in r.text.lower()


class TestResearchKeywordEndpoint:
    """ADR-031 PR3 — Zoro keyword research dispatch."""

    def _stub_zoro_result(self):
        return {
            "keywords": ["肌酸", "creatine", "肌酸補充"],
            "trend_gaps": ["長者族群應用尚未普及"],
            "youtube_titles": ["65 歲後吃肌酸"],
            "blog_titles": ["肌酸不只練肌肉"],
            "analysis_summary": "summary text",
            "trending_videos": [
                {"title": "Creatine 101", "channel": "YT-A", "views": 1000, "url": "https://yt/x"}
            ],
            "social_posts": [],
            "sources_used": ["youtube_zh", "trends_zh"],
            "sources_failed": [],
            "en_topic": "creatine",
            "usage": [],
        }

    def test_keyword_runs_and_writes_frontmatter(self, client, monkeypatch, tmp_path):
        import thousand_sunny.routers.bridge_projects as bp_module

        stub = self._stub_zoro_result()
        monkeypatch.setattr(bp_module, "research_keywords", lambda *a, **kw: stub)

        r = client.post("/bridge/projects/肌酸的妙用/research/keyword")
        assert r.status_code == 200
        assert "Zoro 關鍵字研究結果" in r.text
        assert "肌酸" in r.text

        proj = (tmp_path / "Projects" / "肌酸的妙用.md").read_text(encoding="utf-8")
        fm = yaml.safe_load(proj.split("---")[1])
        assert fm["keywords"] == ["肌酸", "creatine", "肌酸補充"]
        assert "keyword_research_at" in fm

        # Body marker section written
        assert "<!-- nakama:research:zoro:start -->" in proj
        assert "<!-- nakama:research:zoro:end -->" in proj
        assert "## 🗝 Zoro 關鍵字研究" in proj
        # Pre-existing body preserved
        assert "## 專案描述" in proj
        assert "## Script / Outline" in proj

    def test_keyword_idempotent_replace(self, client, monkeypatch, tmp_path):
        import thousand_sunny.routers.bridge_projects as bp_module

        stub = self._stub_zoro_result()
        monkeypatch.setattr(bp_module, "research_keywords", lambda *a, **kw: stub)
        client.post("/bridge/projects/肌酸的妙用/research/keyword")

        # Second run with different keywords
        monkeypatch.setattr(
            bp_module,
            "research_keywords",
            lambda *a, **kw: {**self._stub_zoro_result(), "keywords": ["new-kw"]},
        )
        client.post("/bridge/projects/肌酸的妙用/research/keyword")

        proj = (tmp_path / "Projects" / "肌酸的妙用.md").read_text(encoding="utf-8")
        # Only one marker pair survives
        assert proj.count("<!-- nakama:research:zoro:start -->") == 1
        fm = yaml.safe_load(proj.split("---")[1])
        assert fm["keywords"] == ["new-kw"]

    def test_keyword_400_when_search_topic_empty(self, client, monkeypatch, tmp_path):
        # Strip search_topic from the seed project
        proj_path = tmp_path / "Projects" / "肌酸的妙用.md"
        text = proj_path.read_text(encoding="utf-8")
        text = text.replace("search_topic: 肌酸", "search_topic: ''")
        proj_path.write_text(text, encoding="utf-8")

        r = client.post("/bridge/projects/肌酸的妙用/research/keyword")
        assert r.status_code == 400
        assert "search_topic" in r.text

    def test_keyword_404_when_project_missing(self, client):
        r = client.post("/bridge/projects/no-such/research/keyword")
        assert r.status_code == 404

    def test_keyword_500_on_zoro_failure(self, client, monkeypatch):
        import thousand_sunny.routers.bridge_projects as bp_module

        def _boom(*a, **kw):
            raise RuntimeError("data source dead")

        monkeypatch.setattr(bp_module, "research_keywords", _boom)
        r = client.post("/bridge/projects/肌酸的妙用/research/keyword")
        assert r.status_code == 500
        assert "Zoro 失敗" in r.text

    def test_keyword_dict_shape_persisted_structurally(self, client, monkeypatch, tmp_path):
        """Zoro returns list[dict]; frontmatter must keep the full structure
        (not stringify it via ``str(dict)``). ADR-031 PR3 regression guard."""
        import thousand_sunny.routers.bridge_projects as bp_module

        dict_kw_result = {
            "keywords": [
                {
                    "keyword": "肌酸 睡眠",
                    "keyword_en": "creatine sleep",
                    "search_volume": "medium",
                    "competition": "low",
                    "opportunity": "high",
                    "reason": "ZH 端內容空白",
                    "source": "en",
                },
                {
                    "keyword": "肌酸怎麼吃",
                    "keyword_en": "how to take creatine",
                    "search_volume": "high",
                    "competition": "medium",
                    "opportunity": "medium",
                    "reason": "搜尋建議第二名",
                    "source": "both",
                },
            ],
            "trend_gaps": [],
            "youtube_titles": ["title1"],
            "blog_titles": [],
            "analysis_summary": "",
            "trending_videos": [
                {
                    "title": "T",
                    "channel": "C",
                    "views": 100,
                    "url": "https://youtube.com/watch?v=ABCDEFGHIJK",
                }
            ],
            "social_posts": [],
            "sources_used": ["youtube_zh"],
            "sources_failed": [],
        }
        monkeypatch.setattr(bp_module, "research_keywords", lambda *a, **kw: dict_kw_result)

        r = client.post("/bridge/projects/肌酸的妙用/research/keyword")
        assert r.status_code == 200
        # HTML must render the structured table, not stringified dicts
        assert '<table class="pj-kw-table">' in r.text
        assert "肌酸 睡眠" in r.text
        assert "creatine sleep" in r.text
        # Iframe embed extracted from URL
        assert "youtube-nocookie.com/embed/ABCDEFGHIJK" in r.text

        # Frontmatter persists list-of-dicts, NOT list-of-stringified-dicts
        proj = (tmp_path / "Projects" / "肌酸的妙用.md").read_text(encoding="utf-8")
        fm = yaml.safe_load(proj.split("---")[1])
        assert isinstance(fm["keywords"], list)
        assert isinstance(fm["keywords"][0], dict)
        assert fm["keywords"][0]["keyword"] == "肌酸 睡眠"
        assert fm["keywords"][0]["search_volume"] == "medium"
        # Markdown table syntax landed in body
        assert "| 中文關鍵字 | EN | 搜尋量" in proj


class TestResearchKbEndpoint:
    """ADR-031 PR3 — Robin KB hits dispatch."""

    def _stub_hits(self):
        return [
            {
                "type": "source",
                "title": "肌酸效益",
                "path": "KB/Wiki/Sources/creatine-overview",
                "preview": "Creatine basics",
                "relevance_reason": "直接對應主題",
            },
            {
                "type": "concept",
                "title": "creatine monohydrate",
                "path": "KB/Wiki/Concepts/creatine-monohydrate",
                "preview": "化學結構與吸收",
                "relevance_reason": "補充形式",
            },
        ]

    def test_kb_uses_search_topic_when_no_keywords(self, client, monkeypatch, tmp_path):
        import thousand_sunny.routers.bridge_projects as bp_module

        captured = {}

        def _fake(query, vault, *, purpose, engine, top_k=8):
            captured["query"] = query
            captured["purpose"] = purpose
            return self._stub_hits()

        monkeypatch.setattr(bp_module, "search_kb", _fake)

        r = client.post("/bridge/projects/肌酸的妙用/research/kb")
        assert r.status_code == 200
        assert captured["query"] == "肌酸"  # search_topic value
        assert captured["purpose"] == "youtube"
        assert "Robin KB 命中" in r.text
        assert "肌酸效益" in r.text

        proj = (tmp_path / "Projects" / "肌酸的妙用.md").read_text(encoding="utf-8")
        assert "<!-- nakama:research:kb:start -->" in proj

    def test_kb_uses_keywords_when_populated(self, client, monkeypatch, tmp_path):
        # Inject keywords frontmatter
        proj_path = tmp_path / "Projects" / "肌酸的妙用.md"
        text = proj_path.read_text(encoding="utf-8")
        text = text.replace(
            "tags:\n  - project\n  - youtube",
            "keywords:\n  - 肌酸\n  - creatine\ntags:\n  - project\n  - youtube",
        )
        proj_path.write_text(text, encoding="utf-8")

        import thousand_sunny.routers.bridge_projects as bp_module

        captured = {}

        def _fake(query, vault, *, purpose, engine, top_k=8):
            captured["query"] = query
            return self._stub_hits()

        monkeypatch.setattr(bp_module, "search_kb", _fake)
        r = client.post("/bridge/projects/肌酸的妙用/research/kb")
        assert r.status_code == 200
        assert "肌酸" in captured["query"]
        assert "creatine" in captured["query"]

    def test_kb_400_when_no_query_source(self, client, monkeypatch, tmp_path):
        # Strip search_topic AND ensure no keywords frontmatter
        proj_path = tmp_path / "Projects" / "肌酸的妙用.md"
        text = proj_path.read_text(encoding="utf-8")
        text = text.replace("search_topic: 肌酸", "search_topic: ''")
        proj_path.write_text(text, encoding="utf-8")

        r = client.post("/bridge/projects/肌酸的妙用/research/kb")
        assert r.status_code == 400
        assert "keywords" in r.text or "search_topic" in r.text

    def test_kb_404_when_project_missing(self, client):
        r = client.post("/bridge/projects/no-such/research/kb")
        assert r.status_code == 404

    def test_kb_500_on_robin_failure(self, client, monkeypatch):
        import thousand_sunny.routers.bridge_projects as bp_module

        def _boom(*a, **kw):
            raise RuntimeError("KB index missing")

        monkeypatch.setattr(bp_module, "search_kb", _boom)
        r = client.post("/bridge/projects/肌酸的妙用/research/kb")
        assert r.status_code == 500
        assert "Robin 失敗" in r.text


class TestResearchSynthesisEndpoint:
    """ADR-031 PR3 Phase 1 — Robin KB synthesis dispatch."""

    def _seed_kb_cache(self, tmp_path):
        import json

        cache_dir = tmp_path / "research_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / "肌酸的妙用.kb.json").write_text(
            json.dumps(
                {
                    "hits": [
                        {
                            "type": "concept",
                            "title": "Creatine Supplementation",
                            "path": "KB/Wiki/Concepts/creatine supplementation",
                            "heading": "Core Principles",
                            "chunk_text": "20g/5 days loading...",
                            "relevance_tier": "strong",
                        },
                        {
                            "type": "source",
                            "title": "Sport Nutrition ch11",
                            "path": "KB/Wiki/Sources/sport-nutrition/ch11",
                            "chunk_text": "review questions...",
                            "relevance_tier": "weak",
                        },
                    ],
                    "timestamp": "2026-05-25T20:00:00+08:00",
                    "query_label": "肌酸",
                    "used_keywords": False,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def test_synthesis_happy_path(self, client, monkeypatch, tmp_path):
        self._seed_kb_cache(tmp_path)
        import thousand_sunny.routers.bridge_projects as bp_module

        captured = {}

        def _fake_synth(hits, brief, zoro_keywords):
            captured["hit_count"] = len(hits)
            captured["search_topic"] = brief.get("search_topic")
            return (
                "## 機制\n肌酸提升 PCr。\n\n## 證據\n20g/5 天 [[creatine supplementation]]。\n\n"
                "## 爭議\nKB 中目前缺長期 endpoint。\n\n## 角度\n- 負荷期 vs 慢速法\n"
            )

        monkeypatch.setattr(bp_module, "_synthesize_kb_hits", _fake_synth)

        r = client.post("/bridge/projects/肌酸的妙用/research/synthesis")
        assert r.status_code == 200, r.text
        assert captured["hit_count"] == 2
        assert captured["search_topic"] == "肌酸"
        assert "Robin 摘要" in r.text
        # Markdown should be rendered to HTML
        assert "<h2>" in r.text and "機制" in r.text
        # Vault write-back
        proj = (tmp_path / "Projects" / "肌酸的妙用.md").read_text(encoding="utf-8")
        assert "<!-- nakama:research:kb-synthesis:start -->" in proj
        assert "肌酸提升 PCr" in proj
        # Sidecar cache
        synth_cache = tmp_path / "research_cache" / "肌酸的妙用.synthesis.json"
        assert synth_cache.exists()

    def test_synthesis_400_when_no_kb_cache(self, client, tmp_path):
        # No KB cache seeded
        r = client.post("/bridge/projects/肌酸的妙用/research/synthesis")
        assert r.status_code == 400
        assert "KB" in r.text

    def test_synthesis_404_when_project_missing(self, client, tmp_path):
        r = client.post("/bridge/projects/no-such/research/synthesis")
        assert r.status_code == 404

    def test_synthesis_500_on_llm_failure(self, client, monkeypatch, tmp_path):
        self._seed_kb_cache(tmp_path)
        import thousand_sunny.routers.bridge_projects as bp_module

        def _boom(*a, **kw):
            raise RuntimeError("LLM provider down")

        monkeypatch.setattr(bp_module, "_synthesize_kb_hits", _boom)
        r = client.post("/bridge/projects/肌酸的妙用/research/synthesis")
        assert r.status_code == 500
        assert "Robin 摘要失敗" in r.text

    def test_synthesis_500_on_empty_llm_output(self, client, monkeypatch, tmp_path):
        self._seed_kb_cache(tmp_path)
        import thousand_sunny.routers.bridge_projects as bp_module

        monkeypatch.setattr(bp_module, "_synthesize_kb_hits", lambda *a, **kw: "   ")
        r = client.post("/bridge/projects/肌酸的妙用/research/synthesis")
        assert r.status_code == 500


class TestResearchDrPromptEndpoint:
    """ADR-031 PR3 Phase 2 — Deep Research prompt builder."""

    def test_dr_prompt_minimal_returns_json(self, client):
        r = client.post("/bridge/projects/肌酸的妙用/research/dr-prompt")
        assert r.status_code == 200
        data = r.json()
        assert "prompt" in data
        prompt = data["prompt"]
        assert "肌酸" in prompt
        assert "報告需求" in prompt
        assert "繁體中文" in prompt

    def test_dr_prompt_includes_zoro_keywords_when_cached(self, client, tmp_path):
        import json

        cache_dir = tmp_path / "research_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / "肌酸的妙用.zoro.json").write_text(
            json.dumps(
                {
                    "result": {
                        "keywords": [
                            {"keyword": "肌酸副作用", "keyword_en": "creatine side effects"},
                            {"keyword": "肌酸負荷期"},
                        ],
                        "trend_gaps": [
                            {"topic": "認知功能", "en_signal": "rising", "zh_status": "稀少"}
                        ],
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        r = client.post("/bridge/projects/肌酸的妙用/research/dr-prompt")
        assert r.status_code == 200
        prompt = r.json()["prompt"]
        assert "肌酸副作用" in prompt
        assert "creatine side effects" in prompt
        assert "認知功能" in prompt
        assert "趨勢落差" in prompt

    def test_dr_prompt_includes_synthesis_when_cached(self, client, tmp_path):
        import json

        cache_dir = tmp_path / "research_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / "肌酸的妙用.synthesis.json").write_text(
            json.dumps(
                {"synthesis_md": "## 機制\n肌酸提升 PCr", "timestamp": "x", "hits_used": 1},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        r = client.post("/bridge/projects/肌酸的妙用/research/dr-prompt")
        prompt = r.json()["prompt"]
        assert "肌酸提升 PCr" in prompt
        assert "我自己 KB 已經整理出的摘要" in prompt

    def test_dr_prompt_404_when_project_missing(self, client):
        r = client.post("/bridge/projects/no-such/research/dr-prompt")
        assert r.status_code == 404


class TestResearchDrReportEndpoint:
    """ADR-031 PR3 Phase 2 — Deep Research paste-back."""

    def test_dr_report_writes_marker_and_cache(self, client, tmp_path):
        report = "## 摘要\n肌酸對認知有正面效果\n\n## 證據\nRCT n=200..."
        r = client.post(
            "/bridge/projects/肌酸的妙用/research/dr-report",
            data={"report": report, "source": "chatgpt-deepresearch"},
        )
        assert r.status_code == 200, r.text
        assert "Deep Research 報告" in r.text
        assert "<h2>" in r.text and "摘要" in r.text

        proj = (tmp_path / "Projects" / "肌酸的妙用.md").read_text(encoding="utf-8")
        assert "<!-- nakama:research:dr:start -->" in proj
        assert "肌酸對認知有正面效果" in proj
        assert "chatgpt-deepresearch" in proj

        dr_cache = tmp_path / "research_cache" / "肌酸的妙用.dr.json"
        assert dr_cache.exists()

    def test_dr_report_400_when_empty(self, client):
        r = client.post(
            "/bridge/projects/肌酸的妙用/research/dr-report",
            data={"report": "   ", "source": "manual"},
        )
        assert r.status_code == 400

    def test_dr_report_sanitizes_source_label(self, client, tmp_path):
        r = client.post(
            "/bridge/projects/肌酸的妙用/research/dr-report",
            data={"report": "body", "source": "weird/path<script>;tag"},
        )
        assert r.status_code == 200
        proj = (tmp_path / "Projects" / "肌酸的妙用.md").read_text(encoding="utf-8")
        # < / > / ; / / should all be normalized to -
        assert "<script>" not in proj
        assert "weird-path-script-tag" in proj

    def test_dr_report_404_when_project_missing(self, client):
        r = client.post(
            "/bridge/projects/no-such/research/dr-report",
            data={"report": "body", "source": "manual"},
        )
        assert r.status_code == 404


class TestProjectsDetailHydratesNewCaches:
    """GET /bridge/projects/{slug} should hydrate synthesis + DR caches into the page."""

    def test_detail_hydrates_synthesis_html(self, client, tmp_path):
        import json

        cache_dir = tmp_path / "research_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / "肌酸的妙用.synthesis.json").write_text(
            json.dumps(
                {
                    "synthesis_md": "## 機制\n說明",
                    "timestamp": "2026-05-25T20:00:00+08:00",
                    "hits_used": 5,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        r = client.get("/bridge/projects/肌酸的妙用?tab=research")
        assert r.status_code == 200
        assert "Robin 摘要" in r.text
        assert "<h2>" in r.text and "機制" in r.text

    def test_detail_hydrates_dr_html(self, client, tmp_path):
        import json

        cache_dir = tmp_path / "research_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / "肌酸的妙用.dr.json").write_text(
            json.dumps(
                {
                    "report_md": "## 結論\n肌酸效果顯著",
                    "timestamp": "2026-05-25T20:00:00+08:00",
                    "source": "chatgpt-deepresearch",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        r = client.get("/bridge/projects/肌酸的妙用?tab=research")
        assert r.status_code == 200
        assert "Deep Research 報告" in r.text
        assert "肌酸效果顯著" in r.text
