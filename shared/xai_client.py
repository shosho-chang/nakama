"""xAI Grok API wrapper — 走 OpenAI-compatible REST endpoint（`https://api.x.ai/v1`）。

為什麼不用 `xai-sdk`：它是 gRPC，部分 VPS / corporate proxy 會擋。OpenAI SDK
走 HTTP 更穩，response shape 也對齊既有 `record_api_call` 的鉤子。

Thread-local agent 與 `shared.anthropic_client` 共用，由 `BaseAgent.execute()`
呼叫 `set_current_agent()` 設定，兩邊都吃得到，cost tracking 不需要分兩套。
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

from shared.anthropic_client import _local  # thread-local (agent, run_id)
from shared.retry import with_retry

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

    `model=None` 時會走 `shared.llm_router.get_model()` 依當前 agent 解析。
    Resolved model 若不是 Grok 系列會直接 raise — 避免 xAI endpoint 對
    非 grok ID 噴模糊 404 後被 retry 包住白等 6 秒（對稱 anthropic_client
    的 `_require_claude_model` guard）。跨 provider 路由請改走 `shared.llm.ask()`。
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

    _record_usage(model, response, latency_ms=latency_ms)

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

    若提供 `system`，自動 prepend 為 system message。
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

    _record_usage(model, response, latency_ms=latency_ms)

    return response.choices[0].message.content or ""


def _require_grok_model(model: str) -> None:
    """Fail fast if router resolved a non-Grok model for an xAI call.

    xAI endpoint would otherwise return a generic 404/invalid-model error,
    and the retry wrapper would burn ~6s before surfacing it. Route via
    `shared.llm.ask()` for cross-provider dispatch, or fix a wrong
    `MODEL_<AGENT>` env var. 對稱 `anthropic_client._require_claude_model`。
    """
    if not model.startswith("grok-"):
        raise ValueError(
            f"ask_grok / ask_grok_multi received non-Grok model '{model}'. "
            f"Use shared.llm.ask() for cross-provider routing, "
            f"or check MODEL_<AGENT> env vars for a wrong value."
        )


def _record_usage(model: str, response, *, latency_ms: int = 0) -> None:
    """把 OpenAI-shape usage 轉成 record_api_call 的欄位。

    xAI quirk：`prompt_tokens` 包含 cached_tokens（不是附加），所以
    `input_tokens = prompt_tokens - cached_tokens` 才不會重複計費。
    xAI 沒有 cache_write 計費，固定填 0。
    ``latency_ms`` 由 caller 提供（end-to-end 含 retry 時間）。
    """
    try:
        from shared.state import record_api_call

        usage = response.usage
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        details = getattr(usage, "prompt_tokens_details", None)
        cached = 0
        if details is not None:
            cached = getattr(details, "cached_tokens", 0) or 0

        agent = getattr(_local, "agent", "unknown")
        run_id = getattr(_local, "run_id", None)
        record_api_call(
            agent=agent,
            model=model,
            input_tokens=max(prompt_tokens - cached, 0),
            output_tokens=completion_tokens,
            run_id=run_id,
            cache_read_tokens=cached,
            cache_write_tokens=0,
            latency_ms=latency_ms,
        )
    except Exception:
        pass  # cost tracking 失敗不影響主流程
