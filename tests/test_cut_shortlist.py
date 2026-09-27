"""選段 gate（run_cut_shortlist）——排名規則與 winners.json 寫入。

修修 2026-08-11 裁決：panel 排完停下來給他挑，不自動 top-3 進製作。
本測試鎖住三件會靜默出錯的事：中位數（不是平均）、同群組只有最高分佔排名、
--pick 的順序就是 rank。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_MOD_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_cut_shortlist.py"
_spec = importlib.util.spec_from_file_location("run_cut_shortlist", _MOD_PATH)
shortlist = importlib.util.module_from_spec(_spec)
sys.modules["run_cut_shortlist"] = shortlist
_spec.loader.exec_module(shortlist)


def test_direct_script_help_bootstraps_repo_imports() -> None:
    result = subprocess.run(
        [sys.executable, str(_MOD_PATH), "--help"],
        cwd=_MOD_PATH.parents[1],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "選段 gate" in result.stdout


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
    candidates_path = hl / "candidates.json"
    candidates_path.write_text(
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
    source_sha256 = hashlib.sha256(candidates_path.read_bytes()).hexdigest()
    # 中位數 vs 平均：A2 的平均 (60+90+91)/3 = 80.3，中位數 90 → 中位數規則下 A2 > B1
    totals = {
        "azhe": {"A1": 95, "A2": 60, "B1": 85, "C1": 70},
        "kevin": {"A1": 92, "A2": 90, "B1": 84, "C1": 70},
        "shufen": {"A1": 93, "A2": 91, "B1": 83, "C1": 70},
    }
    for who, rows in totals.items():
        (hl / f"review_{who}.json").write_text(
            json.dumps(
                {
                    "persona": who,
                    "source_sha256": source_sha256,
                    "scores": [{"id": i, "total": t} for i, t in rows.items()],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    (hl / "lens_brand.json").write_text(
        json.dumps(
            {
                "lens": "brand",
                "source_sha256": source_sha256,
                "findings": [
                    {"id": "A1", "severity": "", "issue": "", "mitigation": ""},
                    {"id": "A2", "severity": "", "issue": "", "mitigation": ""},
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
    (hl / "lens_renee.json").write_text(
        json.dumps(
            {
                "lens": "renee",
                "source_sha256": source_sha256,
                "findings": [
                    {
                        "id": candidate_id,
                        "hook_risk": "",
                        "retention_risk": "",
                        "boundary_action": "keep",
                    }
                    for candidate_id in ("A1", "A2", "B1", "C1")
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


def test_winners_preserve_verified_projection_lineage(episode):
    hl = episode / "highlights"
    candidates_path = hl / "candidates.json"
    candidates = json.loads(candidates_path.read_text(encoding="utf-8"))
    candidates["subtitle_lineage"] = {
        "subtitle_mode": "verified-v2",
        "projection_id": "projection-123",
        "generation_id": "generation-123",
    }
    candidates_path.write_text(json.dumps(candidates), encoding="utf-8")
    source_sha256 = hashlib.sha256(candidates_path.read_bytes()).hexdigest()
    for name in (
        "review_azhe.json",
        "review_kevin.json",
        "review_shufen.json",
        "lens_brand.json",
        "lens_renee.json",
    ):
        path = hl / name
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["source_sha256"] = source_sha256
        path.write_text(json.dumps(payload), encoding="utf-8")

    rows = shortlist.collect(hl, "long")
    shortlist.write_winners(hl, rows, ["A1"])

    winners = json.loads((hl / "winners.json").read_text(encoding="utf-8"))
    assert winners["subtitle_lineage"] == candidates["subtitle_lineage"]


@pytest.mark.parametrize(
    "name",
    ["review_azhe.json", "review_kevin.json", "review_shufen.json", "lens_brand.json"],
)
def test_missing_required_review_fails_closed(episode, name):
    path = episode / "highlights" / name
    path.rename(path.with_suffix(".missing"))

    with pytest.raises(SystemExit, match="missing required highlight input"):
        shortlist.collect(episode / "highlights", "long")


def test_missing_renee_lens_fails_closed(episode):
    path = episode / "highlights" / "lens_renee.json"
    path.rename(path.with_suffix(".missing"))

    with pytest.raises(SystemExit, match="missing required highlight input"):
        shortlist.collect(episode / "highlights", "long")


def test_review_partial_coverage_fails_closed(episode):
    path = episode / "highlights" / "review_azhe.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["scores"] = []
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SystemExit, match="coverage drift"):
        shortlist.collect(episode / "highlights", "long")


def test_brand_partial_coverage_fails_closed(episode):
    path = episode / "highlights" / "lens_brand.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["findings"] = []
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SystemExit, match="coverage drift"):
        shortlist.collect(episode / "highlights", "long")


def test_stale_review_source_hash_fails_closed(episode):
    path = episode / "highlights" / "review_kevin.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["source_sha256"] = "0" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SystemExit, match="source_sha256"):
        shortlist.collect(episode / "highlights", "long")


def test_stale_renee_source_hash_fails_closed(episode):
    path = episode / "highlights" / "lens_renee.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["source_sha256"] = "0" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SystemExit, match="lens_renee.json source_sha256"):
        shortlist.collect(episode / "highlights", "long")


def test_renee_partial_coverage_fails_closed(episode):
    path = episode / "highlights" / "lens_renee.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["findings"] = payload["findings"][:-1]
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SystemExit, match="lens_renee.json candidate coverage drift"):
        shortlist.collect(episode / "highlights", "long")


def test_renee_extra_candidate_fails_closed(episode):
    path = episode / "highlights" / "lens_renee.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["findings"].append(
        {
            "id": "EXTRA",
            "hook_risk": "",
            "retention_risk": "",
            "boundary_action": "keep",
        }
    )
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SystemExit, match=r"extra=\['EXTRA'\]"):
        shortlist.collect(episode / "highlights", "long")


def test_renee_duplicate_candidate_fails_closed(episode):
    path = episode / "highlights" / "lens_renee.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["findings"].append(payload["findings"][0])
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SystemExit, match="duplicate id: A1"):
        shortlist.collect(episode / "highlights", "long")


def test_renee_non_string_finding_field_fails_closed(episode):
    path = episode / "highlights" / "lens_renee.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["findings"][0]["retention_risk"] = None
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SystemExit, match="retention_risk must be a string"):
        shortlist.collect(episode / "highlights", "long")
