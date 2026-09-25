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

ADR-070 D9（2026-09-25）：

- :func:`spawn_thread` / :func:`submit` — 開 thread 或丟 executor 時，**每次**都重新
  ``contextvars.copy_context()``，讓 agent 標記跟著走。``threading.Thread`` 本身不繼承
  ContextVar（PR #1298 事故：記憶抽取 thread 掉回 ``agent=None``）；而同一個 Context
  物件被兩條 thread 同時 ``run`` 會丟 ``RuntimeError: cannot enter context``，所以不能
  共用一份。
- **runtime group**（:func:`set_runtime_group` / :func:`get_runtime_group`）— 這個 process
  屬於哪一類執行環境（``gateway`` / ``cron`` / ``bridge`` / ``desktop``）。在 process 入口
  設一次，是 process 層級的全域值（不是 ContextVar：FastAPI lifespan 裡設的值不會流進
  request 的 context）。ADR-070 S1a–d 依這個值分批把 Claude 呼叫切到 L1；S1 只設值、
  不改任何行為。
"""

from __future__ import annotations

import contextvars
import threading
from collections.abc import Callable
from concurrent.futures import Executor, Future
from contextvars import ContextVar
from typing import Any, TypeVar

_T = TypeVar("_T")

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


# ── ADR-070 D9：帶 context 開 thread / 丟 executor ────────────────────────


def spawn_thread(
    target: Callable[..., Any],
    *args: Any,
    name: str | None = None,
    daemon: bool = True,
    **kwargs: Any,
) -> threading.Thread:
    """開一條 thread 跑 ``target(*args, **kwargs)``，並帶著呼叫端當下的 context。

    每次呼叫都重新 ``copy_context()``：agent / run_id / usage buffer / scope 都跟著走，
    thread 裡改的 ContextVar 不會回流到呼叫端。回傳**已啟動**的 thread。

    ``name`` / ``daemon`` 是給 ``threading.Thread`` 的，不會傳給 ``target``；
    預設 ``daemon=True``（背景工作，跟既有記憶抽取 thread 一致）。
    """
    ctx = contextvars.copy_context()
    thread = threading.Thread(
        target=ctx.run, args=(target, *args), kwargs=kwargs, name=name, daemon=daemon
    )
    thread.start()
    return thread


def submit(executor: Executor, fn: Callable[..., _T], *args: Any, **kwargs: Any) -> Future[_T]:
    """``executor.submit`` 的帶 context 版：每一次 submit 各自 ``copy_context()``。

    不能把一份 context 給多個 submit 共用 —— 兩個 worker 同時 ``ctx.run`` 同一個
    Context 會丟 ``RuntimeError: cannot enter context``。
    """
    ctx = contextvars.copy_context()
    return executor.submit(ctx.run, fn, *args, **kwargs)


# ── ADR-070：runtime group（process 屬於哪一類執行環境）──────────────────

RUNTIME_GROUPS: frozenset[str] = frozenset({"gateway", "cron", "bridge", "desktop"})
DEFAULT_RUNTIME_GROUP = "desktop"

_runtime_group: str = DEFAULT_RUNTIME_GROUP


def set_runtime_group(name: str) -> None:
    """在 process 入口宣告這個 process 的執行環境類別。

    - ``gateway``：``python -m gateway``（Slack gateway）
    - ``cron``：VPS cron 叫起來的 ``agents.*`` 與 ``shared.memory_reflection``
    - ``bridge``：Thousand Sunny（FastAPI）
    - ``desktop``：其他腳本（預設值）

    S1 只記錄，不影響行為；S1a–d 由 ``shared.llm.L1_CUTOVER_GROUPS`` 決定哪些 group
    的 Claude 呼叫改走 L1。
    """
    global _runtime_group
    if name not in RUNTIME_GROUPS:
        raise ValueError(f"未知的 runtime group '{name}'；必須是 {sorted(RUNTIME_GROUPS)} 之一")
    _runtime_group = name


def get_runtime_group() -> str:
    """這個 process 的執行環境類別；沒設過時是 ``desktop``。"""
    return _runtime_group
