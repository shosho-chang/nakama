"""Slice 2 — facade transport seam dispatch 測試（mock，無網路）。

驗證矩陣：
- ``LLM_TRANSPORT=openrouter`` → Anthropic / Gemini 的 api-tier 文字呼叫走 OpenRouter
- 未設（kill-switch）→ 原生 SDK，行為與接線前一致（回歸保護）
- carve-outs：Anthropic 訂閱走 ``claude -p`` CLI、xAI 恆原生、tool-use 恆原生
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _reset_singletons_and_transport(monkeypatch):
    monkeypatch.delenv("LLM_TRANSPORT", raising=False)
    monkeypatch.delenv("NAKAMA_REQUIRE_MAX_PLAN", raising=False)
    import shared.anthropic_client as ac
    import shared.xai_client as xai
    from shared.llm_context import _local

    ac._client = None
    xai._client = None
    _local.agent = None  # 清 set_current_agent 殘留，避免 per-agent transport 漏到別測試
    yield
    ac._client = None
    xai._client = None
    _local.agent = None


def _fake_anthropic_msg(text="native-claude"):
    return SimpleNamespace(
        content=[SimpleNamespace(text=text)],
        usage=SimpleNamespace(
            input_tokens=10,
            output_tokens=5,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
    )


# ── Anthropic ──────────────────────────────────────────────────────────────
def test_anthropic_api_routes_to_openrouter_when_enabled(monkeypatch):
    monkeypatch.setenv("LLM_TRANSPORT", "openrouter")
    import shared.anthropic_client as ac

    with (
        patch("shared.openrouter_client.ask_openrouter", return_value="or-claude") as m,
        patch.object(ac, "get_client") as gc,
    ):
        out = ac.ask_claude("hi", system="s", model="claude-sonnet-4-6")

    assert out == "or-claude"
    gc.assert_not_called()  # 原生 SDK 完全沒被碰
    kw = m.call_args.kwargs
    assert kw["model"] == "claude-sonnet-4-6"  # bare ID（OpenRouter client 內部翻 slug）
    assert kw["auth_actual"] == "api"  # auth 稽核欄位與原生 api 呼叫對齊
    assert kw["auth_requested"] == "api"


def test_anthropic_multi_routes_to_openrouter_when_enabled(monkeypatch):
    monkeypatch.setenv("LLM_TRANSPORT", "openrouter")
    import shared.anthropic_client as ac

    with (
        patch("shared.openrouter_client.ask_openrouter_multi", return_value="or-multi") as m,
        patch.object(ac, "get_client") as gc,
    ):
        out = ac.ask_claude_multi([{"role": "user", "content": "hi"}], model="claude-sonnet-4-6")

    assert out == "or-multi"
    gc.assert_not_called()
    assert m.call_args.kwargs["model"] == "claude-sonnet-4-6"


def test_anthropic_native_when_transport_unset(monkeypatch):
    # LLM_TRANSPORT 由 fixture 清掉 → kill-switch 預設 native
    import shared.anthropic_client as ac

    fake_client = MagicMock()
    fake_client.messages.create.return_value = _fake_anthropic_msg("native-claude")

    with (
        patch.object(ac, "get_client", return_value=fake_client),
        patch("shared.state.record_api_call", lambda **k: None),
        patch("shared.openrouter_client.ask_openrouter") as m,
    ):
        out = ac.ask_claude("hi", model="claude-sonnet-4-6")

    assert out == "native-claude"
    m.assert_not_called()  # kill-switch：沒走 OpenRouter，逐位元回到原生路徑


def test_anthropic_subscription_stays_cli_even_when_enabled(monkeypatch):
    """訂閱 carve-out：即使 LLM_TRANSPORT=openrouter，subscription 仍走 claude -p CLI。"""
    monkeypatch.setenv("LLM_TRANSPORT", "openrouter")
    import shared.anthropic_client as ac

    monkeypatch.setattr(ac, "_oauth_token_available", lambda: True)
    monkeypatch.setattr(ac, "_cli_binary_available", lambda: True)

    with (
        patch("shared.claude_cli_client.ask_via_cli", return_value="cli-result") as cli,
        patch("shared.openrouter_client.ask_openrouter") as orc,
    ):
        out = ac.ask_claude("hi", model="claude-sonnet-4-6", auth_policy="subscription_required")

    assert out == "cli-result"
    cli.assert_called_once()
    orc.assert_not_called()  # OpenRouter 不碰訂閱呼叫


# ── Gemini ─────────────────────────────────────────────────────────────────
def test_gemini_routes_to_openrouter_when_enabled(monkeypatch):
    monkeypatch.setenv("LLM_TRANSPORT", "openrouter")
    import shared.gemini_client as gem

    # seam 在 import google.genai 之前 return → 不需要本機裝 google-genai
    with patch("shared.openrouter_client.ask_openrouter", return_value="or-gemini") as m:
        out = gem.ask_gemini("hi", model="gemini-2.5-pro")

    assert out == "or-gemini"
    assert m.call_args.kwargs["model"] == "gemini-2.5-pro"


def test_gemini_multi_routes_to_openrouter_when_enabled(monkeypatch):
    monkeypatch.setenv("LLM_TRANSPORT", "openrouter")
    import shared.gemini_client as gem

    with patch("shared.openrouter_client.ask_openrouter_multi", return_value="or-gem-multi") as m:
        out = gem.ask_gemini_multi([{"role": "user", "content": "hi"}], model="gemini-2.5-pro")

    assert out == "or-gem-multi"
    assert m.call_args.kwargs["model"] == "gemini-2.5-pro"


def test_gemini_native_when_unset():
    pytest.importorskip("google.genai")
    import shared.gemini_client as gem

    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = SimpleNamespace(
        text="native-gemini",
        usage_metadata=SimpleNamespace(
            prompt_token_count=10,
            candidates_token_count=5,
            thoughts_token_count=0,
            cached_content_token_count=0,
        ),
    )

    with (
        patch.object(gem, "get_client", return_value=fake_client),
        patch("shared.state.record_api_call", lambda **k: None),
        patch("shared.openrouter_client.ask_openrouter") as m,
    ):
        out = gem.ask_gemini("hi", model="gemini-2.5-pro", thinking_budget=0)

    assert out == "native-gemini"
    m.assert_not_called()


# ── xAI carve-out（OpenRouter 不載 grok-4-fast tier → 恆原生）────────────────
def test_xai_stays_native_even_when_enabled(monkeypatch):
    monkeypatch.setenv("LLM_TRANSPORT", "openrouter")
    monkeypatch.setenv("XAI_API_KEY", "fake")
    import shared.xai_client as xai

    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="grok-native"))],
        usage=SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=5,
            prompt_tokens_details=SimpleNamespace(cached_tokens=0),
        ),
    )

    with (
        patch.object(xai, "get_client", return_value=fake_client),
        patch("shared.state.record_api_call", lambda **k: None),
        patch("shared.openrouter_client.ask_openrouter") as m,
    ):
        out = xai.ask_grok("hi", model="grok-4-fast-non-reasoning")

    assert out == "grok-native"
    m.assert_not_called()


# ── tool-use carve-out（OpenAI-compat tool schema 不同 → 恆原生）─────────────
def test_tool_use_stays_native_even_when_enabled(monkeypatch):
    monkeypatch.setenv("LLM_TRANSPORT", "openrouter")
    import shared.anthropic_client as ac

    sentinel = MagicMock(name="tool-use-message")
    fake_client = MagicMock()
    fake_client.messages.create.return_value = sentinel

    with (
        patch.object(ac, "get_client", return_value=fake_client),
        patch("shared.state.record_api_call", lambda **k: None),
        patch("shared.openrouter_client.ask_openrouter") as m1,
        patch("shared.openrouter_client.ask_openrouter_multi") as m2,
    ):
        out = ac.call_claude_with_tools(
            [{"role": "user", "content": "hi"}],
            [{"name": "t", "description": "d", "input_schema": {"type": "object"}}],
            model="claude-haiku-4-5",
        )

    assert out is sentinel
    m1.assert_not_called()
    m2.assert_not_called()


# ── OpenAI（無原生 client；只在 OpenRouter transport 下可用，Slice 4）──────────
def test_openai_routes_to_openrouter_when_enabled(monkeypatch):
    monkeypatch.setenv("LLM_TRANSPORT", "openrouter")
    from shared import llm

    with patch("shared.openrouter_client.ask_openrouter", return_value="or-gpt") as m:
        out = llm.ask("hi", model="gpt-5")

    assert out == "or-gpt"
    assert m.call_args.kwargs["model"] == "gpt-5"


def test_openai_multi_routes_to_openrouter_when_enabled(monkeypatch):
    monkeypatch.setenv("LLM_TRANSPORT", "openrouter")
    from shared import llm

    with patch("shared.openrouter_client.ask_openrouter_multi", return_value="or-gpt-m") as m:
        out = llm.ask_multi([{"role": "user", "content": "hi"}], model="gpt-5-mini")

    assert out == "or-gpt-m"
    assert m.call_args.kwargs["model"] == "gpt-5-mini"


def test_openai_raises_clearly_when_transport_native(monkeypatch):
    """native（kill-switch）時選到 OpenAI → fail loud（無原生 SDK），不 silent。"""
    monkeypatch.delenv("LLM_TRANSPORT", raising=False)
    from shared import llm

    with pytest.raises(NotImplementedError, match="LLM_TRANSPORT=openrouter"):
        llm.ask("hi", model="gpt-5")


# ── per-agent transport override（Slice 6 canary enabler）─────────────────────
def test_per_agent_override_enables_single_agent(monkeypatch):
    from shared.llm_transport import openrouter_enabled

    monkeypatch.delenv("LLM_TRANSPORT", raising=False)  # 全域 off
    monkeypatch.setenv("LLM_TRANSPORT_SANJI", "openrouter")
    assert openrouter_enabled(agent="sanji") is True
    assert openrouter_enabled(agent="nami") is False  # 沒被 override，全域也沒開


def test_per_agent_override_can_exclude_from_global(monkeypatch):
    from shared.llm_transport import openrouter_enabled

    monkeypatch.setenv("LLM_TRANSPORT", "openrouter")  # 全域開
    monkeypatch.setenv("LLM_TRANSPORT_SANJI", "native")  # 但 Sanji 反向排除
    assert openrouter_enabled(agent="sanji") is False
    assert openrouter_enabled(agent="nami") is True


def test_per_agent_override_reads_threadlocal_agent(monkeypatch):
    from shared.llm_context import set_current_agent
    from shared.llm_transport import openrouter_enabled

    monkeypatch.delenv("LLM_TRANSPORT", raising=False)
    monkeypatch.setenv("LLM_TRANSPORT_SANJI", "openrouter")
    set_current_agent("sanji", run_id=None)
    assert openrouter_enabled() is True  # 無顯式 agent → 讀 threadlocal
    set_current_agent("nami", run_id=None)
    assert openrouter_enabled() is False
