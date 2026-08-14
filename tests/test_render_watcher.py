"""render_watcher 的狀態機（修修 2026-08-14：存配方 → 自動出圖，但同一份只出一次）。

真正的 render 很貴（Chrome + 字型 + mediapipe），這裡只測「決定要不要跑」那層：
- 新配方 → 待處理
- 同一個 requested_at 已處理 → 不再跑（連按五次存配方也只 render 一次）
- 改了配方（requested_at 變新）→ 再跑一次
- 沒有 render_request 的 cut → 完全不碰
"""

from __future__ import annotations

import json

import pytest

from scripts.render_watcher import find_packaging_dir, load_state, pending_requests, save_state


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
