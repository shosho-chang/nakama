"""Unit tests for gateway.conversation_state."""

from __future__ import annotations

import time

from gateway.conversation_state import ConversationStore


def test_start_stores_conversation():
    store = ConversationStore()
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
    store = ConversationStore()
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
    store = ConversationStore()
    assert store.get("nonexistent") is None


def test_update_modifies_state_and_bumps_activity():
    store = ConversationStore()
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
    store = ConversationStore()
    store.update("nonexistent", {"x": 1})  # should not raise


def test_end_removes_conversation():
    store = ConversationStore()
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
    store = ConversationStore(timeout_seconds=0)
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
    store = ConversationStore()
    store.start(thread_ts="a", channel="C", user_id="U", agent_name="nami", flow_name="f1")
    store.start(thread_ts="b", channel="C", user_id="U", agent_name="zoro", flow_name="f2")
    assert store.active_count() == 2
    assert store.get("a").agent_name == "nami"
    assert store.get("b").agent_name == "zoro"
