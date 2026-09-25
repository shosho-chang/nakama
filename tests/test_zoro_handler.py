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


def test_zoro_handle_declares_interactive_call_class():
    """ADR-070 D5：修修此刻在等 Slack 回覆，call_class 要是 interactive。"""
    handler = ZoroHandler()

    with patch("gateway.handlers.zoro.ask", return_value="ok") as m_ask:
        handler.handle(intent="general", text="hi", user_id="U123")

    assert m_ask.call_args.kwargs["call_class"] == "interactive"


def test_zoro_handle_routes_to_l1_under_gateway_group(monkeypatch):
    """ADR-070 S1a：gateway process 下，Zoro 的預設 Claude model 改走訂閱（L1）。"""
    from shared.llm_context import set_runtime_group

    monkeypatch.delenv("MODEL_ZORO", raising=False)
    set_runtime_group("gateway")
    handler = ZoroHandler()
    calls: list[tuple[str, str]] = []

    def _fake_run_text(prompt, **kwargs):
        calls.append((kwargs["model"], kwargs["call_class"]))
        return "Zoro 的回覆"

    with patch("shared.agent_sdk.run_text", side_effect=_fake_run_text):
        response = handler.handle(intent="general", text="最近什麼熱門", user_id="U123")

    assert response.text == "Zoro 的回覆"
    assert calls == [("claude-sonnet-4-6", "interactive")]


def test_zoro_handle_sets_current_agent_to_zoro():
    """thread-local agent 要在 ask 被呼叫前設成 'zoro'，讓 llm_router 走 MODEL_ZORO，
    cost DB 也記對 agent。"""
    handler = ZoroHandler()
    captured_agent: list[str | None] = []

    def fake_ask(**kwargs):
        from shared.llm_context import get_current_agent

        captured_agent.append(get_current_agent())
        return "x"

    with patch("gateway.handlers.zoro.ask", side_effect=fake_ask):
        handler.handle(intent="general", text="hi", user_id="U123")

    assert captured_agent == ["zoro"]
