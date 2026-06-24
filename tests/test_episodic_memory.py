"""Tests for shared.episodic_memory + episodic capture + episodic→semantic promotion."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from shared import agent_memory, episodic_memory, memory_extractor, memory_reflection


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
    episodic_memory._SCHEMA_INITIALIZED = False
    yield db_path
    if state._conn is not None:
        try:
            state._conn.close()
        except sqlite3.Error:
            pass
        state._conn = None


def _old(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


# ── storage ───────────────────────────────────────────────────────────────────


def test_add_and_list_recent():
    episodic_memory.add_episodic("nami", "U1", "刪了 7/22 拍攝")
    episodic_memory.add_episodic("nami", "U1", "把錄課排在週二")
    ev = episodic_memory.list_recent("nami", "U1")
    assert {e.observation for e in ev} == {"刪了 7/22 拍攝", "把錄課排在週二"}


def test_list_recent_filters_old_by_days():
    episodic_memory.add_episodic("nami", "U1", "舊事件", occurred_at=_old(40))
    episodic_memory.add_episodic("nami", "U1", "新事件")
    ev = episodic_memory.list_recent("nami", "U1", days=30)
    assert [e.observation for e in ev] == ["新事件"]


def test_mark_promoted_excludes_from_backlog():
    a = episodic_memory.add_episodic("nami", "U1", "週二錄課#1")
    b = episodic_memory.add_episodic("nami", "U1", "週二錄課#2")
    episodic_memory.mark_promoted([a, b], semantic_id=999)
    assert episodic_memory.list_recent("nami", "U1") == []  # promoted → out of backlog
    assert len(episodic_memory.list_recent("nami", "U1", include_promoted=True)) == 2


def test_forget_older_than_soft_invalidates():
    episodic_memory.add_episodic("nami", "U1", "很舊", occurred_at=_old(90))
    episodic_memory.add_episodic("nami", "U1", "最近")
    assert episodic_memory.forget_older_than(days=60) == 1
    survivors = [e.observation for e in episodic_memory.list_recent("nami", "U1", days=365)]
    assert survivors == ["最近"]


# ── capture (extractor) ─────────────────────────────────────────────────────────


def test_extract_episodic_writes_events(monkeypatch):
    monkeypatch.setattr(
        memory_extractor,
        "ask",
        lambda **kw: '["刪了 7/22 拍攝，理由是專心準備論文", "把錄課排在週二"]',
    )
    msgs = [{"role": "user", "content": "我把 7/22 拍攝刪了，要專心準備論文，以後錄課都排週二"}]
    ids = memory_extractor.extract_episodic_from_messages("nami", "U1", msgs)
    assert len(ids) == 2
    assert "把錄課排在週二" in {e.observation for e in episodic_memory.list_recent("nami", "U1")}


# ── promotion (reflection) ───────────────────────────────────────────────────────


def test_reflect_promote_episodic_creates_semantic(monkeypatch):
    e1 = episodic_memory.add_episodic("nami", "U1", "週二早上錄課")
    e2 = episodic_memory.add_episodic("nami", "U1", "又在週二錄課")
    e3 = episodic_memory.add_episodic("nami", "U1", "這週還是週二錄課")
    monkeypatch.setattr(
        memory_reflection,
        "ask",
        lambda **kw: json.dumps(
            [
                {
                    "op": "promote_episodic",
                    "episodic_ids": [e1, e2, e3],
                    "subject": "錄課習慣",
                    "type": "preference",
                    "content": "固定週二早上錄課",
                    "confidence": 0.85,
                    "reason": "連三次週二",
                }
            ],
            ensure_ascii=False,
        ),
    )
    res = memory_reflection.reflect("nami", "U1", apply=True)
    assert res.promoted_episodic == 1
    assert "錄課習慣" in {m.subject for m in agent_memory.search("nami", "U1")}
    assert episodic_memory.list_recent("nami", "U1") == []  # all three folded in


def test_reflect_promote_episodic_rejects_hallucinated_eid(monkeypatch):
    for obs in ("事件A", "事件B", "事件C"):
        episodic_memory.add_episodic("nami", "U1", obs)
    monkeypatch.setattr(
        memory_reflection,
        "ask",
        lambda **kw: json.dumps(
            [
                {
                    "op": "promote_episodic",
                    "episodic_ids": [99998, 99999],
                    "subject": "x",
                    "type": "fact",
                    "content": "y",
                    "confidence": 0.8,
                    "reason": "ghost",
                }
            ],
            ensure_ascii=False,
        ),
    )
    res = memory_reflection.reflect("nami", "U1", apply=True)
    assert res.promoted_episodic == 0 and len(res.skipped) == 1
