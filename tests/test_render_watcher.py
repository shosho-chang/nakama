"""render_watcher 的狀態機（修修 2026-08-14：存配方 → 自動出圖，但同一份只出一次）。

真正的 render 很貴（Chrome + 字型 + mediapipe），這裡只測「決定要不要跑」那層：
- 新配方 → 待處理
- 同一個 requested_at 已處理 → 不再跑（連按五次存配方也只 render 一次）
- 改了配方（requested_at 變新）→ 再跑一次
- 沒有 render_request 的 cut → 完全不碰
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from scripts.render_watcher import (
    filter_render_requests,
    find_packaging_dir,
    load_state,
    main,
    pending_requests,
    render_one,
    save_state,
)


def _approval(requested_at: str | None, cut_id: str = "full") -> dict:
    entry: dict = {
        "cut_id": cut_id,
        "approved": False,
        "primary_package": 1,
        "reject_note": None,
        "decided_at": "2026-08-14T00:00:00+00:00",
    }
    if requested_at:
        entry["render_request"] = {
            "title_rank": 2,
            "host_cutout": "Attachments/cutouts/podcast/ep/host_v1_serious.png",
            "guest_cutout": "Attachments/cutouts/podcast/ep/guest_v1_serious.png",
            "big_text": ["每天封鎖", "十個帳號"],
            "highlight_text": "十個",
            "requested_at": requested_at,
            "rendered_png": None,
        }
    return {"episode": "ep-slug", "approvals": [entry]}


@pytest.fixture
def vault(tmp_path):
    d = tmp_path / "Attachments" / "packaging" / "20260721-zhengguowei"
    d.mkdir(parents=True)
    (d / "approval.json").write_text(
        json.dumps(_approval("2026-08-14T10:00:00+00:00"), ensure_ascii=False), encoding="utf-8"
    )
    return tmp_path


def test_new_request_is_pending(vault):
    jobs = pending_requests(vault, {})
    assert len(jobs) == 1
    assert jobs[0]["cut_id"] == "full"
    assert jobs[0]["key"] == "20260721-zhengguowei/full"


def test_same_request_is_not_rendered_twice(vault):
    state = {"20260721-zhengguowei/full": {"requested_at": "2026-08-14T10:00:00+00:00"}}
    assert pending_requests(vault, state) == []


def test_edited_request_is_pending_again(vault):
    state = {"20260721-zhengguowei/full": {"requested_at": "2026-08-14T10:00:00+00:00"}}
    path = vault / "Attachments" / "packaging" / "20260721-zhengguowei" / "approval.json"
    path.write_text(
        json.dumps(_approval("2026-08-14T11:30:00+00:00"), ensure_ascii=False), encoding="utf-8"
    )
    jobs = pending_requests(vault, state)
    assert len(jobs) == 1
    assert jobs[0]["req"]["requested_at"] == "2026-08-14T11:30:00+00:00"


def test_cut_without_request_is_ignored(vault):
    path = vault / "Attachments" / "packaging" / "20260721-zhengguowei" / "approval.json"
    path.write_text(json.dumps(_approval(None), ensure_ascii=False), encoding="utf-8")
    assert pending_requests(vault, {}) == []


def test_render_one_persists_running_before_subprocess_and_done_after(monkeypatch, tmp_path):
    state_path = tmp_path / "state.json"
    working = tmp_path / "working"
    working.mkdir()
    state: dict = {}
    requested_at = "2026-08-27T13:17:07+00:00"
    job = {
        "slug": "episode-slug",
        "episode": "episode name",
        "cut_id": "value-L01",
        "package_rank": 1,
        "key": "episode-slug/value-L01/r1",
        "req": {"requested_at": requested_at, "big_text": []},
    }

    monkeypatch.setattr(
        "scripts.render_watcher.find_packaging_dir", lambda *args, **kwargs: working
    )

    def fake_run(*args, **kwargs):
        live = json.loads(state_path.read_text(encoding="utf-8"))[job["key"]]
        assert live["requested_at"] == requested_at
        assert live["status"] == "running"
        assert live["started_at"]
        assert live["last_error"] is None
        return SimpleNamespace(returncode=0, stdout="rendered", stderr="")

    monkeypatch.setattr("scripts.render_watcher.subprocess.run", fake_run)

    assert render_one(job, state, state_path, None)
    terminal = json.loads(state_path.read_text(encoding="utf-8"))[job["key"]]
    assert terminal["status"] == "done"
    assert terminal["rendered_at"]
    assert terminal["last_error"] is None


def test_render_one_persists_failed_terminal_state(monkeypatch, tmp_path):
    state_path = tmp_path / "state.json"
    working = tmp_path / "working"
    working.mkdir()
    state: dict = {}
    job = {
        "slug": "episode-slug",
        "episode": "episode name",
        "cut_id": "value-L01",
        "package_rank": 2,
        "key": "episode-slug/value-L01/r2",
        "req": {"requested_at": "2026-08-27T13:18:00+00:00", "big_text": []},
    }
    monkeypatch.setattr(
        "scripts.render_watcher.find_packaging_dir", lambda *args, **kwargs: working
    )
    monkeypatch.setattr(
        "scripts.render_watcher.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=7, stdout="", stderr="render failed visibly"
        ),
    )

    assert not render_one(job, state, state_path, None)
    terminal = json.loads(state_path.read_text(encoding="utf-8"))[job["key"]]
    assert terminal["status"] == "failed"
    assert "render failed visibly" in terminal["last_error"]


def test_per_package_recipes_are_queued_independently(vault):
    root = vault / "Attachments" / "packaging" / "20260721-zhengguowei"
    packages = {
        "episode": "ep-slug",
        "cuts": [
            {
                "cut_id": "full",
                "packages": [
                    {"title_rank": 1, "render_recipe": None},
                    {
                        "title_rank": 3,
                        "render_recipe": {
                            "title_rank": 3,
                            "big_text": ["分工是昆蟲", "人要變通才"],
                            "requested_at": "2026-08-21T08:05:28+00:00",
                        },
                    },
                ],
            }
        ],
    }
    (root / "packages.json").write_text(json.dumps(packages), encoding="utf-8")

    jobs = pending_requests(vault, {})

    assert len(jobs) == 1
    assert jobs[0]["package_rank"] == 3
    assert jobs[0]["key"] == "20260721-zhengguowei/full/r3"


def test_render_request_filters_are_exact_and_default_to_all():
    jobs = [
        {
            "slug": "20260805-linzhichen",
            "cut_id": "full",
            "package_rank": 3,
            "key": "20260805-linzhichen/full/r3",
        },
        {
            "slug": "20260805-linzhichen",
            "cut_id": "value-L01",
            "package_rank": 1,
            "key": "20260805-linzhichen/value-L01/r1",
        },
        {
            "slug": "another-episode",
            "cut_id": "value-L01",
            "package_rank": 1,
            "key": "another-episode/value-L01/r1",
        },
    ]

    assert filter_render_requests(jobs) == jobs
    assert [
        job["key"]
        for job in filter_render_requests(
            jobs,
            episode_slug="20260805-linzhichen",
            cut_id="value-L01",
            package_rank=1,
        )
    ] == ["20260805-linzhichen/value-L01/r1"]
    assert (
        filter_render_requests(
            jobs,
            episode_slug="20260805-linzhichen",
            cut_id="value-L01",
            package_rank=2,
        )
        == []
    )


def _neutralise_watcher_preflight(monkeypatch, tmp_path) -> None:
    """讓 CLI 的環境前驗過關——這兩支測的是 job 分派，不是這台機器裝了什麼。

    watcher 啟動前會確認 render QA 用的套件與 composition 檔在，缺了就 return 1。
    那個前驗本身有它自己的意義（在 QA 那步才炸太晚），只是跟這裡要驗的事無關。
    """
    import sys
    from types import ModuleType

    for name in ("mediapipe",):
        if name not in sys.modules:
            monkeypatch.setitem(sys.modules, name, ModuleType(name))
    request_stub = tmp_path / "render_request.py"
    request_stub.write_text("", encoding="utf-8")
    monkeypatch.setattr("scripts.render_watcher.RENDER_REQUEST", request_stub)


def test_render_requests_only_cli_still_filters_render_requests(monkeypatch, tmp_path):
    calls: list[tuple[str, str]] = []
    render_jobs = [
        {
            "slug": "20260805-linzhichen",
            "cut_id": "full",
            "package_rank": 3,
            "key": "20260805-linzhichen/full/r3",
        },
        {
            "slug": "20260805-linzhichen",
            "cut_id": "value-L01",
            "package_rank": 1,
            "key": "20260805-linzhichen/value-L01/r1",
        },
    ]
    _neutralise_watcher_preflight(monkeypatch, tmp_path)
    monkeypatch.setattr("scripts.render_watcher.get_vault_path", lambda: tmp_path)
    monkeypatch.setattr("scripts.render_watcher.pending_requests", lambda vault, state: render_jobs)
    monkeypatch.setattr(
        "scripts.render_watcher.render_one",
        lambda job, state, state_path, log_path: calls.append(("render", job["key"])),
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "render_watcher.py",
            "--once",
            "--render-requests-only",
            "--episode-slug",
            "20260805-linzhichen",
            "--cut-id",
            "value-L01",
            "--package-rank",
            "1",
            "--log",
            str(tmp_path / "watcher.log"),
            "--state",
            str(tmp_path / "state.json"),
        ],
    )

    assert main() == 0
    assert calls == [("render", "20260805-linzhichen/value-L01/r1")]


def test_default_cli_only_runs_render_requests(monkeypatch, tmp_path):
    """本來是三類：2026-09-15 拿掉 Reject，2026-11-14 拿掉初始 packaging，只剩存配方 render。"""
    calls: list[str] = []
    _neutralise_watcher_preflight(monkeypatch, tmp_path)
    monkeypatch.setattr("scripts.render_watcher.get_vault_path", lambda: tmp_path)
    monkeypatch.setattr(
        "scripts.render_watcher.pending_requests",
        lambda vault, state: [
            {
                "slug": "episode",
                "cut_id": "value-L01",
                "package_rank": 1,
                "key": "render",
            }
        ],
    )
    monkeypatch.setattr(
        "scripts.render_watcher.render_one",
        lambda job, state, state_path, log_path: calls.append(job["key"]),
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "render_watcher.py",
            "--once",
            "--log",
            str(tmp_path / "watcher.log"),
            "--state",
            str(tmp_path / "state.json"),
        ],
    )

    assert main() == 0
    assert calls == ["render"]


def test_watcher_no_longer_knows_how_to_run_a_packaging_revision(tmp_path):
    """Reject 的後端一起退場：殘留的 revision_job 不會再被任何人撿走。

    gate 上已經沒有產生 revision job 的入口；如果 watcher 還留著 runner，舊檔裡
    那些 job 會在某次重啟後突然被執行，拿一份過期的 feedback 去重做整包封面。
    """
    import scripts.render_watcher as watcher

    for gone in (
        "pending_revision_jobs",
        "run_revision_job",
        "dispatch_revision_agent",
        "_update_revision_job",
    ):
        assert not hasattr(watcher, gone), gone


def test_watcher_no_longer_dispatches_a_packaging_agent():
    """修修 2026-11-14 裁決：packaging 交給當下對話中的 agent，不在背景另開供應商。

    以前 Highlight shortlist 一核准，watcher 就叫 Codex 在背景跑 title + thumbnail——
    比 podcast-pipeline 派工圖裡「第二次定稿之後才做 packaging」早，而且會跟對話中的
    agent 同時寫 packages.json。這些入口留著，某次重啟就會把 queued 的 cut 撿去跑。
    """
    import inspect

    import scripts.render_watcher as watcher

    for gone in (
        "pending_packaging_jobs",
        "run_packaging_job",
        "dispatch_packaging_agent",
        "_codex_command",
    ):
        assert not hasattr(watcher, gone), gone
    assert "codex" not in inspect.getsource(watcher).lower()


def test_broken_approval_json_does_not_crash_the_loop(vault):
    path = vault / "Attachments" / "packaging" / "20260721-zhengguowei" / "approval.json"
    path.write_text("{ 這不是 JSON", encoding="utf-8")
    assert pending_requests(vault, {}) == []  # 壞檔跳過，watcher 不倒


def test_state_round_trips(tmp_path):
    p = tmp_path / "state.json"
    assert load_state(p) == {}
    save_state(p, {"a/b": {"requested_at": "x"}})
    assert load_state(p)["a/b"]["requested_at"] == "x"


def test_state_survives_corruption(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("garbage", encoding="utf-8")
    assert load_state(p) == {}  # 壞掉就當空的重來，不是 crash


def test_find_packaging_dir_returns_none_when_absent():
    assert find_packaging_dir("no-such-episode-slug-xyz") is None


def test_find_packaging_dir_matches_episode_name_not_vault_slug(tmp_path, monkeypatch):
    footage = tmp_path / "Footages"
    packaging = footage / "20260805 林之晨" / "packaging"
    packaging.mkdir(parents=True)
    (packaging / "packages.json").write_text(
        json.dumps({"episode": "20260805 林之晨"}, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr("scripts.render_watcher.FOOTAGE_ROOTS", (footage,))

    assert find_packaging_dir("20260805-linzhichen", episode_name="20260805 林之晨") == packaging


def test_watcher_records_which_cuts_it_is_covering():
    """沒有心跳，Bridge 就分不出「排隊中」和「根本沒人在聽」。"""
    import scripts.render_watcher as watcher

    state = {}
    watcher.record_heartbeat(
        state,
        episode_slug="20260805-linzhichen",
        cut_id="value-L02",
        package_rank=None,
        now="2026-08-29T14:00:00+00:00",
    )

    row = state["_watchers"]["20260805-linzhichen/value-L02/r*"]
    assert row["cut_id"] == "value-L02"
    assert row["seen_at"] == "2026-08-29T14:00:00+00:00"
    assert row["pid"] > 0


def test_a_second_watcher_does_not_erase_the_first():
    import scripts.render_watcher as watcher

    state = {}
    for cut in ("value-L02", "punch-L04"):
        watcher.record_heartbeat(
            state,
            episode_slug="20260805-linzhichen",
            cut_id=cut,
            package_rank=None,
            now="2026-08-29T14:00:00+00:00",
        )

    assert len(state["_watchers"]) == 2


_CHILD_TIMEOUT_STDERR = (
    "Traceback (most recent call last):\n"
    '  File "render_request.py", line 101, in _run\n'
    "subprocess.TimeoutExpired: Command '[...]' timed out after 600 seconds"
)


def _timeout_job() -> dict:
    return {
        "slug": "ep",
        "cut_id": "punch-L02",
        "package_rank": 1,
        "episode": "ep",
        "key": "ep/punch-L02/r1",
        "req": {"requested_at": "t0", "big_text": []},
    }


def test_a_child_timeout_counts_even_though_the_watcher_never_sees_TimeoutExpired(
    monkeypatch, tmp_path
):
    """真實事故走的是 returncode 這條路，不是例外那條。

    600 秒的逾時不是 watcher 這一層丟的：`render_request.py` 自己用 `timeout=600` 跑
    `render_still.py`，而且沒有 try/except 包住它，所以 child 帶著 traceback 以 exit
    code 1 結束、watcher 的 `subprocess.run` **正常回傳**。只認 `TimeoutExpired` 的話，
    2026-09-17 蘇予昕 punch-L02 連五次逾時一次都不會被算到，畫面永遠說「再按一次」。
    """
    from scripts import render_watcher as rw

    state_path = tmp_path / "state.json"
    state: dict = {}
    now = datetime.now(timezone.utc).isoformat()
    rw.record_heartbeat(state, episode_slug=None, cut_id=None, package_rank=None, now=now)
    monkeypatch.setattr(rw, "find_packaging_dir", lambda *a, **k: tmp_path)
    monkeypatch.setattr(
        rw.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr=_CHILD_TIMEOUT_STDERR),
    )

    rw.render_one(_timeout_job(), state, state_path, None)

    assert rw._timeout_streak(state) == 1, "child 逾時沒被算到——只認例外是不夠的"


def test_the_streak_survives_the_heartbeat_at_the_top_of_each_loop(monkeypatch, tmp_path):
    """真實迴圈是 load_state → record_heartbeat → render。

    心跳每一圈都重寫那筆 row，如果它把剛累加的連號洗掉，計數就永遠停在 1、永遠不會
    達到 ≥2 的門檻。這條就是在守那個——在同一個 dict 上連呼叫 `render_one` 測不出來。
    """
    from scripts import render_watcher as rw

    state_path = tmp_path / "state.json"
    state: dict = {}
    monkeypatch.setattr(rw, "find_packaging_dir", lambda *a, **k: tmp_path)
    monkeypatch.setattr(
        rw.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr=_CHILD_TIMEOUT_STDERR),
    )

    for expected in (1, 2, 3):
        # 每一圈都重新讀檔、重寫心跳，跟 main() 一樣
        state = load_state(state_path) if state_path.is_file() else state
        rw.record_heartbeat(
            state,
            episode_slug=None,
            cut_id=None,
            package_rank=None,
            now=datetime.now(timezone.utc).isoformat(),
        )
        save_state(state_path, state)
        rw.render_one(_timeout_job(), state, state_path, None)
        assert rw._timeout_streak(state) == expected


def test_a_run_that_finishes_resets_the_streak(monkeypatch, tmp_path):
    """跑得完就不是卡住——成功失敗都算。"""
    from scripts import render_watcher as rw

    state_path = tmp_path / "state.json"
    state: dict = {}
    now = datetime.now(timezone.utc).isoformat()
    rw.record_heartbeat(state, episode_slug=None, cut_id=None, package_rank=None, now=now)
    monkeypatch.setattr(rw, "find_packaging_dir", lambda *a, **k: tmp_path)
    monkeypatch.setattr(
        rw.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr=_CHILD_TIMEOUT_STDERR),
    )
    rw.render_one(_timeout_job(), state, state_path, None)
    assert rw._timeout_streak(state) == 1

    monkeypatch.setattr(
        rw.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="FileNotFoundError: x"),
    )
    rw.render_one(_timeout_job(), state, state_path, None)

    assert rw._timeout_streak(state) == 0


def test_a_restarted_process_does_not_inherit_the_old_streak(monkeypatch, tmp_path):
    """重啟就是為了清掉這個狀態。

    身分不能用 pid：Windows 會回收 pid，而事故裡那支 watcher 活了十四小時。用
    `pid: 999999` 當測資永遠碰不到碰撞，所以測不到真正的風險——這裡改成讓新行程拿到
    **一模一樣的 pid**，只有 run_id 不同。
    """
    from scripts import render_watcher as rw

    now = datetime.now(timezone.utc).isoformat()
    state = {
        "_watchers": {
            "*/*/r*": {
                "episode_slug": None,
                "cut_id": None,
                "package_rank": None,
                "seen_at": now,
                "pid": os.getpid(),  # 同一個 pid——pid 回收後就是這個樣子
                "run_id": "a-dead-watcher",
                "consecutive_timeouts": 5,
                "last_timeout_at": now,
            }
        }
    }

    rw.record_heartbeat(state, episode_slug=None, cut_id=None, package_rank=None, now=now)

    assert rw._timeout_streak(state) == 0


def test_the_heartbeat_prunes_watchers_that_stopped_reporting(tmp_path):
    """死掉的 watcher 的 row 沒人刪，而 Bridge 讀的是所有 row 的最大值。

    重啟換 scope 會換 heartbeat key，舊 row 原地不動——不清掉的話，一支卡死的 watcher
    留下的連號會永遠掛在畫面上，照著「重啟它」做一百次也清不掉。
    """
    from scripts import render_watcher as rw

    long_gone = datetime.now(timezone.utc) - timedelta(seconds=rw.WATCHER_ROW_TTL_SEC + 60)
    stale = long_gone.isoformat()
    now = datetime.now(timezone.utc).isoformat()
    state = {
        "_watchers": {
            "ep/punch-L02/r1": {
                "episode_slug": "ep",
                "cut_id": "punch-L02",
                "package_rank": 1,
                "seen_at": stale,
                "pid": 4242,
                "run_id": "a-dead-watcher",
                "consecutive_timeouts": 5,
                "last_timeout_at": stale,
            }
        }
    }

    rw.record_heartbeat(state, episode_slug=None, cut_id=None, package_rank=None, now=now)

    assert list(state["_watchers"]) == ["*/*/r*"], "過期的 row 應該被掃掉"
    assert rw._timeout_streak(state) == 0


def test_the_bridge_still_reads_the_streak_after_a_render_longer_than_the_stale_window():
    """這是唯一一條把 watcher 端跟 Bridge 端接起來驗的測試，其他都只驗半邊。

    心跳寫在每一圈開頭，接著 render 可以卡滿 600 秒才逾時。Bridge 只信 120 秒內的
    心跳——所以「正在出事的那支 watcher」的心跳，在修修看到錯誤訊息的那一刻**必然**
    是過期的。少了 `record_render_outcome` 那行 seen_at，watcher 這邊數到 5，Bridge
    那邊每次都讀成 0，畫面照舊對他說「再按一次就會過」，按五次也還是那句。
    """
    from scripts import render_watcher as rw
    from thousand_sunny.routers import packaging as bridge

    started = datetime.now(timezone.utc) - timedelta(seconds=600)
    finished = datetime.now(timezone.utc)
    assert 600 > bridge._WATCHER_STALE_SEC, "前提：render 可以跑得比 Bridge 的新鮮度窗口久"

    state: dict = {}
    for _ in range(3):
        # 一圈 = 開頭寫心跳，然後 render 卡滿十分鐘才逾時。
        rw.record_heartbeat(
            state,
            episode_slug="ep",
            cut_id="punch-L02",
            package_rank=1,
            now=started.isoformat(),
        )
        rw.record_render_outcome(state, timed_out=True, now=finished.isoformat())

    assert rw._timeout_streak(state) == 3, "watcher 自己要數得對"
    assert bridge._timeout_streak(state) == 3, "Bridge 也要讀得到同一個數字"
    assert bridge._watcher_covering(state, "ep", "punch-L02", 1) is not None
    assert "連續第 3 次" in bridge._render_failure_sentence(
        "TimeoutExpired", timeout_streak=bridge._timeout_streak(state)
    )


def test_a_watcher_stuck_inside_a_long_render_does_not_get_pruned_by_another_watcher():
    """卡在 600 秒 render 裡的 watcher 是**活的**，只是不新鮮——這兩件事分不出來。

    prune 授權的是刪除，判錯會弄掉一支還在跑的 watcher 的連號，於是連號永遠回到 1、
    永遠湊不滿兩次、那句「重啟它」永遠不會出現。所以保留期不能跟 Bridge 那個「進度條
    該不該動」的 120 秒共用同一個數字——那邊判錯只是顯示問題，成本是零。
    """
    from scripts import render_watcher as rw

    mid_render = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
    now = datetime.now(timezone.utc).isoformat()
    state = {
        "_watchers": {
            "ep/punch-L02/r1": {
                "episode_slug": "ep",
                "cut_id": "punch-L02",
                "package_rank": 1,
                "seen_at": mid_render,
                "pid": 4242,
                "run_id": "still-rendering",
                "consecutive_timeouts": 1,
                "last_timeout_at": mid_render,
            }
        }
    }

    # 另一支 watcher 起來了，寫它自己的心跳——不可以順手把上面那支掃掉。
    rw.record_heartbeat(state, episode_slug=None, cut_id=None, package_rank=None, now=now)

    survivor = state["_watchers"].get("ep/punch-L02/r1")
    assert survivor is not None, "還在跑的 watcher 不該被掃掉"
    assert survivor["consecutive_timeouts"] == 1, "它累積的連號要原封不動留著"


def _packages_with_recipe(requested_at: str, cut_id: str = "punch-L02") -> dict:
    return {
        "episode": "20260901 蘇予昕",
        "cuts": [
            {
                "cut_id": cut_id,
                "format": "long",
                "packages": [
                    {
                        "title_rank": 1,
                        "thumbnail_png": f"Attachments/packaging/ep/pkg-{cut_id}-1.png",
                        "render_recipe": {
                            "title_rank": 1,
                            "big_text": [],
                            "requested_at": requested_at,
                            "rendered_png": None,
                        },
                    }
                ],
            }
        ],
    }


@pytest.fixture
def modern_episode(tmp_path):
    """既有 `package.render_recipe`、又留著舊 `approval.json` 的一集。"""
    d = tmp_path / "Attachments" / "packaging" / "20260901-suyuxin"
    d.mkdir(parents=True)
    stamp = "2026-09-17T00:24:23Z"
    (d / "packages.json").write_text(
        json.dumps(_packages_with_recipe(stamp), ensure_ascii=False), encoding="utf-8"
    )
    (d / "approval.json").write_text(
        json.dumps(_approval(stamp, cut_id="punch-L02"), ensure_ascii=False), encoding="utf-8"
    )
    return tmp_path, stamp


def test_a_failed_package_job_does_not_fall_through_to_the_legacy_path(modern_episode):
    """做失敗了 ≠ 沒有 render_recipe。

    舊的 `approval.json` 路徑是給「還沒有 package.render_recipe」的舊資料用的，判斷
    條件卻只看「這一輪有沒有待辦」——做完了和做失敗了都算沒有待辦。於是每按一次
    〈存配方〉，同一支 cut 會被 render 兩次：package 路徑一次、舊路徑用另一個 key
    （少了 `/r<n>`）再一次，各燒十分鐘。

    2026-09-17 蘇予昕 punch-L02 的 log：00:29:21 與 00:29:27 兩條就是同一次存配方。
    """
    vault_root, stamp = modern_episode
    state = {"20260901-suyuxin/punch-L02/r1": {"requested_at": stamp, "status": "failed"}}

    jobs = pending_requests(vault_root, state)

    assert jobs == [], "已經有 package.render_recipe 的一集不該再走舊路徑"


def test_a_pending_package_job_still_wins_over_the_legacy_path(modern_episode):
    vault_root, stamp = modern_episode

    jobs = pending_requests(vault_root, {})

    assert [job["key"] for job in jobs] == ["20260901-suyuxin/punch-L02/r1"]


def test_a_recipe_that_already_has_its_png_is_not_rendered_again(modern_episode):
    """attach 交付時配方就帶著 rendered_png——那張圖就是這份配方出的。

    2026-10-09 洪瀞集 full：attach 寫完配方，watcher 把 attach 的 requested_at 當成
    新配方，重解幾何、蓋掉剛交付的三張 PNG。gate 存配方一律不帶 rendered_png，
    所以「有成品」就是「不需要再出」。
    """
    vault_root, _ = modern_episode
    path = vault_root / "Attachments" / "packaging" / "20260901-suyuxin" / "packages.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    recipe = data["cuts"][0]["packages"][0]["render_recipe"]
    recipe["rendered_png"] = data["cuts"][0]["packages"][0]["thumbnail_png"]
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    assert pending_requests(vault_root, {}) == [], "已有成品的配方不該排進 render"


def test_an_episode_with_no_recipe_still_uses_the_legacy_path(vault):
    """真正的舊資料（只有 approval.json）照舊撿得到——這條 fallback 還有用。"""
    jobs = pending_requests(vault, {})

    assert [job["key"] for job in jobs] == ["20260721-zhengguowei/full"]


def test_a_cut_without_a_recipe_still_uses_the_legacy_path_in_a_mixed_episode(tmp_path):
    """同一集裡混著有 recipe 和沒 recipe 的 cut——判斷必須 per-cut，不能 per-episode。

    這個形狀不是歷史遺跡，`attach_packages.py` 現行行為就會產生：它整支
    `cut["packages"] = packages` 換掉，而 spec 缺 `render_spec` 時
    `_recipe_from_render_spec` 回 None。真實 vault 裡 20260805-linzhichen 的
    value-L02 現在就是三個 recipe 全 null、approval 還在。

    per-episode 的旗標會把整集的 fallback 關掉，那支 cut 的工作就永遠、無聲地消失。
    """
    d = tmp_path / "Attachments" / "packaging" / "20260805-linzhichen"
    d.mkdir(parents=True)
    stamp = "2026-09-17T00:24:23Z"
    packages = _packages_with_recipe(stamp, cut_id="value-L01")
    # 同一集的第二支 cut：packages 在、但 render_recipe 是 null
    packages["cuts"].append(
        {
            "cut_id": "value-L02",
            "format": "long",
            "packages": [{"title_rank": 1, "thumbnail_png": "x.png", "render_recipe": None}],
        }
    )
    (d / "packages.json").write_text(json.dumps(packages, ensure_ascii=False), encoding="utf-8")
    approval = _approval(stamp, cut_id="value-L01")
    approval["approvals"].append(_approval(stamp, cut_id="value-L02")["approvals"][0])
    (d / "approval.json").write_text(json.dumps(approval, ensure_ascii=False), encoding="utf-8")

    # value-L01 的 package 工作已經處理過（成功或失敗都一樣）
    state = {"20260805-linzhichen/value-L01/r1": {"requested_at": stamp, "status": "failed"}}

    keys = [job["key"] for job in pending_requests(tmp_path, state)]

    assert keys == ["20260805-linzhichen/value-L02"], (
        "有 recipe 的 cut 不該重複派工，沒有 recipe 的 cut 不該被一起關掉"
    )
