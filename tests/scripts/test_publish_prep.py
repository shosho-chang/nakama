from __future__ import annotations

import json

from scripts.publish_prep import _covers_full_transcript


def test_mode_b_short_does_not_burn_subtitles(tmp_path):
    """mode B 的字卡已經承接全部逐字稿，再燒一層字幕就是同一句話出現兩次。"""
    tighten = tmp_path / "highlights/tighten"
    tighten.mkdir(parents=True)
    (tighten / "punch-S02_titles.json").write_text(
        json.dumps({"covers_full_transcript": True, "titles": []}), encoding="utf-8"
    )
    (tighten / "punch-S09_titles.json").write_text(
        json.dumps({"covers_full_transcript": False, "titles": []}), encoding="utf-8"
    )
    assert _covers_full_transcript(tmp_path, "punch-S02") is True
    # 沒宣告、企劃壞掉、或根本沒有字卡企劃 → 走燒字幕的舊路，不要安靜改行為
    assert _covers_full_transcript(tmp_path, "punch-S09") is False
    assert _covers_full_transcript(tmp_path, "punch-S99") is False
    (tighten / "punch-S98_titles.json").write_text("{ not json", encoding="utf-8")
    assert _covers_full_transcript(tmp_path, "punch-S98") is False


def test_load_plan_merges_format_split_winners(tmp_path):
    """短片在 winners.short.json——只讀 winners.json 的話發布線看不到它。"""
    from scripts.publish_prep import _load_plan

    hdir = tmp_path / "highlights"
    hdir.mkdir()
    (hdir / "candidates.json").write_text(
        json.dumps({"candidates": [{"id": "value-L01"}, {"id": "punch-S07"}]}), encoding="utf-8"
    )
    (hdir / "winners.json").write_text(
        json.dumps({"winners": [{"id": "value-L01", "rank": 1}]}), encoding="utf-8"
    )
    (hdir / "winners.short.json").write_text(
        json.dumps({"winners": [{"id": "punch-S07", "rank": 3}]}), encoding="utf-8"
    )
    # parked 是被擱置的名單，不是當選名單，不可以被收進來
    (hdir / "winners.short.parked.json").write_text(
        json.dumps({"winners": [{"id": "punch-S99", "rank": 9}]}), encoding="utf-8"
    )
    _cands, winners = _load_plan(tmp_path)
    assert sorted(w["id"] for w in winners) == ["punch-S07", "value-L01"]


def test_load_plan_prefers_per_format_entry(tmp_path):
    """同一個 id 兩邊都有時，per-format 檔是該 format 自己的權威。"""
    from scripts.publish_prep import _load_plan

    hdir = tmp_path / "highlights"
    hdir.mkdir()
    (hdir / "candidates.json").write_text(json.dumps({"candidates": []}), encoding="utf-8")
    (hdir / "winners.json").write_text(
        json.dumps({"winners": [{"id": "punch-S07", "rank": 1}]}), encoding="utf-8"
    )
    (hdir / "winners.short.json").write_text(
        json.dumps({"winners": [{"id": "punch-S07", "rank": 3}]}), encoding="utf-8"
    )
    _cands, winners = _load_plan(tmp_path)
    assert [w["rank"] for w in winners] == [3]
