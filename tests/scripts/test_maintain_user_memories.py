"""Tests for scripts.maintain_user_memories — decay/prune CLI for user_memories."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts import maintain_user_memories
from shared import agent_memory


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "test.db"

    import shared.state as state

    monkeypatch.setattr(state, "get_db_path", lambda: db_path)

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


def _set_last_accessed(memory_id: int, days_ago: int) -> None:
    """Backdate a memory's last_accessed_at — agent_memory has no public API for this."""
    from shared.state import _get_conn

    when = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    _get_conn().execute(
        "UPDATE user_memories SET last_accessed_at = ? WHERE id = ?",
        (when, memory_id),
    )
    _get_conn().commit()


def test_decay_dry_run_counts_but_does_not_change():
    mid = agent_memory.add(agent="nami", user_id="U1", type="fact", subject="s", content="c")
    _set_last_accessed(mid, days_ago=40)

    exit_code = maintain_user_memories.main(["decay", "--dry-run"])
    assert exit_code == 0

    fresh = agent_memory.get(mid)
    assert fresh is not None
    assert fresh.confidence == 1.0


def test_decay_applies_factor_to_stale_rows():
    fresh_id = agent_memory.add(
        agent="nami", user_id="U1", type="fact", subject="fresh", content="x"
    )
    stale_id = agent_memory.add(
        agent="nami", user_id="U1", type="fact", subject="stale", content="y"
    )
    _set_last_accessed(stale_id, days_ago=40)

    exit_code = maintain_user_memories.main(["decay", "--factor", "0.5"])
    assert exit_code == 0

    assert agent_memory.get(fresh_id).confidence == 1.0
    assert agent_memory.get(stale_id).confidence == pytest.approx(0.5)


def test_prune_deletes_below_threshold():
    keep_id = agent_memory.add(
        agent="nami",
        user_id="U1",
        type="fact",
        subject="keep",
        content="x",
        confidence=0.5,
    )
    drop_id = agent_memory.add(
        agent="nami",
        user_id="U1",
        type="fact",
        subject="drop",
        content="y",
        confidence=0.05,
    )

    exit_code = maintain_user_memories.main(["prune"])
    assert exit_code == 0

    assert agent_memory.get(keep_id) is not None
    assert agent_memory.get(drop_id) is None


def test_prune_dry_run_does_not_delete():
    drop_id = agent_memory.add(
        agent="nami",
        user_id="U1",
        type="fact",
        subject="drop",
        content="y",
        confidence=0.05,
    )

    exit_code = maintain_user_memories.main(["prune", "--dry-run"])
    assert exit_code == 0
    assert agent_memory.get(drop_id) is not None


def test_prune_custom_threshold():
    mid = agent_memory.add(
        agent="nami",
        user_id="U1",
        type="fact",
        subject="s",
        content="c",
        confidence=0.3,
    )
    exit_code = maintain_user_memories.main(["prune", "--threshold", "0.5"])
    assert exit_code == 0
    assert agent_memory.get(mid) is None


def test_decay_no_stale_rows_is_noop():
    agent_memory.add(agent="nami", user_id="U1", type="fact", subject="s", content="c")
    exit_code = maintain_user_memories.main(["decay"])
    assert exit_code == 0


def test_observability_log_on_inject(caplog):
    agent_memory.add(agent="nami", user_id="U1", type="fact", subject="s1", content="c1")
    agent_memory.add(agent="nami", user_id="U1", type="preference", subject="s2", content="c2")

    with caplog.at_level("INFO", logger="nakama.agent_memory"):
        block = agent_memory.format_as_context("nami", "U1")

    assert block != ""
    inject_logs = [r for r in caplog.records if "agent_memory.inject " in r.getMessage()]
    assert len(inject_logs) == 1
    msg = inject_logs[0].getMessage()
    assert "agent=nami" in msg
    assert "count=2" in msg
    assert "s1" in msg and "s2" in msg


def test_observability_log_on_empty_inject(caplog):
    with caplog.at_level("INFO", logger="nakama.agent_memory"):
        block = agent_memory.format_as_context("nami", "U_NONE")

    assert block == ""
    empty_logs = [r for r in caplog.records if "agent_memory.inject_empty" in r.getMessage()]
    assert len(empty_logs) == 1
