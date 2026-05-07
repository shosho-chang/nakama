"""Tests for agents/franky/news_synthesis.py — hard quality gate + core pipeline."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from agents.franky import news_synthesis as ns

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_candidate(
    proposal_id="franky-proposal-2026-w18-1",
    pattern_type="trend",
    supporting_item_ids=("2026-05-06-1", "2026-05-07-2"),
    direct_issue_mapping=None,
    direct_adr_mapping=None,
    metric_type="checklist",
    success_metric="Feature shipped and smoke test passes",
    related_adr=None,
    related_issues=None,
):
    return {
        "proposal_id": proposal_id,
        "title": "Test proposal",
        "pattern_type": pattern_type,
        "description": "Test description",
        "metric_type": metric_type,
        "success_metric": success_metric,
        "related_adr": related_adr or [],
        "related_issues": related_issues or [],
        "try_cost_estimate": "$1 + 1hr",
        "panel_recommended_reasons": [],
        "supporting_item_ids": list(supporting_item_ids),
        "direct_issue_mapping": direct_issue_mapping,
        "direct_adr_mapping": direct_adr_mapping,
    }


def _make_pick(date="2026-05-06", rank=1, title="Claude 5 released", publisher="Anthropic"):
    return {
        "item_id": f"{date}-{rank}",
        "date": date,
        "rank": rank,
        "title": title,
        "publisher": publisher,
        "url": f"https://anthropic.com/news/{date}-{rank}",
        "verdict": "Big release",
        "why": "Important for Nakama",
    }


# ---------------------------------------------------------------------------
# Hard quality gate
# ---------------------------------------------------------------------------


def test_quality_gate_two_supporting_items_passes():
    cand = _make_candidate(supporting_item_ids=["2026-05-06-1", "2026-05-07-2"])
    assert ns._passes_quality_gate(cand) is True


def test_quality_gate_one_item_direct_issue_passes():
    cand = _make_candidate(
        supporting_item_ids=["2026-05-06-1"],
        direct_issue_mapping="#449",
    )
    assert ns._passes_quality_gate(cand) is True


def test_quality_gate_one_item_direct_adr_passes():
    cand = _make_candidate(
        supporting_item_ids=["2026-05-06-1"],
        direct_adr_mapping="ADR-020",
    )
    assert ns._passes_quality_gate(cand) is True


def test_quality_gate_one_item_no_mapping_fails():
    cand = _make_candidate(supporting_item_ids=["2026-05-06-1"])
    assert ns._passes_quality_gate(cand) is False


def test_quality_gate_zero_items_fails():
    cand = _make_candidate(supporting_item_ids=[])
    assert ns._passes_quality_gate(cand) is False


def test_quality_gate_exactly_two_items_passes():
    cand = _make_candidate(supporting_item_ids=["2026-05-06-1", "2026-05-06-2"])
    assert ns._passes_quality_gate(cand) is True


def test_quality_gate_three_items_passes():
    cand = _make_candidate(supporting_item_ids=["2026-05-05-1", "2026-05-06-1", "2026-05-07-1"])
    assert ns._passes_quality_gate(cand) is True


# ---------------------------------------------------------------------------
# Digest page parser
# ---------------------------------------------------------------------------


def test_parse_digest_page_extracts_items():
    page_text = """---
date: 2026-05-06
created_by: franky
---

# AI 每日情報 — 2026-05-06

> Editor note here

**候選總數**：20　**精選**：2

---

## 1. Claude 5 released

- **Publisher**: Anthropic
- **Category**: `model_release`
- **Published**: 2026-05-06T08:00:00+00:00 (4.0h ago)
- **Score**: 4.6 (5-dim) / 4.7 (4-dim)  (S5/N4/A5/Q5/R3)
- **Verdict**: Claude 5 大型發布
- **Why**: 對 Nakama 全 stack 有直接影響
- **Key**: 今天起 paid API 可用
- **Noise note**: 無明顯炒作
- **→** [https://anthropic.com/claude5](https://anthropic.com/claude5)

## 2. GPT-5 released

- **Publisher**: OpenAI
- **Category**: `model_release`
- **Published**: 2026-05-06T10:00:00+00:00 (2.0h ago)
- **Score**: 4.2 (5-dim) / 4.3 (4-dim)  (S4/N4/A4/Q5/R2)
- **Verdict**: GPT-5 發布
- **Why**: 競爭格局改變
- **Key**: Multimodal improvements
- **Noise note**: 無
- **→** [https://openai.com/gpt5](https://openai.com/gpt5)
"""
    items = ns._parse_digest_page(page_text, date="2026-05-06")
    assert len(items) == 2
    assert items[0]["item_id"] == "2026-05-06-1"
    assert items[0]["title"] == "Claude 5 released"
    assert items[0]["publisher"] == "Anthropic"
    assert items[1]["item_id"] == "2026-05-06-2"


def test_parse_digest_page_empty_returns_empty():
    items = ns._parse_digest_page("", date="2026-05-06")
    assert items == []


def test_parse_digest_page_no_items_returns_empty():
    page_text = "---\ndate: 2026-05-06\n---\n\n# AI 每日情報 — 2026-05-06\n\n無精選\n"
    items = ns._parse_digest_page(page_text, date="2026-05-06")
    assert items == []


# ---------------------------------------------------------------------------
# Dry-run: no vault write, no DB insert, no issue create
# ---------------------------------------------------------------------------


def test_run_dry_run_does_not_write_vault_or_db(monkeypatch):
    monkeypatch.setattr(ns, "_collect_seven_day_picks", lambda now=None: [_make_pick()])
    monkeypatch.setattr(ns, "_load_context_snapshot", lambda: "")

    synthesis_json = json.dumps(
        {"candidates": [_make_candidate(supporting_item_ids=["2026-05-06-1", "2026-05-07-1"])]}
    )
    monkeypatch.setattr(ns.llm, "ask", lambda *a, **kw: synthesis_json)

    write_mock = MagicMock()
    monkeypatch.setattr(ns, "write_page", write_mock)
    insert_mock = MagicMock()
    monkeypatch.setattr(ns, "insert_candidate", insert_mock)
    gh_mock = MagicMock()
    monkeypatch.setattr(ns, "_create_gh_issue", gh_mock)

    pipeline = ns.NewsSynthesisPipeline(dry_run=True, slack_bot=MagicMock())
    result = pipeline.run()

    write_mock.assert_not_called()
    insert_mock.assert_not_called()
    gh_mock.assert_not_called()
    assert "dry_run=True" in result


def test_run_no_picks_returns_skip_message(monkeypatch):
    monkeypatch.setattr(ns, "_collect_seven_day_picks", lambda now=None: [])
    monkeypatch.setattr(ns, "_load_context_snapshot", lambda: "")

    pipeline = ns.NewsSynthesisPipeline(dry_run=True)
    result = pipeline.run()
    assert "略過" in result or "無精選" in result or "skip" in result.lower()


def test_run_llm_failure_returns_error_summary(monkeypatch):
    monkeypatch.setattr(ns, "_collect_seven_day_picks", lambda now=None: [_make_pick()])
    monkeypatch.setattr(ns, "_load_context_snapshot", lambda: "")
    monkeypatch.setattr(ns.llm, "ask", MagicMock(side_effect=RuntimeError("LLM down")))

    pipeline = ns.NewsSynthesisPipeline(dry_run=True)
    result = pipeline.run()
    assert "失敗" in result or "error" in result.lower()
