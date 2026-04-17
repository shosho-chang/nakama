"""Anthropic Claude API wrapper，內建 retry 與 cost tracking。"""

import os
import threading

import anthropic

from shared.retry import with_retry

_client: anthropic.Anthropic | None = None

# Thread-local 儲存當前 run_id，供 cost tracking 使用
_local = threading.local()


def get_client() -> anthropic.Anthropic:
    """取得或建立 Anthropic client（singleton）。"""
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def set_current_agent(agent: str, run_id: int | None = None) -> None:
    """設定當前執行的 agent 名稱與 run_id，供 cost tracking 使用。

    在 BaseAgent.execute() 開始時呼叫。
    """
    _local.agent = agent
    _local.run_id = run_id


def ask_claude(
    prompt: str,
    *,
    system: str = "",
    model: str = "claude-sonnet-4-20250514",
    max_tokens: int = 4096,
    temperature: float | None = None,
) -> str:
    """送出一次 Claude API 請求，回傳純文字回應。

    自動重試（最多 3 次，指數退避）並記錄 token 用量。
    Claude 4.7 以後的模型已廢除 temperature，預設不送。
    """

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

    response = with_retry(_call, max_attempts=3, backoff_base=2.0)

    # Cost tracking
    try:
        from shared.state import record_api_call

        agent = getattr(_local, "agent", "unknown")
        run_id = getattr(_local, "run_id", None)
        record_api_call(
            agent=agent,
            model=model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            run_id=run_id,
        )
    except Exception:
        pass  # cost tracking 失敗不影響主流程

    return response.content[0].text


def ask_claude_multi(
    messages: list[dict],
    *,
    system: str = "",
    model: str = "claude-sonnet-4-20250514",
    max_tokens: int = 4096,
    temperature: float = 0.3,
) -> str:
    """送出多回合 Claude API 請求，回傳純文字回應。

    與 ask_claude() 相同的 retry / cost tracking 機制，
    但接受完整 messages 陣列以支援多回合對話。

    Args:
        messages: Claude API messages 格式，
                  例如 [{"role": "user", "content": "..."}, ...]
        system:   系統 prompt
        model:    模型名稱
        max_tokens: 最大回應 token 數
        temperature: 溫度

    Returns:
        assistant 回應的純文字
    """

    def _call() -> anthropic.types.Message:
        client = get_client()
        kwargs: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        return client.messages.create(**kwargs)

    response = with_retry(_call, max_attempts=3, backoff_base=2.0)

    # Cost tracking
    try:
        from shared.state import record_api_call

        agent = getattr(_local, "agent", "unknown")
        run_id = getattr(_local, "run_id", None)
        record_api_call(
            agent=agent,
            model=model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            run_id=run_id,
        )
    except Exception:
        pass  # cost tracking 失敗不影響主流程

    return response.content[0].text
