# ruff: noqa: E501  — 錯誤訊息斷言含 CJK 長行。
"""packaging_manifest.py 測試（ADR-054 D14，issue #1072）。

Coverage:
- status 空目錄 → next=None
- mark 依序推進 + 冪等（重標不覆寫時間戳）
- 跳序 mark（titles 未完成標 thumbnails）→ SystemExit（停段不跳段）
- 模擬中斷：前 2 支完成、第 3 支 titles 完成 → status.next 指向第 3 支 thumbnails，
  已完成 stage 時間戳不變（產物不重生的帳本前提）
- manifest 壞損 → fail loud 不靜默重建
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "packaging_manifest.py"
spec = importlib.util.spec_from_file_location("packaging_manifest", _SCRIPT)
pm = importlib.util.module_from_spec(spec)
sys.modules["packaging_manifest"] = pm
spec.loader.exec_module(pm)


def test_status_empty_dir(tmp_path):
    out = pm.status(tmp_path)
    assert out == {"cuts": {}, "next": None}


def test_mark_progression_and_idempotency(tmp_path):
    first = pm.mark(tmp_path, "punch-L5", "titles")
    again = pm.mark(tmp_path, "punch-L5", "titles")
    assert first["at"] == again["at"]  # 冪等：重標不覆寫

    pm.mark(tmp_path, "punch-L5", "thumbnails")
    pm.mark(tmp_path, "punch-L5", "emitted")
    out = pm.status(tmp_path)
    assert out["cuts"]["punch-L5"]["done"] == ["titles", "thumbnails", "emitted"]
    assert out["next"] is None


def test_mark_out_of_order_fails_loud(tmp_path):
    with pytest.raises(SystemExit, match="前置 stage 未完成"):
        pm.mark(tmp_path, "punch-L5", "thumbnails")
    with pytest.raises(SystemExit, match="titles, thumbnails"):
        pm.mark(tmp_path, "story-L1", "emitted")


def test_interrupted_run_resumes_at_third_cut(tmp_path):
    """D14 acceptance：跑到第 3 支中斷 → 重跑從第 3 支續、前 2 支不重生。"""
    for cut in ("punch-L5", "story-L1"):
        for stage in ("titles", "thumbnails", "emitted"):
            pm.mark(tmp_path, cut, stage)
    pm.mark(tmp_path, "util-L4", "titles")  # 第 3 支跑到 titles 就掛

    before = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))

    out = pm.status(tmp_path)
    assert out["next"] == {"cut_id": "util-L4", "stage": "thumbnails"}
    assert out["cuts"]["punch-L5"]["pending"] == []
    assert out["cuts"]["story-L1"]["pending"] == []

    # 續跑：只補第 3 支 — 前兩支的時間戳完全不動
    pm.mark(tmp_path, "util-L4", "thumbnails")
    pm.mark(tmp_path, "util-L4", "emitted")
    after = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    for cut in ("punch-L5", "story-L1"):
        assert after["cuts"][cut] == before["cuts"][cut]
    assert pm.status(tmp_path)["next"] is None


def test_corrupted_manifest_fails_loud(tmp_path):
    (tmp_path / "manifest.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(SystemExit, match="不自動重建"):
        pm.status(tmp_path)
    with pytest.raises(SystemExit, match="不自動重建"):
        pm.mark(tmp_path, "x", "titles")


def test_unknown_stage_rejected(tmp_path):
    with pytest.raises(SystemExit, match="unknown stage"):
        pm.mark(tmp_path, "punch-L5", "render")


def test_stage_parallel_jobs_preserves_progress_and_is_idempotent(tmp_path):
    pm.mark(tmp_path, "full", "titles")
    pm.mark(tmp_path, "full", "thumbnails")
    pm.mark(tmp_path, "full", "emitted")
    jobs = [
        {
            "cut_id": f"value-L0{rank}",
            "rank": rank,
            "title": f"Long {rank}",
            "selected_at": "2026-08-27T01:00:00+00:00",
            "video": {"status": "queued"},
            "packaging": {"status": "queued"},
        }
        for rank in range(1, 4)
    ]

    first = pm.stage_parallel_jobs(tmp_path, jobs)
    first["cuts"]["value-L01"]["video"]["status"] = "running"
    first["cuts"]["value-L01"]["titles"] = "2026-08-27T02:00:00+00:00"
    pm._save(tmp_path, first)
    second = pm.stage_parallel_jobs(tmp_path, jobs)

    assert "emitted" in second["cuts"]["full"]
    assert second["cuts"]["value-L01"]["video"]["status"] == "running"
    assert second["cuts"]["value-L01"]["packaging"]["status"] == "running"
    assert [second["cuts"][f"value-L0{rank}"]["rank"] for rank in range(1, 4)] == [
        1,
        2,
        3,
    ]


def test_stage_parallel_jobs_rejects_duplicate_rank_without_mutating_manifest(tmp_path):
    original = {"cuts": {"full": {"emitted": "2026-08-27T01:00:00+00:00"}}}
    pm._save(tmp_path, original)
    jobs = [
        {
            "cut_id": cut_id,
            "rank": 1,
            "video": {"status": "queued"},
            "packaging": {"status": "queued"},
        }
        for cut_id in ("L1", "L2")
    ]

    with pytest.raises(ValueError, match="rank must be unique"):
        pm.stage_parallel_jobs(tmp_path, jobs)

    assert json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8")) == original
