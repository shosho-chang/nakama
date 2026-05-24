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


class TestReviewStub:
    def test_pr1_review_returns_501(self, client):
        r = client.post("/bridge/projects/肌酸的妙用/review/storyteller")
        assert r.status_code == 501
        assert "PR2" in r.text

    def test_review_invalid_persona_400(self, client):
        r = client.post("/bridge/projects/肌酸的妙用/review/seo")
        assert r.status_code == 400
