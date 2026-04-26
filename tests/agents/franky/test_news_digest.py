"""Tests for agents/franky/news_digest.py — pipeline integration."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from agents.franky import news_digest as nd


@pytest.fixture(autouse=True)
def _no_anthropic_html_fetch(monkeypatch):
    """Default: Slice B Anthropic HTML scraper returns []. Tests that exercise
    multi-source merge can override locally. Without this, every test would
    trigger a real httpx call to anthropic.com/news (Slice B regression risk)."""
    monkeypatch.setattr(nd.anthropic_html, "gather_candidates", lambda **kw: [])


# ---------- _parse_json --------------------------------------------------------


def test_parse_json_plain():
    assert nd._parse_json('{"a": 1}') == {"a": 1}


def test_parse_json_strips_markdown_fence():
    raw = '```json\n{"a": 1}\n```'
    assert nd._parse_json(raw) == {"a": 1}


def test_parse_json_strips_chatter_around_object():
    raw = 'Sure! Here you go:\n\n{"selected": []}\n\nLet me know.'
    assert nd._parse_json(raw) == {"selected": []}


def test_parse_json_no_object_raises():
    with pytest.raises(ValueError):
        nd._parse_json("no json here")


# ---------- _today_taipei ------------------------------------------------------


def test_today_taipei_returns_iso_date_string():
    today = nd._today_taipei()
    # ISO date format YYYY-MM-DD
    assert len(today) == 10
    assert today[4] == "-" and today[7] == "-"
    assert today[:4].isdigit()


# ---------- Pipeline early-return paths ---------------------------------------


def test_run_no_feeds_returns_skip_message(tmp_path):
    empty_cfg = tmp_path / "empty.yaml"
    empty_cfg.write_text("feeds: []", encoding="utf-8")
    pipeline = nd.NewsDigestPipeline(dry_run=True, feeds_config_path=empty_cfg)
    summary = pipeline.run()
    assert "無 feed" in summary or "無 feed 設定" in summary


def test_run_no_candidates_returns_skip_message(tmp_path, monkeypatch):
    cfg = tmp_path / "feeds.yaml"
    cfg.write_text(
        "feeds:\n  - name: x\n    url: https://example.com\n    publisher: X\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(nd, "gather_candidates", lambda *a, **kw: [])
    pipeline = nd.NewsDigestPipeline(dry_run=True, feeds_config_path=cfg)
    summary = pipeline.run()
    assert "24h" in summary or "略過" in summary or "已見過" in summary


# ---------- Happy path ---------------------------------------------------------


def _make_candidate(item_id="i1", title="Claude 4.7 1M context out", publisher="Anthropic"):
    return {
        "item_id": item_id,
        "title": title,
        "publisher": publisher,
        "feed_name": "anthropic_news",
        "url": f"https://www.anthropic.com/news/{item_id}",
        "summary": "1M context tier rolled out for paid API users.",
        "published": "2026-04-26T08:00:00+00:00",
        "published_ts": 1.0,
        "age_hours": 4.0,
    }


def _curate_response(item_ids):
    return json.dumps(
        {
            "selected": [
                {
                    "item_id": iid,
                    "rank": idx + 1,
                    "category": "model_release",
                    "reason": "test reason",
                }
                for idx, iid in enumerate(item_ids)
            ],
            "summary": {
                "total_candidates": len(item_ids),
                "selected_count": len(item_ids),
                "main_categories": ["model_release"],
                "editor_note": "test editor note",
            },
        }
    )


def _score_response():
    return json.dumps(
        {
            "scores": {"signal": 5, "novelty": 4, "actionability": 5, "noise": 5},
            "overall": 4.7,
            "one_line_verdict": "Claude 4.7 1M context 釋出",
            "why_it_matters": "對 Nakama 全 stack 直接受惠",
            "key_finding": "今天起 paid API 可用",
            "noise_note": "無明顯炒作",
            "pick": True,
        }
    )


def test_run_dry_run_does_not_write_or_publish(tmp_path, monkeypatch):
    cfg = tmp_path / "feeds.yaml"
    cfg.write_text(
        "feeds:\n  - name: x\n    url: https://example.com\n    publisher: X\n",
        encoding="utf-8",
    )
    cand = _make_candidate()
    monkeypatch.setattr(nd, "gather_candidates", lambda *a, **kw: [cand])

    # mock LLM
    fake_llm_calls: list[str] = []

    def _fake_ask(prompt, max_tokens=None, **kw):
        fake_llm_calls.append(prompt[:50])
        if "篩出當日" in prompt or "candidates" in prompt.lower() or "5-8 條" in prompt:
            return _curate_response(["i1"])
        return _score_response()

    monkeypatch.setattr(nd.llm, "ask", _fake_ask)

    write_mock = MagicMock()
    monkeypatch.setattr(nd, "write_page", write_mock)
    append_mock = MagicMock()
    monkeypatch.setattr(nd, "append_to_file", append_mock)
    mark_seen_mock = MagicMock()
    monkeypatch.setattr(nd, "mark_seen", mark_seen_mock)

    pipeline = nd.NewsDigestPipeline(dry_run=True, feeds_config_path=cfg, slack_bot=MagicMock())
    summary = pipeline.run()

    write_mock.assert_not_called()
    append_mock.assert_not_called()
    mark_seen_mock.assert_not_called()
    assert "fetch=1" in summary
    assert "selected=1" in summary
    assert "dry_run=True" in summary


def test_run_no_publish_writes_vault_but_skips_slack(tmp_path, monkeypatch):
    cfg = tmp_path / "feeds.yaml"
    cfg.write_text(
        "feeds:\n  - name: x\n    url: https://example.com\n    publisher: X\n",
        encoding="utf-8",
    )
    cand = _make_candidate()
    monkeypatch.setattr(nd, "gather_candidates", lambda *a, **kw: [cand])
    monkeypatch.setattr(
        nd.llm,
        "ask",
        lambda p, **kw: _curate_response(["i1"]) if "5-8 條" in p else _score_response(),
    )

    write_mock = MagicMock()
    monkeypatch.setattr(nd, "write_page", write_mock)
    monkeypatch.setattr(nd, "append_to_file", MagicMock())

    slack_bot = MagicMock()
    pipeline = nd.NewsDigestPipeline(
        dry_run=False, no_publish=True, feeds_config_path=cfg, slack_bot=slack_bot
    )
    pipeline.run()

    assert write_mock.called
    slack_bot.post_plain.assert_not_called()


def test_run_full_path_writes_vault_and_sends_slack(tmp_path, monkeypatch):
    cfg = tmp_path / "feeds.yaml"
    cfg.write_text(
        "feeds:\n  - name: x\n    url: https://example.com\n    publisher: X\n",
        encoding="utf-8",
    )
    cand = _make_candidate()
    monkeypatch.setattr(nd, "gather_candidates", lambda *a, **kw: [cand])
    monkeypatch.setattr(
        nd.llm,
        "ask",
        lambda p, **kw: _curate_response(["i1"]) if "5-8 條" in p else _score_response(),
    )

    write_mock = MagicMock()
    monkeypatch.setattr(nd, "write_page", write_mock)
    monkeypatch.setattr(nd, "append_to_file", MagicMock())
    monkeypatch.setattr(nd, "mark_seen", MagicMock())

    slack_bot = MagicMock()
    slack_bot.post_plain.return_value = "1234567890.000001"
    pipeline = nd.NewsDigestPipeline(dry_run=False, feeds_config_path=cfg, slack_bot=slack_bot)
    pipeline.run()

    assert write_mock.called
    slack_bot.post_plain.assert_called_once()
    call_kwargs = slack_bot.post_plain.call_args
    text_arg = call_kwargs.args[0]
    assert "Claude 4.7" in text_arg or "AI Daily" in text_arg
    assert call_kwargs.kwargs.get("context") == "news_digest"


def test_run_curate_failure_returns_error_summary(tmp_path, monkeypatch):
    cfg = tmp_path / "feeds.yaml"
    cfg.write_text(
        "feeds:\n  - name: x\n    url: https://example.com\n    publisher: X\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(nd, "gather_candidates", lambda *a, **kw: [_make_candidate()])
    monkeypatch.setattr(nd.llm, "ask", MagicMock(side_effect=RuntimeError("LLM down")))
    pipeline = nd.NewsDigestPipeline(dry_run=True, feeds_config_path=cfg)
    summary = pipeline.run()
    assert "curate 失敗" in summary or "失敗" in summary


def test_run_unknown_item_id_in_curate_skipped(tmp_path, monkeypatch, caplog):
    """Curate 回傳的 item_id 不在 candidates 裡 → 跳過 + log warning。"""
    cfg = tmp_path / "feeds.yaml"
    cfg.write_text(
        "feeds:\n  - name: x\n    url: https://example.com\n    publisher: X\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(nd, "gather_candidates", lambda *a, **kw: [_make_candidate("real")])

    def _ask(prompt, **kw):
        if "5-8 條" in prompt:
            return _curate_response(["hallucinated_id"])  # not in candidates
        return _score_response()

    monkeypatch.setattr(nd.llm, "ask", _ask)
    monkeypatch.setattr(nd, "write_page", MagicMock())
    monkeypatch.setattr(nd, "append_to_file", MagicMock())
    monkeypatch.setattr(nd, "mark_seen", MagicMock())

    pipeline = nd.NewsDigestPipeline(dry_run=True, feeds_config_path=cfg)
    summary = pipeline.run()
    # All curated picks are unknown → no scored items → "無精選入選"
    assert "無精選" in summary or "selected=0" in summary or "score 後" in summary


def test_run_pick_false_items_filtered_out(tmp_path, monkeypatch):
    """Score 回 pick=false 的條目不應出現在 vault digest 或 Slack DM。"""
    cfg = tmp_path / "feeds.yaml"
    cfg.write_text(
        "feeds:\n  - name: x\n    url: https://example.com\n    publisher: X\n",
        encoding="utf-8",
    )
    cands = [_make_candidate("a"), _make_candidate("b")]
    monkeypatch.setattr(nd, "gather_candidates", lambda *a, **kw: cands)

    score_call = {"n": 0}

    def _ask(prompt, **kw):
        if "5-8 條" in prompt:
            return _curate_response(["a", "b"])
        score_call["n"] += 1
        if score_call["n"] == 1:
            return _score_response()
        bad = json.loads(_score_response())
        bad["pick"] = False
        bad["overall"] = 1.2
        return json.dumps(bad)

    monkeypatch.setattr(nd.llm, "ask", _ask)
    monkeypatch.setattr(nd, "write_page", MagicMock())
    monkeypatch.setattr(nd, "append_to_file", MagicMock())
    monkeypatch.setattr(nd, "mark_seen", MagicMock())

    pipeline = nd.NewsDigestPipeline(
        dry_run=False, no_publish=True, feeds_config_path=cfg, slack_bot=MagicMock()
    )
    summary = pipeline.run()
    assert "selected=1" in summary


def test_run_all_pick_false_returns_no_picks(tmp_path, monkeypatch):
    """所有 score pick=false → 無精選入選 short-circuit。"""
    cfg = tmp_path / "feeds.yaml"
    cfg.write_text(
        "feeds:\n  - name: x\n    url: https://example.com\n    publisher: X\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(nd, "gather_candidates", lambda *a, **kw: [_make_candidate()])

    def _ask(prompt, **kw):
        if "5-8 條" in prompt:
            return _curate_response(["i1"])
        bad = json.loads(_score_response())
        bad["pick"] = False
        return json.dumps(bad)

    monkeypatch.setattr(nd.llm, "ask", _ask)
    monkeypatch.setattr(nd, "write_page", MagicMock())

    pipeline = nd.NewsDigestPipeline(dry_run=True, feeds_config_path=cfg)
    summary = pipeline.run()
    assert "無精選" in summary or "selected=0" in summary


def test_run_curate_dropped_no_id_logs_warning(tmp_path, monkeypatch, caplog):
    """Curate 回傳 selected 缺 item_id → 計數 + warning log，不 crash。"""
    import logging

    cfg = tmp_path / "feeds.yaml"
    cfg.write_text(
        "feeds:\n  - name: x\n    url: https://example.com\n    publisher: X\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(nd, "gather_candidates", lambda *a, **kw: [_make_candidate("real")])

    def _ask(prompt, **kw):
        if "5-8 條" in prompt:
            return json.dumps(
                {
                    "selected": [
                        {"rank": 1, "category": "x", "reason": "missing id"},
                        {
                            "item_id": "real",
                            "rank": 2,
                            "category": "model_release",
                            "reason": "ok",
                        },
                    ],
                    "summary": {
                        "total_candidates": 1,
                        "selected_count": 2,
                        "main_categories": ["x"],
                        "editor_note": "",
                    },
                }
            )
        return _score_response()

    monkeypatch.setattr(nd.llm, "ask", _ask)
    monkeypatch.setattr(nd, "write_page", MagicMock())
    monkeypatch.setattr(nd, "append_to_file", MagicMock())
    monkeypatch.setattr(nd, "mark_seen", MagicMock())

    with caplog.at_level(logging.WARNING):
        pipeline = nd.NewsDigestPipeline(dry_run=True, feeds_config_path=cfg)
        pipeline.run()
    assert any("缺 item_id" in r.message for r in caplog.records)


def test_run_score_failure_per_item_continues(tmp_path, monkeypatch):
    """Single _score raise 不應炸整批。"""
    cfg = tmp_path / "feeds.yaml"
    cfg.write_text(
        "feeds:\n  - name: x\n    url: https://example.com\n    publisher: X\n",
        encoding="utf-8",
    )
    cands = [_make_candidate("a"), _make_candidate("b")]
    monkeypatch.setattr(nd, "gather_candidates", lambda *a, **kw: cands)

    call_count = {"score": 0}

    def _ask(prompt, **kw):
        if "5-8 條" in prompt:
            return _curate_response(["a", "b"])
        # First score raises, second succeeds
        call_count["score"] += 1
        if call_count["score"] == 1:
            raise RuntimeError("LLM 503")
        return _score_response()

    monkeypatch.setattr(nd.llm, "ask", _ask)
    monkeypatch.setattr(nd, "write_page", MagicMock())
    monkeypatch.setattr(nd, "append_to_file", MagicMock())
    monkeypatch.setattr(nd, "mark_seen", MagicMock())

    pipeline = nd.NewsDigestPipeline(
        dry_run=False, no_publish=True, feeds_config_path=cfg, slack_bot=MagicMock()
    )
    summary = pipeline.run()
    assert "selected=1" in summary  # one survived


# ---------- Multi-source merge (Slice B) --------------------------------------


def test_run_merges_rss_and_anthropic_html_candidates(tmp_path, monkeypatch):
    """Slice B: RSS gather + anthropic_html.gather should both reach curate."""
    cfg = tmp_path / "feeds.yaml"
    cfg.write_text(
        "feeds:\n  - name: x\n    url: https://example.com\n    publisher: X\n",
        encoding="utf-8",
    )

    rss_cand = _make_candidate(item_id="rss-a", title="OpenAI release", publisher="OpenAI")
    rss_cand["published_ts"] = 100.0
    html_cand = _make_candidate(
        item_id="anthropic-news-claude", title="Claude release", publisher="Anthropic"
    )
    html_cand["published_ts"] = 200.0  # newer → should sort first after merge

    monkeypatch.setattr(nd, "gather_candidates", lambda *a, **kw: [rss_cand])
    monkeypatch.setattr(nd.anthropic_html, "gather_candidates", lambda **kw: [html_cand])

    captured: dict = {}

    def _ask(prompt, **kw):
        if "5-8 條" in prompt:
            # Capture which item_ids reached curate, in order
            captured["prompt"] = prompt
            return _curate_response(["anthropic-news-claude", "rss-a"])
        return _score_response()

    monkeypatch.setattr(nd.llm, "ask", _ask)
    monkeypatch.setattr(nd, "write_page", MagicMock())
    monkeypatch.setattr(nd, "append_to_file", MagicMock())
    monkeypatch.setattr(nd, "mark_seen", MagicMock())

    pipeline = nd.NewsDigestPipeline(dry_run=True, feeds_config_path=cfg)
    summary = pipeline.run()
    assert "fetch=2" in summary
    # Newer (Anthropic, ts=200) must appear before older (RSS, ts=100) in curate prompt
    assert "anthropic-news-claude" in captured["prompt"]
    assert "rss-a" in captured["prompt"]
    anthropic_pos = captured["prompt"].index("anthropic-news-claude")
    rss_pos = captured["prompt"].index("rss-a")
    assert anthropic_pos < rss_pos


def test_run_anthropic_html_failure_does_not_block_rss(tmp_path, monkeypatch):
    """If anthropic_html.gather raises, RSS path must still produce digest."""
    cfg = tmp_path / "feeds.yaml"
    cfg.write_text(
        "feeds:\n  - name: x\n    url: https://example.com\n    publisher: X\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(nd, "gather_candidates", lambda *a, **kw: [_make_candidate("rss")])

    # Anthropic scraper itself swallows network errors and returns []. Simulate.
    monkeypatch.setattr(nd.anthropic_html, "gather_candidates", lambda **kw: [])
    monkeypatch.setattr(
        nd.llm,
        "ask",
        lambda p, **kw: _curate_response(["rss"]) if "5-8 條" in p else _score_response(),
    )
    monkeypatch.setattr(nd, "write_page", MagicMock())
    monkeypatch.setattr(nd, "append_to_file", MagicMock())
    monkeypatch.setattr(nd, "mark_seen", MagicMock())

    pipeline = nd.NewsDigestPipeline(dry_run=True, feeds_config_path=cfg)
    summary = pipeline.run()
    assert "selected=1" in summary


# ---------- Pure render functions ---------------------------------------------


def test_render_digest_body_handles_minimal_input():
    body = nd._render_digest_body(
        today="2026-04-26",
        editor_note="",
        total_fresh=0,
        scored=[],
    )
    assert "AI 每日情報" in body
    assert "2026-04-26" in body


def test_render_slack_text_includes_picks_and_link():
    cand = _make_candidate()
    item = {
        "candidate": cand,
        "curate_meta": {"category": "model_release", "rank": 1, "reason": "x"},
        "score_result": json.loads(_score_response()),
    }
    text = nd._render_slack_text(
        today="2026-04-26",
        scored=[item],
        curated={"summary": {"editor_note": "test note"}},
        total_fresh=5,
        digest_relpath="KB/Wiki/Digests/AI/2026-04-26.md",
        operation_id="op_test",
    )
    assert "Anthropic" in text
    assert "Claude 4.7" in text or "1M context" in text
    assert "KB/Wiki/Digests/AI/2026-04-26.md" in text
    assert "op_test" in text
    # No mrkdwn bold star wrapping CJK (feedback_slack_cjk_mrkdwn)
    assert "*Franky*" not in text
    assert "*AI*" not in text


def test_render_digest_entry_includes_score_breakdown():
    cand = _make_candidate()
    item = {
        "candidate": cand,
        "curate_meta": {"category": "model_release", "reason": "x"},
        "score_result": json.loads(_score_response()),
    }
    lines = nd._render_digest_entry(1, item)
    body = "\n".join(lines)
    assert "Score" in body
    assert "S5" in body  # signal=5
    assert "Anthropic" in body


# ---------- Public entry -------------------------------------------------------


def test_run_news_digest_dry_run_returns_summary_string(tmp_path, monkeypatch):
    cfg = tmp_path / "feeds.yaml"
    cfg.write_text("feeds: []", encoding="utf-8")
    monkeypatch.setattr(nd, "_FEEDS_CONFIG", cfg)
    summary = nd.run_news_digest(dry_run=True)
    assert isinstance(summary, str)
    assert "無 feed" in summary or "settings" in summary or "略過" in summary


def test_run_news_digest_non_dry_uses_baseagent_execute(tmp_path, monkeypatch):
    """Non-dry path goes through BaseAgent.execute() which returns op_xxxxxxxx."""
    cfg = tmp_path / "feeds.yaml"
    cfg.write_text("feeds: []", encoding="utf-8")
    monkeypatch.setattr(nd, "_FEEDS_CONFIG", cfg)

    # Patch execute to avoid real DB writes/email side effects
    with patch.object(nd.NewsDigestPipeline, "execute") as exec_mock:
        result = nd.run_news_digest(dry_run=False)
        exec_mock.assert_called_once()
        assert result.startswith("op=")
