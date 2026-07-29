"""LLM 跨 provider context — agent 屬性 + opt-in usage tracking buffer。

被 :mod:`shared.anthropic_client` / :mod:`shared.gemini_client` /
:mod:`shared.xai_client` 共用，讓 cost tracking 跨 provider 一致：
``BaseAgent.execute()`` 呼叫 :func:`set_current_agent` 設定當前 agent，
所有 provider client 的 usage recording 自動吃到同一個標記。

歷史：步驟 2-4 期間 context 由 ``shared.anthropic_client`` 擁有，其他
provider client 從那邊 import；2026-04-27 抽出獨立 module，移除 silent
cross-module coupling，三個 provider client 一視同仁從這裡讀。

2026-07-29 起底層由 ``threading.local`` 改為 :mod:`contextvars`（Nami
Agent SDK 遷移 S2 前置）：純執行緒場景語義不變（每條 thread 各自獨立、
未設定時讀到 ``None``），但 asyncio task 與 ``asyncio.to_thread`` 會正確
繼承 context —— tool handler 搬進 event loop 後 cost tracking 歸屬不再斷裂
（此前 ``shared.multimodal_arbiter`` 已為這個限制付過 workaround 成本）。
讀取端一律走本模組的 ``get_*`` accessor，不再暴露底層 storage。
"""

from __future__ import annotations

from contextvars import ContextVar

_agent: ContextVar[str | None] = ContextVar("llm_agent", default=None)
_run_id: ContextVar[int | None] = ContextVar("llm_run_id", default=None)
_usage_buffer: ContextVar[list[dict] | None] = ContextVar("llm_usage_buffer", default=None)
_scope_json: ContextVar[str | None] = ContextVar("llm_scope_json", default=None)


def set_current_agent(agent: str, run_id: int | None = None) -> None:
    """設定當前執行的 agent 名稱與 run_id，供 cost tracking 使用。

    在 :meth:`agents.base.BaseAgent.execute` 開始時呼叫。
    """
    _agent.set(agent)
    _run_id.set(run_id)


def get_current_agent() -> str | None:
    """當前 context 的 agent 名稱；未設定時 ``None``。"""
    return _agent.get()


def get_current_run_id() -> int | None:
    """當前 context 的 run_id；未設定時 ``None``。"""
    return _run_id.get()


def clear_current_agent() -> None:
    """清除 agent 標記（測試隔離、以及重用 thread/context 時的 hygiene）。"""
    _agent.set(None)
    _run_id.set(None)


def start_usage_tracking() -> None:
    """Opt-in：開始累積本 context 的 LLM usage（給 skill / one-off script 算單次成本用）。

    啟用後，每次 LLM call 會把 ``{model, input_tokens, output_tokens, cache_*}``
    append 到 buffer。呼叫 :func:`stop_usage_tracking` 取出並停止累積。
    對未啟用的 context 不影響（buffer 為 ``None`` 時 record 端 no-op）。
    """
    _usage_buffer.set([])


def stop_usage_tracking() -> list[dict]:
    """停止累積並回傳累計 usage 列表。Idempotent — 沒啟用過時回傳空 list。"""
    buf = _usage_buffer.get()
    _usage_buffer.set(None)
    return list(buf) if buf else []


def get_usage_buffer() -> list[dict] | None:
    """回傳可變的 usage buffer（record 端直接 append）；未啟用時 ``None``。"""
    return _usage_buffer.get()


def get_scope_json() -> str | None:
    """當前 context 的 per-call audit scope（ADR-030 #700）；未設定時 ``None``。"""
    return _scope_json.get()


def set_scope_json(scope_json: str | None) -> None:
    """設定（或以 ``None`` 清除）per-call audit scope。

    呼叫端沿用 save-prior / restore 模式：``prior = get_scope_json()`` →
    ``set_scope_json(new)`` → finally ``set_scope_json(prior)``。
    """
    _scope_json.set(scope_json)
