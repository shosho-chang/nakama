"""shared/pushed_topics.py — CRUD + Jaccard 近似度單元測試。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from shared import pushed_topics


@pytest.fixture(autouse=True)
def _clean_zoro():
    """每個 test case 前清掉 zoro 的 pushed_topics，確保獨立。"""
    pushed_topics.delete_for_agent("zoro")
    yield
    pushed_topics.delete_for_agent("zoro")


def test_normalize_keywords_strips_punct_and_dedupes():
    out = pushed_topics.normalize_keywords(["CGM", "cgm", "  Glucose, ", "", "睡眠！"])
    assert out == ["cgm", "glucose", "睡眠"]


def test_jaccard_empty_is_zero_not_one():
    """空集合對空集合回 0 — 避免把空題當成「跟任何東西近似」。"""
    assert pushed_topics.jaccard(set(), set()) == 0.0
    assert pushed_topics.jaccard({"a"}, set()) == 0.0


def test_jaccard_basic():
    a = {"cgm", "glucose", "non-diabetic"}
    b = {"cgm", "glucose", "fasting"}
    # inter=2 union=4 → 0.5
    assert pushed_topics.jaccard(a, b) == pytest.approx(0.5)


def test_record_and_recent_round_trip():
    pushed_topics.record("zoro", "CGM for non-diabetics", ["cgm", "glucose", "biohack"])
    topics = pushed_topics.recent("zoro", since=timedelta(hours=1))
    assert len(topics) == 1
    t = topics[0]
    assert t.topic == "CGM for non-diabetics"
    assert t.normalized_keywords == frozenset({"cgm", "glucose", "biohack"})
    assert (datetime.now(timezone.utc) - t.pushed_at).total_seconds() < 5


def test_is_novel_true_when_no_overlap():
    pushed_topics.record("zoro", "睡眠", ["sleep", "circadian"])
    assert pushed_topics.is_novel("zoro", ["fasting", "autophagy"]) is True


def test_is_novel_false_when_similar_past_push():
    pushed_topics.record("zoro", "CGM biohack", ["cgm", "glucose", "biohack"])
    # 新題 3 詞重合 2 詞，Jaccard=2/4=0.5 < 0.6 → 仍然 novel
    assert pushed_topics.is_novel("zoro", ["cgm", "glucose", "ketone"]) is True
    # 新題 3 詞重合 3 詞，Jaccard=3/3=1.0 → not novel
    assert pushed_topics.is_novel("zoro", ["cgm", "glucose", "biohack"]) is False


def test_is_on_cooldown_stricter_than_novelty():
    """Cooldown 閾值 0.3 比 novelty 0.6 嚴 — Jaccard=0.5 兩題：cooldown=True, novelty=True。"""
    pushed_topics.record("zoro", "CGM biohack", ["cgm", "glucose", "biohack"])
    # Jaccard=2/4=0.5 ≥ 0.3 cooldown threshold
    assert pushed_topics.is_on_cooldown("zoro", ["cgm", "glucose", "ketone"]) is True


def test_is_on_cooldown_respects_time_window():
    """超過 48h 的舊 push 不觸發 cooldown。"""
    # 手動插入一筆 72h 前的 row（繞過 record 的 now() 寫入）
    pushed_topics._ensure_schema()
    from shared.state import _get_conn

    conn = _get_conn()
    old = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
    conn.execute(
        "INSERT INTO pushed_topics (agent, topic, normalized_keywords, pushed_at) "
        "VALUES (?, ?, ?, ?)",
        ("zoro", "old CGM", "cgm glucose biohack", old),
    )
    conn.commit()

    # 48h 窗外 → 不 cooldown
    assert pushed_topics.is_on_cooldown("zoro", ["cgm", "glucose"], hours=48) is False
    # 14d 窗內 → still not novel
    assert pushed_topics.is_novel("zoro", ["cgm", "glucose", "biohack"], days=14) is False


def test_agents_are_isolated():
    """A agent 的 pushed_topic 不影響 B agent 的 novelty 判斷。"""
    pushed_topics.record("zoro", "CGM", ["cgm", "glucose"])
    assert pushed_topics.is_novel("other_agent", ["cgm", "glucose"]) is True


def test_empty_candidate_is_not_novel():
    """空候選 keywords 不算 novel（防呆）。"""
    assert pushed_topics.is_novel("zoro", []) is False
    assert pushed_topics.is_on_cooldown("zoro", []) is False
