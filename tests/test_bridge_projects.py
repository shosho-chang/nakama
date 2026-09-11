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

    def test_bracketed_name_rejected_with_guidance(self, client, tmp_path):
        """修修 2026-09-10: [Pod] 前綴曾寫出 [[[Pod] …]] 這種壞連結。"""
        r = client.post(
            "/bridge/projects/new", data={"name": "[Pod] 蘇予昕"}, follow_redirects=False
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/bridge/projects?err=bracket"
        assert not (tmp_path / "Projects").exists() or not list((tmp_path / "Projects").iterdir())
        html = client.get("/bridge/projects?err=bracket").text
        assert "全形" in html and "【Pod】" in html

    def test_fullwidth_brackets_accepted(self, client, tmp_path):
        r = client.post(
            "/bridge/projects/new", data={"name": "【Pod】蘇予昕"}, follow_redirects=False
        )
        assert r.status_code == 303
        assert (tmp_path / "Projects" / "【Pod】蘇予昕.md").is_file()

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
        listing = html.split('data-pane="list"', 1)[1].split("pjd-attach", 1)[0]
        assert "社群文章" in listing
        assert "舊任務" in listing
        # non-members stay out of the task list (they DO appear in the attach picker)
        assert "別的事" not in listing
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


# Codepoint order for these three is 後製與上架 < 節目錄製 < 訪綱撰寫 — the exact
# REVERSE of the template order, so an ordering test on them is meaningful (the
# vault scan sorts filenames).
TEMPLATE_YAML = """
templates:
  podcast:
    label: Podcast · 訪談集
    stages:
      - { name: 訪綱撰寫, pomodoros: 3 }
      - { name: 節目錄製, pomodoros: 4 }
      - { name: 後製與上架, pomodoros: 8 }
"""


@pytest.fixture
def tclient(monkeypatch, tmp_path):
    """Same harness as ``client`` plus a controlled project-templates.yaml."""
    cfg = tmp_path / "project-templates.yaml"
    cfg.write_text(TEMPLATE_YAML, encoding="utf-8")
    monkeypatch.setenv("NAKAMA_PROJECT_TEMPLATES", str(cfg))
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


class TestCreateWithTemplate:
    """修修 2026-09-10: 建立時選類型 → 樣板任務一次開好。"""

    def test_picker_lists_the_kinds(self, tclient):
        html = tclient.get("/bridge/projects").text
        assert 'name="kind"' in html
        assert 'value="podcast"' in html
        assert "Podcast · 訪談集" in html
        assert "空專案" in html  # the no-template option

    def test_creates_every_stage_task(self, tclient, tmp_path):
        r = tclient.post(
            "/bridge/projects/new",
            data={"name": "【Pod】蘇予昕", "kind": "podcast"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        tasks = tmp_path / "TaskNotes" / "Tasks"
        assert (tasks / "【Pod】蘇予昕 - 訪綱撰寫.md").is_file()
        assert (tasks / "【Pod】蘇予昕 - 節目錄製.md").is_file()
        fm = yaml.safe_load(
            (tmp_path / "Projects" / "【Pod】蘇予昕.md").read_text(encoding="utf-8").split("---")[1]
        )
        assert fm["kind"] == "podcast"

    def test_blank_kind_creates_no_tasks(self, tclient, tmp_path):
        tclient.post("/bridge/projects/new", data={"name": "自由專案", "kind": ""})
        assert not list((tmp_path / "TaskNotes" / "Tasks").glob("自由專案*"))

    def test_unknown_kind_errs_without_writing(self, tclient, tmp_path):
        r = tclient.post(
            "/bridge/projects/new", data={"name": "X", "kind": "nope"}, follow_redirects=False
        )
        assert r.headers["location"] == "/bridge/projects?err=template"
        assert not (tmp_path / "Projects" / "X.md").exists()

    def test_list_is_flat_and_in_template_order(self, tclient, tmp_path):
        """修修 2026-09-11:「已經重複了…我只需要乾淨的任務列表」— no per-stage
        headers, but the ORDER still follows the template (the vault scan is
        alphabetical, which would list 後製與上架 before 訪綱撰寫)."""
        tclient.post("/bridge/projects/new", data={"name": "P", "kind": "podcast"})
        html = tclient.get("/bridge/projects/P").text
        listing = html.split('data-pane="list"', 1)[1].split("pjd-attach", 1)[0]
        assert "pjd-group-t" not in listing  # no group headings at all
        assert "其他任務" not in listing
        assert listing.index("訪綱撰寫") < listing.index("節目錄製") < listing.index("後製與上架")

    def test_attached_tasks_sort_after_the_template_ones(self, tclient, tmp_path):
        tclient.post("/bridge/projects/new", data={"name": "P", "kind": "podcast"})
        _write_task(tmp_path, "散裝任務")
        tclient.post("/bridge/projects/P/attach", data={"task": ["散裝任務"]})
        listing = (tclient.get("/bridge/projects/P").text.split('data-pane="list"', 1)[1]).split(
            "pjd-attach", 1
        )[0]
        assert listing.index("後製與上架") < listing.index("散裝任務")

    def test_no_progress_rail_is_rendered(self, tclient, tmp_path):
        """修修 2026-09-11:「這一列我沒有跟你講說我要做」— the stage rail was my
        own addition to the mockup, never a requirement. Pinned so it does not
        creep back in."""
        tclient.post("/bridge/projects/new", data={"name": "P", "kind": "podcast"})
        html = tclient.get("/bridge/projects/P").text
        assert "pjd-rail" not in html
        assert "pjd-stage" not in html


class TestDashboardReadouts:
    """還剩多少 / 還差多久 — every figure computed on read."""

    def test_remaining_eta_and_unscheduled(self, tclient, tmp_path):
        _write_project(tmp_path, "P")
        _write_task(tmp_path, "P - 已排的", project="P", est=6)
        _write_task(tmp_path, "P - 沒排的", project="P", est=4)
        sched = tmp_path / "TaskNotes" / "Tasks" / "P - 已排的.md"
        sched.write_text(
            sched.read_text(encoding="utf-8").replace(
                "timeEntries: []", "plan:\n- {date: 2099-01-05, pomodoros: 6}\ntimeEntries: []"
            ),
            encoding="utf-8",
        )
        html = tclient.get("/bridge/projects/P").text
        stats = html.split('class="pjd-stats"', 1)[1].split("</div>\n    </div>", 1)[0]
        assert "10" in stats  # 6 + 4 remaining 🍅
        assert "01-05" in stats  # ETA = last planned day
        assert "未排時間" in stats

    def test_three_views_all_render(self, tclient, tmp_path):
        _write_project(tmp_path, "P")
        _write_task(tmp_path, "P - 任務", project="P")
        html = tclient.get("/bridge/projects/P").text
        for pane in ("list", "kanban", "sched"):
            assert f'data-pane="{pane}"' in html
        assert "待辦" in html and "進行中" in html and "完成" in html


class TestAttachTasks:
    """複選加入既有任務 — 走既有 reassign 路徑，不另造 bulk-only 邏輯。"""

    def test_attaches_multiple_including_one_owned_elsewhere(self, tclient, tmp_path):
        _write_project(tmp_path, "目標")
        _write_project(tmp_path, "別條線")
        _write_task(tmp_path, "散裝任務")
        _write_task(tmp_path, "別條線 - 借調任務", project="別條線")
        r = tclient.post(
            "/bridge/projects/目標/attach",
            data={"task": ["散裝任務", "別條線 - 借調任務"]},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert "saved=attached&n=2&failed=0" in r.headers["location"]
        tasks = tmp_path / "TaskNotes" / "Tasks"
        assert (tasks / "目標 - 散裝任務.md").is_file()
        assert (tasks / "目標 - 借調任務.md").is_file()
        fm = yaml.safe_load(
            (tasks / "目標 - 借調任務.md").read_text(encoding="utf-8").split("---")[1]
        )
        assert fm["projects"] == ["[[目標]]"]

    def test_no_selection_errs(self, tclient, tmp_path):
        _write_project(tmp_path, "目標")
        r = tclient.post("/bridge/projects/目標/attach", data={}, follow_redirects=False)
        assert r.headers["location"].endswith("?err=attach_none")

    def test_partial_failure_reports_both_counts(self, tclient, tmp_path):
        _write_project(tmp_path, "目標")
        _write_task(tmp_path, "真的有")
        r = tclient.post(
            "/bridge/projects/目標/attach",
            data={"task": ["真的有", "根本不存在"]},
            follow_redirects=False,
        )
        assert "saved=attached&n=1&failed=1" in r.headers["location"]
        assert (tmp_path / "TaskNotes" / "Tasks" / "目標 - 真的有.md").is_file()
        html = tclient.get("/bridge/projects/目標?saved=attached&n=1&failed=1").text
        assert "已加入 1 個任務" in html and "1 個失敗" in html

    def test_all_failed_is_never_reported_as_partial_success(self, tclient, tmp_path):
        """review 2026-09-10: ok=0 used to render 「部分成功…其餘已完成」."""
        _write_project(tmp_path, "目標")
        r = tclient.post(
            "/bridge/projects/目標/attach",
            data={"task": ["不存在A", "不存在B"]},
            follow_redirects=False,
        )
        assert "saved=attached&n=0&failed=2" in r.headers["location"]
        html = tclient.get("/bridge/projects/目標?saved=attached&n=0&failed=2").text
        assert "都沒有加入成功" in html
        assert "已加入" not in html
        assert "其餘已完成" not in html

    def test_picker_excludes_members_and_done_tasks(self, tclient, tmp_path):
        _write_project(tmp_path, "目標")
        _write_task(tmp_path, "目標 - 已是成員", project="目標")
        _write_task(tmp_path, "已完成的", done=True)
        _write_task(tmp_path, "可以加的")
        html = tclient.get("/bridge/projects/目標").text
        picker = html.split('class="pjd-attach-list"', 1)[1].split("</div>", 1)[0]
        assert "可以加的" in picker
        assert "已完成的" not in picker
        assert "已是成員" not in picker

    def test_missing_project_redirects(self, tclient, tmp_path):
        r = tclient.post(
            "/bridge/projects/不存在/attach",
            data={"task": ["x"]},
            follow_redirects=False,
        )
        assert r.headers["location"] == "/bridge/projects?err=missing"


class TestTaskCheckbox:
    """修修 2026-09-10：「我希望也跟在 Weekly Dashboard 那邊一樣，有一個可以 check
    掉的格子」。走的是 Weekly 同一個 set_task_done writer，不是專案頁專用的變體。"""

    def _fm(self, tmp_path, basename):
        raw = (tmp_path / "TaskNotes" / "Tasks" / f"{basename}.md").read_text(encoding="utf-8")
        return yaml.safe_load(raw.split("---", 2)[1])

    def test_tick_marks_done_and_mirrors_plan_slices(self, client, tmp_path):
        _write_project(tmp_path, "P")
        p = tmp_path / "TaskNotes" / "Tasks"
        p.mkdir(parents=True, exist_ok=True)
        (p / "P - 任務.md").write_text(
            '---\ntitle: P - 任務\nstatus: to-do\nprojects: ["[[P]]"]\n預估🍅: 2\n'
            "plan:\n- {date: 2026-09-12, pomodoros: 2}\ntimeEntries: []\n---\n",
            encoding="utf-8",
        )
        r = client.post(
            "/bridge/projects/P/task/P - 任務/done", data={"done": "1"}, follow_redirects=False
        )
        assert r.status_code == 303
        assert r.headers["location"].startswith("/bridge/projects/P")
        fm = self._fm(tmp_path, "P - 任務")
        assert fm["status"] == "done"
        # the whole-task checkbox finishes every day it was scheduled on
        assert fm["plan"][0]["done"] is True

    def test_untick_reopens_task_and_slices(self, client, tmp_path):
        _write_project(tmp_path, "P")
        p = tmp_path / "TaskNotes" / "Tasks"
        p.mkdir(parents=True, exist_ok=True)
        (p / "P - 任務.md").write_text(
            '---\ntitle: P - 任務\nstatus: done\n✅: true\nprojects: ["[[P]]"]\n預估🍅: 2\n'
            "plan:\n- {date: 2026-09-12, pomodoros: 2, done: true}\ntimeEntries: []\n---\n",
            encoding="utf-8",
        )
        client.post("/bridge/projects/P/task/P - 任務/done", data={"done": "0"})
        fm = self._fm(tmp_path, "P - 任務")
        assert fm["status"] == "to-do"
        assert not fm.get("✅")
        assert not fm["plan"][0].get("done")

    def test_missing_task_redirects_with_err(self, client, tmp_path):
        _write_project(tmp_path, "P")
        r = client.post(
            "/bridge/projects/P/task/不存在/done", data={"done": "1"}, follow_redirects=False
        )
        assert r.headers["location"] == "/bridge/projects/P?err=task"

    def test_box_is_a_sibling_form_never_nested_in_the_row_link(self, client, tmp_path):
        """A <button> inside an <a> is invalid HTML and the click would navigate
        instead of submitting."""
        _write_project(tmp_path, "P")
        _write_task(tmp_path, "P - 任務", project="P")
        html = client.get("/bridge/projects/P").text
        assert 'class="sho-box' in html
        assert "/task/P%20-%20%E4%BB%BB%E5%8B%99/done" in html
        # no anchor may contain a tick-box form
        for chunk in html.split("<a ")[1:]:
            assert "sho-box" not in chunk.split("</a>")[0]

    def test_uses_the_shared_box_not_a_page_local_copy(self, client, tmp_path):
        """修修 2026-09-10:「這裡你為什麼要重新發明一個？」— the tick box is
        bridge.css's .sho-box, the same one the Weekly dashboard renders."""
        _write_project(tmp_path, "P")
        _write_task(tmp_path, "P - 任務", project="P")
        html = client.get("/bridge/projects/P").text
        assert "pjd-box" not in html
        assert 'class="sho-box-form"' in html

    def test_pomodoro_readout_shows_actual_over_estimate(self, client, tmp_path):
        """修修: 剩餘工作要用預估🍅與實際🍅表示。"""
        _write_project(tmp_path, "P")
        _write_task(
            tmp_path,
            "P - 任務",
            project="P",
            est=4,
            entries=(
                '{startTime: "2026-08-25T09:00:00+08:00", endTime: "2026-08-25T09:50:00+08:00"}'
            ),
        )
        html = client.get("/bridge/projects/P").text
        stats = html.split('class="pjd-stats"', 1)[1].split('class="pjd-secbar"', 1)[0]
        assert "番茄 實際 / 預估" in stats
        assert "2" in stats and "/ 4" in stats  # actual 2 (50min//25) over est 4
        assert "剩 2" in stats  # remaining still shown, demoted to the sub-line
