"""xAI Grok API wrapper — 走 OpenAI-compatible REST endpoint（``https://api.x.ai/v1``）。

為什麼不用 ``xai-sdk``：它是 gRPC，部分 VPS / corporate proxy 會擋。OpenAI SDK
走 HTTP 更穩，response shape 也對齊既有 cost-tracking 入口的鉤子。

Thread-local context 與 cost-tracking 入口統一在 :mod:`shared.llm_context` /
:mod:`shared.llm_observability`，本檔只關心 xAI-specific 的 request building +
response parsing（OpenAI shape 的 token 抽取，cached_tokens 從 prompt_tokens
扣除避免重複計費等）。
"""

from __future__ import annotations

import os
import time

from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)

from shared.llm_context import _local
from shared.llm_observability import record_call
from shared.log import get_logger
from shared.retry import with_retry

logger = get_logger("nakama.xai_client")

_client: OpenAI | None = None

# openai SDK 的可重試例外（xAI 同 endpoint 行為一致）
_XAI_RETRYABLE = (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)


def get_client() -> OpenAI:
    """取得或建立 xAI client（singleton）。"""
    global _client
    if _client is None:
        # dotenv 對 inline `#` 註解處理不一致 → env 值可能變成「# 留空...」這種
        # 非 URL 字串，讓 httpx 噴「missing protocol」。保險做法：只接受
        # 明確的 http(s) URL，其他全部 fallback 到官方 endpoint。
        raw_base_url = (os.environ.get("XAI_BASE_URL") or "").strip()
        base_url = (
            raw_base_url
            if raw_base_url.startswith(("http://", "https://"))
            else "https://api.x.ai/v1"
        )
        _client = OpenAI(
            api_key=os.environ["XAI_API_KEY"],
            base_url=base_url,
        )
    return _client


def ask_grok(
    prompt: str,
    *,
    system: str = "",
    model: str | None = None,
    max_tokens: int = 4096,
    temperature: float | None = None,
) -> str:
    """送出一次 xAI Grok 請求，回傳純文字回應。

    自動重試（最多 3 次，指數退避）並記錄 token 用量。

    ``model=None`` 時會走 :func:`shared.llm_router.get_model` 依當前 agent 解析。
    Resolved model 若不是 Grok 系列會直接 raise — 避免 xAI endpoint 對
    非 ``grok-`` ID 噴模糊 404 後被 retry 包住白等 6 秒（對稱 anthropic_client
    的 ``_require_claude_model`` guard）。跨 provider 路由請改走
    :func:`shared.llm.ask`。
    """
    if model is None:
        from shared.llm_router import get_model

        model = get_model(agent=getattr(_local, "agent", None), task="default")
    _require_grok_model(model)

    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    def _call():
        client = get_client()
        kwargs: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        return client.chat.completions.create(**kwargs)

    start = time.perf_counter()
    response = with_retry(_call, max_attempts=3, backoff_base=2.0, retryable=_XAI_RETRYABLE)
    latency_ms = int((time.perf_counter() - start) * 1000)

    _record_xai_usage(model, response, latency_ms=latency_ms)

    return response.choices[0].message.content or ""


def ask_grok_multi(
    messages: list[dict],
    *,
    system: str = "",
    model: str | None = None,
    max_tokens: int = 4096,
    temperature: float | None = None,
) -> str:
    """多回合 Grok 請求。messages 用 OpenAI 格式（role: user/assistant）。

    若提供 ``system``，自動 prepend 為 system message。
    """
    if model is None:
        from shared.llm_router import get_model

        model = get_model(agent=getattr(_local, "agent", None), task="default")
    _require_grok_model(model)

    full_messages: list[dict] = []
    if system:
        full_messages.append({"role": "system", "content": system})
    full_messages.extend(messages)

    def _call():
        client = get_client()
        kwargs: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": full_messages,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        return client.chat.completions.create(**kwargs)

    start = time.perf_counter()
    response = with_retry(_call, max_attempts=3, backoff_base=2.0, retryable=_XAI_RETRYABLE)
    latency_ms = int((time.perf_counter() - start) * 1000)

    _record_xai_usage(model, response, latency_ms=latency_ms)

    return response.choices[0].message.content or ""


def _require_grok_model(model: str) -> None:
    """Fail fast if router resolved a non-Grok model for an xAI call.

    xAI endpoint would otherwise return a generic 404/invalid-model error,
    and the retry wrapper would burn ~6s before surfacing it. Route via
    :func:`shared.llm.ask` for cross-provider dispatch, or fix a wrong
    ``MODEL_<AGENT>`` env var. 對稱 :func:`shared.anthropic_client._require_claude_model`。
    """
    if not model.startswith("grok-"):
        raise ValueError(
            f"ask_grok / ask_grok_multi received non-Grok model '{model}'. "
            f"Use shared.llm.ask() for cross-provider routing, "
            f"or check MODEL_<AGENT> env vars for a wrong value."
        )


def _record_xai_usage(model: str, response, *, latency_ms: int = 0) -> None:
    """把 OpenAI-shape usage 抽成共通欄位後 delegate 給 observability.record_call。

    xAI quirk：``prompt_tokens`` 包含 cached_tokens（不是附加），所以
    ``input_tokens = prompt_tokens - cached_tokens`` 才不會重複計費。
    xAI 沒有 cache_write 計費，固定填 0。

    Cost tracking 不影響主流程：response 形狀異常（測試 stub 沒帶 usage 等）
    或下游 record_call 出錯都吞掉，只記 debug log。
    """
    try:
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        details = getattr(usage, "prompt_tokens_details", None)
        cached = 0
        if details is not None:
            cached = getattr(details, "cached_tokens", 0) or 0

        record_call(
            model=model,
            input_tokens=max(prompt_tokens - cached, 0),
            output_tokens=completion_tokens,
            cache_read_tokens=cached,
            cache_write_tokens=0,
            latency_ms=latency_ms,
        )
    except Exception as e:
        logger.debug("cost tracking 失敗（忽略）：%s", e)
