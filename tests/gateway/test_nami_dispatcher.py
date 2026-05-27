"""Test that NamiHandler._execute_tool routes the new tool names to the right executors.

Catches typos / ordering regressions in the ``if name == ...`` dispatcher chain in
``gateway/handlers/nami.py:_execute_tool``. Each new tool gets a monkeypatched
executor that records being called; we then verify dispatcher routes correctly.
"""

from __future__ import annotations

import pytest

from gateway.handlers.nami import NAMI_TOOLS, NamiHandler, _ToolOutcome


@pytest.fixture
def handler():
    return NamiHandler()


_NEW_TOOL_NAMES = ["arxiv_lookup", "arxiv_citations", "youtube_transcript"]


def test_new_tools_are_registered_in_NAMI_TOOLS():
    """Each new tool name must appear exactly once in the public NAMI_TOOLS list."""
    names = [t["name"] for t in NAMI_TOOLS]
    for tool_name in _NEW_TOOL_NAMES:
        assert names.count(tool_name) == 1, f"{tool_name} not in NAMI_TOOLS or duplicated"


@pytest.mark.parametrize(
    "tool_name,method_name",
    [
        ("arxiv_lookup", "_tool_arxiv_lookup"),
        ("arxiv_citations", "_tool_arxiv_citations"),
        ("youtube_transcript", "_tool_youtube_transcript"),
    ],
)
def test_execute_tool_dispatches_to_correct_executor(monkeypatch, handler, tool_name, method_name):
    """Calling _execute_tool with a new tool name must route to its matching _tool_X method."""
    sentinel = _ToolOutcome(content=f"sentinel-{tool_name}", is_error=False)
    called_with: dict = {}

    def fake_executor(self, input_):
        called_with["input"] = input_
        return sentinel

    monkeypatch.setattr(NamiHandler, method_name, fake_executor)
    result = handler._execute_tool(tool_name, {"probe": "yes"})

    assert result is sentinel, f"{tool_name} routed somewhere other than {method_name}"
    assert called_with["input"] == {"probe": "yes"}


def test_execute_tool_unknown_name_returns_error(handler):
    """Sanity check the dispatcher's fall-through for unrecognised tool names."""
    result = handler._execute_tool("definitely_not_a_real_tool", {})
    assert result.is_error is True
    assert "Unknown tool" in result.content


def test_new_tools_have_valid_anthropic_tool_schema():
    """NAMI_TOOLS entries must have the shape Anthropic tool-use API expects."""
    for tool in NAMI_TOOLS:
        if tool["name"] not in _NEW_TOOL_NAMES:
            continue
        assert "name" in tool
        assert "description" in tool
        assert isinstance(tool["description"], str) and len(tool["description"]) > 20
        schema = tool["input_schema"]
        assert schema["type"] == "object"
        assert "properties" in schema
        assert isinstance(schema["properties"], dict)
        # Every property declared in `required` must exist in `properties`.
        for req in schema.get("required", []):
            assert req in schema["properties"], (
                f"{tool['name']}: required field {req!r} not in properties"
            )
