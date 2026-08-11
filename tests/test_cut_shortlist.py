"""選段 gate（run_cut_shortlist）——排名規則與 winners.json 寫入。

修修 2026-08-11 裁決：panel 排完停下來給他挑，不自動 top-3 進製作。
本測試鎖住三件會靜默出錯的事：中位數（不是平均）、同群組只有最高分佔排名、
--pick 的順序就是 rank。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_MOD_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_cut_shortlist.py"
_spec = importlib.util.spec_from_file_location("run_cut_shortlist", _MOD_PATH)
shortlist = importlib.util.module_from_spec(_spec)
sys.modules["run_cut_shortlist"] = shortlist
_spec.loader.exec_module(shortlist)


def _cand(cid: str, group: str, title: str) -> dict:
    return {
        "id": cid,
        "format": "long",
        "variant_group": group,
        "title": title,
        "hook": f"{cid} 的 hook",
        "duration_sec": 500.0,
    }


@pytest.fixture
def episode(tmp_path):
    hl = tmp_path / "highlights"
    hl.mkdir()
    (hl / "candidates.json").write_text(
        json.dumps(
            {
                "candidates": [
                    _cand("A1", "G1", "群組一 高分"),
                    _cand("A2", "G1", "群組一 低分"),
                    _cand("B1", "G2", "群組二"),
                    _cand("C1", "G3", "被否決的"),
                    {**_cand("S1", "G4", "短片不該出現"), "format": "short"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    # 中位數 vs 平均：A2 的平均 (60+90+91)/3 = 80.3，中位數 90 → 中位數規則下 A2 > B1
    totals = {
        "azhe": {"A1": 95, "A2": 60, "B1": 85, "C1": 70},
        "kevin": {"A1": 92, "A2": 90, "B1": 84, "C1": 70},
        "shufen": {"A1": 93, "A2": 91, "B1": 83, "C1": 70},
    }
    for who, rows in totals.items():
        (hl / f"review_{who}.json").write_text(
            json.dumps(
                {"persona": who, "scores": [{"id": i, "total": t} for i, t in rows.items()]},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    (hl / "lens_brand.json").write_text(
        json.dumps(
            {
                "lens": "brand",
                "findings": [
                    {
                        "id": "C1",
                        "severity": "veto",
                        "issue": "會害到來賓",
                        "mitigation": "改用別支",
                    },
                    {
                        "id": "B1",
                        "severity": "caution",
                        "issue": "標題不要停在某句",
                        "mitigation": "改過去式",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_median_not_mean(episode):
    rows = {r["id"]: r for r in shortlist.collect(episode / "highlights", "long")}
    assert rows["A2"]["median"] == 90  # 平均只有 80.3
    assert rows["A1"]["median"] == 93


def test_short_format_excluded(episode):
    ids = [r["id"] for r in shortlist.collect(episode / "highlights", "long")]
    assert "S1" not in ids


def test_group_dedup_only_top_gets_rank(episode):
    rows = {r["id"]: r for r in shortlist.collect(episode / "highlights", "long")}
    assert rows["A1"]["rank"] == 1  # 群組 G1 最高分
    assert rows["A2"]["rank"] is None  # 同群組落選，仍留在表上
    assert rows["A2"]["group_top"] is False
    assert rows["B1"]["rank"] == 2  # 排名跳過落選 variant
    assert rows["C1"]["rank"] == 3


def test_table_marks_veto_and_caution(episode):
    rows = shortlist.collect(episode / "highlights", "long")
    table = shortlist.render_table(rows, "long")
    assert "⛔ 否決" in table
    assert "⚠️ 注意" in table
    assert "會害到來賓" in table


def test_pick_order_is_rank(episode):
    hl = episode / "highlights"
    rows = shortlist.collect(hl, "long")
    shortlist.write_winners(hl, rows, ["B1", "A1"])
    data = json.loads((hl / "winners.json").read_text(encoding="utf-8"))
    assert [w["id"] for w in data["winners"]] == ["B1", "A1"]
    assert [w["rank"] for w in data["winners"]] == [1, 2]
    assert data["winners"][0]["score"] == 84
    assert data["picked_by"] == "修修 (gate)"
    assert [v["id"] for v in data["vetoed"]] == ["C1"]


def test_pick_unknown_id_fails_loud(episode):
    hl = episode / "highlights"
    rows = shortlist.collect(hl, "long")
    with pytest.raises(SystemExit):
        shortlist.write_winners(hl, rows, ["A1", "NOPE"])


def test_pick_keeps_existing_excluded_group(episode):
    hl = episode / "highlights"
    (hl / "winners.json").write_text(
        json.dumps({"winners": [], "excluded_group": [{"ids": ["X"], "reason": "blocker"}]}),
        encoding="utf-8",
    )
    rows = shortlist.collect(hl, "long")
    shortlist.write_winners(hl, rows, ["A1"])
    data = json.loads((hl / "winners.json").read_text(encoding="utf-8"))
    assert data["excluded_group"][0]["ids"] == ["X"]


def test_vetoed_pick_allowed_but_warned(episode, capsys):
    """修修可以覆蓋 brand-lens 否決，但不能靜默——stderr 要出現警告。"""
    hl = episode / "highlights"
    rows = shortlist.collect(hl, "long")
    shortlist.write_winners(hl, rows, ["C1"])
    assert "C1" in capsys.readouterr().err
