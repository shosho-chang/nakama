"""publish_prep 純函數測試（Resolve render 部分靠首跑 UAT 驗）。"""

import json
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


class _FakeTimeline:
    def __init__(self, name: str, frames: int) -> None:
        self._name = name
        self._frames = frames

    def GetName(self) -> str:  # noqa: N802 - Resolve API casing
        return self._name

    def GetSetting(self, key: str) -> str:  # noqa: N802
        return "30.0" if key == "timelineFrameRate" else ""

    def GetStartFrame(self) -> int:  # noqa: N802
        return 0

    def GetEndFrame(self) -> int:  # noqa: N802
        return self._frames - 1


class _FakeProject:
    def __init__(self, *timelines: _FakeTimeline) -> None:
        self._timelines = timelines

    def GetTimelineCount(self) -> int:  # noqa: N802
        return len(self._timelines)

    def GetTimelineByIndex(self, index: int) -> _FakeTimeline:  # noqa: N802
        return self._timelines[index - 1]


def _episode_with_map(tmp_path: Path, plan_id: str | None) -> Path:
    episode_dir = tmp_path / "20260901 蘇予昕"
    (episode_dir / "highlights").mkdir(parents=True)
    (episode_dir / "highlights" / "publish-timelines.v1.json").write_text(
        json.dumps(
            {
                "schema": "nakama.publish_timelines.v1",
                "episode": episode_dir.name,
                "cuts": {
                    "punch-L03": {
                        "timeline": "長2-final",
                        "plan_id": plan_id,
                        "release_cut_id": "punch-L03",
                        "expected_duration_sec": 490.304,
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return episode_dir


def test_pick_timeline_carries_the_plan_id_not_the_cut_id(tmp_path):
    """成品身分是 plan record 的 id——不是 cut id，也不是 DB 的 release 列 id。

    ADR-069 把身分欄位從 `release_id` 改名成 `plan_id`，`_pick_timeline` 漏改，
    於是它讀 `target.release_id` 直接 AttributeError：**任何有 timeline 對應表的
    集數，publish_prep 一跑就掛**（2026-09-16 蘇予昕長2 實際踩到，當時正要把重出的
    成品登錄回 Release）。

    而且就算只補屬性名，receipt 那一格原本也叫 `release_id`，會被下游
    `register_release` 回傳的 DB 列 id（整數）蓋掉——`export_matches_plan_record`
    拿整數去比 plan id 永遠不相等，症狀會從「當場掛掉」變成「每次核准都重 render
    一次」。所以欄位名也一起改掉，跟 DB 列 id 分開。
    """
    from publish_prep import _pick_timeline

    plan_id = "plan-b626f774d1c74e7784057fcc381f0b88"
    episode_dir = _episode_with_map(tmp_path, plan_id)
    project = _FakeProject(_FakeTimeline("長2-final", 14709))

    timeline, label, identity = _pick_timeline(
        project, episode_dir, {"id": "punch-L03", "format": "long", "rank": 2, "title": "x"}
    )

    assert label == "長2-final"
    assert timeline.GetName() == "長2-final"
    assert identity == plan_id
    assert identity != "punch-L03"


def test_pick_timeline_accepts_a_cut_with_no_plan_record(tmp_path):
    """沒有 plan record 的成品（短片線）對應表明寫 null——合法狀態，不是缺欄位。"""
    from publish_prep import _pick_timeline

    episode_dir = _episode_with_map(tmp_path, None)
    project = _FakeProject(_FakeTimeline("長2-final", 14709))

    _, _, identity = _pick_timeline(
        project, episode_dir, {"id": "punch-L03", "format": "long", "rank": 2, "title": "x"}
    )

    assert identity is None
