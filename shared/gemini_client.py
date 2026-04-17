"""Google Gemini API wrapper，支援音訊輸入 + JSON schema 結構化輸出。

用於多模態仲裁：對 ASR uncertain 片段用 Gemini 2.5 Pro audio 聽音檔做仲裁。

依賴 `google-genai>=1.73`（lazy import — 沒裝時不影響其他模組）。

用法：
    from shared.gemini_client import ask_gemini_audio
    from pydantic import BaseModel

    class Arbitration(BaseModel):
        text: str
        confidence: float

    result = ask_gemini_audio(
        audio_path="clip.wav",
        prompt="這段語音最可能的逐字稿是什麼？",
        response_schema=Arbitration,
    )
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from shared.log import get_logger
from shared.retry import with_retry

if TYPE_CHECKING:
    from pydantic import BaseModel

logger = get_logger("nakama.gemini_client")

_client: Any | None = None
_local = threading.local()


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


def set_current_agent(agent: str, run_id: int | None = None) -> None:
    """設定當前執行的 agent 名稱與 run_id，供 cost tracking 使用。"""
    _local.agent = agent
    _local.run_id = run_id


def _get_retryable_exceptions() -> tuple[type[Exception], ...]:
    """組出 google-genai 的可重試例外清單（lazy import）。"""
    base = (TimeoutError, ConnectionError, OSError)
    try:
        from google.genai import errors as genai_errors

        return base + (
            genai_errors.APIError,
            genai_errors.ServerError,
        )
    except (ImportError, AttributeError):
        return base


def ask_gemini_audio(
    audio_path: str | Path,
    prompt: str,
    *,
    response_schema: type[BaseModel] | None = None,
    model: str = "gemini-2.5-pro",
    system: str = "",
    temperature: float = 0.2,
    max_output_tokens: int = 1024,
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

    config_kwargs: dict[str, Any] = {
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
    }
    if system:
        config_kwargs["system_instruction"] = system
    if response_schema is not None:
        config_kwargs["response_mime_type"] = "application/json"
        config_kwargs["response_json_schema"] = response_schema

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

    response = with_retry(
        _call,
        max_attempts=3,
        backoff_base=2.0,
        retryable=_get_retryable_exceptions(),
    )

    _record_usage(response, model)

    if response_schema is not None:
        parsed = getattr(response, "parsed", None)
        if parsed is not None:
            return parsed
        text = getattr(response, "text", None)
        if text is None:
            raise RuntimeError("Gemini 回應沒有 parsed 也沒有 text")
        return response_schema.model_validate_json(text)

    text = getattr(response, "text", None)
    if not text:
        raise RuntimeError("Gemini 回應為空")
    return text


def _record_usage(response: Any, model: str) -> None:
    """記錄 token 用量到 state.api_calls（失敗不影響主流程）。"""
    try:
        from shared.state import record_api_call

        usage = getattr(response, "usage_metadata", None)
        if usage is None:
            return
        input_tokens = getattr(usage, "prompt_token_count", 0) or 0
        output_tokens = getattr(usage, "candidates_token_count", 0) or 0

        agent = getattr(_local, "agent", "unknown")
        run_id = getattr(_local, "run_id", None)
        record_api_call(
            agent=agent,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            run_id=run_id,
        )
    except Exception as e:
        logger.debug(f"cost tracking 失敗（忽略）：{e}")
