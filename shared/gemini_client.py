"""Google Gemini API wrapper：支援文字、音訊輸入 + JSON schema 結構化輸出。

用法：
- :func:`ask_gemini` — 純文字（給 facade / Robin ingest 用）
- :func:`ask_gemini_multi` — 多回合文字
- :func:`ask_gemini_audio` — 音訊（transcriber 多模態仲裁用）

依賴 ``google-genai>=1.73``（lazy import — 沒裝時不影響其他模組）。

Thread-local context 與 cost-tracking 入口統一在 :mod:`shared.llm_context` /
:mod:`shared.llm_observability`，本檔只關心 Gemini-specific 的 request
building、response parsing、token 抽取（thinking token 算入 output 等）。

``set_current_agent`` 仍 re-export，:mod:`shared.transcriber` 在用。
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from shared.llm_context import _local, set_current_agent  # re-export for existing callers
from shared.llm_observability import record_call
from shared.log import get_logger
from shared.retry import with_retry

if TYPE_CHECKING:
    from pydantic import BaseModel

logger = get_logger("nakama.gemini_client")

_client: Any | None = None

__all__ = [
    "ask_gemini",
    "ask_gemini_multi",
    "ask_gemini_audio",
    "get_client",
    "set_current_agent",  # 仍 re-export，multimodal_arbiter 在用
]


def _audio_mime_type(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".flac": "audio/flac",
        ".ogg": "audio/ogg",
    }.get(suffix, "audio/wav")


def get_client() -> Any:
    """取得或建立 google-genai client（singleton）。"""
    global _client
    if _client is None:
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError("缺少 GEMINI_API_KEY（或 GOOGLE_API_KEY）環境變數。請在 .env 設定。")
        try:
            from google import genai
        except ImportError as e:
            raise RuntimeError(
                "未安裝 google-genai。請執行：pip install 'google-genai>=1.73'"
            ) from e
        _client = genai.Client(api_key=api_key)
    return _client


def _get_retryable_exceptions() -> tuple[type[Exception], ...]:
    """組出 google-genai 的可重試例外清單（lazy import）。

    只白名單 5xx (``ServerError``) — 不含 base ``APIError``，否則會把
    4xx ``ClientError``（壞 API key、400 bad request）也重試，浪費時間。
    照 :mod:`shared.retry` 對 anthropic 的寫法，刻意只列具體子類。
    """
    base = (TimeoutError, ConnectionError, OSError)
    try:
        from google.genai import errors as genai_errors

        return base + (genai_errors.ServerError,)
    except (ImportError, AttributeError):
        return base


def _require_gemini_model(model: str) -> None:
    """Fail fast if router resolved a non-Gemini model for a Gemini call.

    對稱 :func:`shared.anthropic_client._require_claude_model` /
    :func:`shared.xai_client._require_grok_model`。避免 google-genai SDK 對
    non-gemini ID 噴模糊錯後被 retry 包住白等。
    """
    if not model.startswith("gemini-"):
        raise ValueError(
            f"ask_gemini / ask_gemini_multi received non-Gemini model '{model}'. "
            f"Use shared.llm.ask() for cross-provider routing, "
            f"or check MODEL_<AGENT> env vars for a wrong value."
        )


def _clamp_thinking_budget(thinking_budget: int | None, max_tokens: int) -> int | None:
    """若 thinking_budget 會吃光 output quota，自動縮成 max_tokens // 4。

    Gemini 2.5 的 thinking token 計入 max_output_tokens。當 ``max_tokens=200 +
    thinking_budget=512`` 時 thinking 把整個 output quota 吃光，最終文字只回傳
    幾個字（或 finish_reason=MAX_TOKENS），成本花了卻拿不到有用輸出。

    特殊語義保留：
    - ``None`` → 不注入 ThinkingConfig，由 SDK 決定（Pro 家族是 dynamic）
    - ``<= 0`` → 明確要關 thinking（Flash 支援傳 0）
    """
    if thinking_budget is None or thinking_budget <= 0:
        return thinking_budget
    cap = max_tokens // 4
    if thinking_budget > cap:
        logger.warning(
            "thinking_budget=%d 超過 max_tokens(%d) // 4，自動縮為 %d 避免餓死 output",
            thinking_budget,
            max_tokens,
            cap,
        )
        return cap
    return thinking_budget


def _extract_system_messages(messages: list[dict], existing_system: str) -> tuple[list[dict], str]:
    """把 messages 裡 role="system" 的項目抽出來併進 system_instruction。

    Gemini SDK ``generate_content.contents`` 只吃 role in (user, model)，
    傳入 role="system" 會在 runtime 被拒絕。這個 helper 讓 caller 用共通
    messages 格式（含 system），facade 層不用處理 provider 差異。
    """
    system_parts: list[str] = []
    other: list[dict] = []
    for msg in messages:
        if msg.get("role") == "system":
            content = msg.get("content", "")
            if isinstance(content, list):
                content = "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
            text = str(content).strip()
            if text:
                system_parts.append(text)
        else:
            other.append(msg)
    if not system_parts:
        return other, existing_system
    extra = "\n\n".join(system_parts)
    merged = f"{existing_system}\n\n{extra}" if existing_system else extra
    return other, merged


def ask_gemini(
    prompt: str,
    *,
    system: str = "",
    model: str | None = None,
    max_tokens: int = 4096,
    temperature: float | None = None,
    thinking_budget: int | None = 512,
) -> str:
    """送出一次 Gemini 純文字請求，回傳文字回應。

    自動重試（最多 3 次，指數退避）並記錄 token 用量（含 thinking token）。

    ``model=None`` 時會走 :func:`shared.llm_router.get_model` 依當前 agent 解析。
    Resolved model 若不是 Gemini 系列會直接 raise。跨 provider 路由建議走
    :func:`shared.llm.ask` facade。

    Args:
        thinking_budget: thinking token 上限。Gemini 2.5 Pro output 含 thinking
            計費，dynamic 模式常吃滿 max_tokens 讓成本爆掉。預設 512 對大部分
            ingest / 摘要類任務足夠。要完全關掉 thinking 傳 0。
    """
    if model is None:
        from shared.llm_router import get_model

        model = get_model(agent=getattr(_local, "agent", None), task="default")
    _require_gemini_model(model)

    from google.genai import types

    thinking_budget = _clamp_thinking_budget(thinking_budget, max_tokens)

    config_kwargs: dict[str, Any] = {"max_output_tokens": max_tokens}
    if system:
        config_kwargs["system_instruction"] = system
    if temperature is not None:
        config_kwargs["temperature"] = temperature
    if thinking_budget is not None:
        config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=thinking_budget)

    def _call() -> Any:
        client = get_client()
        return client.models.generate_content(
            model=model,
            contents=[prompt],
            config=types.GenerateContentConfig(**config_kwargs),
        )

    start = time.perf_counter()
    response = with_retry(
        _call,
        max_attempts=3,
        backoff_base=2.0,
        retryable=_get_retryable_exceptions(),
    )
    latency_ms = int((time.perf_counter() - start) * 1000)

    _record_gemini_usage(response, model, latency_ms=latency_ms)

    text = getattr(response, "text", None)
    if not text:
        raise RuntimeError(f"Gemini 回應為空（{_describe_finish(response)}）")
    return text


def ask_gemini_multi(
    messages: list[dict],
    *,
    system: str = "",
    model: str | None = None,
    max_tokens: int = 4096,
    temperature: float | None = None,
    thinking_budget: int | None = 512,
) -> str:
    """多回合 Gemini 請求。messages 用共通格式（role: user/assistant/model）。

    Gemini SDK 自己的 ``contents`` 是 turn-based list；這裡把通用 messages
    展平成 Gemini 期望的 Content 陣列。
    """
    if model is None:
        from shared.llm_router import get_model

        model = get_model(agent=getattr(_local, "agent", None), task="default")
    _require_gemini_model(model)

    from google.genai import types

    # Gemini 不吃 role="system"；抽出來併進 system_instruction
    messages, system = _extract_system_messages(messages, system)
    thinking_budget = _clamp_thinking_budget(thinking_budget, max_tokens)

    # Gemini 的 role 是 "user" / "model"（不是 "assistant"）
    contents: list[Any] = []
    for msg in messages:
        role = msg.get("role", "user")
        if role == "assistant":
            role = "model"
        content = msg.get("content", "")
        if isinstance(content, list):
            # 若是 block list（例如 tool_result），展平成 plain text
            text_parts = [b.get("text", "") for b in content if isinstance(b, dict)]
            content = "\n".join(p for p in text_parts if p)
        contents.append(types.Content(role=role, parts=[types.Part.from_text(text=str(content))]))

    config_kwargs: dict[str, Any] = {"max_output_tokens": max_tokens}
    if system:
        config_kwargs["system_instruction"] = system
    if temperature is not None:
        config_kwargs["temperature"] = temperature
    if thinking_budget is not None:
        config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=thinking_budget)

    def _call() -> Any:
        client = get_client()
        return client.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(**config_kwargs),
        )

    start = time.perf_counter()
    response = with_retry(
        _call,
        max_attempts=3,
        backoff_base=2.0,
        retryable=_get_retryable_exceptions(),
    )
    latency_ms = int((time.perf_counter() - start) * 1000)

    _record_gemini_usage(response, model, latency_ms=latency_ms)

    text = getattr(response, "text", None)
    if not text:
        raise RuntimeError(f"Gemini 回應為空（{_describe_finish(response)}）")
    return text


def ask_gemini_audio(
    audio_path: str | Path,
    prompt: str,
    *,
    response_schema: type[BaseModel] | None = None,
    model: str = "gemini-2.5-pro",
    system: str = "",
    temperature: float = 0.2,
    max_output_tokens: int = 8192,
    thinking_budget: int | None = 512,
) -> Any:
    """對音訊檔送一次 Gemini 請求。

    Args:
        audio_path: 音檔路徑（≤20MB，建議 16kHz mono WAV）
        prompt: 使用者 prompt（描述要 Gemini 做什麼）
        response_schema: Pydantic BaseModel 子類；有則回傳 parsed 實例，沒則回傳純文字
        model: 模型名（預設 gemini-2.5-pro）
        system: system instruction
        temperature: 溫度（預設 0.2，仲裁需要穩定）
        max_output_tokens: 最大輸出 token
        thinking_budget: thinking token 上限（預設 512；None = SDK 預設 dynamic）。
            Gemini 2.5 Pro output $10/M 含 thinking，dynamic 模式常吃滿 max_output_tokens；
            仲裁這類「聽 clip 選 candidate」的封閉推理任務，512 足夠且省 5-10x 成本。

    Returns:
        若 response_schema 有給 → BaseModel 實例
        否則 → str（純文字回應）

    Raises:
        RuntimeError: 缺 API key、google-genai 未安裝、或回應空
        FileNotFoundError: 音檔不存在
    """
    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(f"音檔不存在：{audio_path}")

    audio_bytes = audio_path.read_bytes()
    mime_type = _audio_mime_type(audio_path)

    from google.genai import types

    thinking_budget = _clamp_thinking_budget(thinking_budget, max_output_tokens)

    config_kwargs: dict[str, Any] = {
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
    }
    if system:
        config_kwargs["system_instruction"] = system
    if response_schema is not None:
        config_kwargs["response_mime_type"] = "application/json"
        config_kwargs["response_schema"] = response_schema
    if thinking_budget is not None:
        config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=thinking_budget)

    def _call() -> Any:
        client = get_client()
        return client.models.generate_content(
            model=model,
            contents=[
                prompt,
                types.Part.from_bytes(data=audio_bytes, mime_type=mime_type),
            ],
            config=types.GenerateContentConfig(**config_kwargs),
        )

    start = time.perf_counter()
    response = with_retry(
        _call,
        max_attempts=3,
        backoff_base=2.0,
        retryable=_get_retryable_exceptions(),
    )
    latency_ms = int((time.perf_counter() - start) * 1000)

    _record_gemini_usage(response, model, latency_ms=latency_ms)

    if response_schema is not None:
        parsed = getattr(response, "parsed", None)
        if parsed is not None:
            return parsed
        text = getattr(response, "text", None)
        if text is None:
            diag = _describe_finish(response)
            raise RuntimeError(f"Gemini 回應沒有 parsed 也沒有 text（{diag}）")
        return response_schema.model_validate_json(text)

    text = getattr(response, "text", None)
    if not text:
        raise RuntimeError(f"Gemini 回應為空（{_describe_finish(response)}）")
    return text


def _describe_finish(response: Any) -> str:
    """把 response 的 finish_reason / token 用量濃縮成一行診斷訊息。"""
    try:
        cand = (response.candidates or [None])[0]
        finish = getattr(cand, "finish_reason", None)
        usage = getattr(response, "usage_metadata", None)
        thought = getattr(usage, "thoughts_token_count", None) if usage else None
        out = getattr(usage, "candidates_token_count", None) if usage else None
        total = getattr(usage, "total_token_count", None) if usage else None
        return f"finish={finish}, thoughts={thought}, output={out}, total={total}"
    except Exception as e:
        return f"diagnostic 失敗: {e}"


def _record_gemini_usage(response: Any, model: str, *, latency_ms: int = 0) -> None:
    """把 Gemini-shape usage 抽成共通欄位後 delegate 給 observability.record_call。

    Reasoning model（Gemini 2.5 Pro）的 thinking token 也是 output 計費，必須併入
    output_tokens 否則 cost tracking 會少算大半（實測 thinking 常為 candidates 的 2-5 倍）。

    Gemini 的 ``prompt_token_count`` 已經是「扣掉 cache 的」實際計費 input（與 xAI
    相反 — xAI 的 prompt_tokens 含 cached），所以這裡不需要再做減法。
    ``cached_content_token_count`` 單獨記錄到 cache_read_tokens 供 Bridge 觀測。
    Gemini 沒有 cache_write 計費（cache 要另外走 Context Caching API 建立，
    那才有寫入成本；這層 implicit cache 是 free write），固定填 0。

    Cost tracking 不影響主流程：response 形狀異常（測試 stub 沒帶 usage_metadata
    等）或下游 record_call 出錯都吞掉，只記 debug log。
    """
    try:
        usage = getattr(response, "usage_metadata", None)
        if usage is None:
            return
        input_tokens = getattr(usage, "prompt_token_count", 0) or 0
        candidates_tokens = getattr(usage, "candidates_token_count", 0) or 0
        thoughts_tokens = getattr(usage, "thoughts_token_count", 0) or 0
        cached_tokens = getattr(usage, "cached_content_token_count", 0) or 0
        output_tokens = candidates_tokens + thoughts_tokens

        record_call(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cached_tokens,
            cache_write_tokens=0,
            latency_ms=latency_ms,
        )
    except Exception as e:
        logger.debug("cost tracking 失敗（忽略）：%s", e)
