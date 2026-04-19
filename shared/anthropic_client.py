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


def call_claude_with_tools(
    messages: list[dict],
    tools: list[dict],
    *,
    system: str = "",
    model: str = "claude-haiku-4-5",
    max_tokens: int = 2048,
) -> "anthropic.types.Message":
    """呼叫 Claude 的 tool-use API，回傳完整 Message 物件（含 stop_reason、content blocks）。

    與 ask_claude_multi() 不同，這個函式 **不** 把 response 擷取為字串——呼叫端需要
    inspect `stop_reason`（"end_turn" / "tool_use"）以及 content blocks 才能驅動
    agent loop。

    Prompt caching：會在 system prompt 的最後一個 block 加 `cache_control`，讓
    tools + system 整段被 cache。呼叫端請確保 `system` 與 `tools` 是確定性的
    （不要含 `datetime.now()`、UUID 等每次變化的內容），否則 cache 不會命中。

    Args:
        messages: Claude API messages 格式（alternate user/assistant）
        tools: Tool definitions（JSON schema 陣列）
        system: System prompt
        model: 模型（預設 Haiku 4.5，tool-routing 任務通常夠用）
        max_tokens: 最大輸出 token 數

    Returns:
        anthropic.types.Message（含 content + stop_reason + usage）
    """

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
        return client.messages.create(**kwargs)

    response = with_retry(_call, max_attempts=3, backoff_base=2.0)

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
        pass

    return response


def ask_claude_multi(
    messages: list[dict],
    *,
    system: str = "",
    model: str = "claude-sonnet-4-20250514",
    max_tokens: int = 4096,
    temperature: float | None = None,
) -> str:
    """送出多回合 Claude API 請求，回傳純文字回應。

    與 ask_claude() 相同的 retry / cost tracking 機制，
    但接受完整 messages 陣列以支援多回合對話。
    Claude 4.7 以後的模型已廢除 temperature，預設不送。

    Args:
        messages: Claude API messages 格式，
                  例如 [{"role": "user", "content": "..."}, ...]
        system:   系統 prompt
        model:    模型名稱
        max_tokens: 最大回應 token 數
        temperature: 溫度（None 表示不送）

    Returns:
        assistant 回應的純文字
    """

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
