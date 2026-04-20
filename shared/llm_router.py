"""LLM model router — 把 (agent, task) 映射到 model ID。

Resolution 優先序（高到低）：
1. Caller 顯式傳 `model=...`（router 不介入）
2. Env var `MODEL_<AGENT>_<TASK>`（例如 `MODEL_BROOK_TOOL_USE`）
3. Env var `MODEL_<AGENT>`（例如 `MODEL_BROOK`）
4. `DEFAULT_MODELS[task]`

這是 production routing 層。Bench / eval 腳本請改走 LiteLLM（設計決策見
`memory/claude/project_multi_model_architecture.md`）。

目前 coverage：Anthropic models only。後續步驟會擴到 Google / xAI / OpenAI。
"""

from __future__ import annotations

import os

DEFAULT_MODELS: dict[str, str] = {
    "default": "claude-sonnet-4-20250514",
    "tool_use": "claude-haiku-4-5",
}


def get_model(agent: str | None = None, task: str = "default") -> str:
    """解析 (agent, task) 對應的 model ID。

    Args:
        agent: Agent 名稱（例如 "brook"、"robin"）。大小寫不敏感。
            None 代表沒有 agent 上下文，跳過 agent 層級覆寫。
        task: 任務類型。目前已知："default"、"tool_use"。

    Returns:
        Model ID 字串（例如 "claude-sonnet-4-6"、"gemini-2.5-pro"）。
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
    return DEFAULT_MODELS.get(task, DEFAULT_MODELS["default"])
