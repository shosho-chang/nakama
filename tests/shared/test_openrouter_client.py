"""shared.openrouter_client 的單元測試（mock OpenAI SDK，不打真 API）。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _reset_openrouter_client_singleton():
    import shared.openrouter_client as orc

    orc._client = None
    yield
    orc._client = None


@pytest.fixture
def _fake_response():
    """OpenRouter response shape（OpenAI-compatible + usage.cost from usage accounting）。"""

    def _make(text="hello from openrouter", prompt_tokens=100, cached=20, cost=0.0123):
        usage = SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=50,
            total_tokens=prompt_tokens + 50,
            prompt_tokens_details=SimpleNamespace(cached_tokens=cached),
            cost=cost,
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=text, role="assistant"))],
            usage=usage,
        )

    return _make


def test_ask_openrouter_translates_bare_to_slug(monkeypatch, _fake_response):
    """ask_openrouter 收 bare ID，送給 OpenRouter 的 model 必須是翻好的 slug。"""
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-key")
    import shared.openrouter_client as orc

    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = _fake_response("reply")

    with patch.object(orc, "get_client", return_value=fake_client):
        out = orc.ask_openrouter("hi", system="be nice", model="claude-sonnet-4-6")

    assert out == "reply"
    call_kwargs = fake_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "anthropic/claude-sonnet-4.6"  # slug, 不是 bare ID
    assert call_kwargs["messages"][0] == {"role": "system", "content": "be nice"}
    assert call_kwargs["messages"][1] == {"role": "user", "content": "hi"}


def test_extra_body_requests_usage_and_disables_byok_fallback(monkeypatch, _fake_response):
    """extra_body 帶 usage.include=True + provider.allow_fallbacks=False（BYOK 安全）。"""
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-key")
    import shared.openrouter_client as orc

    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = _fake_response()

    with patch.object(orc, "get_client", return_value=fake_client):
        orc.ask_openrouter("hi", model="claude-sonnet-4-6")

    extra = fake_client.chat.completions.create.call_args.kwargs["extra_body"]
    assert extra["usage"] == {"include": True}
    assert extra["provider"]["allow_fallbacks"] is False


def test_models_fallback_list_translated_to_slugs(monkeypatch, _fake_response):
    """models[] fallback 鏈也用 bare ID 傳入，逐一翻 slug。"""
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-key")
    import shared.openrouter_client as orc

    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = _fake_response()

    with patch.object(orc, "get_client", return_value=fake_client):
        orc.ask_openrouter(
            "hi", model="claude-sonnet-4-6", models=["claude-opus-4-8", "gpt-5-mini"]
        )

    extra = fake_client.chat.completions.create.call_args.kwargs["extra_body"]
    assert extra["models"] == ["anthropic/claude-opus-4.8", "openai/gpt-5-mini"]


def test_records_usage_with_bare_id_subtracting_cached(monkeypatch, _fake_response):
    """record_api_call 拿到的是 bare model ID（非 slug），input 扣掉 cached。"""
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-key")
    import shared.openrouter_client as orc

    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = _fake_response(prompt_tokens=100, cached=30)

    recorded = {}

    def fake_record_api_call(**kwargs):
        recorded.update(kwargs)

    with (
        patch.object(orc, "get_client", return_value=fake_client),
        patch("shared.state.record_api_call", fake_record_api_call),
    ):
        orc.ask_openrouter("hi", model="claude-sonnet-4-6")

    assert recorded["model"] == "claude-sonnet-4-6"  # bare ID, 對齊系統其餘命名
    assert recorded["input_tokens"] == 70  # 100 - 30
    assert recorded["cache_read_tokens"] == 30
    assert recorded["cache_write_tokens"] == 0
    assert recorded["output_tokens"] == 50


def test_resolves_model_via_router_default(monkeypatch, _fake_response):
    """model=None → router 解析；無 agent / env 時走 DEFAULT_MODELS['default']（可翻 slug）。"""
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-key")
    import shared.openrouter_client as orc

    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = _fake_response()

    with patch.object(orc, "get_client", return_value=fake_client):
        orc.ask_openrouter("hi")  # model=None

    # DEFAULT_MODELS['default'] == claude-opus-5 → anthropic/claude-opus-5
    assert (
        fake_client.chat.completions.create.call_args.kwargs["model"] == "anthropic/claude-opus-5"
    )


def test_grok_model_raises_before_network(monkeypatch):
    """grok-* 在送網路前就 raise（slug 翻譯即 fail-fast guard），xAI 留原生。"""
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-key")
    from shared.openrouter_client import ask_openrouter
    from shared.openrouter_models import UnmappedModelError

    with pytest.raises(UnmappedModelError):
        ask_openrouter("hi", model="grok-4-fast-non-reasoning")


def test_extract_cost_uses_openrouter_actual_cost():
    """usage.cost 有值 → 用實際 cost，source='openrouter'。"""
    from shared.openrouter_client import _extract_cost

    usage = SimpleNamespace(cost=0.0456)
    cost, source = _extract_cost(
        "claude-sonnet-4-6",
        usage,
        input_tokens=1000,
        output_tokens=500,
        cache_read_tokens=0,
        cache_write_tokens=0,
    )
    assert cost == 0.0456
    assert source == "openrouter"


def test_extract_cost_reads_pydantic_model_extra():
    """真實 OpenAI SDK 把額外欄位放 model_extra；cost 從那裡取得也算 'openrouter'。"""
    from shared.openrouter_client import _extract_cost

    usage = SimpleNamespace(model_extra={"cost": 0.009})
    cost, source = _extract_cost(
        "claude-sonnet-4-6",
        usage,
        input_tokens=10,
        output_tokens=5,
        cache_read_tokens=0,
        cache_write_tokens=0,
    )
    assert cost == 0.009
    assert source == "openrouter"


def test_extract_cost_falls_back_when_missing():
    """usage 無 cost → fallback 到 pricing.calc_cost，source='fallback'，不丟例外。"""
    from shared.openrouter_client import _extract_cost

    usage = SimpleNamespace(prompt_tokens=1000, completion_tokens=500)  # 無 cost / model_extra
    cost, source = _extract_cost(
        "claude-sonnet-4-6",
        usage,
        input_tokens=1000,
        output_tokens=500,
        cache_read_tokens=0,
        cache_write_tokens=0,
    )
    assert source == "fallback"
    assert cost > 0  # claude- family 在 pricing.py 有估算條目


def test_usage_recording_never_raises_on_bad_shape(monkeypatch):
    """response 沒有 usage 時，記錄端必須安靜吞掉（cost tracking 不影響主流程）。"""
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-key")
    import shared.openrouter_client as orc

    bad_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
    )  # 無 usage 屬性
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = bad_response

    with patch.object(orc, "get_client", return_value=fake_client):
        out = orc.ask_openrouter("hi", model="claude-sonnet-4-6")

    assert out == "ok"  # 主流程不受 usage 缺失影響


def test_records_actual_cost_usd_to_record_call(monkeypatch, _fake_response):
    """OpenRouter 回 usage.cost → record_api_call 收到實際 cost_usd（Slice 3）。"""
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-key")
    import shared.openrouter_client as orc

    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = _fake_response(cost=0.0789)
    recorded = {}

    with (
        patch.object(orc, "get_client", return_value=fake_client),
        patch("shared.state.record_api_call", lambda **k: recorded.update(k)),
    ):
        orc.ask_openrouter("hi", model="claude-sonnet-4-6")

    assert recorded["cost_usd"] == pytest.approx(0.0789)


def test_records_none_cost_when_openrouter_omits_cost(monkeypatch, _fake_response):
    """OpenRouter 沒回 cost → cost_usd=None（不把 fallback 估算混進實際 cost 欄位）。"""
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-key")
    import shared.openrouter_client as orc

    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = _fake_response(cost=None)
    recorded = {}

    with (
        patch.object(orc, "get_client", return_value=fake_client),
        patch("shared.state.record_api_call", lambda **k: recorded.update(k)),
    ):
        orc.ask_openrouter("hi", model="claude-sonnet-4-6")

    assert recorded["cost_usd"] is None
