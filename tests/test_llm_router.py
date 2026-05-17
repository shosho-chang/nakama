"""shared.llm_router 的解析優先序測試。"""

from __future__ import annotations

import pytest

from shared.llm_router import DEFAULT_AUTH, DEFAULT_MODELS, get_auth_policy, get_model, get_provider


@pytest.fixture(autouse=True)
def _clean_model_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """確保測試間 env var 不污染：刪掉所有 MODEL_ / AUTH_ / hard-lock key。"""
    import os

    for key in list(os.environ.keys()):
        if key.startswith(("MODEL_", "AUTH_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("NAKAMA_REQUIRE_MAX_PLAN", raising=False)


def test_default_when_no_agent_and_no_env() -> None:
    assert get_model() == DEFAULT_MODELS["default"]
    assert get_model(task="tool_use") == DEFAULT_MODELS["tool_use"]


def test_default_when_agent_has_no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    assert get_model(agent="brook") == DEFAULT_MODELS["default"]
    assert get_model(agent="nami", task="tool_use") == DEFAULT_MODELS["tool_use"]


def test_agent_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_BROOK", "claude-opus-4-7")
    assert get_model(agent="brook") == "claude-opus-4-7"
    # Case-insensitive agent lookup
    assert get_model(agent="Brook") == "claude-opus-4-7"
    assert get_model(agent="BROOK") == "claude-opus-4-7"


def test_agent_env_affects_both_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    """MODEL_<AGENT> 是 agent-wide fallback，所有 task 都吃到。"""
    monkeypatch.setenv("MODEL_BROOK", "claude-opus-4-7")
    assert get_model(agent="brook", task="default") == "claude-opus-4-7"
    assert get_model(agent="brook", task="tool_use") == "claude-opus-4-7"


def test_task_specific_overrides_agent_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_NAMI", "claude-sonnet-4-6")
    monkeypatch.setenv("MODEL_NAMI_TOOL_USE", "claude-haiku-4-5")
    assert get_model(agent="nami", task="default") == "claude-sonnet-4-6"
    assert get_model(agent="nami", task="tool_use") == "claude-haiku-4-5"


def test_other_agent_env_does_not_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_BROOK", "claude-opus-4-7")
    assert get_model(agent="robin") == DEFAULT_MODELS["default"]


def test_none_agent_skips_agent_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_BROOK", "claude-opus-4-7")
    # agent=None 應該忽略 agent-level env
    assert get_model(agent=None) == DEFAULT_MODELS["default"]


def test_unknown_task_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    assert get_model(task="nonexistent") == DEFAULT_MODELS["default"]


def test_none_agent_emits_debug_log(caplog: pytest.LogCaptureFixture) -> None:
    """Issue C：thread 忘了呼叫 set_current_agent 時，至少留 debug 痕跡。"""
    import logging

    with caplog.at_level(logging.DEBUG, logger="nakama.llm_router"):
        get_model()

    assert any("without agent context" in r.message for r in caplog.records)


def test_get_provider_claude() -> None:
    assert get_provider("claude-sonnet-4-20250514") == "anthropic"
    assert get_provider("claude-opus-4-7") == "anthropic"
    assert get_provider("claude-haiku-4-5") == "anthropic"


def test_get_provider_grok() -> None:
    assert get_provider("grok-4") == "xai"
    assert get_provider("grok-4-fast-non-reasoning") == "xai"


def test_get_provider_gemini_and_openai() -> None:
    """未來 wire 上去之前先把識別面 coverage 敲穩。"""
    assert get_provider("gemini-2.5-pro") == "google"
    assert get_provider("gpt-4o") == "openai"
    assert get_provider("o1-preview") == "openai"
    assert get_provider("o3-mini") == "openai"


def test_get_provider_unknown_raises() -> None:
    with pytest.raises(ValueError, match="Unknown model provider"):
        get_provider("mystery-model")


# ---------------------------------------------------------------------------
# ADR-026: get_auth_policy
# ---------------------------------------------------------------------------


def test_auth_default_when_no_env() -> None:
    assert get_auth_policy() == DEFAULT_AUTH["default"]
    assert get_auth_policy() == "api"


def test_auth_tool_use_default_is_api() -> None:
    """tool_use 強制 api：CLI subprocess path 拿不到 raw tool-use JSON。"""
    assert get_auth_policy(task="tool_use") == "api"


def test_auth_default_when_agent_set_but_no_env() -> None:
    """Agent context without explicit AUTH_<AGENT> falls through to api too."""
    assert get_auth_policy(agent="brook") == "api"
    assert get_auth_policy(agent="robin", task="translate") == "api"


def test_auth_agent_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_ROBIN", "api")
    assert get_auth_policy(agent="robin") == "api"
    assert get_auth_policy(agent="Robin") == "api"


def test_auth_task_specific_overrides_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_ROBIN", "api")
    monkeypatch.setenv("AUTH_ROBIN_TRANSLATE", "subscription_required")
    assert get_auth_policy(agent="robin", task="default") == "api"
    assert get_auth_policy(agent="robin", task="translate") == "subscription_required"


def test_auth_hard_lock_overrides_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    """NAKAMA_REQUIRE_MAX_PLAN=1 是 process-wide hard-lock，蓋過 agent/task env。"""
    monkeypatch.setenv("AUTH_ROBIN", "api")
    monkeypatch.setenv("AUTH_BROOK_TRANSLATE", "api")
    monkeypatch.setenv("NAKAMA_REQUIRE_MAX_PLAN", "1")
    assert get_auth_policy(agent="robin") == "subscription_required"
    assert get_auth_policy(agent="brook", task="translate") == "subscription_required"
    assert get_auth_policy(task="tool_use") == "subscription_required"


def test_auth_hard_lock_only_when_exactly_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """避免 NAKAMA_REQUIRE_MAX_PLAN=0 / 空字串被誤判成 truthy。"""
    monkeypatch.setenv("NAKAMA_REQUIRE_MAX_PLAN", "0")
    assert get_auth_policy() == "api"
    monkeypatch.setenv("NAKAMA_REQUIRE_MAX_PLAN", "")
    assert get_auth_policy() == "api"


def test_auth_invalid_value_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_ROBIN", "subscription")  # 拼錯，少了 _preferred
    with pytest.raises(ValueError, match="Invalid auth policy"):
        get_auth_policy(agent="robin")


def test_auth_other_agent_env_does_not_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_ROBIN", "subscription_required")
    assert get_auth_policy(agent="brook") == "api"


def test_auth_unknown_task_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    assert get_auth_policy(task="nonexistent") == DEFAULT_AUTH["default"]


def test_get_provider_o_series_requires_hyphen() -> None:
    """裸 "o1"/"o3" prefix 會誤吃未來非 openai 的怪 ID，所以 prefix 必須帶 hyphen。"""
    # 合法的 o-series（帶 hyphen）
    assert get_provider("o1-preview") == "openai"
    assert get_provider("o3-mini") == "openai"
    # 不該被吃的 — 不帶 hyphen 的奇怪 prefix
    with pytest.raises(ValueError, match="Unknown model provider"):
        get_provider("o100-xyz")
    with pytest.raises(ValueError, match="Unknown model provider"):
        get_provider("o1something")
