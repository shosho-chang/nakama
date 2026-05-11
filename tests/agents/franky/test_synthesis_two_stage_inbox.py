"""Tests for two-stage inbox: Stage 1 always writes vault, Stage 2 is conditional."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from agents.franky import news_synthesis as ns


def _make_pick(date="2026-05-06", rank=1):
    return {
        "item_id": f"{date}-{rank}",
        "date": date,
        "rank": rank,
        "title": "Some AI news",
        "publisher": "Anthropic",
        "url": "https://anthropic.com/news",
        "verdict": "Big news",
        "why": "Important",
    }


def _synthesis_json_no_promote(week_iso="2026-w18"):
    return json.dumps(
        {
            "candidates": [
                {
                    "proposal_id": f"franky-proposal-{week_iso}-1",
                    "title": "Test proposal",
                    "pattern_type": "trend",
                    "description": "Trend detected",
                    "metric_type": "checklist",
                    "success_metric": "Feature shipped",
                    "related_adr": [],
                    "related_issues": [],
                    "try_cost_estimate": "$1 + 1hr",
                    "panel_recommended_reasons": [],
                    "supporting_item_ids": ["2026-05-06-1", "2026-05-07-1"],
                    "direct_issue_mapping": None,
                    "direct_adr_mapping": None,
                }
            ]
        }
    )


# ---------------------------------------------------------------------------
# Stage 1: unconditional vault write
# ---------------------------------------------------------------------------


def test_stage1_always_writes_vault_page(monkeypatch):
    """Stage 1: vault page written even when promote=false."""
    monkeypatch.setattr(ns, "_collect_seven_day_picks", lambda now=None: [_make_pick()])
    monkeypatch.setattr(ns, "_load_context_snapshot", lambda: "")
    monkeypatch.setattr(ns.llm, "ask", lambda *a, **kw: _synthesis_json_no_promote())

    write_mock = MagicMock()
    monkeypatch.setattr(ns, "write_page", write_mock)
    monkeypatch.setattr(ns, "append_to_file", MagicMock())
    monkeypatch.setattr(ns, "insert_candidate", MagicMock(return_value=1))
    monkeypatch.setattr(ns, "_create_gh_issue", MagicMock())

    pipeline = ns.NewsSynthesisPipeline(dry_run=False, no_publish=True, slack_bot=MagicMock())
    pipeline.run()

    # vault page should be written
    assert write_mock.called
    # path should be KB/Wiki/Digests/AI/Weekly-YYYY-WW.md
    call_args = write_mock.call_args
    path_arg = call_args.args[0] if call_args.args else call_args.kwargs.get("relative_path", "")
    assert "Weekly-" in path_arg
    assert path_arg.endswith(".md")


def test_stage2_weak_singleton_does_not_create_issue(monkeypatch):
    """Stage 2: no GH issue when candidate has 1 supporting item + no direct mapping."""
    weak_json = json.dumps(
        {
            "candidates": [
                {
                    "proposal_id": "franky-proposal-2026-w18-1",
                    "title": "Weak singleton",
                    "pattern_type": "trend",
                    "description": "Only one item supports this.",
                    "metric_type": "checklist",
                    "success_metric": "Feature shipped",
                    "related_adr": [],
                    "related_issues": [],
                    "try_cost_estimate": "$1 + 1hr",
                    "panel_recommended_reasons": [],
                    "supporting_item_ids": ["2026-05-06-1"],  # only 1
                    "direct_issue_mapping": None,
                    "direct_adr_mapping": None,
                }
            ]
        }
    )
    monkeypatch.setattr(ns, "_collect_seven_day_picks", lambda now=None: [_make_pick()])
    monkeypatch.setattr(ns, "_load_context_snapshot", lambda: "")
    monkeypatch.setattr(ns.llm, "ask", lambda *a, **kw: weak_json)
    monkeypatch.setattr(ns, "write_page", MagicMock())
    monkeypatch.setattr(ns, "append_to_file", MagicMock())
    monkeypatch.setattr(ns, "insert_candidate", MagicMock(return_value=1))

    gh_mock = MagicMock()
    monkeypatch.setattr(ns, "_create_gh_issue", gh_mock)

    pipeline = ns.NewsSynthesisPipeline(dry_run=False, no_publish=True, slack_bot=MagicMock())
    pipeline.run()

    gh_mock.assert_not_called()


def test_stage2_two_item_rule_creates_issue(monkeypatch):
    """Stage 2: GH issue created when candidate has ≥2 supporting items (even promote=false)."""
    monkeypatch.setattr(ns, "_collect_seven_day_picks", lambda now=None: [_make_pick()])
    monkeypatch.setattr(ns, "_load_context_snapshot", lambda: "")
    monkeypatch.setattr(ns.llm, "ask", lambda *a, **kw: _synthesis_json_no_promote())
    monkeypatch.setattr(ns, "write_page", MagicMock())
    monkeypatch.setattr(ns, "append_to_file", MagicMock())
    monkeypatch.setattr(ns, "insert_candidate", MagicMock(return_value=1))

    gh_mock = MagicMock(return_value=101)
    monkeypatch.setattr(ns, "_create_gh_issue", gh_mock)
    mark_promoted_mock = MagicMock()
    monkeypatch.setattr(ns, "mark_promoted", mark_promoted_mock)

    # Two supporting items → ≥2-item rule fires → issue created
    pipeline = ns.NewsSynthesisPipeline(dry_run=False, no_publish=True, slack_bot=MagicMock())
    pipeline.run()

    gh_mock.assert_called_once()
    mark_promoted_mock.assert_called_once()


def test_stage2_quality_gate_fails_no_issue(monkeypatch):
    """Candidate with 1 item + no direct mapping → fails gate → no GH issue."""
    weak_json = json.dumps(
        {
            "candidates": [
                {
                    "proposal_id": "franky-proposal-2026-w18-1",
                    "title": "Weak proposal",
                    "pattern_type": "trend",
                    "description": "Weak",
                    "metric_type": "checklist",
                    "success_metric": "Feature shipped",
                    "related_adr": [],
                    "related_issues": [],
                    "try_cost_estimate": "$1 + 1hr",
                    "panel_recommended_reasons": [],
                    "supporting_item_ids": ["2026-05-06-1"],  # only 1 item
                    "direct_issue_mapping": None,
                    "direct_adr_mapping": None,
                }
            ]
        }
    )
    monkeypatch.setattr(ns, "_collect_seven_day_picks", lambda now=None: [_make_pick()])
    monkeypatch.setattr(ns, "_load_context_snapshot", lambda: "")
    monkeypatch.setattr(ns.llm, "ask", lambda *a, **kw: weak_json)
    monkeypatch.setattr(ns, "write_page", MagicMock())
    monkeypatch.setattr(ns, "append_to_file", MagicMock())
    monkeypatch.setattr(ns, "insert_candidate", MagicMock(return_value=1))

    gh_mock = MagicMock()
    monkeypatch.setattr(ns, "_create_gh_issue", gh_mock)

    pipeline = ns.NewsSynthesisPipeline(dry_run=False, no_publish=True, slack_bot=MagicMock())
    pipeline.run()

    gh_mock.assert_not_called()


def test_proposal_metrics_row_inserted_with_candidate_status(monkeypatch):
    """DB row inserted with status=candidate for every Stage 1 candidate."""
    monkeypatch.setattr(ns, "_collect_seven_day_picks", lambda now=None: [_make_pick()])
    monkeypatch.setattr(ns, "_load_context_snapshot", lambda: "")
    monkeypatch.setattr(ns.llm, "ask", lambda *a, **kw: _synthesis_json_no_promote())
    monkeypatch.setattr(ns, "write_page", MagicMock())
    monkeypatch.setattr(ns, "append_to_file", MagicMock())
    monkeypatch.setattr(ns, "_create_gh_issue", MagicMock(return_value=101))
    monkeypatch.setattr(ns, "mark_promoted", MagicMock())

    insert_mock = MagicMock(return_value=42)
    monkeypatch.setattr(ns, "insert_candidate", insert_mock)

    pipeline = ns.NewsSynthesisPipeline(dry_run=False, no_publish=True, slack_bot=MagicMock())
    pipeline.run()

    insert_mock.assert_called_once()
    # Verify it was called with a ProposalFrontmatterV1 and week_iso kwarg
    call_args = insert_mock.call_args
    assert "week_iso" in call_args.kwargs


def test_rescan_promotions_creates_issue_for_promoted_page(monkeypatch, tmp_path):
    """re_scan_promotions reads a weekly page with promote=true in fenced block, opens GH issue."""
    weekly_page = tmp_path / "Weekly-2026-W18.md"
    weekly_page.write_text(
        """---
week_iso: 2026-W18
created_by: franky
---

# Franky Weekly Synthesis — 2026-W18

## Candidate 1: Test proposal

```yaml frontmatter
proposal_id: franky-proposal-2026-w18-1
metric_type: checklist
success_metric: Feature shipped
related_adr: []
related_issues: []
panel_recommended: false
promote: true
```

Some description.
""",
        encoding="utf-8",
    )

    gh_mock = MagicMock(return_value=99)
    monkeypatch.setattr(ns, "_create_gh_issue", gh_mock)
    mark_promoted_mock = MagicMock()
    monkeypatch.setattr(ns, "mark_promoted", mark_promoted_mock)

    # Provide the path directly
    ns._re_scan_and_promote_page(str(weekly_page))

    gh_mock.assert_called_once()
    mark_promoted_mock.assert_called_once()


def test_rescan_promotions_no_action_for_promote_false(monkeypatch, tmp_path):
    """re_scan_promotions does nothing when promote=false in fenced block."""
    weekly_page = tmp_path / "Weekly-2026-W18.md"
    weekly_page.write_text(
        """---
week_iso: 2026-W18
created_by: franky
---

# Franky Weekly Synthesis — 2026-W18

## Candidate 1

```yaml frontmatter
proposal_id: franky-proposal-2026-w18-1
metric_type: checklist
success_metric: Feature shipped
related_adr: []
related_issues: []
panel_recommended: false
promote: false
```

Some description.
""",
        encoding="utf-8",
    )

    gh_mock = MagicMock()
    monkeypatch.setattr(ns, "_create_gh_issue", gh_mock)

    ns._re_scan_and_promote_page(str(weekly_page))
    gh_mock.assert_not_called()
