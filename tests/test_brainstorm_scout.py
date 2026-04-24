"""agents/zoro/brainstorm_scout.py — scout 四道濾網 + pick_best + publish 測試。

原始資料源（Trends/Reddit/YouTube）全部 mock — 測邏輯不測外部 API。
"""

from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest

from agents.zoro import brainstorm_scout as scout
from agents.zoro.brainstorm_scout import Signal, Topic
from shared import pushed_topics


@pytest.fixture(autouse=True)
def _clean_zoro():
    pushed_topics.delete_for_agent("zoro")
    yield
    pushed_topics.delete_for_agent("zoro")


# ── Velocity gate ──────────────────────────────────────────────────────────


def test_velocity_gate_filters_below_threshold():
    signals = [
        Signal(source="trends", topic="A", velocity_score=50.0),
        Signal(source="trends", topic="B", velocity_score=10.0),
        Signal(source="trends", topic="C", velocity_score=30.0),
    ]
    kept = scout.velocity_gate(signals, min_velocity=30.0)
    assert [s.topic for s in kept] == ["A", "C"]


# ── Signals → Topics ───────────────────────────────────────────────────────


def test_signals_to_topics_normalizes_keywords():
    s = Signal(source="reddit", topic="CGM, Glucose Monitoring", velocity_score=60.0)
    topics = scout.signals_to_topics([s])
    assert len(topics) == 1
    t = topics[0]
    assert t.title == "CGM, Glucose Monitoring"
    assert set(t.normalized_keywords) == {"cgm", "glucose", "monitoring"}
    assert t.velocity_score == 60.0


# ── Keyword pre-filter ─────────────────────────────────────────────────────


def test_keyword_prefilter_hits_nutrition_domain():
    t = Topic(title="new glucose monitor", normalized_keywords=[], signals=[])
    assert "飲食" in scout._keyword_prefilter(t)


def test_keyword_prefilter_misses_unrelated():
    t = Topic(title="new iPhone benchmark", normalized_keywords=[], signals=[])
    assert scout._keyword_prefilter(t) == set()


# ── Relevance gate ─────────────────────────────────────────────────────────


def test_relevance_gate_rejects_no_keyword_hit():
    t = Topic(title="Taylor Swift tour", normalized_keywords=["taylor"], signals=[])
    # keyword 預濾就砍，根本不會呼叫 LLM
    with patch("agents.zoro.brainstorm_scout._llm_judge_relevance") as m:
        kept = scout.relevance_gate([t])
    m.assert_not_called()
    assert kept == []


def test_relevance_gate_keeps_high_score_from_llm():
    t = Topic(
        title="continuous glucose monitor for non-diabetics",
        normalized_keywords=["glucose"],
        signals=[],
    )
    with patch(
        "agents.zoro.brainstorm_scout._llm_judge_relevance",
        return_value={"score": 0.87, "reason": "近期多篇研究", "domain": "飲食"},
    ):
        kept = scout.relevance_gate([t])
    assert len(kept) == 1
    assert kept[0].relevance_score == 0.87
    assert kept[0].domain == "飲食"


def test_relevance_gate_rejects_low_score_from_llm():
    t = Topic(title="sleep hack trend", normalized_keywords=["sleep"], signals=[])
    with patch(
        "agents.zoro.brainstorm_scout._llm_judge_relevance",
        return_value={"score": 0.4, "reason": "hype", "domain": "睡眠"},
    ):
        kept = scout.relevance_gate([t])
    assert kept == []


def test_relevance_gate_bypasses_llm_when_disabled():
    t = Topic(title="fasting protocol", normalized_keywords=["fasting"], signals=[])
    with patch("agents.zoro.brainstorm_scout._llm_judge_relevance") as m:
        kept = scout.relevance_gate([t], llm_judge=False)
    m.assert_not_called()
    assert len(kept) == 1
    assert kept[0].relevance_score == 0.75


# ── LLM judge parse ────────────────────────────────────────────────────────


def test_llm_judge_parses_valid_json():
    fake = json.dumps({"score": 0.9, "reason": "X", "domain": "飲食"})
    with patch("agents.zoro.brainstorm_scout.ask", return_value=fake):
        out = scout._llm_judge_relevance("topic")
    assert out == {"score": 0.9, "reason": "X", "domain": "飲食"}


def test_llm_judge_rejects_non_json():
    with patch("agents.zoro.brainstorm_scout.ask", return_value="I think it's 0.9"):
        out = scout._llm_judge_relevance("topic")
    assert out["score"] == 0.0
    assert "parse" in out["reason"]


def test_llm_judge_clamps_score_to_0_1():
    fake = json.dumps({"score": 1.5, "reason": "overflow", "domain": None})
    with patch("agents.zoro.brainstorm_scout.ask", return_value=fake):
        out = scout._llm_judge_relevance("topic")
    assert out["score"] == 1.0


def test_llm_judge_handles_llm_exception():
    with patch("agents.zoro.brainstorm_scout.ask", side_effect=RuntimeError("Anthropic 529")):
        out = scout._llm_judge_relevance("topic")
    assert out["score"] == 0.0
    assert "Anthropic 529" in out["reason"]


# ── Novelty + Cooldown gates ──────────────────────────────────────────────


def test_novelty_gate_filters_recently_pushed():
    pushed_topics.record("zoro", "CGM biohack", ["cgm", "glucose", "biohack"])
    candidate = Topic(
        title="CGM glucose biohack", normalized_keywords=["cgm", "glucose", "biohack"], signals=[]
    )
    fresh = Topic(
        title="Zone 2 cardio", normalized_keywords=["zone", "cardio", "endurance"], signals=[]
    )
    kept = scout.novelty_gate([candidate, fresh])
    assert [t.title for t in kept] == ["Zone 2 cardio"]


def test_cooldown_gate_filters_48h_similar():
    pushed_topics.record("zoro", "glucose topic", ["cgm", "glucose"])
    candidate = Topic(title="cgm monitoring", normalized_keywords=["cgm", "glucose"], signals=[])
    kept = scout.cooldown_gate([candidate])
    assert kept == []


# ── Pick best ──────────────────────────────────────────────────────────────


def test_pick_best_topic_maximizes_velocity_times_relevance():
    lo = Topic(
        title="low", normalized_keywords=[], signals=[], velocity_score=50.0, relevance_score=0.8
    )  # 40
    hi = Topic(
        title="hi", normalized_keywords=[], signals=[], velocity_score=80.0, relevance_score=0.9
    )  # 72
    mid = Topic(
        title="mid", normalized_keywords=[], signals=[], velocity_score=90.0, relevance_score=0.5
    )  # 45
    best = scout.pick_best_topic([lo, hi, mid])
    assert best.title == "hi"


def test_pick_best_topic_empty_returns_none():
    assert scout.pick_best_topic([]) is None


# ── Format publish message ────────────────────────────────────────────────


def test_format_publish_message_includes_topic_signals_mentions():
    t = Topic(
        title="CGM for non-diabetics",
        normalized_keywords=["cgm"],
        signals=[
            Signal(
                source="reddit", topic="CGM", velocity_score=75.0, metadata={"subs": "biohackers"}
            )
        ],
        velocity_score=75.0,
        relevance_reason="近期多篇研究",
    )
    msg = scout.format_publish_message(t, mentions=["@Sanji", "@Robin"])
    assert "CGM for non-diabetics" in msg
    assert "reddit" in msg
    assert "75" in msg
    assert "近期多篇研究" in msg
    assert "@Sanji" in msg and "@Robin" in msg


# ── publish_to_slack ──────────────────────────────────────────────────────


def test_publish_to_slack_calls_web_client():
    topic = Topic(title="t", normalized_keywords=[], signals=[])

    fake_client = MagicMock()
    fake_client.chat_postMessage.return_value = {"ts": "1234.5678", "ok": True}

    with patch("slack_sdk.WebClient", return_value=fake_client) as m_ctor:
        ts = scout.publish_to_slack(topic, channel="C123", bot_token="xoxb-x")

    m_ctor.assert_called_once_with(token="xoxb-x")
    fake_client.chat_postMessage.assert_called_once()
    kwargs = fake_client.chat_postMessage.call_args.kwargs
    assert kwargs["channel"] == "C123"
    assert "t" in kwargs["text"]
    assert ts == "1234.5678"


def test_publish_to_slack_swallows_exception():
    topic = Topic(title="t", normalized_keywords=[], signals=[])

    fake_client = MagicMock()
    fake_client.chat_postMessage.side_effect = RuntimeError("channel_not_found")

    with patch("slack_sdk.WebClient", return_value=fake_client):
        ts = scout.publish_to_slack(topic, channel="Cbad", bot_token="xoxb-x")

    assert ts is None  # 失敗不 raise，回 None


# ── run() — full pipeline ────────────────────────────────────────────────


def test_run_when_no_signals_returns_none():
    """無 signals → 全 pipeline pass through → None。"""
    with patch("agents.zoro.brainstorm_scout.gather_signals", return_value=[]):
        assert scout.run(publish=False) is None


def test_run_injected_signal_passes_all_gates_and_records():
    """注入一個會全過濾網的 signal，驗證 run() 有 record 到 pushed_topics。"""
    sig = Signal(
        source="reddit",
        topic="glucose monitoring biohack",
        velocity_score=80.0,
        metadata={"subreddit": "biohackers"},
    )

    with (
        patch("agents.zoro.brainstorm_scout.gather_signals", return_value=[sig]),
        patch(
            "agents.zoro.brainstorm_scout._llm_judge_relevance",
            return_value={"score": 0.85, "reason": "OK", "domain": "飲食"},
        ),
    ):
        best = scout.run(publish=False)

    assert best is not None
    assert best.title == "glucose monitoring biohack"
    assert best.relevance_score == 0.85

    # 被記下，下次 run 同題應該 cooldown
    from datetime import timedelta

    history = pushed_topics.recent("zoro", since=timedelta(hours=1))
    assert len(history) == 1
    assert history[0].topic == "glucose monitoring biohack"


def test_run_publishes_when_env_set(monkeypatch):
    sig = Signal(source="reddit", topic="glucose biohack", velocity_score=70.0)
    monkeypatch.setenv("ZORO_BRAINSTORM_CHANNEL_ID", "C_BRAIN")
    monkeypatch.setenv("ZORO_SLACK_BOT_TOKEN", "xoxb-zoro")

    fake_client = MagicMock()
    fake_client.chat_postMessage.return_value = {"ts": "t1", "ok": True}

    with (
        patch("agents.zoro.brainstorm_scout.gather_signals", return_value=[sig]),
        patch(
            "agents.zoro.brainstorm_scout._llm_judge_relevance",
            return_value={"score": 0.85, "reason": "OK", "domain": "飲食"},
        ),
        patch("slack_sdk.WebClient", return_value=fake_client),
    ):
        best = scout.run(publish=True)

    assert best is not None
    fake_client.chat_postMessage.assert_called_once()
    kwargs = fake_client.chat_postMessage.call_args.kwargs
    assert kwargs["channel"] == "C_BRAIN"


def test_run_skips_publish_when_env_missing(monkeypatch, caplog):
    """env 缺 channel → log warning 但不 raise，仍 record 到 pushed_topics（下次才不會重推）。"""
    sig = Signal(source="reddit", topic="glucose biohack", velocity_score=70.0)
    monkeypatch.delenv("ZORO_BRAINSTORM_CHANNEL_ID", raising=False)
    monkeypatch.delenv("ZORO_SLACK_BOT_TOKEN", raising=False)

    with (
        patch("agents.zoro.brainstorm_scout.gather_signals", return_value=[sig]),
        patch(
            "agents.zoro.brainstorm_scout._llm_judge_relevance",
            return_value={"score": 0.85, "reason": "OK", "domain": "飲食"},
        ),
    ):
        best = scout.run(publish=True)

    assert best is not None
    # 即使沒 publish，也已經 record
    from datetime import timedelta

    history = pushed_topics.recent("zoro", since=timedelta(hours=1))
    assert len(history) == 1


def test_run_dry_run_does_not_record(monkeypatch):
    """record=False 不寫 pushed_topics（dry-run 用）。"""
    sig = Signal(source="reddit", topic="glucose biohack", velocity_score=70.0)

    with (
        patch("agents.zoro.brainstorm_scout.gather_signals", return_value=[sig]),
        patch(
            "agents.zoro.brainstorm_scout._llm_judge_relevance",
            return_value={"score": 0.85, "reason": "OK", "domain": "飲食"},
        ),
    ):
        best = scout.run(publish=False, record=False)

    assert best is not None
    history = pushed_topics.recent("zoro", since=timedelta(hours=1))
    assert history == []


# ── _strip_json_fence ────────────────────────────────────────────────────


def test_strip_fence_removes_json_wrapper():
    raw = '```json\n{"score": 0.9}\n```'
    assert scout._strip_json_fence(raw) == '{"score": 0.9}'


def test_strip_fence_removes_bare_wrapper():
    raw = '```\n{"score": 0.9}\n```'
    assert scout._strip_json_fence(raw) == '{"score": 0.9}'


def test_strip_fence_noop_on_plain_json():
    assert scout._strip_json_fence('{"score": 0.9}') == '{"score": 0.9}'


def test_llm_judge_parses_fenced_json():
    """LLM 偶爾無視 prompt 加 ```json``` 外殼 — fence strip 後要能 parse。"""
    fenced = '```json\n{"score": 0.9, "reason": "OK", "domain": "飲食"}\n```'
    with patch("agents.zoro.brainstorm_scout.ask", return_value=fenced):
        out = scout._llm_judge_relevance("topic")
    assert out["score"] == 0.9
    assert out["domain"] == "飲食"


# ── gather_signals (Trends primary, Slice C1) ────────────────────────────


def test_gather_signals_calls_trends_discover():
    terms = [
        {
            "title": "ozempic side effects",
            "velocity_score": 80.0,
            "subreddit": "trends",
            "score": 800,
            "num_comments": 0,
            "age_hours": 0.0,
            "url": "",
            "volume": 500_000,
            "related": ["ozempic", "glp-1", "weight loss"],
        },
        {
            "title": "zone 2 cardio",
            "velocity_score": 30.0,
            "subreddit": "trends",
            "score": 300,
            "num_comments": 0,
            "age_hours": 0.0,
            "url": "",
            "volume": 100_000,
            "related": ["zone 2", "cardio", "vo2"],
        },
    ]
    with patch("agents.zoro.trends_api.discover_trending_health", return_value=terms):
        signals = scout.gather_signals()

    assert len(signals) == 2
    assert signals[0].source == "trends"
    assert signals[0].topic == "ozempic side effects"
    assert signals[0].velocity_score == 80.0
    assert signals[0].metadata["volume"] == 500_000
    assert signals[0].metadata["growth_pct"] == 800
    assert signals[0].metadata["related"][:3] == ["ozempic", "glp-1", "weight loss"]


def test_gather_signals_handles_trends_api_failure():
    with patch(
        "agents.zoro.trends_api.discover_trending_health",
        side_effect=RuntimeError("Trends down"),
    ):
        signals = scout.gather_signals()
    assert signals == []


def test_gather_signals_skips_empty_titles():
    terms = [
        {
            "title": "",
            "velocity_score": 80.0,
            "subreddit": "trends",
            "score": 800,
            "num_comments": 0,
            "age_hours": 0.0,
            "url": "",
            "volume": 500_000,
            "related": [],
        },
        {
            "title": "real topic",
            "velocity_score": 60.0,
            "subreddit": "trends",
            "score": 600,
            "num_comments": 0,
            "age_hours": 0.0,
            "url": "",
            "volume": 200_000,
            "related": ["glucose"],
        },
    ]
    with patch("agents.zoro.trends_api.discover_trending_health", return_value=terms):
        signals = scout.gather_signals()
    assert [s.topic for s in signals] == ["real topic"]


# Slice C1: gather_signals 主 source 切到 Trends；Reddit 留到 Slice D OAuth。
# Reddit client 本身仍有單元測試（tests/test_reddit_hot_discovery.py），只是 scout 不呼叫。
