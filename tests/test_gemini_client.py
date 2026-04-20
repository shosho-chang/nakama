"""shared.gemini_client 測試。

不打真 API — monkeypatch `client.models.generate_content` 驗證：
- payload 組得對（audio bytes + mime_type + schema config）
- 有/沒有 response_schema 兩種路徑
- 缺 API key / 音檔不存在 的錯誤處理
- cost tracking 呼叫
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

import shared.gemini_client as gc


class Arbitration(BaseModel):
    text: str
    confidence: float


@pytest.fixture
def fake_audio(tmp_path: Path) -> Path:
    path = tmp_path / "clip.wav"
    path.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfake-audio-bytes")
    return path


@pytest.fixture(autouse=True)
def reset_singleton(monkeypatch):
    """每個測試前重置 client singleton + API key。"""
    monkeypatch.setattr(gc, "_client", None)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    yield


def _fake_response(
    text: str = "",
    parsed=None,
    input_tokens: int = 100,
    output_tokens: int = 50,
    thoughts_tokens: int = 0,
):
    return SimpleNamespace(
        text=text,
        parsed=parsed,
        usage_metadata=SimpleNamespace(
            prompt_token_count=input_tokens,
            candidates_token_count=output_tokens,
            thoughts_token_count=thoughts_tokens,
        ),
    )


class FakeClient:
    """捕捉 generate_content 呼叫，回傳預設 response。"""

    def __init__(self, response):
        self.response = response
        self.last_kwargs: dict | None = None

        class Models:
            def generate_content(inner_self, **kwargs):
                self.last_kwargs = kwargs
                return self.response

        self.models = Models()


def test_missing_api_key_raises(monkeypatch, fake_audio):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setattr(gc, "_client", None)

    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        gc.ask_gemini_audio(fake_audio, "prompt")


def test_missing_audio_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        gc.ask_gemini_audio(tmp_path / "nope.wav", "prompt")


def test_text_response(monkeypatch, fake_audio):
    fake_client = FakeClient(_fake_response(text="逐字稿內容"))
    monkeypatch.setattr(gc, "get_client", lambda: fake_client)

    result = gc.ask_gemini_audio(fake_audio, "請轉寫")

    assert result == "逐字稿內容"
    kwargs = fake_client.last_kwargs
    assert kwargs["model"] == "gemini-2.5-pro"
    # contents 應該是 [prompt, Part(audio_bytes)]
    assert kwargs["contents"][0] == "請轉寫"
    part = kwargs["contents"][1]
    assert part.inline_data.mime_type == "audio/wav"
    assert part.inline_data.data == fake_audio.read_bytes()


def test_structured_response_with_parsed(monkeypatch, fake_audio):
    """response.parsed 有值時直接用。"""
    parsed = Arbitration(text="嗨", confidence=0.9)
    fake_client = FakeClient(_fake_response(parsed=parsed))
    monkeypatch.setattr(gc, "get_client", lambda: fake_client)

    result = gc.ask_gemini_audio(fake_audio, "prompt", response_schema=Arbitration)

    assert isinstance(result, Arbitration)
    assert result.text == "嗨"
    assert result.confidence == 0.9

    # 驗證 schema 確實傳進 config
    config = fake_client.last_kwargs["config"]
    assert config.response_mime_type == "application/json"
    assert config.response_schema is not None


def test_structured_response_fallback_to_text(monkeypatch, fake_audio):
    """response.parsed 為 None 時 fallback 用 text JSON 解析。"""
    fake_client = FakeClient(_fake_response(text='{"text":"嗨","confidence":0.8}', parsed=None))
    monkeypatch.setattr(gc, "get_client", lambda: fake_client)

    result = gc.ask_gemini_audio(fake_audio, "prompt", response_schema=Arbitration)

    assert isinstance(result, Arbitration)
    assert result.text == "嗨"
    assert result.confidence == 0.8


def test_empty_text_response_raises(monkeypatch, fake_audio):
    fake_client = FakeClient(_fake_response(text=""))
    monkeypatch.setattr(gc, "get_client", lambda: fake_client)

    with pytest.raises(RuntimeError, match="Gemini 回應為空"):
        gc.ask_gemini_audio(fake_audio, "prompt")


def test_system_instruction_passed(monkeypatch, fake_audio):
    fake_client = FakeClient(_fake_response(text="ok"))
    monkeypatch.setattr(gc, "get_client", lambda: fake_client)

    gc.ask_gemini_audio(fake_audio, "prompt", system="你是仲裁員")

    config = fake_client.last_kwargs["config"]
    assert config.system_instruction == "你是仲裁員"


def test_custom_temperature_and_model(monkeypatch, fake_audio):
    fake_client = FakeClient(_fake_response(text="ok"))
    monkeypatch.setattr(gc, "get_client", lambda: fake_client)

    gc.ask_gemini_audio(
        fake_audio,
        "prompt",
        model="gemini-2.5-flash",
        temperature=0.7,
        max_output_tokens=512,
    )

    kwargs = fake_client.last_kwargs
    assert kwargs["model"] == "gemini-2.5-flash"
    config = kwargs["config"]
    assert config.temperature == 0.7
    assert config.max_output_tokens == 512


def test_mp3_mime_type(monkeypatch, tmp_path):
    path = tmp_path / "clip.mp3"
    path.write_bytes(b"ID3fake")
    fake_client = FakeClient(_fake_response(text="ok"))
    monkeypatch.setattr(gc, "get_client", lambda: fake_client)

    gc.ask_gemini_audio(path, "prompt")

    part = fake_client.last_kwargs["contents"][1]
    assert part.inline_data.mime_type == "audio/mpeg"


def test_cost_tracking_called(monkeypatch, fake_audio):
    fake_client = FakeClient(_fake_response(text="ok", input_tokens=123, output_tokens=45))
    monkeypatch.setattr(gc, "get_client", lambda: fake_client)

    recorded = {}

    def fake_record(**kwargs):
        recorded.update(kwargs)

    monkeypatch.setattr("shared.state.record_api_call", fake_record)
    gc.set_current_agent("test-agent", run_id=7)

    gc.ask_gemini_audio(fake_audio, "prompt")

    assert recorded["agent"] == "test-agent"
    assert recorded["run_id"] == 7
    assert recorded["input_tokens"] == 123
    assert recorded["output_tokens"] == 45
    assert recorded["model"] == "gemini-2.5-pro"


def test_cost_tracking_sums_thoughts_tokens(monkeypatch, fake_audio):
    """Reasoning model 的 thoughts_token_count 也是 output 計費，必須併入 output_tokens。"""
    fake_client = FakeClient(
        _fake_response(text="ok", input_tokens=100, output_tokens=50, thoughts_tokens=400)
    )
    monkeypatch.setattr(gc, "get_client", lambda: fake_client)

    recorded = {}

    def fake_record(**kwargs):
        recorded["output_tokens"] = kwargs["output_tokens"]

    monkeypatch.setattr("shared.state.record_api_call", fake_record)

    gc.ask_gemini_audio(fake_audio, "prompt")

    assert recorded["output_tokens"] == 450


def test_thinking_budget_default_applied(monkeypatch, fake_audio):
    """預設 thinking_budget=512 會注入 ThinkingConfig 到 GenerateContentConfig。"""
    fake_client = FakeClient(_fake_response(text="ok"))
    monkeypatch.setattr(gc, "get_client", lambda: fake_client)

    gc.ask_gemini_audio(fake_audio, "prompt")

    config = fake_client.last_kwargs["config"]
    assert config.thinking_config is not None
    assert config.thinking_config.thinking_budget == 512


def test_thinking_budget_custom_value(monkeypatch, fake_audio):
    fake_client = FakeClient(_fake_response(text="ok"))
    monkeypatch.setattr(gc, "get_client", lambda: fake_client)

    gc.ask_gemini_audio(fake_audio, "prompt", thinking_budget=128)

    config = fake_client.last_kwargs["config"]
    assert config.thinking_config.thinking_budget == 128


def test_thinking_budget_none_omits_config(monkeypatch, fake_audio):
    """thinking_budget=None 走 SDK 預設 dynamic thinking，不送 ThinkingConfig。"""
    fake_client = FakeClient(_fake_response(text="ok"))
    monkeypatch.setattr(gc, "get_client", lambda: fake_client)

    gc.ask_gemini_audio(fake_audio, "prompt", thinking_budget=None)

    config = fake_client.last_kwargs["config"]
    assert config.thinking_config is None


def test_cost_tracking_failure_does_not_break(monkeypatch, fake_audio):
    """state.record_api_call 炸掉不能影響主流程。"""
    fake_client = FakeClient(_fake_response(text="ok"))
    monkeypatch.setattr(gc, "get_client", lambda: fake_client)

    def boom(**kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr("shared.state.record_api_call", boom)

    # 不應該 raise
    result = gc.ask_gemini_audio(fake_audio, "prompt")
    assert result == "ok"


def test_retry_on_connection_error(monkeypatch, fake_audio):
    """ConnectionError 會 retry。"""
    calls = {"n": 0}

    class FlakyClient:
        class models:
            @staticmethod
            def generate_content(**kwargs):
                calls["n"] += 1
                if calls["n"] < 2:
                    raise ConnectionError("net blip")
                return _fake_response(text="ok")

    monkeypatch.setattr(gc, "get_client", lambda: FlakyClient())
    # 避免真的等 exponential backoff
    monkeypatch.setattr("shared.retry.time.sleep", lambda s: None)

    result = gc.ask_gemini_audio(fake_audio, "prompt")
    assert result == "ok"
    assert calls["n"] == 2


# ── 步驟 4：文字 API (ask_gemini / ask_gemini_multi) 測試 ─────────────


def _make_text_response(
    text: str = "hello",
    prompt_tokens: int = 100,
    candidates_tokens: int = 50,
    thoughts_tokens: int = 30,
    cached: int = 0,
):
    """組出 google-genai 回傳物件的形狀。"""
    from types import SimpleNamespace

    usage = SimpleNamespace(
        prompt_token_count=prompt_tokens,
        candidates_token_count=candidates_tokens,
        thoughts_token_count=thoughts_tokens,
        cached_content_token_count=cached,
        total_token_count=prompt_tokens + candidates_tokens + thoughts_tokens,
    )
    return SimpleNamespace(
        text=text,
        usage_metadata=usage,
        candidates=[SimpleNamespace(finish_reason="STOP")],
    )


def test_ask_gemini_returns_text(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    import shared.gemini_client as gc

    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = _make_text_response("gemini reply")

    monkeypatch.setattr(gc, "get_client", lambda: fake_client)
    out = gc.ask_gemini("hi", system="be concise", model="gemini-2.5-pro")

    assert out == "gemini reply"
    call_kwargs = fake_client.models.generate_content.call_args.kwargs
    assert call_kwargs["model"] == "gemini-2.5-pro"
    assert call_kwargs["config"].system_instruction == "be concise"


def test_ask_gemini_records_output_includes_thinking_tokens(monkeypatch):
    """Gemini 2.5 Pro 的 thinking token 也是 output 計費，必須併進 output_tokens。"""
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    import shared.gemini_client as gc

    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = _make_text_response(
        prompt_tokens=100, candidates_tokens=50, thoughts_tokens=200, cached=20
    )

    recorded = {}

    def fake_record(**kwargs):
        recorded.update(kwargs)

    monkeypatch.setattr(gc, "get_client", lambda: fake_client)
    monkeypatch.setattr("shared.state.record_api_call", fake_record)
    gc.ask_gemini("hi", model="gemini-2.5-pro")

    # thinking (200) + candidates (50) = 250 output
    assert recorded["output_tokens"] == 250
    assert recorded["input_tokens"] == 100
    assert recorded["cache_read_tokens"] == 20
    assert recorded["cache_write_tokens"] == 0  # Gemini implicit cache 不計 write


def test_ask_gemini_rejects_non_gemini_model():
    from shared.gemini_client import ask_gemini

    with pytest.raises(ValueError, match="non-Gemini model"):
        ask_gemini("hi", model="claude-sonnet-4-20250514")


def test_ask_gemini_multi_rejects_grok_model():
    from shared.gemini_client import ask_gemini_multi

    with pytest.raises(ValueError, match="non-Gemini model"):
        ask_gemini_multi(
            [{"role": "user", "content": "hi"}],
            model="grok-4-fast-non-reasoning",
        )


def test_ask_gemini_guard_fires_via_router(monkeypatch):
    """MODEL_<AGENT> 被誤設成非 Gemini ID 時也要擋下來。"""
    monkeypatch.setenv("MODEL_ROBIN", "claude-sonnet-4-20250514")
    from shared.anthropic_client import set_current_agent
    from shared.gemini_client import ask_gemini

    set_current_agent("robin", run_id=None)
    with pytest.raises(ValueError, match="non-Gemini model"):
        ask_gemini("hi")


def test_ask_gemini_resolves_model_via_router(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setenv("MODEL_ROBIN", "gemini-2.5-pro")
    import shared.gemini_client as gc
    from shared.anthropic_client import set_current_agent

    set_current_agent("robin", run_id=None)

    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = _make_text_response()

    monkeypatch.setattr(gc, "get_client", lambda: fake_client)
    gc.ask_gemini("hi")

    assert fake_client.models.generate_content.call_args.kwargs["model"] == "gemini-2.5-pro"


def test_gemini_client_shares_local_with_anthropic():
    """unified thread-local：anthropic_client.set_current_agent 也能標記 Gemini 呼叫。"""
    from shared.anthropic_client import _local as a_local
    from shared.gemini_client import _local as g_local

    assert a_local is g_local  # 同一個 threading.local 物件
