"""publish_prep 純函數測試（Resolve render 部分靠首跑 UAT 驗）。"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from publish_prep import cuts_to_prep, timeline_label  # noqa: E402

CANDS = [
    {"id": "punch-L5", "format": "long", "title": "腦腐對策：從睡眠到冥想"},
    {"id": "punch-S1", "format": "short", "title": "手機偷走的是耐心"},
]
WINNERS = [
    {"id": "punch-L5", "rank": 1, "score": 82},
    {"id": "punch-S1", "rank": 1, "score": 82},
]


def test_cuts_to_prep_all():
    cuts = cuts_to_prep(CANDS, WINNERS)
    assert [c["id"] for c in cuts] == ["punch-L5", "punch-S1"]
    assert cuts[0]["rank"] == 1


def test_cuts_to_prep_single():
    cuts = cuts_to_prep(CANDS, WINNERS, only="punch-S1")
    assert len(cuts) == 1
    assert cuts[0]["format"] == "short"


def test_cuts_to_prep_unknown_cut_fails_loud():
    """--cut 打錯 id 必須停下——不是默默出整集（嚴禁幻想紅線）。"""
    with pytest.raises(SystemExit):
        cuts_to_prep(CANDS, WINNERS, only="punch-L99")


def test_cuts_to_prep_winner_missing_candidate_fails_loud():
    with pytest.raises(SystemExit):
        cuts_to_prep(CANDS, WINNERS + [{"id": "ghost-1", "rank": 5}])


def test_timeline_label_matches_materialize_convention():
    """雙 id 陷阱：winner id ↔ timeline 顯示名的對應必須機器保證。"""
    assert (
        timeline_label(
            {"id": "punch-L5", "format": "long", "rank": 1, "title": "腦腐對策：從睡眠到冥想"}
        )
        == "長1 - 腦腐對策：從睡眠到冥想（緊·導播）"
    )
    assert (
        timeline_label(
            {"id": "punch-S1", "format": "short", "rank": 1, "title": "手機偷走的是耐心"}
        )
        == "短1 - 手機偷走的是耐心（緊·導播）"
    )
