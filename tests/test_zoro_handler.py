"""gateway/handlers/zoro.py — ZoroHandler 單元測試。"""

from __future__ import annotations

from unittest.mock import patch

from gateway.handlers.zoro import ZoroHandler


def test_zoro_handler_registered():
    """Zoro 有註冊進 handler registry。"""
    from gateway.handlers import get_handler

    handler = get_handler("zoro")
    assert isinstance(handler, ZoroHandler)


def test_zoro_handle_calls_facade_with_persona():
    handler = ZoroHandler()

    with patch("gateway.handlers.zoro.ask", return_value="Zoro 的回覆") as m_ask:
        response = handler.handle(intent="general", text="最近什麼熱門", user_id="U123")

    assert response.text == "Zoro 的回覆"
    assert response.continuation is None
    kwargs = m_ask.call_args.kwargs
    assert kwargs["prompt"] == "最近什麼熱門"
    system = kwargs.get("system", "")
    assert "Zoro" in system or "劍士" in system


def test_zoro_handle_catches_llm_error():
    """LLM 掛了要回一個友好的錯誤訊息，不能穿出 handler。"""
    handler = ZoroHandler()

    with patch("gateway.handlers.zoro.ask", side_effect=RuntimeError("Anthropic 529")):
        response = handler.handle(intent="general", text="hi", user_id="U123")

    assert "巡邏" in response.text or "中斷" in response.text
    assert "Anthropic 529" in response.text


def test_zoro_handle_sets_current_agent_to_zoro():
    """thread-local agent 要在 ask 被呼叫前設成 'zoro'，讓 llm_router 走 MODEL_ZORO，
    cost DB 也記對 agent。"""
    handler = ZoroHandler()
    captured_agent: list[str | None] = []

    def fake_ask(**kwargs):
        from shared.llm_context import _local

        captured_agent.append(getattr(_local, "agent", None))
        return "x"

    with patch("gateway.handlers.zoro.ask", side_effect=fake_ask):
        handler.handle(intent="general", text="hi", user_id="U123")

    assert captured_agent == ["zoro"]
