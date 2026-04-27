"""LLM 跨 provider thread-local context — agent 屬性 + opt-in usage tracking buffer.

被 :mod:`shared.anthropic_client` / :mod:`shared.gemini_client` /
:mod:`shared.xai_client` 共用，讓 cost tracking 跨 provider 一致：
``BaseAgent.execute()`` 呼叫 :func:`set_current_agent` 設定當前 agent，
所有 provider client 的 usage recording 自動吃到同一個標記。

歷史：步驟 2-4 期間 ``_local`` 由 ``shared.anthropic_client`` 擁有，其他
provider client 從那邊 import；2026-04-27 抽出獨立 module，移除 silent
cross-module coupling，三個 provider client 一視同仁從這裡讀。
"""

from __future__ import annotations

import threading

_local = threading.local()


def set_current_agent(agent: str, run_id: int | None = None) -> None:
    """設定當前執行的 agent 名稱與 run_id，供 cost tracking 使用。

    在 :meth:`agents.base.BaseAgent.execute` 開始時呼叫。
    """
    _local.agent = agent
    _local.run_id = run_id


def start_usage_tracking() -> None:
    """Opt-in：開始累積本 thread 的 LLM usage（給 skill / one-off script 算單次成本用）。

    啟用後，每次 LLM call 會把 ``{model, input_tokens, output_tokens, cache_*}``
    append 到 buffer。呼叫 :func:`stop_usage_tracking` 取出並停止累積。
    對未啟用的 thread 不影響（buffer 為 ``None`` 時 record 端 no-op）。
    """
    _local.usage_buffer = []


def stop_usage_tracking() -> list[dict]:
    """停止累積並回傳累計 usage 列表。Idempotent — 沒啟用過時回傳空 list。"""
    buf = getattr(_local, "usage_buffer", None)
    _local.usage_buffer = None
    return list(buf) if buf else []
