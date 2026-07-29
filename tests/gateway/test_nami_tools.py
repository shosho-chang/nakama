"""Tests for gateway/handlers/nami_tools.py (S1 in-process MCP server wrapper)."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from gateway.handlers.nami import NAMI_TOOLS, NamiHandler, _ToolOutcome
from gateway.handlers.nami_tools import build_nami_sdk_tools, build_nami_server


@pytest.fixture
def handler():
    return NamiHandler()


def _get_tool(tools, name):
    return next(t for t in tools if t.name == name)


# ── Schema coverage ────────────────────────────────────────────────────


def test_tool_count(handler):
    """27 wrappers built — NAMI_TOOLS minus the ask_user entry."""
    tools = build_nami_sdk_tools(handler)
    assert len(tools) == len(NAMI_TOOLS) - 1


def test_ask_user_excluded(handler):
    tools = build_nami_sdk_tools(handler)
    assert "ask_user" not in {t.name for t in tools}


def test_coverage_exact(handler):
    """Wrapped set equals NAMI_TOOLS minus ask_user — no extra, no missing."""
    tools = build_nami_sdk_tools(handler)
    expected = {s["name"] for s in NAMI_TOOLS if s["name"] != "ask_user"}
    assert {t.name for t in tools} == expected


@pytest.mark.parametrize("spec", [s for s in NAMI_TOOLS if s["name"] != "ask_user"])
def test_schema_matches_nami_tools(handler, spec):
    """Each wrapper's description and input_schema mirror the NAMI_TOOLS entry exactly."""
    tools = build_nami_sdk_tools(handler)
    t = _get_tool(tools, spec["name"])
    assert t.description == spec["description"]
    assert t.input_schema == spec["input_schema"]


# ── Behaviour ──────────────────────────────────────────────────────────


def test_content_goes_to_text(handler):
    """(a) outcome.content appears as the MCP result text."""
    tools = build_nami_sdk_tools(handler)
    t = _get_tool(tools, "create_project")
    with patch.object(handler, "_execute_tool", return_value=_ToolOutcome(content="hello")):
        result = asyncio.run(t.handler({}))
    assert result["content"][0]["text"] == "hello"


def test_is_error_true_propagated(handler):
    """(b) is_error=True is forwarded into the MCP result."""
    tools = build_nami_sdk_tools(handler)
    t = _get_tool(tools, "create_project")
    with patch.object(
        handler, "_execute_tool", return_value=_ToolOutcome(content="boom", is_error=True)
    ):
        result = asyncio.run(t.handler({}))
    assert result["is_error"] is True


def test_is_error_false_propagated(handler):
    """(b) is_error=False is forwarded into the MCP result."""
    tools = build_nami_sdk_tools(handler)
    t = _get_tool(tools, "create_project")
    with patch.object(handler, "_execute_tool", return_value=_ToolOutcome(content="ok")):
        result = asyncio.run(t.handler({}))
    assert result["is_error"] is False


def test_emit_and_kb_log_called_on_success_with_event(handler):
    """(c) When outcome has an event and is_error=False, emit and kb_log are each called once."""
    tools = build_nami_sdk_tools(handler)
    t = _get_tool(tools, "create_project")
    event = {
        "name": "project_created",
        "payload": {"title": "test", "content_type": "youtube"},
        "log": "test project",
    }
    outcome = _ToolOutcome(content="✅", event=event)
    with (
        patch.object(handler, "_execute_tool", return_value=outcome),
        patch("gateway.handlers.nami_tools.emit") as mock_emit,
        patch("gateway.handlers.nami_tools.kb_log") as mock_kb_log,
    ):
        asyncio.run(t.handler({}))
    mock_emit.assert_called_once_with("nami", "project_created", event["payload"])
    mock_kb_log.assert_called_once_with("nami", "project_created", "test project")


def test_no_emit_on_error(handler):
    """(d) When is_error=True, emit and kb_log are NOT called even if event is present."""
    tools = build_nami_sdk_tools(handler)
    t = _get_tool(tools, "create_project")
    event = {"name": "project_created", "payload": {}, "log": ""}
    outcome = _ToolOutcome(content="err", is_error=True, event=event)
    with (
        patch.object(handler, "_execute_tool", return_value=outcome),
        patch("gateway.handlers.nami_tools.emit") as mock_emit,
        patch("gateway.handlers.nami_tools.kb_log") as mock_kb_log,
    ):
        asyncio.run(t.handler({}))
    mock_emit.assert_not_called()
    mock_kb_log.assert_not_called()


def test_build_nami_server_returns_server_config(handler):
    """build_nami_server returns a dict-shaped McpSdkServerConfig for the 'nami' server."""
    server = build_nami_server(handler)
    # McpSdkServerConfig is a TypedDict; validate its structural keys
    assert server["type"] == "sdk"
    assert server["name"] == "nami"
