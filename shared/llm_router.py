"""LLM model router — 把 (agent, task) 映射到 model ID，並由 ID 反推 provider。

Resolution 優先序（高到低）：
1. Caller 顯式傳 `model=...`（router 不介入）
2. Env var `MODEL_<AGENT>_<TASK>`（例如 `MODEL_BROOK_TOOL_USE`）
3. Env var `MODEL_<AGENT>`（例如 `MODEL_BROOK`）
4. `DEFAULT_MODELS[task]`

這是 production routing 層。Bench / eval 腳本請改走 LiteLLM（設計決策見
`memory/claude/project_multi_model_architecture.md`）。

Provider coverage（2026-04 步驟 2）：Anthropic + xAI。後續步驟擴 Google + OpenAI。
"""

from __future__ import annotations

import os

from shared.log import get_logger

logger = get_logger("nakama.llm_router")

DEFAULT_MODELS: dict[str, str] = {
    "default": "claude-sonnet-4-20250514",
    "tool_use": "claude-haiku-4-5",
}

# ADR-026: auth policy 解析。ternary 值 — "api" / "subscription_preferred" /
# "subscription_required"。預設 subscription_preferred（修修長期 Max Plan
# 訂閱者），缺 OAuth token 時軟降 API 並寫 fallback_reason 觀察。
# tool_use 強制 api：CLI subprocess path 拿不到 raw tool-use JSON。
_VALID_AUTH_POLICIES: frozenset[str] = frozenset(
    {"api", "subscription_preferred", "subscription_required"}
)

DEFAULT_AUTH: dict[str, str] = {
    "default": "subscription_preferred",
    "tool_use": "api",
}

# Prefix → provider。擴 provider 時在這裡加一行，`get_provider` 與
# `shared/llm.py` 的 dispatch 就自動吃到。
_PROVIDER_PREFIXES: tuple[tuple[str, str], ...] = (
    ("claude-", "anthropic"),
    ("grok-", "xai"),
    ("gemini-", "google"),
    ("gpt-", "openai"),
    # 全帶 trailing hyphen 才一致，避免 "o1"/"o3" 的裸 prefix 誤吃
    # 未來無關模型（e.g. 非 openai 的 "o1something"、"o100-xyz"）
    ("o1-", "openai"),
    ("o3-", "openai"),
)


def get_model(agent: str | None = None, task: str = "default") -> str:
    """解析 (agent, task) 對應的 model ID。

    Args:
        agent: Agent 名稱（例如 "brook"、"robin"）。大小寫不敏感。
            None 代表沒有 agent 上下文，跳過 agent 層級覆寫並記 debug log
            （協助診斷忘了呼叫 `set_current_agent` 的 silent fallback）。
        task: 任務類型。目前已知："default"、"tool_use"。

    Returns:
        Model ID 字串（例如 "claude-sonnet-4-6"、"grok-4-fast-non-reasoning"）。
    """
    if agent:
        agent_upper = agent.upper()
        task_upper = task.upper()
        specific = os.environ.get(f"MODEL_{agent_upper}_{task_upper}")
        if specific:
            return specific
        agent_default = os.environ.get(f"MODEL_{agent_upper}")
        if agent_default:
            return agent_default
    else:
        # 沒有 agent context — 可能是 caller 沒呼叫 set_current_agent。
        # debug 層級不影響正常輸出，但 `LOG_LEVEL=DEBUG` 時能看到。
        logger.debug(
            "get_model called without agent context; falling back to DEFAULT_MODELS[%s]",
            task,
        )
    return DEFAULT_MODELS.get(task, DEFAULT_MODELS["default"])


def get_auth_policy(agent: str | None = None, task: str = "default") -> str:
    """解析 (agent, task) 對應的 auth policy（ADR-026）。

    Resolution 優先序（高到低）：
    1. `NAKAMA_REQUIRE_MAX_PLAN=1` → 強制 `subscription_required`
       （process-wide hard-lock override，保留給 textbook ingest / sandcastle
       這種 100% 必須走 Max Plan 的場景）
    2. Env var `AUTH_<AGENT>_<TASK>`
    3. Env var `AUTH_<AGENT>`
    4. `DEFAULT_AUTH[task]`（預設 ``subscription_preferred``）

    Args:
        agent: Agent 名稱（大小寫不敏感）。``None`` 跳過 agent 層覆寫。
        task: 任務類型（"default" / "tool_use" / "translate" 等）。

    Returns:
        ``"api"`` / ``"subscription_preferred"`` / ``"subscription_required"`` 之一。

    Raises:
        ValueError: env 設了未知值（拼錯保護，避免 silent 走預設）。
    """
    if os.environ.get("NAKAMA_REQUIRE_MAX_PLAN") == "1":
        return "subscription_required"

    if agent:
        agent_upper = agent.upper()
        task_upper = task.upper()
        specific = os.environ.get(f"AUTH_{agent_upper}_{task_upper}")
        if specific:
            return _validate_auth_policy(specific, f"AUTH_{agent_upper}_{task_upper}")
        agent_default = os.environ.get(f"AUTH_{agent_upper}")
        if agent_default:
            return _validate_auth_policy(agent_default, f"AUTH_{agent_upper}")
    else:
        logger.debug(
            "get_auth_policy called without agent context; falling back to DEFAULT_AUTH[%s]",
            task,
        )
    return DEFAULT_AUTH.get(task, DEFAULT_AUTH["default"])


def _validate_auth_policy(value: str, source: str) -> str:
    if value not in _VALID_AUTH_POLICIES:
        raise ValueError(
            f"Invalid auth policy '{value}' from {source}; "
            f"must be one of {sorted(_VALID_AUTH_POLICIES)}"
        )
    return value


def get_provider(model: str) -> str:
    """由 model ID 推出 provider（"anthropic" / "xai" / "google" / "openai"）。

    Raises:
        ValueError: 無法辨識的 model ID prefix。
    """
    for prefix, provider in _PROVIDER_PREFIXES:
        if model.startswith(prefix):
            return provider
    raise ValueError(
        f"Unknown model provider for '{model}'. "
        f"Known prefixes: {[p for p, _ in _PROVIDER_PREFIXES]}"
    )
