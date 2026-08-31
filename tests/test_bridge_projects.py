"""Tests for thousand_sunny.routers.bridge_projects — ADR-068 戰線 surface.

Mirrors the bridge_weekly harness: no auth when WEB_PASSWORD/WEB_SECRET are
unset, VAULT_PATH → tmp_path, reload modules, drive with TestClient.
"""

from __future__ import annotations

import importlib

import pytest
import yaml
from fastapi.testclient import TestClient


def _write_project(tmp_path, name, status="active", body=""):
    d = tmp_path / "Projects"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(
        f"---\ntype: project\nstatus: {status}\ncreated: 2026-08-01T00:00:00Z\n---\n{body}",
        encoding="utf-8",
    )


def _write_task(tmp_path, basename, *, project=None, done=False, est=4, entries=""):
    d = tmp_path / "TaskNotes" / "Tasks"
    d.mkdir(parents=True, exist_ok=True)
    fm = [f"title: {basename}", f"status: {'done' if done else 'to-do'}", f"預估🍅: {est}"]
    if project:
        fm.append(f'projects: ["[[{project}]]"]')
    fm.append(f"timeEntries: [{entries}]")
    (d / f"{basename}.md").write_text("---\n" + "\n".join(fm) + "\n---\n", encoding="utf-8")


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_SECRET", raising=False)
    monkeypatch.setenv("DISABLE_ROBIN", "1")
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    monkeypatch.setenv("NAKAMA_DOC_INDEX_DB_PATH", str(tmp_path / "doc_index.db"))

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.bridge_projects as bp_module

    importlib.reload(auth_module)
    importlib.reload(bp_module)
    importlib.reload(app_module)
    return TestClient(app_module.app)


class TestIndex:
    def test_renders_active_with_live_rollup(self, client, tmp_path):
        _write_project(tmp_path, "自由艦隊")
        _write_task(tmp_path, "自由艦隊 - 社群文章", project="自由艦隊", est=5)
        _write_task(tmp_path, "自由艦隊 - 完成的事", project="自由艦隊", done=True, est=3)
        r = client.get("/bridge/projects")
        assert r.status_code == 200
        html = r.text
        assert "自由艦隊" in html
        assert "✓ 1" in html  # 1 done / 2 total
        assert "/2" in html
        assert "/8" in html  # est total 5+3

    def test_archived_collapsed_separately(self, client, tmp_path):
        _write_project(tmp_path, "現役戰線")
        _write_project(tmp_path, "舊戰線", status="archived")
        html = client.get("/bridge/projects").text
        assert "現役戰線" in html
        assert "已封存" in html
        assert "舊戰線" in html

    def test_non_project_files_ignored(self, client, tmp_path):
        (tmp_path / "Projects").mkdir()
        (tmp_path / "Projects" / "Brook 風格訓練.md").write_text(
            "---\ntype: agent-workspace\n---\n", encoding="utf-8"
        )
        html = client.get("/bridge/projects").text
        assert "Brook 風格訓練" not in html


class TestCreate:
    def test_create_redirects_to_detail_and_writes_stub(self, client, tmp_path):
        r = client.post("/bridge/projects/new", data={"name": "電子報"}, follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/bridge/projects/%E9%9B%BB%E5%AD%90%E5%A0%B1"
        raw = (tmp_path / "Projects" / "電子報.md").read_text(encoding="utf-8")
        fm = yaml.safe_load(raw.split("---", 2)[1])
        assert fm == {"type": "project", "status": "active", "created": fm["created"]}

    def test_invalid_name_errs(self, client, tmp_path):
        r = client.post("/bridge/projects/new", data={"name": "  "}, follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/bridge/projects?err=invalid"

    def test_duplicate_errs(self, client, tmp_path):
        _write_project(tmp_path, "電子報")
        r = client.post("/bridge/projects/new", data={"name": "電子報"}, follow_redirects=False)
        assert r.headers["location"] == "/bridge/projects?err=exists"


class TestDetail:
    def test_lists_members_fm_and_legacy_prefix(self, client, tmp_path):
        _write_project(tmp_path, "自由艦隊", body="戰線目標：百人社群。")
        _write_task(tmp_path, "自由艦隊 - 社群文章", project="自由艦隊")
        _write_task(tmp_path, "自由艦隊 - 舊任務")  # legacy: prefix only
        _write_task(tmp_path, "別的事")
        html = client.get("/bridge/projects/自由艦隊").text
        assert "社群文章" in html
        assert "舊任務" in html
        assert "別的事" not in html
        assert "百人社群" in html  # body notes rendered
        # task rows deep-link into the weekly task page
        assert "/bridge/weekly/task/" in html

    def test_missing_redirects_with_err(self, client, tmp_path):
        r = client.get("/bridge/projects/不存在", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/bridge/projects?err=missing"

    def test_actual_pomodoros_from_time_entries(self, client, tmp_path):
        _write_project(tmp_path, "自由艦隊")
        _write_task(
            tmp_path,
            "自由艦隊 - 社群文章",
            project="自由艦隊",
            est=4,
            entries=(
                '{startTime: "2026-08-25T09:00:00+08:00", endTime: "2026-08-25T09:50:00+08:00"}'
            ),
        )
        html = client.get("/bridge/projects/自由艦隊").text
        assert "🍅 2" in html  # 50 min // 25 = 2


class TestStatusToggle:
    def test_archive_then_restore(self, client, tmp_path):
        _write_project(tmp_path, "自由艦隊")
        r = client.post(
            "/bridge/projects/自由艦隊/status",
            data={"status": "archived"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        raw = (tmp_path / "Projects" / "自由艦隊.md").read_text(encoding="utf-8")
        assert "status: archived" in raw
        client.post("/bridge/projects/自由艦隊/status", data={"status": "active"})
        raw = (tmp_path / "Projects" / "自由艦隊.md").read_text(encoding="utf-8")
        assert "status: active" in raw

    def test_missing_project_errs(self, client, tmp_path):
        r = client.post(
            "/bridge/projects/不存在/status",
            data={"status": "archived"},
            follow_redirects=False,
        )
        assert r.headers["location"] == "/bridge/projects?err=missing"
