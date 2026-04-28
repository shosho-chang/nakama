"""Retry 機制：自動重試失敗的 API 呼叫。

用法：
    from shared.retry import with_retry

    result = with_retry(ask, prompt="...", system="...")

    # 或直接用在任何 callable
    result = with_retry(
        my_func,
        arg1, arg2,
        max_attempts=3,
        backoff_base=2.0,
    )
"""

import time
from collections.abc import Callable
from typing import Any, TypeVar

from shared.log import get_logger

logger = get_logger("nakama.retry")

T = TypeVar("T")

# 預設視為可重試的例外類型
_RETRYABLE_EXCEPTIONS = (
    TimeoutError,
    ConnectionError,
    OSError,
)

# 嘗試匯入 anthropic 的例外（若未安裝則略過）
try:
    import anthropic

    _RETRYABLE_EXCEPTIONS = _RETRYABLE_EXCEPTIONS + (
        anthropic.APITimeoutError,
        anthropic.APIConnectionError,
        anthropic.InternalServerError,
        anthropic.RateLimitError,
    )
except ImportError:
    pass


def with_retry(
    func: Callable[..., T],
    *args: Any,
    max_attempts: int = 3,
    backoff_base: float = 2.0,
    retryable: tuple[type[Exception], ...] | None = None,
    **kwargs: Any,
) -> T:
    """執行 func，失敗時自動重試（指數退避）。

    Args:
        func:          要執行的函數
        *args:         傳給 func 的位置參數
        max_attempts:  最多嘗試次數（預設 3）
        backoff_base:  退避底數，第 n 次等待 backoff_base^(n-1) 秒（預設 2.0）
        retryable:     可重試的例外類型，None 使用預設清單
        **kwargs:      傳給 func 的關鍵字參數

    Returns:
        func 的回傳值

    Raises:
        最後一次嘗試的例外（若全部失敗）
    """
    retryable_exc = retryable or _RETRYABLE_EXCEPTIONS
    last_exc: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            return func(*args, **kwargs)
        except retryable_exc as e:
            last_exc = e
            if attempt == max_attempts:
                logger.error(f"已達最大重試次數 ({max_attempts})，放棄：{e}")
                break
            wait = backoff_base ** (attempt - 1)
            logger.warning(f"第 {attempt} 次失敗（{type(e).__name__}: {e}），{wait:.1f}s 後重試")
            time.sleep(wait)
        except Exception:
            # 非可重試例外，直接往上拋
            raise

    raise last_exc
