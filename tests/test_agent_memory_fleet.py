"""Cross-agent fleet sharing for user_memories (Memory v2 Phase 2b).

A memory promoted to FLEET_AGENT is read by every agent, so the whole fleet
"knows you" — not just the agent that happened to learn it. Agent-specific rows
override the fleet copy of the same subject.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from shared import agent_memory, memory_reflection
from shared.agent_memory import FLEET_AGENT


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


def test_share_moves_to_fleet_and_supersedes_origin():
    mid = agent_memory.add("nami", "U1", "fact", "專業領域", "健康長壽創作者", confidence=0.9)
    fleet_id = agent_memory.share(mid)
    assert fleet_id is not None and fleet_id != mid
    # origin no longer in nami's own active set...
    assert all(m.subject != "專業領域" for m in agent_memory.list_active("nami", "U1"))
    # ...but nami still sees it via the fleet scope
    assert "專業領域" in {m.subject for m in agent_memory.search("nami", "U1")}


def test_fleet_memory_visible_to_other_agent():
    mid = agent_memory.add("nami", "U1", "preference", "溝通風格", "偏好簡潔直接", confidence=0.9)
    agent_memory.share(mid)
    # zoro never learned this, but reads it via the fleet scope
    assert "溝通風格" in {m.subject for m in agent_memory.search("zoro", "U1")}
    assert "偏好簡潔直接" in agent_memory.format_as_context("zoro", "U1")


def test_agent_specific_overrides_fleet_same_subject():
    f = agent_memory.add("nami", "U1", "fact", "居住地", "住台北", confidence=0.8)
    agent_memory.share(f)  # fleet now has 居住地=住台北
    agent_memory.add("zoro", "U1", "fact", "居住地", "住台北信義區", confidence=0.95)
    hits = [m for m in agent_memory.search("zoro", "U1") if m.subject == "居住地"]
    assert len(hits) == 1 and hits[0].content == "住台北信義區"  # zoro's override wins


def test_include_fleet_false_sees_only_own():
    mid = agent_memory.add("nami", "U1", "fact", "x", "y")
    agent_memory.share(mid)  # x moves to fleet; nami's row superseded
    assert agent_memory.search("nami", "U1", include_fleet=False) == []


def test_list_agents_excludes_fleet():
    agent_memory.add("nami", "U1", "fact", "a", "b")
    agent_memory.share(agent_memory.add("nami", "U1", "fact", "c", "d"))
    agents = agent_memory.list_agents_with_memory()
    assert "nami" in agents and FLEET_AGENT not in agents


def test_reflect_share_op_promotes_to_fleet(monkeypatch):
    uid = "U1"
    a = agent_memory.add("nami", uid, "fact", "身分", "健康長壽內容創作者", confidence=0.9)
    agent_memory.add("nami", uid, "preference", "回報格式", "要每週趨勢報告", confidence=0.8)
    agent_memory.add("nami", uid, "fact", "填充", "湊到門檻", confidence=0.5)
    monkeypatch.setattr(
        memory_reflection,
        "ask",
        lambda **kw: json.dumps(
            [{"op": "share", "id": a, "reason": "通用身分"}], ensure_ascii=False
        ),
    )
    res = memory_reflection.reflect("nami", uid, apply=True)
    assert res.shared == 1
    # a different agent now sees the shared identity fact
    assert "身分" in {m.subject for m in agent_memory.search("sanji", uid)}
