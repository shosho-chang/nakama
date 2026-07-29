"""OpenRouter transport — 單一 OpenAI-compatible client（BYOK），消化既有
OpenAI / Anthropic credit，並記錄 OpenRouter 回報的『實際』cost（usage accounting）。

為什麼走 OpenAI SDK：OpenRouter 是 OpenAI-compatible endpoint
（``https://openrouter.ai/api/v1``），response shape 對齊既有 cost-tracking 入口；
整支結構照 :mod:`shared.xai_client`。本檔只關心 OpenRouter-specific 的 request
building（slug 翻譯、usage accounting 開關、BYOK fallback 安全旗標）+ response
parsing（實際 cost 抽取）。

跨 provider routing 與 facade 接線是 Slice 2 —— 本檔目前不被任何 call site 直接
import。兩個 carve-out（facade 在 seam 判斷，不在這裡）：

- ADR-026：Anthropic 訂閱（Max Plan）呼叫永遠走原生 ``claude_cli_client``。
- xAI ``grok-*``：OpenRouter 不載這個 tier，留原生 ``xai_client``（slug 翻譯 raise）。

Slice 1 邊界：cost 先『解析 + debug-log』；落庫（state.api_calls 新 ``cost_usd``
欄位）是 Slice 3。``thinking_budget`` / reasoning 參數的映射留給 Slice 2 facade。
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

from shared.llm_context import get_current_agent
from shared.llm_observability import record_call
from shared.log import get_logger
from shared.openrouter_models import to_openrouter_slug
from shared.pricing import calc_cost
from shared.retry import with_retry

logger = get_logger("nakama.openrouter_client")

_BASE_URL = "https://openrouter.ai/api/v1"

_client: OpenAI | None = None

# openai SDK 的可重試例外（OpenRouter 同 endpoint 行為一致，對齊 xai_client）。
_OPENROUTER_RETRYABLE = (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)


def get_client() -> OpenAI:
    """取得或建立 OpenRouter client（singleton），帶來源歸屬 headers。"""
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=os.environ["OPENROUTER_API_KEY"],
            base_url=_BASE_URL,
            default_headers={
                # OpenRouter 用這兩個 header 做來源歸屬（後台 / leaderboard）。
                "HTTP-Referer": os.environ.get("OPENROUTER_REFERER", "https://nakama.shosho.tw"),
                "X-Title": os.environ.get("OPENROUTER_TITLE", "Nakama"),
            },
        )
    return _client


def _resolve_model(model: str | None) -> str:
    """``model=None`` 時依當前 agent 走 router 解析（對齊 ask_grok）。回傳 bare ID。"""
    if model is not None:
        return model
    from shared.llm_router import get_model

    return get_model(agent=get_current_agent(), task="default")


def _build_extra_body(
    *,
    models: list[str] | None,
    provider: dict | None,
    allow_fallbacks: bool,
) -> dict:
    """組 OpenRouter 專屬的 ``extra_body``。

    - ``usage.include=True``：要 OpenRouter 回報實際 cost（不靠 prefix 估算）。
    - ``models``：model 層 fallback 鏈（primary 失敗時依序試；bare ID 逐一翻 slug）。
    - ``provider``：品質 / 路由控制（``quantizations`` / ``sort`` 等）。
    - ``provider.allow_fallbacks``：BYOK 安全 —— 預設 ``False``，避免自帶 key 失敗時
      OpenRouter 偷用 OR credit 繞道別家 provider（前置作業 §4.5）。
    """
    extra: dict = {"usage": {"include": True}}
    prov: dict = dict(provider) if provider else {}
    prov.setdefault("allow_fallbacks", allow_fallbacks)
    extra["provider"] = prov
    if models:
        extra["models"] = [to_openrouter_slug(m) for m in models]
    return extra


def _dispatch(
    slug: str,
    messages: list[dict],
    max_tokens: int,
    temperature: float | None,
    models: list[str] | None,
    provider: dict | None,
    allow_fallbacks: bool,
):
    """共用的請求路徑：組 kwargs + extra_body，with_retry 執行，回 (response, latency_ms)。"""
    extra_body = _build_extra_body(
        models=models, provider=provider, allow_fallbacks=allow_fallbacks
    )

    def _call():
        client = get_client()
        kwargs: dict = {
            "model": slug,
            "max_tokens": max_tokens,
            "messages": messages,
            "extra_body": extra_body,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        return client.chat.completions.create(**kwargs)

    start = time.perf_counter()
    response = with_retry(_call, max_attempts=3, backoff_base=2.0, retryable=_OPENROUTER_RETRYABLE)
    latency_ms = int((time.perf_counter() - start) * 1000)
    return response, latency_ms


def ask_openrouter(
    prompt: str,
    *,
    system: str = "",
    model: str | None = None,
    max_tokens: int = 4096,
    temperature: float | None = None,
    models: list[str] | None = None,
    provider: dict | None = None,
    allow_fallbacks: bool = False,
    auth_requested: str | None = None,
    auth_actual: str | None = None,
    fallback_reason: str | None = None,
) -> str:
    """送一次 OpenRouter 請求，回傳純文字。

    ``model`` 是 Nakama bare ID（如 ``claude-sonnet-4-6``），內部翻成 OpenRouter
    slug；``model=None`` 時依當前 agent 走 router 解析。``models``（fallback 鏈）
    也用 bare ID 傳入。非可翻譯的 model（如 ``grok-*`` / 未登錄）在送網路前 raise。
    """
    bare = _resolve_model(model)
    slug = to_openrouter_slug(bare)

    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    response, latency_ms = _dispatch(
        slug, messages, max_tokens, temperature, models, provider, allow_fallbacks
    )
    _record_openrouter_usage(
        bare,
        response,
        latency_ms=latency_ms,
        auth_requested=auth_requested,
        auth_actual=auth_actual,
        fallback_reason=fallback_reason,
    )
    return response.choices[0].message.content or ""


def ask_openrouter_multi(
    messages: list[dict],
    *,
    system: str = "",
    model: str | None = None,
    max_tokens: int = 4096,
    temperature: float | None = None,
    models: list[str] | None = None,
    provider: dict | None = None,
    allow_fallbacks: bool = False,
    auth_requested: str | None = None,
    auth_actual: str | None = None,
    fallback_reason: str | None = None,
) -> str:
    """多回合版本。messages 用 OpenAI 格式（role: user/assistant）；``system`` 自動 prepend。"""
    bare = _resolve_model(model)
    slug = to_openrouter_slug(bare)

    full_messages: list[dict] = []
    if system:
        full_messages.append({"role": "system", "content": system})
    full_messages.extend(messages)

    response, latency_ms = _dispatch(
        slug, full_messages, max_tokens, temperature, models, provider, allow_fallbacks
    )
    _record_openrouter_usage(
        bare,
        response,
        latency_ms=latency_ms,
        auth_requested=auth_requested,
        auth_actual=auth_actual,
        fallback_reason=fallback_reason,
    )
    return response.choices[0].message.content or ""


def _extract_cost(
    model: str,
    usage,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
) -> tuple[float, str]:
    """回傳 ``(cost_usd, source)``，``source`` ∈ {``"openrouter"``, ``"fallback"``}。

    OpenRouter 在 ``usage.include=True`` 時回報 ``usage.cost``（BYOK 後的『實際』
    花費）—— 這正是當初排斥 LiteLLM 的痛點（cache 計費估算不準）的解法。取不到時
    fallback 到 ``pricing.calc_cost`` 的 prefix 估算並標 ``"fallback"`` 供觀察。

    相容兩種形狀：測試的 ``SimpleNamespace``（有 ``.cost``）與真實 OpenAI SDK 的
    pydantic usage（額外欄位落在 ``model_extra``）。
    """
    cost = getattr(usage, "cost", None) if usage is not None else None
    if cost is None and usage is not None:
        extra = getattr(usage, "model_extra", None)
        if isinstance(extra, dict):
            cost = extra.get("cost")
    if cost is not None:
        try:
            return float(cost), "openrouter"
        except (TypeError, ValueError):
            pass
    fallback = calc_cost(
        model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
    )
    return fallback, "fallback"


def _record_openrouter_usage(
    model: str,
    response,
    *,
    latency_ms: int = 0,
    auth_requested: str | None = None,
    auth_actual: str | None = None,
    fallback_reason: str | None = None,
) -> None:
    """把 OpenAI-shape usage（含 OpenRouter 實際 cost）抽成共通欄位後記錄。

    用 **bare model ID**（非 slug）記錄，讓 observability / cost panel 與系統其餘部分
    的命名一致。token 切分對齊 :func:`shared.xai_client._record_xai_usage`
    （``input = prompt_tokens - cached``，避免重複計費）。

    Slice 1 先解析實際 cost 並 debug-log；落庫（``state.api_calls`` 新 ``cost_usd``
    欄位 + migration）是 Slice 3。Cost tracking 不影響主流程：response 形狀異常
    （測試 stub 沒帶 usage 等）或下游 record_call 出錯全吞，只記 debug log。
    """
    try:
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        details = getattr(usage, "prompt_tokens_details", None)
        cached = (getattr(details, "cached_tokens", 0) or 0) if details is not None else 0
        input_tokens = max(prompt_tokens - cached, 0)

        cost_usd, source = _extract_cost(
            model,
            usage,
            input_tokens=input_tokens,
            output_tokens=completion_tokens,
            cache_read_tokens=cached,
            cache_write_tokens=0,
        )
        logger.debug(
            "OpenRouter usage model=%s in=%d out=%d cached=%d cost=$%.6f (%s)",
            model,
            input_tokens,
            completion_tokens,
            cached,
            cost_usd,
            source,
        )

        record_call(
            model=model,
            input_tokens=input_tokens,
            output_tokens=completion_tokens,
            cache_read_tokens=cached,
            cache_write_tokens=0,
            latency_ms=latency_ms,
            auth_requested=auth_requested,
            auth_actual=auth_actual,
            fallback_reason=fallback_reason,
            # 只落庫 OpenRouter 回報的『實際』cost；取不到時留 None，讓 cost panel
            # 用 calc_cost 估算（與原生呼叫一致），不把估算值混進「實際 cost」欄位。
            cost_usd=cost_usd if source == "openrouter" else None,
        )
    except Exception as e:
        logger.debug("cost tracking 失敗（忽略）：%s", e)


__all__ = ["ask_openrouter", "ask_openrouter_multi", "get_client"]
