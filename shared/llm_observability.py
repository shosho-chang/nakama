"""LLM cost tracking 統一入口 — 把每次 call 的 usage 寫進 state.api_calls
+ 可選的 thread-local buffer。

各 provider client 在抽完 token 數後 call :func:`record_call`，不要再各自
``try: from shared.state import record_api_call`` 重複那段樣板。

設計約束：
- ``record_call`` 失敗（例如 SQLite 暫時不可寫）**不影響主流程**（保留既
  有語意，主路徑只記 debug log）。
- 不依賴任何 provider SDK — 只吃已抽好的整數 token 數，避免 leak provider
  細節到這層。
"""

from __future__ import annotations

from shared.llm_context import _local
from shared.log import get_logger

logger = get_logger("nakama.llm_observability")


def record_call(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    latency_ms: int = 0,
) -> None:
    """記錄一次 LLM call 的 usage。

    Agent / run_id 從 thread-local context 讀取（由
    :func:`shared.llm_context.set_current_agent` 設定）。沒有 agent
    context 時 fallback 到 ``"unknown"``。

    Args:
        model: model ID（``claude-sonnet-4-...``、``gemini-2.5-pro`` 等）
        input_tokens: 不含 cache_read 的實際計費 input
        output_tokens: 含 thinking / reasoning token（reasoning model 主成本）
        cache_read_tokens: 從 prompt cache 命中的 token（單獨計費）
        cache_write_tokens: 寫入 prompt cache 的 token（部分 provider 沒有
            這個概念，傳 0）
        latency_ms: end-to-end 含 retry 的延遲

    Side effects:
        - 若 thread-local ``usage_buffer`` 已啟用（opt-in tracking），append
          一筆紀錄
        - 寫一筆 row 到 ``state.api_calls``（失敗時 debug log，不 raise）
    """
    # 1) Opt-in buffer（給 skill / script 算單次成本用）
    buf = getattr(_local, "usage_buffer", None)
    if buf is not None:
        buf.append(
            {
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_tokens": cache_read_tokens,
                "cache_write_tokens": cache_write_tokens,
            }
        )

    # 2) 寫進 state.db（失敗不 raise — cost tracking 不可影響主流程）
    try:
        from shared.state import record_api_call

        agent = getattr(_local, "agent", "unknown")
        run_id = getattr(_local, "run_id", None)
        record_api_call(
            agent=agent,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            run_id=run_id,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            latency_ms=latency_ms,
        )
    except Exception as e:
        logger.debug("cost tracking 失敗（忽略）：%s", e)
