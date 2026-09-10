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


def test_winners_preserve_editorial_master_lineage(episode):
    hl = episode / "highlights"
    candidates_path = hl / "candidates.json"
    candidates = json.loads(candidates_path.read_text(encoding="utf-8"))
    candidates["editorial_master_lineage"] = {
        "contract": "podcast-editorial-master-v1",
        "episode_id": episode.name,
        "content_hash": "1" * 64,
        "master_media_sha256": "2" * 64,
        "master_srt_sha256": "3" * 64,
        "editorial_master_receipt": "editorial-master/v1/EDITORIAL-MASTER.json",
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
    assert winners["editorial_master_lineage"] == candidates["editorial_master_lineage"]


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


# --- 長短片分流（ADR-067）---------------------------------------------------
# gate 一直只寫 winners.json，不分格式；而短片線 (`run_shortform_director.py`)
# 讀的是 winners.short.json。挑短片會蓋掉長片的當選名單，而且短片線照樣沒有輸入。


def _short_panel(hl: Path, ids: tuple[str, ...]) -> None:
    """替這些短片候選補上分格式的盲審檔，綁 format digest。"""
    from shared.highlight_shortlist import _format_digest

    candidates = json.loads((hl / "candidates.json").read_text(encoding="utf-8"))["candidates"]
    digest = _format_digest(candidates, "short")
    for who in ("azhe", "kevin", "shufen"):
        (hl / f"review_{who}.short.json").write_text(
            json.dumps(
                {
                    "persona": who,
                    "source_sha256": digest,
                    "scores": [{"id": i, "total": 80} for i in ids],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    (hl / "lens_brand.short.json").write_text(
        json.dumps(
            {
                "lens": "brand",
                "source_sha256": digest,
                "findings": [
                    {"id": i, "severity": "", "issue": "", "mitigation": ""} for i in ids
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (hl / "lens_renee.short.json").write_text(
        json.dumps(
            {
                "lens": "renee",
                "source_sha256": digest,
                "findings": [
                    {
                        "id": i,
                        "hook_risk": "",
                        "retention_risk": "",
                        "boundary_action": "keep",
                    }
                    for i in ids
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_short_pick_writes_its_own_file_and_leaves_long_winners_alone(episode):
    hl = episode / "highlights"
    _short_panel(hl, ("S1",))

    long_rows = shortlist.collect(hl, "long")
    shortlist.write_winners(hl, long_rows, ["A1", "B1"], fmt="long")
    long_before = (hl / "winners.json").read_bytes()

    short_rows = shortlist.collect(hl, "short")
    out = shortlist.write_winners(hl, short_rows, ["S1"], fmt="short")

    assert out.name == "winners.short.json"
    assert [w["id"] for w in json.loads(out.read_text(encoding="utf-8"))["winners"]] == ["S1"]
    # 長片那份一個 byte 都不能動——L2/L3 的成品線靠它。
    assert (hl / "winners.json").read_bytes() == long_before


def test_long_boundary_polish_does_not_invalidate_the_short_panel(episode):
    """Step 2.5 動長片邊界，短片盤子不該跟著翻。

    這正是 20260901 蘇予昕 卡住的原因：整檔 hash 把兩條線綁在一起，改一支長片
    的 t_start，38 支沒被碰過的短片連同 panel 一起作廢。
    """
    hl = episode / "highlights"
    _short_panel(hl, ("S1",))
    assert [r["id"] for r in shortlist.collect(hl, "short")] == ["S1"]

    doc = json.loads((hl / "candidates.json").read_text(encoding="utf-8"))
    for candidate in doc["candidates"]:
        if candidate["id"] == "A1":
            candidate["t_start"] = 12.5
            candidate["duration_sec"] = 487.5
    (hl / "candidates.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    assert [r["id"] for r in shortlist.collect(hl, "short")] == ["S1"]
    # 長片自己那份仍然綁整檔，所以照樣會擋下來——不是把驗證放掉。
    with pytest.raises(SystemExit, match="source_sha256"):
        shortlist.collect(hl, "long")


def test_short_panel_still_has_to_cover_every_short_candidate(episode):
    hl = episode / "highlights"
    doc = json.loads((hl / "candidates.json").read_text(encoding="utf-8"))
    doc["candidates"].append(
        {**_cand("S2", "G5", "第二支短片"), "format": "short", "duration_sec": 80.0}
    )
    (hl / "candidates.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    _short_panel(hl, ("S1",))

    with pytest.raises(SystemExit, match=r"review_azhe.short.json.*S2"):
        shortlist.collect(hl, "short")


def test_shorts_do_not_need_the_renee_lens(episode):
    """Renee 只審長片——她的 persona 檔與 SKILL 的 reviewer 表都這樣寫。

    gate 本來不分格式一律 required，等於要一份設計上不存在的檔；而且沒有 scoped
    檔時會退回長片那份，把「這個格式沒有 Renee」報成「短片全缺」。
    """
    hl = episode / "highlights"
    _short_panel(hl, ("S1",))
    (hl / "lens_renee.short.json").unlink()

    assert [r["id"] for r in shortlist.collect(hl, "short")] == ["S1"]
    # 長片那份還在，而且照樣是必要的。
    assert (hl / "lens_renee.json").is_file()


def test_long_still_requires_the_renee_lens(episode):
    hl = episode / "highlights"
    (hl / "lens_renee.json").unlink()
    with pytest.raises(SystemExit, match="lens_renee"):
        shortlist.collect(hl, "long")


def test_a_supplied_short_renee_lens_is_still_validated(episode):
    """可以不給；給了就不能是壞的。"""
    hl = episode / "highlights"
    _short_panel(hl, ("S1",))
    (hl / "lens_renee.short.json").write_text(
        json.dumps({"lens": "renee", "source_sha256": "deadbeef", "findings": []}),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="source_sha256"):
        shortlist.collect(hl, "short")


# --- 選段報告寫進 Vault -------------------------------------------------------
# `highlights/` 是 footage 磁碟上的工作目錄，下一季開工時沒有人會去翻它。報告合
# 併長短片，因為挑選時本來就要一起看。


def test_report_merges_both_formats_and_records_the_picks(episode):
    hl = episode / "highlights"
    _short_panel(hl, ("S1",))
    shortlist.write_winners(hl, shortlist.collect(hl, "long"), ["A1", "B1"], "long")

    report = shortlist.render_vault_report(
        "20260901 蘇予昕",
        hl,
        {"long": shortlist.collect(hl, "long"), "short": shortlist.collect(hl, "short")},
    )
    assert "## 長精華（format=long）" in report
    assert "## 短影片（format=short）" in report
    assert "已挑定：A1、B1" in report
    assert "（尚未挑）" in report  # 短片還沒挑
    # 落選的候選也要在，那才是下一季的參考值。
    assert "群組一 低分" in report
    assert "短片不該出現" in report
    # 細節掛在該格式底下，不跟它平輩。
    assert "### 各支 hook 與品牌 lens 細節" in report
    assert "\n## 各支 hook" not in report


def test_report_prints_a_stale_panel_instead_of_refusing(episode):
    """gate 拒收過期綁定是對的；報告是唯讀歷史，拒印才是過嚴。"""
    hl = episode / "highlights"
    payload = json.loads((hl / "review_azhe.json").read_text(encoding="utf-8"))
    payload["source_sha256"] = "0" * 64
    (hl / "review_azhe.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SystemExit, match="source_sha256"):
        shortlist.collect(hl, "long")

    rows, note = shortlist.collect_for_report(hl, "long")
    assert [r["id"] for r in rows] == ["A1", "A2", "B1", "C1"]
    assert "panel 綁定已過期" in note
    report = shortlist.render_vault_report("ep", hl, {"long": rows}, {"long": note})
    assert "⚠️ panel 綁定已過期" in report


def test_report_names_the_missing_format_instead_of_leaving_a_blank(episode):
    hl = episode / "highlights"
    rows, note = shortlist.collect_for_report(hl, "short")
    assert rows == []
    assert "讀不到" in note
    report = shortlist.render_vault_report("ep", hl, {"short": rows}, {"short": note})
    assert "這一節沒有內容" in report


def test_report_lands_in_the_guest_interview_folder(episode, monkeypatch, tmp_path):
    vault = tmp_path / "vault"
    (vault / "AgentOutputs" / "interviews" / "2026-08-31-蘇予昕").mkdir(parents=True)
    (vault / "AgentOutputs" / "interviews" / "2026-08-31-蘇予昕" / "06-x.md").write_text(
        "x", encoding="utf-8"
    )
    monkeypatch.setenv("VAULT_PATH", str(vault))
    _short_panel(hl := episode / "highlights", ("S1",))
    assert hl.is_dir()

    target = episode / "20260901 蘇予昕"
    target.mkdir()
    (episode / "highlights").rename(target / "highlights")

    written = shortlist.write_vault_report(target)
    assert written is not None
    assert written.name == "07-選段報告.md"
    assert "選段報告" in written.read_text(encoding="utf-8")


def test_an_unreachable_vault_warns_but_does_not_kill_the_run(episode, monkeypatch, capsys):
    vault = episode / "no-such-vault"
    monkeypatch.setenv("VAULT_PATH", str(vault))
    target = episode / "20260901 蘇予昕"
    target.mkdir()
    (episode / "highlights").rename(target / "highlights")

    assert shortlist.write_vault_report(target) is None
    assert "選段報告沒寫進 Vault" in capsys.readouterr().err
