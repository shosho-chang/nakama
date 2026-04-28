"""gateway/handlers/sanji.py — SanjiHandler 單元測試。"""

from __future__ import annotations

from unittest.mock import patch

from gateway.handlers.sanji import SanjiHandler


def test_sanji_handler_registered():
    """Sanji 有註冊進 handler registry。"""
    from gateway.handlers import get_handler

    handler = get_handler("sanji")
    assert isinstance(handler, SanjiHandler)


def test_sanji_handle_calls_facade_with_persona():
    handler = SanjiHandler()

    with patch("gateway.handlers.sanji.ask", return_value="Sanji 的回覆") as m_ask:
        response = handler.handle(intent="general", text="最近很累", user_id="U123")

    assert response.text == "Sanji 的回覆"
    assert response.continuation is None  # P1 不做多輪
    # 系統 prompt 應該是 persona（至少含「Sanji」或「廚師」）
    kwargs = m_ask.call_args.kwargs
    assert kwargs["prompt"] == "最近很累"
    system = kwargs.get("system", "")
    assert "Sanji" in system or "廚師" in system


def test_sanji_handle_catches_llm_error():
    """LLM 掛了要回一個友好的錯誤訊息，不能直接 raise 穿出 handler。"""
    handler = SanjiHandler()

    with patch("gateway.handlers.sanji.ask", side_effect=RuntimeError("xAI 503")):
        response = handler.handle(intent="general", text="hi", user_id="U123")

    assert "廚房" in response.text or "狀況" in response.text
    assert "xAI 503" in response.text  # 錯誤資訊要透傳方便 debug


def test_sanji_handle_sets_current_agent_to_sanji():
    """thread-local agent 要在 ask 被呼叫前設成 'sanji'。

    這樣 llm facade 才會走 MODEL_SANJI env → Grok，cost DB 也會記對 agent。
    """
    handler = SanjiHandler()
    captured_agent: list[str | None] = []

    def fake_ask(**kwargs):
        from shared.llm_context import _local

        captured_agent.append(getattr(_local, "agent", None))
        return "x"

    with patch("gateway.handlers.sanji.ask", side_effect=fake_ask):
        handler.handle(intent="general", text="hi", user_id="U123")

    assert captured_agent == ["sanji"]
