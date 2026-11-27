"""DP 總調度：選哪些列、照分類交給哪個元件、單列失敗不中斷、報告。不真的渲。"""

from __future__ import annotations

import json
from pathlib import Path

from agents.brook.youtube_video import dp
from agents.brook.youtube_video.visuals import delivery


def _row(row_id, category, status="approved"):
    return {"id": row_id, "category": category, "status": status}


PLAN = {
    "rows": [
        _row("a", "concept_title"),
        _row("b", "quote_card", None),
        _row("c", "quote_card", "rearrange"),
        _row("d", "montage"),
        _row("e", "own_material"),
        _row("f", "aroll"),
        _row("g", "book_cover"),
        _row("h", None, "approved"),
    ]
}


def test_only_approved_rows_unless_testing():
    assert [r["id"] for r in dp.rows_to_build(PLAN)] == ["a", "d", "e", "f", "g", "h"]
    with_pending = [r["id"] for r in dp.rows_to_build(PLAN, include_pending=True)]
    assert "b" in with_pending and "c" not in with_pending  # 重排的永遠不做


def test_build_routes_by_category_and_keeps_going(tmp_path):
    calls = []

    def deliver_for(category):
        def deliver(episode, plan, row, *, force=False):
            calls.append((category, row["id"], force))
            if row["id"] == "g":
                raise delivery.DeliveryError("g: 書庫沒有這本書")
            return {}, row["id"] == "a"

        return deliver

    rows = [r for r in PLAN["rows"] if r["id"] != "c"]
    outcomes = dp.build(tmp_path, PLAN, rows, deliver_for=deliver_for, log=lambda _: None)
    result = {o.row_id: o.result for o in outcomes}
    assert result == {
        "a": "rendered",
        "b": "reused",
        "d": "pending_build",
        "e": "awaiting_material",
        "f": "not_needed",
        "g": "failed",
        "h": "failed",
    }
    assert [c[0] for c in calls] == ["concept_title", "quote_card", "book_cover"]
    report = dp.write_report(tmp_path, outcomes, include_pending=True)
    data = json.loads(Path(report).read_text(encoding="utf-8"))
    assert data["summary"]["failed"] == 2 and data["include_pending"] is True


def test_dry_run_renders_nothing(tmp_path):
    def deliver_for(category):
        raise AssertionError("dry run must not render")

    outcomes = dp.build(
        tmp_path, PLAN, PLAN["rows"][:1], dry_run=True, deliver_for=deliver_for, log=lambda _: None
    )
    assert outcomes[0].result == "would_render"


def test_every_renderer_module_exists():
    for module in dp.RENDERERS.values():
        assert callable(dp.importlib.import_module(module).deliver_row)
