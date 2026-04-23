"""Tests for shared.agent_memory — user-scoped agent memory store."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from shared import agent_memory


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path: Path, monkeypatch):
    """Each test gets its own SQLite DB in tmp_path; schema is re-initialized."""
    db_path = tmp_path / "test.db"

    import shared.state as state

    # Patch the name as it's imported inside shared.state, not at source
    monkeypatch.setattr(state, "get_db_path", lambda: db_path)

    # Close any existing connection and force a fresh one
    if state._conn is not None:
        try:
            state._conn.close()
        except sqlite3.Error:
            pass
    state._conn = None

    agent_memory._SCHEMA_INITIALIZED = False

    yield db_path

    if state._conn is not None:
        try:
            state._conn.close()
        except sqlite3.Error:
            pass
        state._conn = None


def test_add_creates_memory():
    mid = agent_memory.add(
        agent="nami",
        user_id="U1",
        type="preference",
        subject="工作時段",
        content="修修習慣早上做深度工作",
    )
    assert mid > 0

    mems = agent_memory.list_all("nami", "U1")
    assert len(mems) == 1
    assert mems[0].subject == "工作時段"
    assert mems[0].confidence == 1.0


def test_add_upserts_on_same_subject():
    """Same (agent, user_id, subject) should update, not duplicate."""
    id1 = agent_memory.add("nami", "U1", "preference", "工作時段", "早上做深度工作")
    id2 = agent_memory.add("nami", "U1", "preference", "工作時段", "改為下午做深度工作")

    assert id1 == id2
    mems = agent_memory.list_all("nami", "U1")
    assert len(mems) == 1
    assert "下午" in mems[0].content


def test_isolation_by_agent():
    agent_memory.add("nami", "U1", "fact", "船長稱號", "修修 = 船長")
    agent_memory.add("zoro", "U1", "fact", "船長稱號", "修修 = 船長")

    nami_mems = agent_memory.list_all("nami", "U1")
    zoro_mems = agent_memory.list_all("zoro", "U1")
    assert len(nami_mems) == 1
    assert len(zoro_mems) == 1


def test_isolation_by_user_id():
    agent_memory.add("nami", "U1", "preference", "X", "U1's pref")
    agent_memory.add("nami", "U2", "preference", "X", "U2's pref")

    u1 = agent_memory.list_all("nami", "U1")
    u2 = agent_memory.list_all("nami", "U2")
    assert u1[0].content == "U1's pref"
    assert u2[0].content == "U2's pref"


def test_search_keyword_filter():
    agent_memory.add("nami", "U1", "fact", "喜好 1", "喜歡 research 類型")
    agent_memory.add("nami", "U1", "fact", "喜好 2", "不喜歡 short-form video")
    agent_memory.add("nami", "U1", "fact", "睡眠", "晚上 11 點前睡")

    hits = agent_memory.search("nami", "U1", query="research")
    assert len(hits) == 1
    assert "research" in hits[0].content


def test_search_type_filter():
    agent_memory.add("nami", "U1", "preference", "P1", "pref content")
    agent_memory.add("nami", "U1", "fact", "F1", "fact content")

    prefs = agent_memory.search("nami", "U1", type="preference")
    assert len(prefs) == 1
    assert prefs[0].type == "preference"


def test_search_updates_last_accessed():
    mid = agent_memory.add("nami", "U1", "fact", "X", "content")

    # Force last_accessed_at into the past so the assertion is robust to
    # same-microsecond add/search commits (e.g. on SQLite synchronous=NORMAL).
    import shared.state as state

    state._get_conn().execute(
        "UPDATE user_memories SET last_accessed_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
        (mid,),
    )
    state._get_conn().commit()

    before = agent_memory.list_all("nami", "U1")[0].last_accessed_at
    agent_memory.search("nami", "U1")
    after = agent_memory.list_all("nami", "U1")[0].last_accessed_at
    assert after > before


def test_forget_removes_memory():
    mid = agent_memory.add("nami", "U1", "fact", "X", "content")
    assert agent_memory.forget(mid) is True
    assert agent_memory.list_all("nami", "U1") == []
    assert agent_memory.forget(99999) is False


def test_decay_reduces_confidence():
    mid = agent_memory.add("nami", "U1", "fact", "old", "ancient memory")

    # Force old last_accessed_at
    import shared.state as state

    state._get_conn().execute(
        "UPDATE user_memories SET last_accessed_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
        (mid,),
    )
    state._get_conn().commit()

    affected = agent_memory.decay(older_than_days=30, factor=0.5)
    assert affected == 1

    mems = agent_memory.list_all("nami", "U1")
    assert mems[0].confidence == 0.5


def test_list_subjects_returns_existing():
    agent_memory.add("nami", "U1", "fact", "A", "a")
    agent_memory.add("nami", "U1", "preference", "B", "b")
    agent_memory.add("nami", "U2", "fact", "C", "c")  # different user

    subjects = agent_memory.list_subjects("nami", "U1")
    assert subjects == ["A", "B"]


def test_list_subjects_empty():
    assert agent_memory.list_subjects("nami", "U1") == []


def test_list_subjects_with_content_returns_pairs():
    agent_memory.add("nami", "U1", "fact", "B", "b content")
    agent_memory.add("nami", "U1", "preference", "A", "a content")

    pairs = agent_memory.list_subjects_with_content("nami", "U1")
    assert ("A", "a content") in pairs
    assert ("B", "b content") in pairs


def test_format_as_context_empty():
    """沒有記憶時回空字串。"""
    assert agent_memory.format_as_context("nami", "U1") == ""


def test_format_as_context_renders_memories():
    agent_memory.add("nami", "U1", "preference", "工作時段", "修修習慣早上做深度工作")
    agent_memory.add("nami", "U1", "fact", "角色", "修修是船長")

    text = agent_memory.format_as_context("nami", "U1")
    assert "## 你記得關於使用者的事" in text
    assert "[preference] 工作時段" in text
    assert "修修習慣早上做深度工作" in text
    assert "[fact] 角色" in text


def test_format_as_context_isolated_by_user():
    agent_memory.add("nami", "U1", "fact", "A", "U1 的事")
    agent_memory.add("nami", "U2", "fact", "B", "U2 的事")

    u1_ctx = agent_memory.format_as_context("nami", "U1")
    assert "U1 的事" in u1_ctx
    assert "U2 的事" not in u1_ctx


def test_prune_removes_low_confidence():
    agent_memory.add("nami", "U1", "fact", "high", "high conf", confidence=0.9)
    agent_memory.add("nami", "U1", "fact", "low", "low conf", confidence=0.05)

    removed = agent_memory.prune(confidence_threshold=0.1)
    assert removed == 1

    mems = agent_memory.list_all("nami", "U1")
    assert len(mems) == 1
    assert mems[0].subject == "high"


# ---------------------------------------------------------------------------
# Phase 4 Bridge UI — get / update / list_agents_with_memory
# ---------------------------------------------------------------------------


def test_get_returns_memory_by_id():
    mid = agent_memory.add("nami", "U1", "fact", "A", "content A")
    m = agent_memory.get(mid)
    assert m is not None
    assert m.id == mid
    assert m.content == "content A"


def test_get_returns_none_for_unknown_id():
    assert agent_memory.get(99999) is None


def test_update_content_only():
    mid = agent_memory.add("nami", "U1", "preference", "work", "old content")
    updated = agent_memory.update(mid, content="new content")
    assert updated is not None
    assert updated.content == "new content"
    assert updated.type == "preference"  # preserved
    assert updated.subject == "work"


def test_update_type_and_confidence():
    mid = agent_memory.add("nami", "U1", "preference", "work", "x")
    updated = agent_memory.update(mid, type="fact", confidence=0.4)
    assert updated.type == "fact"
    assert updated.confidence == 0.4


def test_update_rejects_invalid_confidence():
    mid = agent_memory.add("nami", "U1", "fact", "x", "y")
    with pytest.raises(ValueError):
        agent_memory.update(mid, confidence=1.5)
    with pytest.raises(ValueError):
        agent_memory.update(mid, confidence=-0.1)


def test_update_unknown_id_returns_none():
    assert agent_memory.update(99999, content="x") is None


def test_update_subject_collision_raises():
    agent_memory.add("nami", "U1", "fact", "subj_a", "a")
    mid_b = agent_memory.add("nami", "U1", "fact", "subj_b", "b")
    with pytest.raises(ValueError, match="subject collision"):
        agent_memory.update(mid_b, subject="subj_a")


def test_update_noop_returns_current_memory():
    mid = agent_memory.add("nami", "U1", "fact", "x", "y")
    result = agent_memory.update(mid)  # nothing to update
    assert result is not None
    assert result.id == mid


def test_list_agents_with_memory_returns_distinct_sorted():
    agent_memory.add("zoro", "U1", "fact", "a", "1")
    agent_memory.add("nami", "U1", "fact", "b", "2")
    agent_memory.add("nami", "U1", "fact", "c", "3")

    agents = agent_memory.list_agents_with_memory()
    assert agents == ["nami", "zoro"]


def test_list_agents_with_memory_empty():
    assert agent_memory.list_agents_with_memory() == []
