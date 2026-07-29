"""Tests for shared/llm_context.py — contextvars 轉換後的語義保證。

三類保證：
1. 舊 threading.local 語義不變 — 每條 thread 各自獨立、未設定讀 None
2. 新能力 — asyncio task 與 asyncio.to_thread 正確繼承 context
   （Nami Agent SDK S2 的 cost tracking 命脈；threading.local 下必炸）
3. usage buffer / scope_json 的 opt-in 與 save-restore 契約
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from shared.llm_context import (
    clear_current_agent,
    get_current_agent,
    get_current_run_id,
    get_scope_json,
    get_usage_buffer,
    set_current_agent,
    set_scope_json,
    start_usage_tracking,
    stop_usage_tracking,
)


@pytest.fixture(autouse=True)
def _clean_llm_context():
    """本檔的測試會 set agent/buffer/scope — 前後都清乾淨，不漏給別的測試。"""
    clear_current_agent()
    stop_usage_tracking()
    set_scope_json(None)
    yield
    clear_current_agent()
    stop_usage_tracking()
    set_scope_json(None)


# ── 1) threading.local 等價語義 ────────────────────────────────────────


def test_unset_reads_none_in_fresh_thread():
    """未設定的 thread 讀到 None（與 threading.local 的 getattr default 等價）。"""
    seen = {}

    def worker():
        seen["agent"] = get_current_agent()
        seen["run_id"] = get_current_run_id()
        seen["buffer"] = get_usage_buffer()

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert seen == {"agent": None, "run_id": None, "buffer": None}


def test_threads_are_isolated():
    """兩條 thread 各自 set，互不污染（threading.local 的核心語義）。"""
    results = {}
    barrier = threading.Barrier(2)

    def worker(name):
        set_current_agent(name, run_id=hash(name) % 1000)
        barrier.wait()  # 兩邊都 set 完才讀，抓 cross-thread 污染
        results[name] = get_current_agent()

    threads = [threading.Thread(target=worker, args=(n,)) for n in ("robin", "nami")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == {"robin": "robin", "nami": "nami"}


def test_set_then_get_same_thread():
    set_current_agent("zoro", run_id=42)
    assert get_current_agent() == "zoro"
    assert get_current_run_id() == 42


# ── 2) asyncio 繼承 — threading.local 下這兩個測試會 fail ─────────────


def test_asyncio_task_inherits_agent():
    """event loop 內的 task 讀得到呼叫端 set 的 agent。"""

    async def in_task():
        return get_current_agent()

    async def main():
        return await asyncio.create_task(in_task())

    set_current_agent("nami", run_id=7)
    assert asyncio.run(main()) == "nami"


def test_to_thread_inherits_agent_and_buffer():
    """asyncio.to_thread 的 worker 讀得到 context — S2 解 tool handler 阻塞的前提。

    threading.local 下 worker thread 是全新 thread、讀到 None，cost tracking
    歸屬全錯；contextvars 由 to_thread 自動複製 context，此測試是那條地雷的防線。
    """

    def blocking_tool():
        buf = get_usage_buffer()
        if buf is not None:
            buf.append({"model": "probe", "input_tokens": 1, "output_tokens": 2})
        return get_current_agent()

    async def main():
        return await asyncio.to_thread(blocking_tool)

    set_current_agent("nami")
    start_usage_tracking()
    assert asyncio.run(main()) == "nami"
    usage = stop_usage_tracking()
    assert usage == [{"model": "probe", "input_tokens": 1, "output_tokens": 2}]


# ── 3) usage buffer / scope_json 契約 ─────────────────────────────────


def test_usage_tracking_opt_in_and_idempotent_stop():
    assert get_usage_buffer() is None  # 未啟用 → record 端 no-op
    start_usage_tracking()
    get_usage_buffer().append({"model": "m"})
    assert stop_usage_tracking() == [{"model": "m"}]
    assert stop_usage_tracking() == []  # idempotent
    assert get_usage_buffer() is None


def test_scope_json_save_restore_pattern():
    """digest_ask / digest_study_detail 的 prior-save / finally-restore 模式。"""
    assert get_scope_json() is None
    set_scope_json('{"surface": "outer"}')
    prior = get_scope_json()
    set_scope_json('{"surface": "inner"}')
    assert get_scope_json() == '{"surface": "inner"}'
    set_scope_json(prior)
    assert get_scope_json() == '{"surface": "outer"}'
    set_scope_json(None)
