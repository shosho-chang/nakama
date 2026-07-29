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

⚠️ Q2 的 `defer` 決策形狀本檔**沒有實作** —— 我沒有查證到它的確切 API，
   不憑印象寫。跑 q2a 前先讀 https://code.claude.com/docs/en/hooks
   的 "Defer a tool call for later"，把回傳值補進 _can_use_tool()。
   在那之前 q2a/q2b 測到的是「同一個 pending 狀態能不能用 resume 接回」，
   不是完整的 defer 流程。
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
    from claude_agent_sdk.types import HookMatcher, PermissionResultAllow
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


# ── Q2：can_use_tool 暫停 → 進程結束 → 新進程 resume 接回 ─────────────
#
# 這是 ask_user 的命脈。現行 Nami 靠 SQLite 存整份 messages 做到這件事；
# 遷移後要靠 SDK 的 session resume。必須跨進程實測，不是同進程內測。


async def _can_use_tool(tool_name: str, input_data: dict, context: Any) -> Any:
    print(f"\n  >>> can_use_tool 觸發：{tool_name} {input_data}")
    print("  >>> ⚠️ 這裡應該回傳 defer 決策，讓進程可以結束後再接回。")
    print("  >>> 目前先 allow 讓流程跑完；defer 形狀待查證後補上。")
    return PermissionResultAllow(updated_input=input_data)


async def _dummy_hook(input_data: Any, tool_use_id: Any, context: Any) -> dict:
    # Python 的 can_use_tool 需要 streaming mode；沒有 hook 或 in-process MCP
    # server 撐著的話，SDK 會在 callback 被呼叫前就關掉 input stream。
    return {"continue_": True}


async def q2a() -> None:
    print("=== Q2a: 起 session 並觸發 can_use_tool ===")
    print(f"model={MODEL}\n")

    session_id = None
    options = _base_options(
        can_use_tool=_can_use_tool,
        hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[_dummy_hook])]},
        allowed_tools=[],  # 清空 → 讓 nami_echo 走 permission flow 而非自動放行
    )

    async def _prompt_stream():
        yield {
            "type": "user",
            "message": {
                "role": "user",
                "content": "請用 nami_echo 工具回聲 'S0-probe'，然後告訴我結果。",
            },
        }

    try:
        async for message in query(prompt=_prompt_stream(), options=options):
            _dump(message)
            if isinstance(message, ResultMessage):
                session_id = message.session_id
    except Exception as e:  # noqa: BLE001 — spike，要看到原始錯誤
        print(f"  [error] {type(e).__name__}: {e}")

    print(f"\n>>> session_id = {session_id}")
    print(">>> 現在**關掉這個進程**，然後跑：")
    print(f">>>   python scripts/spikes/agent_sdk_probe.py q2b {session_id}")
    print(">>> 注意：resume 依賴 cwd —— q2b 必須從同一個目錄跑。")


async def q2b(session_id: str) -> None:
    print(f"=== Q2b: 新進程用 resume={session_id} 接回 ===")
    print(f"cwd={os.getcwd()}\n")

    prompt = "我剛剛請你做什麼？請完整複述，包含我要你回聲的那個字串。"

    async for message in query(prompt=prompt, options=_base_options(resume=session_id)):
        _dump(message)

    print(
        "\n判讀：模型必須答得出 'S0-probe' 這個字串。答不出來 → session "
        "沒接回（先查 cwd 是否一致、session 檔是否在本機）。"
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
