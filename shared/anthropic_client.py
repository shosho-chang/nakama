"""Anthropic Claude API wrapper，內建 retry + cost tracking。

跨 provider 共用的 thread-local context 與 cost-tracking 入口在
:mod:`shared.llm_context` / :mod:`shared.llm_observability`；本檔只關心
Anthropic-specific 的 request building + response parsing。

Thread-local 設置請直接 ``from shared.llm_context import set_current_agent``。
跨 provider routing 走 :func:`shared.llm.ask`。
"""

from __future__ import annotations

import os
import time

import anthropic

from shared.llm_context import _local
from shared.llm_observability import record_call
from shared.log import get_logger
from shared.retry import with_retry

logger = get_logger("nakama.anthropic_client")

__all__ = [
    "ask_claude",
    "ask_claude_multi",
    "call_claude_with_tools",
    "get_client",
]

_client: anthropic.Anthropic | None = None


def get_client() -> anthropic.Anthropic:
    """取得或建立 Anthropic client（singleton）。"""
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def _record_anthropic_usage(
    response: anthropic.types.Message, model: str, *, latency_ms: int = 0
) -> None:
    """把 Anthropic-shape usage 抽成共通欄位後 delegate 給 observability.record_call。

    Cost tracking 不影響主流程：response 形狀異常（測試 stub 沒帶 usage 等）
    或下游 record_call 出錯都吞掉，只記 debug log。對稱 ``_record_xai_usage`` /
    ``_record_gemini_usage``。
    """
    try:
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        record_call(
            model=model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            latency_ms=latency_ms,
        )
    except Exception as e:
        logger.debug("cost tracking 失敗（忽略）：%s", e)


def ask_claude(
    prompt: str,
    *,
    system: str = "",
    model: str | None = None,
    max_tokens: int = 4096,
    temperature: float | None = None,
) -> str:
    """送出一次 Claude API 請求，回傳純文字回應。

    自動重試（最多 3 次，指數退避）並記錄 token 用量。
    Claude 4.7 以後的模型已廢除 temperature，預設不送。

    ``model=None`` 時會走 :func:`shared.llm_router.get_model` 依當前 agent 解析。
    Resolved model 若不是 Claude 系列會直接 raise — 避免 Anthropic SDK 對
    非 ``claude-`` ID 噴模糊錯誤後自動 retry 3 次浪費時間。跨 provider 路由請
    改走 :func:`shared.llm.ask`。
    """
    if model is None:
        from shared.llm_router import get_model

        model = get_model(agent=getattr(_local, "agent", None), task="default")
    _require_claude_model(model)

    def _call() -> anthropic.types.Message:
        client = get_client()
        kwargs: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        if system:
            kwargs["system"] = system
        return client.messages.create(**kwargs)

    start = time.perf_counter()
    response = with_retry(_call, max_attempts=3, backoff_base=2.0)
    latency_ms = int((time.perf_counter() - start) * 1000)

    _record_anthropic_usage(response, model, latency_ms=latency_ms)

    return response.content[0].text


def call_claude_with_tools(
    messages: list[dict],
    tools: list[dict],
    *,
    system: str = "",
    model: str | None = None,
    max_tokens: int = 2048,
    tool_choice: dict | None = None,
) -> anthropic.types.Message:
    """呼叫 Claude 的 tool-use API，回傳完整 Message 物件（含 stop_reason、content blocks）。

    與 :func:`ask_claude_multi` 不同，這個函式 **不** 把 response 擷取為字串——
    呼叫端需要 inspect ``stop_reason``（``"end_turn"`` / ``"tool_use"``）以及
    content blocks 才能驅動 agent loop。

    Prompt caching：會在 system prompt 的最後一個 block 加 ``cache_control``，讓
    tools + system 整段被 cache。呼叫端請確保 ``system`` 與 ``tools`` 是確定性
    的（不要含 ``datetime.now()``、UUID 等每次變化的內容），否則 cache 不會命中。

    Args:
        messages: Claude API messages 格式（alternate user/assistant）
        tools: Tool definitions（JSON schema 陣列）
        system: System prompt
        model: 模型。``None`` 則走 :func:`shared.llm_router.get_model` 用
            ``task="tool_use"``（預設 Haiku 4.5，tool-routing 任務通常夠用）。
        max_tokens: 最大輸出 token 數
        tool_choice: 強制 tool 選擇策略（例如 ``{"type": "tool", "name": "my_tool"}``
            強制呼叫特定 tool 以確保結構化輸出）。``None`` 表示讓 Claude 自行決定。

    Returns:
        ``anthropic.types.Message``（含 content + stop_reason + usage）
    """
    if model is None:
        from shared.llm_router import get_model

        model = get_model(agent=getattr(_local, "agent", None), task="tool_use")
    _require_claude_model(model)

    def _call() -> anthropic.types.Message:
        client = get_client()
        kwargs: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
            "tools": tools,
        }
        if system:
            kwargs["system"] = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ]
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        return client.messages.create(**kwargs)

    start = time.perf_counter()
    response = with_retry(_call, max_attempts=3, backoff_base=2.0)
    latency_ms = int((time.perf_counter() - start) * 1000)

    _record_anthropic_usage(response, model, latency_ms=latency_ms)

    return response


def ask_claude_multi(
    messages: list[dict],
    *,
    system: str = "",
    model: str | None = None,
    max_tokens: int = 4096,
    temperature: float | None = None,
) -> str:
    """送出多回合 Claude API 請求，回傳純文字回應。

    與 :func:`ask_claude` 相同的 retry / cost tracking 機制，
    但接受完整 messages 陣列以支援多回合對話。
    Claude 4.7 以後的模型已廢除 temperature，預設不送。

    Args:
        messages: Claude API messages 格式，
                  例如 ``[{"role": "user", "content": "..."}, ...]``
        system:   系統 prompt
        model:    模型名稱。``None`` 則走 :func:`shared.llm_router.get_model`。
        max_tokens: 最大回應 token 數
        temperature: 溫度（``None`` 表示不送）

    Returns:
        assistant 回應的純文字
    """
    if model is None:
        from shared.llm_router import get_model

        model = get_model(agent=getattr(_local, "agent", None), task="default")
    _require_claude_model(model)

    def _call() -> anthropic.types.Message:
        client = get_client()
        kwargs: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        if system:
            kwargs["system"] = system
        return client.messages.create(**kwargs)

    start = time.perf_counter()
    response = with_retry(_call, max_attempts=3, backoff_base=2.0)
    latency_ms = int((time.perf_counter() - start) * 1000)

    _record_anthropic_usage(response, model, latency_ms=latency_ms)

    return response.content[0].text


def _require_claude_model(model: str) -> None:
    """Fail fast if router resolved a non-Claude model for an Anthropic call.

    Anthropic SDK would otherwise retry 3 times on a validation error before
    surfacing a confusing message. Route via :func:`shared.llm.ask` instead for
    cross-provider dispatch.
    """
    if not model.startswith("claude-"):
        raise ValueError(
            f"ask_claude / call_claude_with_tools received non-Claude model "
            f"'{model}'. Use shared.llm.ask() for cross-provider routing, "
            f"or check MODEL_<AGENT> env vars for a wrong value."
        )
