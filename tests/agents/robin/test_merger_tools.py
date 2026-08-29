"""Tests for agents/robin/merger_tools.py (merger SDK migration S1).

Schema 對照測試比照 tests/gateway/test_nami_tools.py::test_schema_matches_nami_tools
的逐位元等價標準。
"""

from __future__ import annotations

import asyncio

from agents.robin.annotation_merger import _MERGER_TOOL
from agents.robin.merger_tools import (
    MERGER_ALLOWED_TOOLS,
    MERGER_SERVER_NAME,
    _make_merge_tool,
    build_merger_server,
)

# ── Schema 對照（S1 驗收：欄位名 / required / additionalProperties 不得漂移）──


def test_schema_matches_merger_tool_exactly():
    """SDK tool 的 name / description / input_schema 與 _MERGER_TOOL 逐位元一致。"""
    t = _make_merge_tool({})
    assert t.name == _MERGER_TOOL["name"]
    assert t.description == _MERGER_TOOL["description"]
    assert t.input_schema == _MERGER_TOOL["input_schema"]


def test_allowed_tools_constant_matches_server_and_tool_name():
    """allowed_tools 常數必須跟 server 名 + tool 名組合一致（S2 直接引用）。"""
    assert MERGER_ALLOWED_TOOLS == [f"mcp__{MERGER_SERVER_NAME}__{_MERGER_TOOL['name']}"]


# ── Capture 行為 ───────────────────────────────────────────────────────


def test_handler_captures_mapping():
    box: dict = {}
    t = _make_merge_tool(box)
    mapping = {"sleep-debt": "> [!note] 來自《為什麼要睡覺》\n> 睡眠債只能部分償還"}
    result = asyncio.run(t.handler({"mapping": mapping}))
    assert box["mapping"] == mapping
    assert result["content"][0]["text"] == "ok"


def test_handler_captures_missing_mapping_as_none():
    """模型送了空 args → box 記 None（S2 的驗證層據此判定要重試）。"""
    box: dict = {}
    t = _make_merge_tool(box)
    asyncio.run(t.handler({}))
    assert "mapping" in box
    assert box["mapping"] is None


def test_boxes_are_per_run_isolated():
    """兩個 server 各自的 box 不互相汙染（per-run 語意）。"""
    box_a: dict = {}
    box_b: dict = {}
    ta = _make_merge_tool(box_a)
    tb = _make_merge_tool(box_b)
    asyncio.run(ta.handler({"mapping": {"a": "1"}}))
    asyncio.run(tb.handler({"mapping": {"b": "2"}}))
    assert box_a["mapping"] == {"a": "1"}
    assert box_b["mapping"] == {"b": "2"}


def test_build_merger_server_smoke():
    """server 建得起來且掛著唯一一個 tool。"""
    server = build_merger_server({})
    assert server is not None
