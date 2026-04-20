"""shared.llm_router 的解析優先序測試。"""

from __future__ import annotations

import pytest

from shared.llm_router import DEFAULT_MODELS, get_model


@pytest.fixture(autouse=True)
def _clean_model_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """確保測試間 env var 不污染：刪掉所有 MODEL_ 開頭的 key。"""
    import os

    for key in list(os.environ.keys()):
        if key.startswith("MODEL_"):
            monkeypatch.delenv(key, raising=False)


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
