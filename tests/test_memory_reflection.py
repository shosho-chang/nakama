"""Tests for shared.memory_reflection — the consolidation / self-learning pass,
plus the bi-temporal supersede semantics it relies on in shared.agent_memory."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from shared import agent_memory, memory_reflection
from shared.state import _get_conn


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path: Path, monkeypatch):
    """Each test gets its own SQLite DB; schema re-initialized (mirrors test_agent_memory)."""
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


def _mock_ask(monkeypatch, ops: list[dict]) -> None:
    monkeypatch.setattr(memory_reflection, "ask", lambda **kw: json.dumps(ops, ensure_ascii=False))


def _seed(agent: str = "nami", user: str = "U1") -> tuple[int, int, int]:
    a = agent_memory.add(agent, user, "preference", "工作習慣", "早上深度工作", confidence=0.8)
    b = agent_memory.add(agent, user, "preference", "工作時段", "下午兩點前最專注", confidence=0.7)
    c = agent_memory.add(agent, user, "fact", "居住地", "住台北", confidence=0.9)
    return a, b, c


def _superseded_by(memory_id: int) -> int | None:
    row = (
        _get_conn()
        .execute("SELECT superseded_by FROM user_memories WHERE id = ?", (memory_id,))
        .fetchone()
    )
    return row["superseded_by"] if row else None


# ── bi-temporal supersede semantics (agent_memory) ────────────────────────────


def test_supersede_hides_from_retrieval():
    a, b, _c = _seed()
    agent_memory.supersede(b, replaced_by=a)
    subjects = {m.subject for m in agent_memory.search("nami", "U1")}
    assert "工作時段" not in subjects
    assert "工作習慣" in subjects
    assert all(m.id != b for m in agent_memory.list_active("nami", "U1"))
    assert "工作時段" not in agent_memory.format_as_context("nami", "U1")
    # extractor-merge view must also exclude it
    assert "工作時段" not in agent_memory.list_subjects("nami", "U1")


def test_readd_revives_superseded_subject():
    a, b, _c = _seed()
    agent_memory.supersede(b, replaced_by=a)
    revived = agent_memory.add(
        "nami", "U1", "preference", "工作時段", "下午兩點前最專注，晚上不開會", confidence=0.85
    )
    assert revived == b  # same row (UNIQUE subject)
    active_ids = {m.id for m in agent_memory.list_active("nami", "U1")}
    assert b in active_ids  # re-asserting the subject brought it back


# ── reflection pass ───────────────────────────────────────────────────────────


def test_reflect_merge_folds_duplicates(monkeypatch):
    a, b, _c = _seed()
    _mock_ask(
        monkeypatch,
        [
            {
                "op": "merge",
                "ids": [a, b],
                "subject": "工作時段",
                "type": "preference",
                "content": "早上深度工作；下午兩點前最專注",
                "confidence": 0.95,
                "reason": "dup",
            }
        ],
    )
    res = memory_reflection.reflect("nami", "U1", apply=True)
    assert res.merged == 1 and res.applied is True
    active = {m.subject: m for m in agent_memory.list_active("nami", "U1")}
    assert set(active) == {"工作時段", "居住地"}  # 工作習慣 absorbed
    survivor = active["工作時段"]
    assert "早上深度工作" in survivor.content and "下午兩點前" in survivor.content
    assert survivor.confidence == pytest.approx(0.95)
    assert _superseded_by(a) == survivor.id  # provenance recorded


def test_reflect_supersede_with_replacement(monkeypatch):
    _a, _b, c = _seed()
    _mock_ask(
        monkeypatch,
        [
            {
                "op": "supersede",
                "id": c,
                "reason": "moved",
                "replacement": {
                    "subject": "現居",
                    "type": "fact",
                    "content": "現居高雄",
                    "confidence": 0.9,
                },
            }
        ],
    )
    res = memory_reflection.reflect("nami", "U1", apply=True)
    assert res.superseded == 1
    subs = {m.subject for m in agent_memory.list_active("nami", "U1")}
    assert "居住地" not in subs and "現居" in subs
    new_id = next(m.id for m in agent_memory.list_active("nami", "U1") if m.subject == "現居")
    assert _superseded_by(c) == new_id


def test_reflect_promote_raises_confidence(monkeypatch):
    _a, _b, c = _seed()
    _mock_ask(monkeypatch, [{"op": "promote", "id": c, "confidence": 0.99, "reason": "core"}])
    memory_reflection.reflect("nami", "U1", apply=True)
    assert agent_memory.get(c).confidence == pytest.approx(0.99)


def test_reflect_drop_soft_invalidates(monkeypatch):
    a, _b, _c = _seed()
    _mock_ask(monkeypatch, [{"op": "drop", "id": a, "reason": "noise"}])
    res = memory_reflection.reflect("nami", "U1", apply=True)
    assert res.dropped == 1
    assert all(m.id != a for m in agent_memory.list_active("nami", "U1"))
    assert agent_memory.get(a) is not None  # soft, not hard-deleted


def test_reflect_dry_run_does_not_mutate(monkeypatch):
    _seed()
    _mock_ask(monkeypatch, [{"op": "drop", "id": 1, "reason": "x"}])
    res = memory_reflection.reflect("nami", "U1", apply=False)
    assert res.applied is False and len(res.ops) == 1
    assert len(agent_memory.list_active("nami", "U1")) == 3  # untouched


def test_reflect_skips_hallucinated_id(monkeypatch):
    _seed()
    _mock_ask(monkeypatch, [{"op": "drop", "id": 99999, "reason": "ghost"}])
    res = memory_reflection.reflect("nami", "U1", apply=True)
    assert res.dropped == 0 and len(res.skipped) == 1
    assert len(agent_memory.list_active("nami", "U1")) == 3


def test_reflect_skips_when_too_few_memories(monkeypatch):
    agent_memory.add("nami", "U1", "fact", "x", "y")
    calls = {"n": 0}

    def _boom(**kw):
        calls["n"] += 1
        return "[]"

    monkeypatch.setattr(memory_reflection, "ask", _boom)
    res = memory_reflection.reflect("nami", "U1", apply=True)
    assert res.reviewed == 1 and calls["n"] == 0  # no LLM call below threshold
