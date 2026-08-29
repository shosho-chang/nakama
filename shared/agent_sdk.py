"""Claude Agent SDK 共用件 — 訂閱認證覆寫（ADR-026 tool-use 限制的 SDK 出口）。

ADR-026 的 CLI 訂閱路徑（``shared/claude_cli_client.py``）載不動 tool-use；
Agent SDK 路徑可以（in-process MCP tools），且實測可走訂閱額度 —
見 ``memory/claude/reference_agent_sdk_supports_oauth.md``（2026-08-18）。

⚠️ 為什麼這個 helper 是承重牆不是語法糖
（``docs/research/2026-08-18-merger-sdk-spike-findings.md`` §操作性發現）：

SDK 子進程繼承整份 process env，而 CLI 的認證優先序是
**``ANTHROPIC_API_KEY`` 壓過 ``CLAUDE_CODE_OAUTH_TOKEN``**（2026-08-18 實測）。
call site 忘傳 ``env=subscription_env()`` 不是「退回 API 計費」，而是在
API 額度空時**秒死**於難以自我解釋的 ``error result: success``
（與 2026-08-17 Nami 額度事故同簽名）。所以：

- 每個 SDK call site 都必須把本函式的回傳傳給 ``ClaudeAgentOptions(env=...)``
- 測試必須鎖住「有傳 env」這件事（比照
  ``tests/gateway/test_nami_sdk_loop.py`` 的行為鎖定測試）

Nami 的 ``gateway/handlers/nami.py::_sdk_auth_env``（讀 ``NAMI_SDK_OAUTH_TOKEN``）
是本函式的前身；S4 收斂時它會 delegate 到這裡（migration 計畫
``docs/plans/2026-08-18-annotation-merger-agent-sdk-plan.md`` S4）。
"""

from __future__ import annotations

import os

__all__ = ["subscription_env"]


def subscription_env() -> dict[str, str]:
    """SDK 子進程的訂閱認證覆寫（給 ``ClaudeAgentOptions(env=...)`` 用）。

    設了 ``CLAUDE_CODE_OAUTH_TOKEN``（修修 2026-08-18 裁決 #4 的 process-wide
    token）→ 子進程走 Claude 訂閱額度，**並同時把 ``ANTHROPIC_API_KEY`` 清空**：
    兩憑證並存時 CLI 的優先序是 API key 贏（實測，非文件保證），留著等於把
    「走訂閱」變成賭局。清空只影響 SDK 子進程 —— 同 process 內其他
    ``shared.llm`` 呼叫仍讀得到原本的 key。

    未設 token → 回空 dict，行為與不傳 env 逐位元相同（本機測試 / 未部署
    token 的環境不受影響）。
    """
    token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if not token:
        return {}
    return {"CLAUDE_CODE_OAUTH_TOKEN": token, "ANTHROPIC_API_KEY": ""}
