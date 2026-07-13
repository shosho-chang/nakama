"""Unit tests for gateway.conversation_state."""

from __future__ import annotations

import time

from gateway.conversation_state import ConversationStore


def _store(timeout_seconds: int = 86400) -> ConversationStore:
    """Isolated in-memory store per test (no shared file, no cross-test bleed)."""
    return ConversationStore(timeout_seconds=timeout_seconds, db_path=":memory:")


def test_start_stores_conversation():
    store = _store()
    conv = store.start(
        thread_ts="1.2",
        channel="C1",
        user_id="U1",
        agent_name="nami",
        flow_name="project_bootstrap",
        state={"step": "content_type"},
    )
    assert conv.thread_ts == "1.2"
    assert conv.state == {"step": "content_type"}
    assert store.active_count() == 1


def test_get_returns_conversation():
    store = _store()
    store.start(
        thread_ts="1.2",
        channel="C1",
        user_id="U1",
        agent_name="nami",
        flow_name="project_bootstrap",
    )
    conv = store.get("1.2")
    assert conv is not None
    assert conv.agent_name == "nami"


def test_get_missing_returns_none():
    store = _store()
    assert store.get("nonexistent") is None


def test_update_modifies_state_and_bumps_activity():
    store = _store()
    store.start(
        thread_ts="1.2",
        channel="C1",
        user_id="U1",
        agent_name="nami",
        flow_name="project_bootstrap",
        state={"step": "a"},
    )
    before = store.get("1.2").last_activity
    time.sleep(0.01)
    store.update("1.2", {"step": "b"})
    conv = store.get("1.2")
    assert conv.state == {"step": "b"}
    assert conv.last_activity > before


def test_update_missing_is_noop():
    store = _store()
    store.update("nonexistent", {"x": 1})  # should not raise


def test_end_removes_conversation():
    store = _store()
    store.start(
        thread_ts="1.2",
        channel="C1",
        user_id="U1",
        agent_name="nami",
        flow_name="project_bootstrap",
    )
    store.end("1.2")
    assert store.get("1.2") is None
    assert store.active_count() == 0


def test_timeout_evicts_stale_conversation():
    store = _store(timeout_seconds=0)
    store.start(
        thread_ts="1.2",
        channel="C1",
        user_id="U1",
        agent_name="nami",
        flow_name="project_bootstrap",
    )
    time.sleep(0.01)
    assert store.get("1.2") is None
    assert store.active_count() == 0


def test_multiple_threads_isolated():
    store = _store()
    store.start(thread_ts="a", channel="C", user_id="U", agent_name="nami", flow_name="f1")
    store.start(thread_ts="b", channel="C", user_id="U", agent_name="zoro", flow_name="f2")
    assert store.active_count() == 2
    assert store.get("a").agent_name == "nami"
    assert store.get("b").agent_name == "zoro"


def test_get_latest_for_user_and_agent_picks_most_recent():
    store = _store()
    store.start(thread_ts="old", channel="D1", user_id="U1", agent_name="nami", flow_name="f")
    time.sleep(0.01)
    store.start(thread_ts="new", channel="D1", user_id="U1", agent_name="nami", flow_name="f")
    # different user must not match
    store.start(thread_ts="other", channel="D1", user_id="U2", agent_name="nami", flow_name="f")
    conv = store.get_latest_for_user_and_agent("U1", "nami")
    assert conv is not None and conv.thread_ts == "new"


def test_state_survives_new_store_instance_same_db(tmp_path):
    """The whole point of the SQLite rewrite: a gateway restart (= new store
    pointing at the same file) must NOT drop in-flight conversation context."""
    db = str(tmp_path / "conversations.db")
    s1 = ConversationStore(db_path=db)
    s1.start(
        thread_ts="1.2",
        channel="D1",
        user_id="U1",
        agent_name="nami",
        flow_name="nami_agent",
        state={"messages": [{"role": "user", "content": "刪 7/22 那筆"}]},
    )

    s2 = ConversationStore(db_path=db)  # simulate process restart
    conv = s2.get("1.2")
    assert conv is not None
    assert conv.state["messages"][0]["content"] == "刪 7/22 那筆"
