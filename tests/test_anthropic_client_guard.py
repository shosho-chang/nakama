"""ask_claude / call_claude_with_tools 收到非 Claude model ID 時應 fail fast。

背景：Anthropic SDK 對非 claude- ID 會噴模糊錯誤並自動 retry 3 次才放棄，
浪費時間又讓 log 難解。步驟 2 加的 _require_claude_model guard 要確保這類
誤用直接 raise ValueError。
"""

from __future__ import annotations

import pytest


def test_ask_claude_rejects_grok_model():
    from shared.anthropic_client import ask_claude

    with pytest.raises(ValueError, match="non-Claude model"):
        ask_claude("hi", model="grok-4-fast-non-reasoning")


def test_ask_claude_multi_rejects_gemini_model():
    from shared.anthropic_client import ask_claude_multi

    with pytest.raises(ValueError, match="non-Claude model"):
        ask_claude_multi([{"role": "user", "content": "hi"}], model="gemini-2.5-pro")


def test_call_claude_with_tools_rejects_gpt_model():
    from shared.anthropic_client import call_claude_with_tools

    with pytest.raises(ValueError, match="non-Claude model"):
        call_claude_with_tools(
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
            model="gpt-4o",
        )


def test_ask_claude_guard_fires_via_router(monkeypatch: pytest.MonkeyPatch):
    """MODEL_<AGENT> 被誤設成非 Claude ID 時也要擋下來，不 retry。"""
    monkeypatch.setenv("MODEL_BROOK", "grok-4-fast-non-reasoning")
    from shared.anthropic_client import ask_claude
    from shared.llm_context import set_current_agent

    set_current_agent("brook", run_id=None)
    with pytest.raises(ValueError, match="non-Claude model"):
        ask_claude("hi")
