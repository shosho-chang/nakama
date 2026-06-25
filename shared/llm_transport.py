"""LLM transport 選擇 — kill-switch + per-agent canary（Slice 2 / Slice 6）。

解析序（高到低）：

1. ``LLM_TRANSPORT_<AGENT>``（thread-local 或顯式 agent）— canary 用：只切單一
   agent，其餘維持全域設定。值同樣是 ``openrouter`` / ``native``。
2. ``LLM_TRANSPORT``（全域）
3. ``native``（預設關，kill-switch）

per-agent override 讓 canary 真正漸進（先 ``LLM_TRANSPORT_SANJI=openrouter`` 驗單一
agent，cost panel 對得上再逐步擴），不必一次全域切。``LLM_TRANSPORT_<AGENT>=native``
也能反向把某 agent 排除在全域 openrouter 之外。

這層刻意只放輕量 env 判斷、不 import heavy SDK，讓各 client 每次呼叫便宜地問「要不要
走 OpenRouter」，真正的 ``ask_openrouter`` 只在啟用時才 lazy-import。

兩個 carve-out 不在這裡判斷（在各 client 內）：
- Anthropic 訂閱（Max Plan / ADR-026）永遠走原生 ``claude_cli_client``。
- xAI ``grok-*`` OpenRouter 不載該 tier，永遠留原生 ``xai_client``。
"""

from __future__ import annotations

import os

from shared.llm_context import _local


def _is_openrouter(value: str | None) -> bool:
    return (value or "native").strip().lower() == "openrouter"


def openrouter_enabled(agent: str | None = None) -> bool:
    """api-tier 文字呼叫是否改走 OpenRouter transport。

    Args:
        agent: 指定 agent 名稱以查 ``LLM_TRANSPORT_<AGENT>`` per-agent override。
            ``None`` 時從 thread-local context 讀當前 agent（client 呼叫路徑），讓
            cost-tracking 與 transport 決策吃到同一個 agent。Bridge 面板逐列列舉時
            顯式傳 site 的 agent。

    解析序：``LLM_TRANSPORT_<AGENT>`` > ``LLM_TRANSPORT`` > native（預設關）。
    """
    if agent is None:
        agent = getattr(_local, "agent", None)
    if agent:
        per_agent = os.environ.get(f"LLM_TRANSPORT_{agent.upper()}")
        if per_agent:
            return _is_openrouter(per_agent)
    return _is_openrouter(os.environ.get("LLM_TRANSPORT"))


__all__ = ["openrouter_enabled"]
