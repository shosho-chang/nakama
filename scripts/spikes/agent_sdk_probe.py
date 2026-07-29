"""S0 探針 — Agent SDK 遷移前的三個必答問題。

一次性 spike，不進 CI、不進生產。跑完把結果記進
docs/research/2026-07-XX-agent-sdk-spike-findings.md 後即可刪除。

計畫見 docs/plans/2026-07-29-nami-agent-sdk-migration-plan.md 的 S0。

前置：
    python -m venv .venv-spike
    .venv-spike/Scripts/Activate.ps1        # Windows
    source .venv-spike/bin/activate         # Linux/VPS
    pip install claude-agent-sdk
    # ANTHROPIC_API_KEY 需在環境中（SDK 不讀 .env）

跑法：
    python scripts/spikes/agent_sdk_probe.py q1     # tools=[] 是否真的移除內建工具
    python scripts/spikes/agent_sdk_probe.py q2a    # 起 session、觸發 can_use_tool、印出 session_id
    python scripts/spikes/agent_sdk_probe.py q2b <session_id>   # 新進程接回
    python scripts/spikes/agent_sdk_probe.py q3     # 環境健檢（VPS 上跑）

Q2 的 `defer` 形狀已查證（2026-07-29，hooks 文件 "Defer a tool call for later"）：
   defer 不是 can_use_tool 的回傳值（Python SDK 沒有 PermissionResultDefer），
   是 PreToolUse hook 的 permissionDecision。詳見 q2 區塊註解。
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

try:
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        create_sdk_mcp_server,
        query,
        tool,
    )
    from claude_agent_sdk.types import HookMatcher
except ImportError:
    sys.exit("claude-agent-sdk 未安裝。先 pip install claude-agent-sdk（見本檔 docstring）")

MODEL = os.environ.get("SPIKE_MODEL", "claude-sonnet-4-6")


# ── 一個最小的 in-process MCP tool，模擬 Nami 的 _tool_* wrapper 形狀 ──


@tool("nami_echo", "回聲測試用。把收到的字串原樣回傳。", {"text": str})
async def nami_echo(args: dict[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": f"echo: {args['text']}"}]}


probe_server = create_sdk_mcp_server(name="nami", version="0.0.1", tools=[nami_echo])


def _base_options(**overrides: Any) -> ClaudeAgentOptions:
    opts: dict[str, Any] = {
        "model": MODEL,
        "mcp_servers": {"nami": probe_server},
        "allowed_tools": ["mcp__nami__nami_echo"],
        "max_turns": 6,
    }
    opts.update(overrides)
    return ClaudeAgentOptions(**opts)


def _dump(message: Any) -> None:
    if isinstance(message, AssistantMessage):
        for block in message.content:
            if getattr(block, "type", None) == "text":
                print(f"  [text] {block.text[:400]}")
            elif getattr(block, "type", None) == "tool_use":
                print(f"  [tool_use] {block.name} {block.input}")
    elif isinstance(message, ResultMessage):
        print(f"  [result] subtype={message.subtype} turns={message.num_turns}")
        print(f"  [result] session_id={message.session_id}")
        if message.total_cost_usd is not None:
            print(f"  [result] cost=${message.total_cost_usd:.4f}")
        if message.subtype == "success":
            print(f"  [result] text={message.result[:600]}")


# ── Q1：tools=[] 是否真的把內建工具移出 Claude 的 context ──────────────
#
# 這是 VPS 安全紅線。Nami 跑在有 vault 寫入權限的機器上，Agent SDK 預設
# 帶 Bash / Write / Edit。文件說 tools=[] 會「remove all built-ins」，
# 但這件事必須實測，不能只信文件。
#
# 判讀：模型應該回報它只有 mcp__nami__nami_echo，且明確說沒有 Bash /
# Write / Read。若它列出任何內建工具，或真的成功跑了 Bash → S2 的
# tools=[] 寫法不安全，必須改用 disallowed_tools 逐項封鎖再重測。


async def q1() -> None:
    print("=== Q1: tools=[] 是否移除內建工具 ===")
    print(f"model={MODEL}\n")

    prompt = (
        "請完整列出你目前可用的工具名稱，一行一個，不要省略。"
        "接著明確回答這三題（是/否）："
        "(1) 你有 Bash 工具嗎？"
        "(2) 你有 Write 或 Edit 工具嗎？"
        "(3) 你有 Read 工具嗎？"
        "不要嘗試呼叫任何工具。"
    )

    async for message in query(prompt=prompt, options=_base_options(tools=[])):
        _dump(message)

    print("\n--- 對照組：不設 tools（預設帶內建工具）---\n")
    async for message in query(prompt=prompt, options=_base_options()):
        _dump(message)

    print(
        "\n判讀：兩組輸出必須有明顯差異。tools=[] 那組若列出 Bash/Write/Read "
        "任一項 → 紅線未通過，S2 不可用 tools=[]。"
    )


# ── Q2：PreToolUse hook 回 defer → 進程結束 → 新進程 resume 接回 ────────
#
# 這是 ask_user 的命脈。現行 Nami 靠 SQLite 存整份 messages 做到這件事；
# 遷移後要靠 defer + session resume。必須跨進程實測，不是同進程內測。
#
# 查證結果（2026-07-29，code.claude.com/docs/en/hooks#defer-a-tool-call-for-later）：
# - defer 不是 can_use_tool 的回傳值（Python SDK 沒有 PermissionResultDefer），
#   而是 PreToolUse hook 的 permissionDecision。
# - 流程：hook 回 {"hookSpecificOutput": {"hookEventName": "PreToolUse",
#   "permissionDecision": "defer"}} → tool 不執行、進程結束，result 帶
#   stop_reason="tool_deferred" 與 deferred_tool_use{id, name, input}。
# - resume 後**同一個 tool call 會再次觸發 PreToolUse**，hook 這次回 allow
#   （答案放 updatedInput）→ tool 執行、Claude 繼續。答案還沒到可以再 defer。
# - 限制：turn 內單一 tool call 才有效（多個並發 → defer 被忽略）；
#   defer 時 updatedInput / permissionDecisionReason / additionalContext 全被忽略；
#   session 檔受 cleanupPeriodDays 管，預設 30 天清掉；
#   resume 時 tool 不在（MCP server 沒接）→ stop_reason="tool_deferred_unavailable"。


async def _defer_hook(input_data: Any, tool_use_id: Any, context: Any) -> dict:
    print(f"\n  >>> PreToolUse 觸發：{input_data.get('tool_name')} tool_use_id={tool_use_id}")
    print("  >>> 回 defer —— 進程應在此後結束，pending tool call 留在 transcript")
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "defer",
        }
    }


async def q2a() -> None:
    print("=== Q2a: PreToolUse hook 回 defer，觀察進程如何結束 ===")
    print(f"model={MODEL}\n")

    session_id = None
    options = _base_options(
        hooks={
            "PreToolUse": [
                HookMatcher(matcher="mcp__nami__nami_echo", hooks=[_defer_hook])
            ]
        },
    )

    try:
        async for message in query(
            prompt="請用 nami_echo 工具回聲 'S0-probe'，然後告訴我結果。",
            options=options,
        ):
            _dump(message)
            if isinstance(message, ResultMessage):
                session_id = message.session_id
                # 看 stop_reason / deferred_tool_use 在 Python 型別上有沒有露出
                print(f"  [result:raw] {message!r}")
    except Exception as e:  # noqa: BLE001 — spike，要看到原始錯誤
        print(f"  [error] {type(e).__name__}: {e}")

    print(f"\n>>> session_id = {session_id}")
    print(">>> 判讀：result 應帶 stop_reason='tool_deferred'，deferred_tool_use 帶")
    print(">>>       nami_echo 的 id/name/input。若 Python ResultMessage 沒露出這兩")
    print(">>>       個欄位 → 記進 findings（S3 要嘛升版 SDK、要嘛自己讀 raw JSON）。")
    print(">>> 進程自然結束後跑：")
    print(f">>>   python scripts/spikes/agent_sdk_probe.py q2b {session_id}")
    print(">>> 注意：resume 依賴 cwd —— q2b 必須從同一個目錄跑。")


async def _resume_allow_hook(input_data: Any, tool_use_id: Any, context: Any) -> dict:
    print(f"\n  >>> resume 後 PreToolUse 再次觸發：{input_data.get('tool_name')}")
    print("  >>> 這次回 allow（真實 S3 會把 Slack 收到的答案放進 updatedInput）")
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": input_data.get("tool_input", {}),
        }
    }


async def q2b(session_id: str) -> None:
    print(f"=== Q2b: 新進程用 resume={session_id} 接回 deferred tool call ===")
    print(f"cwd={os.getcwd()}\n")

    options = _base_options(
        resume=session_id,
        hooks={
            "PreToolUse": [
                HookMatcher(matcher="mcp__nami__nami_echo", hooks=[_resume_allow_hook])
            ]
        },
    )

    # 文件說 CLI 層 resume deferred tool call 時可以不給 prompt；
    # Python query() 的 prompt 是必填 —— 先試空字串，實際行為記進 findings。
    try:
        async for message in query(prompt="", options=options):
            _dump(message)
            if isinstance(message, ResultMessage):
                print(f"  [result:raw] {message!r}")
    except Exception as e:  # noqa: BLE001 — spike，要看到原始錯誤
        print(f"  [error] {type(e).__name__}: {e}")
        print("  空 prompt 失敗 → fallback 帶一句 prompt 重試：")
        async for message in query(
            prompt="（繼續剛才被擱置的工具呼叫）", options=options
        ):
            _dump(message)
            if isinstance(message, ResultMessage):
                print(f"  [result:raw] {message!r}")

    print(
        "\n判讀：PreToolUse 必須再次觸發、allow 後 tool 真的執行，"
        "Claude 的回覆要包含 'echo: S0-probe'。沒觸發或答不出 → defer 迴圈斷裂，"
        "S3 設計要重看（先查 cwd 是否一致、session 檔是否在本機）。"
    )


# ── Q3：環境健檢（在 VPS 上跑）─────────────────────────────────────


async def q3() -> None:
    import platform

    print("=== Q3: 環境健檢 ===")
    print(f"platform : {platform.platform()}")
    print(f"python   : {sys.version.split()[0]}")
    print(f"cwd      : {os.getcwd()}")
    print(f"api_key  : {'set' if os.environ.get('ANTHROPIC_API_KEY') else 'MISSING'}")

    try:
        import claude_agent_sdk  # noqa: PLC0415

        print(f"sdk      : {getattr(claude_agent_sdk, '__version__', 'unknown')}")
    except Exception as e:  # noqa: BLE001
        print(f"sdk      : import failed — {e}")

    # session 檔落點 —— 決定 S3 的 resume 能不能跨機器
    cfg = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    print(f"config   : {cfg} (exists={os.path.isdir(cfg)})")

    print("\n--- 實跑一次 query() ---\n")
    async for message in query(
        prompt="只回答兩個字：正常", options=_base_options(tools=[], max_turns=1)
    ):
        _dump(message)

    print(
        "\n判讀：SDK 自帶 native binary，這裡要確認它在 VPS 的 Linux 環境能跑起來。"
        "\n另外記下 session 檔實際落在哪 —— S3 的 resume 依賴它。"
    )


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)

    cmd = sys.argv[1]
    if cmd == "q1":
        asyncio.run(q1())
    elif cmd == "q2a":
        asyncio.run(q2a())
    elif cmd == "q2b":
        if len(sys.argv) < 3:
            sys.exit("q2b 需要 session_id：... q2b <session_id>")
        asyncio.run(q2b(sys.argv[2]))
    elif cmd == "q3":
        asyncio.run(q3())
    else:
        sys.exit(f"未知指令 {cmd!r}。可用：q1 / q2a / q2b <session_id> / q3")


if __name__ == "__main__":
    main()
