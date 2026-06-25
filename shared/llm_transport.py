"""LLM transport 選擇 — 全域 kill-switch（Slice 2）。

``LLM_TRANSPORT=openrouter`` → api-tier 的文字呼叫改走 :mod:`shared.openrouter_client`；
未設 / ``native`` → 各 provider 原生 SDK（行為與接線前逐位元相同）。這層刻意只放一個
輕量 env 判斷、不 import 任何 heavy SDK，讓各 client 能在每次呼叫便宜地問「要不要走
OpenRouter」，真正的 ``ask_openrouter`` 只在啟用時才 lazy-import。

兩個 carve-out 不在這裡判斷（在各 client 內）：
- Anthropic 訂閱（Max Plan / ADR-026）永遠走原生 ``claude_cli_client``。
- xAI ``grok-*`` OpenRouter 不載該 tier，永遠留原生 ``xai_client``。
"""

from __future__ import annotations

import os


def openrouter_enabled() -> bool:
    """api-tier 文字呼叫是否改走 OpenRouter transport。預設關（native kill-switch）。"""
    return (os.environ.get("LLM_TRANSPORT") or "native").strip().lower() == "openrouter"


__all__ = ["openrouter_enabled"]
