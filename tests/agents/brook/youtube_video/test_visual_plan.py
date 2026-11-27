"""畫面規劃表的修改規則（規格 §4）與統計。"""

from __future__ import annotations

import json

import pytest

from agents.brook.youtube_video import visual_plan as vp
from agents.brook.youtube_video.categories import load_categories, parse_categories


def _cue(cue_id: int, start: float, end: float, text: str) -> dict:
    return {"id": cue_id, "start": start, "end": end, "text": text}


def _row(row_id: str, **fields) -> dict:
    row = {
        "id": row_id,
        "origin": "director",
        "kind": "overlay",
        "category": "concept_title",
        "start_cue": 1,
        "end_cue": 1,
        "before_cue": None,
        "insert_sec": None,
        "layout": "overlay",
        "description": "",
        "dp_instruction": "",
        "onscreen_text": "第一個 300 萬",
        "source": "",
        "status": None,
        "note": "",
        "program_state": None,
    }
    row.update(fields)
    row.setdefault("original", vp.snapshot(row))
    return row


@pytest.fixture
def plan() -> dict:
    cues = [_cue(i, i * 4.0, i * 4.0 + 3.5, f"第 {i} 句") for i in range(1, 31)]
    return {
        "schema": vp.SCHEMA,
        "episode": "test-episode",
        "cues": cues,
        "rows": [
            _row(
                "chapter",
                origin="auto",
                kind="insert",
                category="chapter_card",
                start_cue=None,
                end_cue=None,
                before_cue=3,
                insert_sec=2,
                layout="full",
                onscreen_text="六個財富階層",
            ),
            _row("title", start_cue=4, end_cue=5),
            _row(
                "cover",
                category="book_cover",
                start_cue=10,
                end_cue=12,
                dp_instruction="《財富階梯》",
                onscreen_text="",
            ),
            _row("aroll", category="aroll", start_cue=20, end_cue=21, layout=None),
        ],
    }


def test_category_list_is_complete_and_valid():
    categories = load_categories()
    numbered = sorted(c.number for c in categories.values() if c.number is not None)
    assert numbered == list(range(1, 19))
    assert {"section_title", "channel_intro", "outro_transition"} <= {
        c.id for c in categories.values() if c.auto
    }
    assert categories["chapter_card"].kind == "insert"
    assert categories["chapter_card"].auto


def test_category_list_rejects_unknown_required_field():
    with pytest.raises(ValueError, match="unknown required"):
        parse_categories(
            {
                "categories": [
                    {
                        "id": "x",
                        "name": "x",
                        "kind": "overlay",
                        "layout": "either",
                        "readiness": "ready",
                        "required": ["nope"],
                    }
                ]
            }
        )


def test_editing_a_visible_field_approves_the_row(plan):
    row = vp.update_row(plan, "title", {"onscreen_text": "躍升第 5 階"})
    assert row["status"] == "approved"


def test_editing_dp_instruction_or_source_does_not_approve(plan):
    row = vp.update_row(plan, "title", {"dp_instruction": "用橘色強調", "source": "nav.al"})
    assert row["status"] is None


def test_unchanged_value_does_not_approve(plan):
    row = vp.update_row(plan, "title", {"onscreen_text": "第一個 300 萬"})
    assert row["status"] is None


def test_explicit_status_wins_over_edit_approval(plan):
    row = vp.update_row(plan, "title", {"description": "大字", "status": None})
    assert row["status"] is None


def test_rearrange_requires_a_note(plan):
    with pytest.raises(vp.PlanError, match="修正意見"):
        vp.update_row(plan, "title", {"status": "rearrange"})
    row = vp.update_row(plan, "title", {"status": "rearrange", "note": "字太長"})
    assert row["status"] == "rearrange"


def test_clearing_status_returns_to_pending(plan):
    vp.update_row(plan, "title", {"status": "approved"})
    assert vp.update_row(plan, "title", {"status": None})["status"] is None


def test_fixed_layout_category_forces_layout(plan):
    row = vp.update_row(plan, "title", {"category": "quote_card"})
    assert row["layout"] == "full"
    with pytest.raises(vp.PlanError, match="固定"):
        vp.update_row(plan, "title", {"layout": "overlay"})


def test_auto_rows_cannot_change_category_or_be_deleted(plan):
    with pytest.raises(vp.PlanError):
        vp.update_row(plan, "chapter", {"category": "montage"})
    with pytest.raises(vp.PlanError):
        vp.delete_row(plan, "chapter")
    # 但可以改字（例如章節卡上的文字）
    assert vp.update_row(plan, "chapter", {"onscreen_text": "六個階層"})["status"] == "approved"


def test_insert_row_only_offers_insert_categories(plan):
    vp.update_row(plan, "title", {"category": "person"})
    with pytest.raises(vp.PlanError):
        vp.update_row(plan, "title", {"category": "montage"})


def test_time_only_from_subtitles(plan):
    row = vp.update_row(plan, "title", {"start_cue": 6, "end_cue": 8})
    assert (row["start_cue"], row["end_cue"]) == (6, 8)
    with pytest.raises(vp.PlanError):
        vp.update_row(plan, "title", {"start_cue": 9, "end_cue": 7})
    with pytest.raises(vp.PlanError):
        vp.update_row(plan, "title", {"start_cue": 999})
    with pytest.raises(vp.PlanError):
        vp.update_row(plan, "chapter", {"start_cue": 4})
    assert vp.update_row(plan, "chapter", {"before_cue": 7})["before_cue"] == 7


def test_unknown_field_is_rejected(plan):
    with pytest.raises(vp.PlanError):
        vp.update_row(plan, "title", {"program_state": "on_timeline"})


def test_restore_returns_to_director_version(plan):
    vp.update_row(plan, "title", {"onscreen_text": "改過", "start_cue": 6, "end_cue": 6})
    row = vp.restore_row(plan, "title")
    assert row["onscreen_text"] == "第一個 300 萬"
    assert (row["start_cue"], row["end_cue"]) == (4, 5)
    assert row["status"] is None


def test_added_row_waits_for_director(plan):
    row = vp.add_row(plan, 25, 27)
    assert row["origin"] == "manual" and row["category"] is None
    assert vp.missing_required(row) == ["category"]
    assert row in vp.rows_for_agent(plan)
    with pytest.raises(vp.PlanError):
        vp.restore_row(plan, row["id"])


def test_rows_for_agent_picks_rearrange_rows(plan):
    vp.update_row(plan, "cover", {"status": "rearrange", "note": "換封面"})
    assert [row["id"] for row in vp.rows_for_agent(plan)] == ["cover"]


def test_timeline_orders_rows_and_gaps(plan):
    kinds = [
        (kind, item["id"] if kind == "row" else item.cue_ids[0]) for kind, item in vp.timeline(plan)
    ]
    # 章節卡（插入）把空白段切開：1–2 在卡前，3 在卡後
    assert kinds[:5] == [
        ("gap", 1),
        ("row", "chapter"),
        ("gap", 3),
        ("row", "title"),
        ("gap", 6),
    ]
    # 「不上畫面」不算有畫面，所以它蓋住的字幕仍在空白段裡
    gap_ids = {cue for gap in vp.gaps(plan) for cue in gap.cue_ids}
    assert {20, 21} <= gap_ids


def test_stats_count_only_director_chosen_visuals(plan):
    stats = vp.stats(plan)
    # 章節卡（自動）與「不上畫面」不算；title＋書封＝2 個，片長約 2 分鐘
    assert stats["per_minute"] == pytest.approx(2 / ((30 * 4 + 3.5 - 4) / 60), abs=0.05)
    assert stats["pending"] == 4
    assert stats["longest_gap"].cue_ids[0] == 13


def test_missing_required_follows_category(plan):
    title = next(row for row in plan["rows"] if row["id"] == "title")
    title["onscreen_text"] = ""
    assert vp.missing_required(title) == ["onscreen_text"]


def test_flags_derive_from_category_readiness(plan):
    # 還沒有元件的分類（舊例 meme 2026-11-26 已做好，改用 Outro 轉場；它只由 ## Outro 自動產生，
    # update_row 不讓手動改成它，所以直接拿一列來算標示）
    assert vp.row_flags({"category": "outro_transition"}) == ["pending_build"]
    title = next(row for row in plan["rows"] if row["id"] == "title")
    vp.update_row(plan, "title", {"category": "own_material"})
    assert vp.row_flags(title) == ["awaiting_material"]


def test_save_and_load_round_trip(tmp_path, plan):
    path = tmp_path / vp.PLAN_FILE
    vp.save_plan(path, plan)
    loaded = vp.load_plan(path)
    assert loaded["rows"] == plan["rows"]
    assert "updated_at" in json.loads(path.read_text(encoding="utf-8"))
    assert not list(tmp_path.glob(".visual_plan-*"))


def test_save_rejects_rearrange_without_note(tmp_path, plan):
    plan["rows"][1]["status"] = "rearrange"
    with pytest.raises(vp.PlanError):
        vp.save_plan(tmp_path / vp.PLAN_FILE, plan)
