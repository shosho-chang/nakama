"""shared.llm facade — 依 model prefix dispatch 到對的 provider wrapper。"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _clean_model_env(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    for key in list(os.environ.keys()):
        if key.startswith("MODEL_"):
            monkeypatch.delenv(key, raising=False)


def test_ask_dispatches_claude_to_anthropic_client():
    from shared import llm

    with (
        patch("shared.llm.ask_claude", return_value="claude reply") as m_claude,
        patch("shared.xai_client.ask_grok", return_value="grok reply") as m_grok,
    ):
        out = llm.ask("hi", model="claude-sonnet-4-20250514")

    assert out == "claude reply"
    m_claude.assert_called_once()
    m_grok.assert_not_called()


def test_ask_dispatches_grok_to_xai_client():
    from shared import llm

    with (
        patch("shared.llm.ask_claude", return_value="claude reply") as m_claude,
        patch("shared.xai_client.ask_grok", return_value="grok reply") as m_grok,
    ):
        out = llm.ask("hi", model="grok-4-fast-non-reasoning")

    assert out == "grok reply"
    m_grok.assert_called_once()
    m_claude.assert_not_called()


def test_ask_raises_for_unknown_provider():
    from shared import llm

    with pytest.raises(ValueError, match="Unknown model provider"):
        llm.ask("hi", model="mystery-model-v1")


def test_ask_raises_notimplemented_for_recognized_but_unwired_provider():
    """openai 已識別但尚未 wire（gpt-* / o1-* / o3-*）。"""
    from shared import llm

    with pytest.raises(NotImplementedError, match="openai"):
        llm.ask("hi", model="gpt-4o")


def test_ask_dispatches_gemini_to_google_client():
    from shared import llm

    with (
        patch("shared.llm.ask_claude", return_value="claude reply") as m_claude,
        patch("shared.xai_client.ask_grok", return_value="grok reply") as m_grok,
        patch("shared.gemini_client.ask_gemini", return_value="gemini reply") as m_gemini,
    ):
        out = llm.ask("hi", model="gemini-2.5-pro")

    assert out == "gemini reply"
    m_gemini.assert_called_once()
    m_claude.assert_not_called()
    m_grok.assert_not_called()


def test_ask_multi_dispatches_gemini_to_google_client():
    from shared import llm

    messages = [{"role": "user", "content": "hi"}]
    with patch("shared.gemini_client.ask_gemini_multi", return_value="gemini multi") as m:
        out = llm.ask_multi(messages, model="gemini-2.5-flash")

    assert out == "gemini multi"
    m.assert_called_once()


def test_ask_multi_dispatches_by_provider():
    from shared import llm

    messages = [{"role": "user", "content": "hi"}]
    with (
        patch("shared.llm.ask_claude_multi", return_value="claude multi") as m_c,
        patch("shared.xai_client.ask_grok_multi", return_value="grok multi") as m_g,
    ):
        assert llm.ask_multi(messages, model="claude-sonnet-4-20250514") == "claude multi"
        assert llm.ask_multi(messages, model="grok-4-fast-non-reasoning") == "grok multi"

    assert m_c.call_count == 1
    assert m_g.call_count == 1


def test_ask_with_none_model_uses_router(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MODEL_SANJI", "grok-4-fast-non-reasoning")
    from shared import llm
    from shared.llm_context import set_current_agent

    set_current_agent("sanji", run_id=None)

    with patch("shared.xai_client.ask_grok", return_value="ok") as m_grok:
        llm.ask("hi")

    m_grok.assert_called_once()
    assert m_grok.call_args.kwargs["model"] == "grok-4-fast-non-reasoning"


def test_ask_forwards_thinking_budget_only_to_gemini():
    """thinking_budget 只對 Gemini forward，不塞給其他 provider（簽名沒 accept 會炸）。"""
    from shared import llm

    with patch("shared.gemini_client.ask_gemini", return_value="ok") as m_gem:
        llm.ask("hi", model="gemini-2.5-pro", thinking_budget=256)
    assert m_gem.call_args.kwargs["thinking_budget"] == 256

    # None 則不 forward（讓 Gemini wrapper 用自家 default 512）
    with patch("shared.gemini_client.ask_gemini", return_value="ok") as m_gem:
        llm.ask("hi", model="gemini-2.5-pro")
    assert "thinking_budget" not in m_gem.call_args.kwargs

    # 0 要 forward（明確關閉 thinking）
    with patch("shared.gemini_client.ask_gemini", return_value="ok") as m_gem:
        llm.ask("hi", model="gemini-2.5-pro", thinking_budget=0)
    assert m_gem.call_args.kwargs["thinking_budget"] == 0

    # Claude 不該拿到 thinking_budget kwarg
    with patch("shared.llm.ask_claude", return_value="ok") as m_claude:
        llm.ask("hi", model="claude-sonnet-4-20250514", thinking_budget=256)
    assert "thinking_budget" not in m_claude.call_args.kwargs


def test_ask_multi_forwards_thinking_budget_only_to_gemini():
    from shared import llm

    messages = [{"role": "user", "content": "hi"}]
    with patch("shared.gemini_client.ask_gemini_multi", return_value="ok") as m_gem:
        llm.ask_multi(messages, model="gemini-2.5-pro", thinking_budget=128)
    assert m_gem.call_args.kwargs["thinking_budget"] == 128

    with patch("shared.llm.ask_claude_multi", return_value="ok") as m_claude:
        llm.ask_multi(messages, model="claude-sonnet-4-20250514", thinking_budget=128)
    assert "thinking_budget" not in m_claude.call_args.kwargs


# ---------- ask_with_tools ----------


def test_ask_with_tools_dispatches_claude_to_anthropic():
    from shared import llm

    messages = [{"role": "user", "content": "hi"}]
    sentinel = object()
    with patch("shared.llm.call_claude_with_tools", return_value=sentinel) as m:
        out = llm.ask_with_tools(messages, tools=[], model="claude-haiku-4-5")

    assert out is sentinel
    m.assert_called_once()
    assert m.call_args.kwargs["model"] == "claude-haiku-4-5"


def test_ask_with_tools_raises_for_xai_model():
    from shared import llm

    with pytest.raises(NotImplementedError, match="anthropic"):
        llm.ask_with_tools(
            [{"role": "user", "content": "hi"}], tools=[], model="grok-4-fast-non-reasoning"
        )


def test_ask_with_tools_raises_for_gemini_model():
    from shared import llm

    with pytest.raises(NotImplementedError, match="anthropic"):
        llm.ask_with_tools([{"role": "user", "content": "hi"}], tools=[], model="gemini-2.5-pro")


def test_ask_with_tools_uses_router_with_tool_use_task(monkeypatch: pytest.MonkeyPatch):
    """model=None 時應走 router task='tool_use'（預設 Haiku 4.5）。"""
    monkeypatch.setenv("MODEL_BROOK_TOOL_USE", "claude-haiku-4-5")
    from shared import llm
    from shared.llm_context import set_current_agent

    set_current_agent("brook", run_id=None)
    with patch("shared.llm.call_claude_with_tools", return_value=object()) as m:
        llm.ask_with_tools([{"role": "user", "content": "hi"}], tools=[])

    assert m.call_args.kwargs["model"] == "claude-haiku-4-5"


# ---------- ask_with_audio ----------


def test_ask_with_audio_dispatches_gemini_by_default():
    """預設 model='gemini-2.5-pro' → routes to ask_gemini_audio。"""
    from shared import llm

    sentinel = object()
    with patch("shared.gemini_client.ask_gemini_audio", return_value=sentinel) as m:
        out = llm.ask_with_audio("/tmp/clip.wav", "transcribe")

    assert out is sentinel
    m.assert_called_once()
    assert m.call_args.kwargs["model"] == "gemini-2.5-pro"


def test_ask_with_audio_raises_for_claude_model():
    from shared import llm

    with pytest.raises(NotImplementedError, match="google/Gemini"):
        llm.ask_with_audio("/tmp/clip.wav", "transcribe", model="claude-sonnet-4-20250514")


def test_ask_with_audio_raises_for_xai_model():
    from shared import llm

    with pytest.raises(NotImplementedError, match="google/Gemini"):
        llm.ask_with_audio("/tmp/clip.wav", "transcribe", model="grok-4-fast-non-reasoning")
