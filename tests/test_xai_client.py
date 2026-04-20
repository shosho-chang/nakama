"""shared.xai_client 的單元測試（mock OpenAI SDK，不打真 API）。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _reset_xai_client_singleton():
    import shared.xai_client as xai

    xai._client = None
    yield
    xai._client = None


@pytest.fixture
def _fake_openai_response():
    """OpenAI-compatible response shape（xAI 一樣）。"""

    def _make(text: str = "hello from grok", prompt_tokens: int = 100, cached: int = 20):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content=text, role="assistant")),
            ],
            usage=SimpleNamespace(
                prompt_tokens=prompt_tokens,
                completion_tokens=50,
                total_tokens=prompt_tokens + 50,
                prompt_tokens_details=SimpleNamespace(cached_tokens=cached),
            ),
        )

    return _make


def test_ask_grok_returns_content(monkeypatch, _fake_openai_response):
    monkeypatch.setenv("XAI_API_KEY", "fake-key")

    import shared.xai_client as xai

    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = _fake_openai_response("grok reply")

    with patch.object(xai, "get_client", return_value=fake_client):
        out = xai.ask_grok("hi", system="be friendly", model="grok-4-fast-non-reasoning")

    assert out == "grok reply"
    call_kwargs = fake_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "grok-4-fast-non-reasoning"
    # system 應 prepend 成 system message
    assert call_kwargs["messages"][0] == {"role": "system", "content": "be friendly"}
    assert call_kwargs["messages"][1] == {"role": "user", "content": "hi"}


def test_ask_grok_records_usage_subtracting_cached_tokens(
    monkeypatch, _fake_openai_response, tmp_path
):
    """xAI prompt_tokens 包含 cached — record_api_call 拿到的 input 要扣掉 cached。"""
    monkeypatch.setenv("XAI_API_KEY", "fake-key")

    import shared.xai_client as xai

    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = _fake_openai_response(
        prompt_tokens=100, cached=30
    )

    recorded = {}

    def fake_record_api_call(**kwargs):
        recorded.update(kwargs)

    with (
        patch.object(xai, "get_client", return_value=fake_client),
        patch("shared.state.record_api_call", fake_record_api_call),
    ):
        xai.ask_grok("hi", model="grok-4-fast-non-reasoning")

    assert recorded["input_tokens"] == 70  # 100 - 30
    assert recorded["cache_read_tokens"] == 30
    assert recorded["cache_write_tokens"] == 0  # xAI 不收 cache write
    assert recorded["output_tokens"] == 50


def test_ask_grok_resolves_model_via_router(monkeypatch, _fake_openai_response):
    """model=None 應走 router 解析，吃到 MODEL_SANJI env。"""
    monkeypatch.setenv("XAI_API_KEY", "fake-key")
    monkeypatch.setenv("MODEL_SANJI", "grok-4-fast-non-reasoning")

    import shared.xai_client as xai
    from shared.anthropic_client import set_current_agent

    set_current_agent("sanji", run_id=None)

    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = _fake_openai_response()

    with patch.object(xai, "get_client", return_value=fake_client):
        xai.ask_grok("hi")

    assert (
        fake_client.chat.completions.create.call_args.kwargs["model"] == "grok-4-fast-non-reasoning"
    )
