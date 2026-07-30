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
        "arxiv_citations",  # 純 Semantic Scholar GET；名字不吻合 _lookup 後綴所以明列
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


def _make_ask_user_tool(answer_box: dict) -> SdkMcpTool:
    """ask_user 的 SDK 版（S3，PreToolUse defer 流程）。

    不走 ``_execute_tool``：第一次呼叫會被 PreToolUse hook 以 ``defer`` 攔下、
    進程結束把問題送回 Slack —— 執行不到這裡。resume 時同一個 tool call 重新
    觸發 hook，hook allow 並把使用者回覆寫進 **in-process 的 answer_box**，
    這裡讀取後清空、交還給模型當 tool result。

    刻意**完全不信任 ``args``**：回覆不經 CLI 往返（消除 schema 外欄位傳遞
    的單點），且模型自填 answer、或並發 tool call 讓 defer 被忽略時，拿到的
    是 is_error —— 不會發生模型替使用者回答自己的確認問題（PR #1121 review
    M1/M2）。
    """
    spec = next(s for s in NAMI_TOOLS if s["name"] == "ask_user")

    @tool(spec["name"], spec["description"], spec["input_schema"])
    async def _handler(args: dict) -> dict:
        value = answer_box.get("value")
        answer_box["value"] = None
        if value is None:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "（尚未取得使用者回覆。ask_user 必須單獨一輪呼叫、"
                            "不可與其他工具並發；請單獨重新呼叫一次 ask_user。）"
                        ),
                    }
                ],
                "is_error": True,
            }
        return {"content": [{"type": "text", "text": str(value)}]}

    return _handler


def build_nami_sdk_tools(
    handler: NamiHandler,
    *,
    include_ask_user: bool = False,
    answer_box: dict | None = None,
) -> list[SdkMcpTool]:
    """Return one SdkMcpTool per NAMI_TOOLS entry; ask_user 只在 S3 defer 流程要求時附上。"""
    tools = [_make_tool(handler, spec) for spec in NAMI_TOOLS if spec["name"] != "ask_user"]
    if include_ask_user:
        tools.append(_make_ask_user_tool(answer_box if answer_box is not None else {}))
    return tools


def build_nami_server(
    handler: NamiHandler,
    *,
    include_ask_user: bool = False,
    answer_box: dict | None = None,
) -> object:
    """Build an in-process MCP server exposing Nami tools（ask_user 依參數）."""
    return create_sdk_mcp_server(
        name="nami",
        version=_SERVER_VERSION,
        tools=build_nami_sdk_tools(
            handler, include_ask_user=include_ask_user, answer_box=answer_box
        ),
    )
