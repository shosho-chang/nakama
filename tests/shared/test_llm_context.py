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
import time

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


# ── 4) ADR-070 D9：spawn_thread / submit ──────────────────────────────


def test_plain_thread_loses_agent_but_spawn_thread_keeps_it():
    """PR #1298 事故的對照：threading.Thread 不繼承 ContextVar，spawn_thread 會。"""
    from shared.llm_context import spawn_thread

    set_current_agent("nami", run_id=3)
    seen: dict[str, object] = {}

    plain = threading.Thread(target=lambda: seen.setdefault("plain", get_current_agent()))
    plain.start()
    plain.join()

    def _probe(tag, *, suffix):
        seen[tag] = (get_current_agent(), get_current_run_id(), suffix)

    t = spawn_thread(_probe, "spawned", suffix="!", name="probe-thread")
    t.join()
    assert seen["plain"] is None
    assert seen["spawned"] == ("nami", 3, "!")
    assert t.name == "probe-thread" and t.daemon is True


def test_spawn_thread_changes_do_not_leak_back():
    from shared.llm_context import spawn_thread

    set_current_agent("nami")
    t = spawn_thread(set_current_agent, "franky")
    t.join()
    assert get_current_agent() == "nami"


def test_submit_copies_context_per_call_so_concurrent_workers_do_not_collide():
    """每次 submit 各自 copy_context；共用一份 context 會 RuntimeError: cannot enter context。"""
    import contextvars
    from concurrent.futures import ThreadPoolExecutor

    from shared.llm_context import submit

    set_current_agent("robin")
    gate = threading.Barrier(2, timeout=10)

    def _work(i):
        gate.wait()  # 兩個 worker 同時在 context 裡
        return i, get_current_agent()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [submit(pool, _work, i) for i in range(2)]
        assert sorted(f.result(timeout=10) for f in futures) == [(0, "robin"), (1, "robin")]

    # 對照：同一份 context 給兩個 worker 同時 run → RuntimeError
    shared_ctx = contextvars.copy_context()
    gate2 = threading.Barrier(2, timeout=10)

    def _hold():
        gate2.wait()
        time.sleep(0.2)

    def _enter_same():
        gate2.wait()
        time.sleep(0.05)
        return shared_ctx.run(get_current_agent)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(shared_ctx.run, _hold)
        second = pool.submit(_enter_same)
        with pytest.raises(RuntimeError, match="cannot enter context"):
            second.result(timeout=10)
        first.result(timeout=10)


# ── 5) ADR-070：runtime group ─────────────────────────────────────────


def test_runtime_group_defaults_to_desktop_and_validates():
    from shared.llm_context import RUNTIME_GROUPS, get_runtime_group, set_runtime_group

    assert RUNTIME_GROUPS == {"gateway", "cron", "bridge", "desktop"}
    assert get_runtime_group() == "desktop"
    set_runtime_group("cron")
    assert get_runtime_group() == "cron"
    with pytest.raises(ValueError):
        set_runtime_group("vps")
    assert get_runtime_group() == "cron"


def test_runtime_group_is_process_wide_not_per_context():
    """FastAPI lifespan 裡設的值要被所有 request（別的 context / thread）看到。"""
    from shared.llm_context import get_runtime_group, set_runtime_group

    async def _set_in_task():
        set_runtime_group("bridge")

    asyncio.run(_set_in_task())
    seen = {}
    t = threading.Thread(target=lambda: seen.setdefault("g", get_runtime_group()))
    t.start()
    t.join()
    assert get_runtime_group() == "bridge"
    assert seen["g"] == "bridge"


@pytest.mark.parametrize(
    ("path", "group"),
    [
        ("gateway/__main__.py", "gateway"),
        ("agents/robin/__main__.py", "cron"),
        ("agents/zoro/__main__.py", "cron"),
        ("agents/franky/__main__.py", "cron"),
        ("agents/usopp/__main__.py", "cron"),
        ("shared/memory_reflection.py", "cron"),
        ("thousand_sunny/app.py", "bridge"),
    ],
)
def test_process_entry_points_declare_runtime_group(path, group):
    """入口宣告鎖住：S1a–d 依這些值分批切 L1，少設一個就會默默留在 desktop。"""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / path).read_text(encoding="utf-8")
    assert f'set_runtime_group("{group}")' in src
