"""Wraps NamiHandler._execute_tool delegates as Claude Agent SDK in-process MCP tools.

S1 of the Nami → Agent SDK migration (issue #1112). The existing loop logic in
gateway/handlers/nami.py is unchanged; this module only creates the SDK tool
registry for the future S2 Agent SDK loop.
"""

from __future__ import annotations

from claude_agent_sdk import SdkMcpTool, create_sdk_mcp_server, tool
from mcp.types import ToolAnnotations

from gateway.handlers.nami import NAMI_TOOLS, NamiHandler
from shared.events import emit
from shared.log import kb_log

_SERVER_VERSION = "0.1.0"

# Tools whose side effects are purely read-only — readOnlyHint lets MCP clients
# skip confirmation prompts for these calls.
_READ_ONLY_NAMES: frozenset[str] = frozenset(
    [s["name"] for s in NAMI_TOOLS if s["name"].startswith(("list_", "read_"))]
    + [s["name"] for s in NAMI_TOOLS if s["name"].endswith("_lookup")]
    + [
        "search_gmail_history",
        "get_gmail_message",
        "web_search",
        "fetch_url",
        "youtube_transcript",
    ]
)


def _make_tool(handler: NamiHandler, spec: dict) -> SdkMcpTool:
    """Build one SdkMcpTool wrapping handler._execute_tool for a single NAMI_TOOLS entry."""
    name: str = spec["name"]
    description: str = spec["description"]
    input_schema: dict = spec["input_schema"]
    annotations = ToolAnnotations(readOnlyHint=True) if name in _READ_ONLY_NAMES else None

    @tool(name, description, input_schema, annotations=annotations)
    async def _handler(args: dict) -> dict:
        outcome = handler._execute_tool(name, args)
        if outcome.event and not outcome.is_error:
            emit("nami", outcome.event["name"], outcome.event["payload"])
            kb_log("nami", outcome.event["name"], outcome.event.get("log", ""))
        return {
            "content": [{"type": "text", "text": outcome.content}],
            "is_error": outcome.is_error,
        }

    return _handler


def build_nami_sdk_tools(handler: NamiHandler) -> list[SdkMcpTool]:
    """Return one SdkMcpTool per NAMI_TOOLS entry, excluding ask_user."""
    return [_make_tool(handler, spec) for spec in NAMI_TOOLS if spec["name"] != "ask_user"]


def build_nami_server(handler: NamiHandler) -> object:
    """Build an in-process MCP server exposing all Nami tools except ask_user."""
    return create_sdk_mcp_server(
        name="nami",
        version=_SERVER_VERSION,
        tools=build_nami_sdk_tools(handler),
    )
