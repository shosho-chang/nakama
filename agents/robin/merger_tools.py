"""annotation_merger 的 in-process MCP server（S1 — B1 capture 模式）。

計畫：``docs/plans/2026-08-18-annotation-merger-agent-sdk-plan.md``。

tool handler **不執行任何業務邏輯** —— 只把模型提交的 mapping payload 捕獲進
``capture_box``（Nami ask_user ``answer_box`` 同款的 per-run in-process 通道）。
mapping 的型別淨化與 vault 寫入仍在 ``annotation_merger`` 本體（S2 接線）。

Schema 直接原樣取自 ``_MERGER_TOOL``（單一事實來源）——S2 換引擎時模型看到的
tool 介面與現行 forced ``tool_choice`` 版本逐位元一致，schema 對照測試鎖住
不漂移。
"""

from __future__ import annotations

from claude_agent_sdk import SdkMcpTool, create_sdk_mcp_server, tool

from agents.robin.annotation_merger import _MERGER_TOOL

# S2 的 ClaudeAgentOptions.allowed_tools 用這個常數，避免 server 名 / tool 名
# 兩處硬寫字串漂移。
MERGER_SERVER_NAME = "merger"
MERGER_ALLOWED_TOOLS = [f"mcp__{MERGER_SERVER_NAME}__{_MERGER_TOOL['name']}"]


def _make_merge_tool(capture_box: dict) -> SdkMcpTool:
    """單一 tool：捕獲 payload 進 ``capture_box`` 後回 ok。"""

    @tool(_MERGER_TOOL["name"], _MERGER_TOOL["description"], _MERGER_TOOL["input_schema"])
    async def merge_annotations(args: dict) -> dict:
        capture_box["mapping"] = args.get("mapping")
        return {"content": [{"type": "text", "text": "ok"}]}

    return merge_annotations


def build_merger_server(capture_box: dict):
    """建立 per-run 的 in-process MCP server。

    ``capture_box`` 是**本次呼叫**的捕獲通道 —— 每次 merge 呼叫都要建新的
    box + server，不可跨 run 共用（避免上一輪的殘留 payload 汙染判定，
    對映 Nami ``answer_box`` 的 per-run 語意）。
    """
    return create_sdk_mcp_server(
        name=MERGER_SERVER_NAME,
        version="1.0.0",
        tools=[_make_merge_tool(capture_box)],
    )
