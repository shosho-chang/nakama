"""ADR-070 S1：L1 核心（``shared.agent_sdk.run_text`` 與周邊）的行為契約。

涵蓋 issue #1305 驗收清單：憑證清理、一次性預設、structured output、timeout、
機器層級 lease（含跨 process）、async 情境、巢狀呼叫、用量紀錄、context 傳遞、
錯誤分類與不可重試。

**不打網路、不呼叫 LLM**：``claude_agent_sdk.query`` 一律換成假 stream；
SDK 的 message 用真的 dataclass 建（``AssistantMessage`` / ``ResultMessage`` /
``RateLimitEvent``），``ClaudeAgentOptions`` 也是真的，所以欄位名稱錯了會直接炸。
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import anyio
import claude_agent_sdk
import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    RateLimitEvent,
    RateLimitInfo,
    ResultMessage,
    TextBlock,
)

from shared import agent_sdk, state
from shared.agent_sdk import (
    L1_BLANKED_ENV,
    L1_MACHINE_CONCURRENCY,
    AgentSdkError,
    L1LeaseTimeout,
    SubscriptionAuthError,
    SubscriptionExhausted,
    flatten_messages,
    in_l1_session,
    l1_child_env,
    l1_session,
    run_text,
)
from shared.llm_context import clear_current_agent, set_current_agent
from shared.retry import with_retry

_REPO = Path(__file__).resolve().parents[2]
_TOKEN = "sk-ant-oat01-test-token"


# ── fixtures / helpers ──────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _l1_env(monkeypatch):
    """每個測試都有一把假 OAuth token，NAMI 舊 token 與 legacy log 旗標歸零。"""
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", _TOKEN)
    monkeypatch.delenv("NAMI_SDK_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr(agent_sdk, "_legacy_token_logged", False)
    clear_current_agent()
    yield
    clear_current_agent()


class FakeSdk:
    """取代 ``claude_agent_sdk.query``：記下每次的 prompt / options，吐固定的 message。"""

    def __init__(self, messages=(), *, raise_after=None, hang_s=None):
        self.messages = list(messages)
        self.raise_after = raise_after
        self.hang_s = hang_s
        self.calls: list[tuple[str, ClaudeAgentOptions]] = []
        self.threads: list[str] = []
        self.closed = 0
        self.session_flags: list[bool] = []
        self.active_leases: list[int] = []

    def query(self, *, prompt, options):
        self.calls.append((prompt, options))
        self.threads.append(threading.current_thread().name)
        self.session_flags.append(in_l1_session())
        self.active_leases.append(state.count_active_l1_leases())

        async def _gen():
            try:
                for m in self.messages:
                    yield m
                if self.hang_s is not None:
                    await anyio.sleep(self.hang_s)
                if self.raise_after is not None:
                    raise self.raise_after
            finally:
                self.closed += 1

        return _gen()


@pytest.fixture
def fake_sdk(monkeypatch):
    def _install(*messages, **kw) -> FakeSdk:
        fake = FakeSdk(messages, **kw)
        monkeypatch.setattr(claude_agent_sdk, "query", fake.query)
        return fake

    return _install


def _rate_limit(status="allowed", rate_limit_type="five_hour", resets_at=1790000000):
    info = RateLimitInfo(
        status=status,
        resets_at=resets_at,
        rate_limit_type=rate_limit_type,
        utilization=0.5,
        overage_status="rejected",
        overage_resets_at=None,
        overage_disabled_reason="org_level_disabled",
        raw={"status": status},
    )
    return RateLimitEvent(rate_limit_info=info, uuid="u-1", session_id="sess-1")


def _assistant(model="claude-sonnet-5", error=None, text="hello"):
    return AssistantMessage(content=[TextBlock(text=text)], model=model, error=error)


def _result(result="hello", *, subtype="success", is_error=False, structured=None, usage=None):
    return ResultMessage(
        subtype=subtype,
        duration_ms=1500,
        duration_api_ms=1200,
        is_error=is_error,
        num_turns=1,
        session_id="sess-1",
        total_cost_usd=0.0009,
        usage=usage
        if usage is not None
        else {
            "input_tokens": 12,
            "output_tokens": 3,
            "cache_read_input_tokens": 100,
            "cache_creation_input_tokens": 7,
        },
        result=result,
        structured_output=structured,
    )


def _ok_stream(text="hello", model="claude-sonnet-5"):
    return (_rate_limit(), _assistant(model=model, text=text), _result(text))


def _call(**kw):
    kw.setdefault("system", "sys")
    kw.setdefault("model", "sonnet")
    kw.setdefault("timeout_s", 30)
    kw.setdefault("call_class", "batch")
    return run_text(kw.pop("prompt", "hi"), **kw)


def _api_rows() -> list[dict]:
    rows = state._get_conn().execute("SELECT * FROM api_calls ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def _insert_foreign_lease(lease_id: str, *, expires_in: float, pid: int = 999_999) -> None:
    """直接寫 DB，模擬「另一個 process（可能已經掛掉）持有的名額」。"""
    state._get_conn()  # 建 schema
    conn = sqlite3.connect(str(state.get_db_path()))
    try:
        conn.execute(
            "INSERT INTO llm_l1_leases (lease_id, pid, agent, call_class, acquired_at, expires_at)"
            " VALUES (?, ?, 'other', 'batch', '2026-09-25T00:00:00+00:00', ?)",
            (lease_id, pid, time.time() + expires_in),
        )
        conn.commit()
    finally:
        conn.close()


# ── D2 第 1 項 / D3：憑證 ──────────────────────────────────────────────


def test_blanked_env_table_is_locked():
    """清空名單鎖死：少一個就可能讓 API key / Bedrock / gateway 壓過訂閱 token。"""
    assert set(L1_BLANKED_ENV) == {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_UNIX_SOCKET",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
        "CLAUDE_CODE_USE_ANTHROPIC_AWS",
        "CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD",
        "CLAUDE_CODE_USE_MANTLE",
        "CLAUDE_CODE_USE_GATEWAY",
    }


def test_child_env_injects_token_and_blanks_everything_that_outranks_it(monkeypatch):
    for key in L1_BLANKED_ENV:
        monkeypatch.setenv(key, "set-in-parent")
    env = l1_child_env()
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == _TOKEN
    for key in L1_BLANKED_ENV:
        assert env[key] == "", key
    # SDK 的合併方式是 {**os.environ, **options.env}：覆寫後子進程看到的是空字串
    merged = {**os.environ, **env}
    assert all(merged[k] == "" for k in L1_BLANKED_ENV)
    assert "CLAUDE_CODE_MAX_OUTPUT_TOKENS" not in env


def test_child_env_max_output_tokens(monkeypatch):
    assert l1_child_env(max_output_tokens=2048)["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "2048"
    with pytest.raises(ValueError):
        l1_child_env(max_output_tokens=0)


def test_child_env_falls_back_to_nami_token_with_deprecation(monkeypatch, caplog):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN")
    monkeypatch.setenv("NAMI_SDK_OAUTH_TOKEN", "sk-ant-oat01-nami")
    with pytest.warns(DeprecationWarning, match="NAMI_SDK_OAUTH_TOKEN"):
        env = l1_child_env()
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat01-nami"
    assert "NAMI_SDK_OAUTH_TOKEN" in caplog.text


def test_child_env_prefers_claude_code_token_over_nami(monkeypatch):
    monkeypatch.setenv("NAMI_SDK_OAUTH_TOKEN", "sk-ant-oat01-nami")
    assert l1_child_env()["CLAUDE_CODE_OAUTH_TOKEN"] == _TOKEN


def test_child_env_without_any_token_fails_closed(monkeypatch):
    """沒有 OAuth token 就不跑：絕不退回 API key 或 CLI 自己的登入檔。"""
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN")
    with pytest.raises(SubscriptionAuthError) as exc_info:
        l1_child_env()
    assert exc_info.value.details["reason"] == "no_oauth_token"


def test_run_text_without_token_never_spawns(monkeypatch, fake_sdk):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN")
    fake = fake_sdk(*_ok_stream())
    with pytest.raises(SubscriptionAuthError):
        _call()
    assert fake.calls == []
    assert state.count_active_l1_leases() == 0


def test_legacy_subscription_env_is_unchanged(monkeypatch):
    """S1 零行為改變：既有呼叫點用的 subscription_env() 仍然只有兩個 key。"""
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "x")
    assert agent_sdk.subscription_env() == {
        "CLAUDE_CODE_OAUTH_TOKEN": _TOKEN,
        "ANTHROPIC_API_KEY": "",
    }


# ── D2 第 2–3 項：一次性預設、structured output ─────────────────────────


def test_run_text_one_shot_defaults_and_returns_text(fake_sdk):
    fake = fake_sdk(*_ok_stream(text="答案"))
    assert _call(prompt="問題", system="你是助理", max_output_tokens=512) == "答案"
    [(prompt, options)] = fake.calls
    assert prompt == "問題"
    assert options.model == "sonnet"  # 別名原樣交給 CLI 解析
    assert options.system_prompt == "你是助理"
    assert options.tools == []
    assert options.setting_sources == []
    assert options.max_turns == 1
    assert options.output_format is None
    assert options.env["CLAUDE_CODE_OAUTH_TOKEN"] == _TOKEN
    assert options.env["ANTHROPIC_API_KEY"] == ""
    assert options.env["ANTHROPIC_BASE_URL"] == ""
    assert options.env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "512"
    assert callable(options.stderr)
    assert fake.closed == 1


def test_run_text_disables_thinking(fake_sdk):
    """一次性呼叫要關掉 thinking，跟直接打 API（從沒開 thinking）行為一致。

    2026-09-26 S1a 上線實測：CLI 預設會 thinking，thinking token 也算進
    ``CLAUDE_CODE_MAX_OUTPUT_TOKENS``。gateway 意圖分類只回約 15 token 的 JSON，
    卻用掉 193–359 output token，``max_tokens=100`` 直接變成 CLI 錯誤；
    關掉 thinking 後 28 token、2.1 秒。
    """
    fake = fake_sdk(*_ok_stream(text="答案"))
    _call(prompt="問題", system="你是助理", max_output_tokens=100)
    [(_, options)] = fake.calls
    assert options.thinking == {"type": "disabled"}


def test_run_text_structured_output(fake_sdk):
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": {"answer": {"type": "integer"}},
        "required": ["answer"],
    }
    fake = fake_sdk(_assistant(), _result(result="", structured={"answer": 5}))
    assert _call(output_schema=schema) == {"answer": 5}
    [(_, options)] = fake.calls
    assert options.output_format == {"type": "json_schema", "schema": schema}
    assert options.max_turns == 3


def test_run_text_structured_output_missing_is_error(fake_sdk):
    fake_sdk(_assistant(), _result(result="text only", structured=None))
    with pytest.raises(AgentSdkError, match="structured output"):
        _call(output_schema={"type": "object"})


def test_run_text_rejects_2020_12_schema_before_spawning(fake_sdk):
    fake = fake_sdk(*_ok_stream())
    with pytest.raises(ValueError, match="draft-07"):
        _call(output_schema={"$schema": "https://json-schema.org/draft/2020-12/schema"})
    assert fake.calls == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"model": "gpt-5"},
        {"model": "openai/gpt-5.6-terra"},
        {"model": ""},
        {"call_class": "urgent"},
        {"timeout_s": 0},
        {"timeout_s": -1},
    ],
)
def test_run_text_validates_arguments(fake_sdk, kwargs):
    fake = fake_sdk(*_ok_stream())
    with pytest.raises(ValueError):
        _call(**kwargs)
    assert fake.calls == []


@pytest.mark.parametrize("model", ["opus", "sonnet", "haiku", "fable", "claude-opus-4-8"])
def test_run_text_accepts_claude_aliases_and_ids(fake_sdk, model):
    fake = fake_sdk(*_ok_stream())
    _call(model=model)
    assert fake.calls[0][1].model == model


def test_flatten_messages_matches_cli_path_format():
    messages = [
        {"role": "system", "content": "ignored"},
        {"role": "user", "content": "第一句"},
        {"role": "assistant", "content": [{"type": "text", "text": "回覆"}, {"type": "tool_use"}]},
        {"role": "user", "content": "補一句"},
    ]
    assert flatten_messages(messages) == "[USER]\n第一句\n\n[ASSISTANT]\n回覆\n\n[USER]\n補一句"


# ── D2 第 6 項：用量紀錄 + context 傳遞 ────────────────────────────────


def test_usage_row_records_lane_actual_model_and_rate_limit(fake_sdk):
    set_current_agent("robin")
    fake_sdk(
        _rate_limit(status="allowed_warning", rate_limit_type="seven_day"),
        _assistant(model="claude-sonnet-5"),
        _result("ok"),
    )
    _call(model="sonnet")
    [row] = _api_rows()
    assert row["agent"] == "robin"
    assert row["model"] == "sonnet"  # 要求的 model
    assert row["model_actual"] == "claude-sonnet-5"  # 實際跑的（AssistantMessage.model）
    assert row["lane_actual"] == "subscription"
    assert row["auth_actual"] == "subscription"
    assert row["cost_usd"] is None  # U2：SDK cost 是估計值，不入帳
    assert (row["input_tokens"], row["output_tokens"]) == (12, 3)
    assert (row["cache_read_tokens"], row["cache_write_tokens"]) == (100, 7)
    assert row["rate_limit_status"] == "allowed_warning"
    assert row["rate_limit_type"] == "seven_day"
    assert row["rate_limit_resets_at"] == 1790000000
    assert row["latency_ms"] >= 0


def test_usage_row_written_for_exhausted_call_too(fake_sdk):
    fake_sdk(
        _rate_limit(status="rejected", rate_limit_type="seven_day_opus"),
        _assistant(model="claude-opus-5", error="rate_limit", text="limit reached"),
        _result("limit reached", is_error=True),
        raise_after=Exception("Claude Code returned an error result: limit reached"),
    )
    with pytest.raises(SubscriptionExhausted):
        _call(model="opus")
    [row] = _api_rows()
    assert row["rate_limit_status"] == "rejected"
    assert row["rate_limit_type"] == "seven_day_opus"


def test_no_usage_row_when_nothing_reached_the_api(fake_sdk):
    fake_sdk(raise_after=RuntimeError("CLI binary not found"))
    with pytest.raises(AgentSdkError):
        _call()
    assert _api_rows() == []


def test_call_without_agent_context_is_recorded_unknown_and_warned(fake_sdk, caplog):
    """D9：沒帶 context 照樣執行，記成 unknown 並發 warning（S6 驗收 unknown 必須是 0）。"""
    import logging

    caplog.set_level(logging.WARNING, logger=agent_sdk.LANE_LOGGER_NAME)
    fake_sdk(*_ok_stream())
    assert _call() == "hello"
    assert _api_rows()[0]["agent"] == "unknown"
    assert "without agent context" in caplog.text


def test_usage_buffer_opt_in_sees_l1_calls(fake_sdk):
    from shared.llm_context import start_usage_tracking, stop_usage_tracking

    fake_sdk(*_ok_stream())
    start_usage_tracking()
    try:
        _call()
    finally:
        buf = stop_usage_tracking()
    assert [b["model"] for b in buf] == ["sonnet"]
    assert buf[0]["cost_usd"] is None


# ── D2 第 7 項：錯誤分類、不可重試 ──────────────────────────────────────


def test_rate_limit_rejected_raises_subscription_exhausted(fake_sdk):
    fake_sdk(
        _rate_limit(status="rejected", rate_limit_type="five_hour", resets_at=1790001234),
        _assistant(model="<synthetic>", error="rate_limit", text="You've hit your limit"),
        _result("You've hit your limit", is_error=True),
        raise_after=Exception("Claude Code returned an error result: You've hit your limit"),
    )
    with pytest.raises(SubscriptionExhausted) as exc_info:
        _call()
    exc = exc_info.value
    assert exc.rate_limit_type == "five_hour"
    assert exc.resets_at == 1790001234
    assert exc.details["rate_limit_rejected"]["status"] == "rejected"
    assert exc.details["assistant_errors"][0]["error"] == "rate_limit"
    assert "error result" in exc.details["exception"]["text"]
    assert exc.nakama_non_retryable is True


@pytest.mark.parametrize("error", ["rate_limit", "billing_error"])
def test_assistant_error_alone_raises_subscription_exhausted(fake_sdk, error):
    fake_sdk(_assistant(error=error, text="nope"), _result("nope", is_error=True))
    with pytest.raises(SubscriptionExhausted):
        _call()


def test_authentication_failed_raises_auth_error(fake_sdk):
    """S0 桌機實測的樣本：error=authentication_failed、subtype=success 但 is_error=true。"""
    fake_sdk(
        _assistant(
            model="<synthetic>", error="authentication_failed", text="Failed to authenticate"
        ),
        _result("Failed to authenticate: OAuth session expired", is_error=True),
        raise_after=Exception("Claude Code returned an error result: Failed to authenticate"),
    )
    with pytest.raises(SubscriptionAuthError, match="Failed to authenticate"):
        _call()


def test_other_sdk_exception_is_agent_sdk_error_with_details(fake_sdk):
    fake_sdk(_assistant(), raise_after=RuntimeError("CLI crashed"))
    with pytest.raises(AgentSdkError) as exc_info:
        _call()
    assert not isinstance(exc_info.value, (SubscriptionExhausted, SubscriptionAuthError))
    assert exc_info.value.details["exception"] == {"type": "RuntimeError", "text": "CLI crashed"}
    assert isinstance(exc_info.value.__cause__, RuntimeError)


def test_error_result_without_exception_is_agent_sdk_error(fake_sdk):
    fake_sdk(
        _assistant(error="server_error", text="overloaded"),
        _result("overloaded", subtype="error_during_execution", is_error=True),
    )
    with pytest.raises(AgentSdkError, match="overloaded"):
        _call()


def test_missing_result_message_is_error(fake_sdk):
    fake_sdk(_assistant())
    with pytest.raises(AgentSdkError, match="ResultMessage"):
        _call()


@pytest.mark.parametrize("exc_type", [SubscriptionExhausted, SubscriptionAuthError])
def test_subscription_errors_are_not_retried_even_with_broad_retryable(exc_type):
    attempts = []

    def _fn():
        attempts.append(1)
        raise exc_type("known result")

    with pytest.raises(exc_type):
        with_retry(_fn, max_attempts=3, backoff_base=0, retryable=(Exception,))
    assert len(attempts) == 1
    with pytest.raises(exc_type):
        with_retry(_fn, max_attempts=3, backoff_base=0)
    assert len(attempts) == 2


def test_plain_retryable_errors_still_retry():
    attempts = []

    def _fn():
        attempts.append(1)
        raise ConnectionError("flaky")

    with pytest.raises(ConnectionError):
        with_retry(_fn, max_attempts=3, backoff_base=0)
    assert len(attempts) == 3


# ── D2 第 4 項：timeout（anyio.fail_after）──────────────────────────────


def test_timeout_cancels_stream_and_releases_lease(fake_sdk):
    fake = fake_sdk(_rate_limit(), hang_s=30)
    t0 = time.monotonic()
    with pytest.raises(TimeoutError, match="0.3s") as exc_info:
        _call(timeout_s=0.3)
    assert time.monotonic() - t0 < 10
    assert not isinstance(exc_info.value, L1LeaseTimeout)
    assert fake.closed == 1  # aclosing：stream 在 cancel scope 內被關掉
    assert state.count_active_l1_leases() == 0


def test_timeout_uses_anyio_not_asyncio_wait_for():
    import inspect

    src = inspect.getsource(agent_sdk._run_text_async)
    assert "anyio.fail_after(timeout_s)" in src
    assert "wait_for(" not in src and "asyncio.timeout(" not in src  # F17


# ── D2 第 5 項：機器層級 lease ─────────────────────────────────────────


def test_lease_limit_release_and_ttl_reclaim():
    assert state.try_acquire_l1_lease("a", limit=2, ttl_s=10, pid=1, now=1000.0)
    assert state.try_acquire_l1_lease("b", limit=2, ttl_s=10, pid=1, now=1000.0)
    assert not state.try_acquire_l1_lease("c", limit=2, ttl_s=10, pid=1, now=1005.0)
    assert state.count_active_l1_leases(now=1005.0) == 2
    state.release_l1_lease("a")
    assert state.try_acquire_l1_lease("c", limit=2, ttl_s=10, pid=1, now=1005.0)
    assert not state.try_acquire_l1_lease("d", limit=2, ttl_s=10, pid=1, now=1009.0)
    # b 在 1010 過期（process 掛掉沒 release 的情況）→ 下一次 acquire 把它清掉
    assert state.try_acquire_l1_lease("d", limit=2, ttl_s=10, pid=1, now=1010.5)
    assert state.count_active_l1_leases(now=1010.5) == 2
    state.release_l1_lease("does-not-exist")  # 不存在也不算錯


def test_crashed_process_lease_is_reclaimed_after_ttl():
    _insert_foreign_lease("dead-1", expires_in=-1)
    _insert_foreign_lease("dead-2", expires_in=-5)
    assert state.try_acquire_l1_lease("mine", limit=2, ttl_s=60, pid=os.getpid())
    ids = {r[0] for r in state._get_conn().execute("SELECT lease_id FROM llm_l1_leases").fetchall()}
    assert ids == {"mine"}


def test_lease_limit_holds_across_processes(tmp_path, monkeypatch):
    """5 個獨立 process 同時搶（都不歸還），整台機器只會發出 limit=2 個名額。"""
    data_dir = tmp_path / "xproc"
    data_dir.mkdir()
    monkeypatch.setattr(state, "get_db_path", lambda: data_dir / "state.db")
    if state._conn is not None:
        state._conn.close()
    state._conn = None
    state._get_conn()  # 先建 schema，子進程只做 acquire
    code = (
        "import os, sys\n"
        "from shared import state\n"
        "ok = state.try_acquire_l1_lease(sys.argv[1], limit=2, ttl_s=120, pid=os.getpid())\n"
        "print('1' if ok else '0')\n"
    )
    env = {**os.environ, "NAKAMA_DATA_DIR": str(data_dir), "PYTHONPATH": str(_REPO)}
    env.pop("DB_PATH", None)
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", code, f"proc-{i}"],
            cwd=str(_REPO),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for i in range(5)
    ]
    outs = []
    for p in procs:
        out, err = p.communicate(timeout=120)
        assert p.returncode == 0, err
        outs.append(out.strip())
    assert sorted(outs) == ["0", "0", "0", "1", "1"]
    assert state.count_active_l1_leases() == 2
    pids = {r[0] for r in state._get_conn().execute("SELECT pid FROM llm_l1_leases")}
    assert len(pids) == 2 and os.getpid() not in pids


def test_run_text_waits_then_times_out_when_machine_is_full(fake_sdk, monkeypatch):
    monkeypatch.setattr(agent_sdk, "_LEASE_POLL_S", 0.05)
    for i in range(L1_MACHINE_CONCURRENCY):
        _insert_foreign_lease(f"busy-{i}", expires_in=300)
    fake = fake_sdk(*_ok_stream())
    with pytest.raises(L1LeaseTimeout):
        _call(timeout_s=0.3)
    assert fake.calls == []  # 沒拿到名額就不起子進程


def test_run_text_holds_exactly_one_slot_and_releases_it(fake_sdk):
    fake = fake_sdk(*_ok_stream())
    assert state.count_active_l1_leases() == 0
    _call()
    assert fake.active_leases == [1]  # SDK 執行期間佔一個名額
    assert fake.session_flags == [True]  # 並標記為 L1 session
    assert state.count_active_l1_leases() == 0  # 呼叫後歸還
    assert not in_l1_session()


def test_concurrency_limit_constant_is_two():
    assert L1_MACHINE_CONCURRENCY == 2


# ── D2 第 8 項：巢狀呼叫、async 情境 ────────────────────────────────────


def test_nested_call_does_not_take_a_second_slot(fake_sdk, monkeypatch):
    """外層 session 占一個名額、另一個 process 占另一個 → 機器滿了；巢狀呼叫仍能跑。"""
    monkeypatch.setattr(agent_sdk, "_LEASE_POLL_S", 0.05)
    fake = fake_sdk(*_ok_stream())
    _insert_foreign_lease("other-process", expires_in=300)
    with l1_session(call_class="interactive", ttl_s=60, wait_s=1) as lease_id:
        assert lease_id is not None and in_l1_session()
        assert state.count_active_l1_leases() == 2
        assert _call(timeout_s=0.5) == "hello"
        assert state.count_active_l1_leases() == 2
        with l1_session(call_class="batch", ttl_s=60, wait_s=1) as inner:
            assert inner is None
    assert not in_l1_session()
    assert state.count_active_l1_leases() == 1  # 只剩另一個 process 的
    assert len(fake.calls) == 1


def test_top_level_call_on_full_machine_would_block(monkeypatch):
    """對照組：同樣滿載，但不在 session 裡 → 等不到名額。"""
    monkeypatch.setattr(agent_sdk, "_LEASE_POLL_S", 0.05)
    for i in range(L1_MACHINE_CONCURRENCY):
        _insert_foreign_lease(f"busy-{i}", expires_in=300)
    with pytest.raises(L1LeaseTimeout):
        with l1_session(call_class="batch", ttl_s=60, wait_s=0.2):
            pass


def test_run_text_inside_running_event_loop_uses_worker_thread(fake_sdk):
    """F19：async handler 同步呼叫 LLM。不能在正在跑的 loop 裡 asyncio.run。"""
    fake = fake_sdk(*_ok_stream(text="from worker"))
    set_current_agent("nami")

    async def handler():
        loop_thread = threading.current_thread().name
        return loop_thread, _call()

    loop_thread, text = asyncio.run(handler())
    assert text == "from worker"
    assert fake.threads == ["nakama-l1-sdk"]
    assert fake.threads[0] != loop_thread
    [row] = _api_rows()
    assert row["agent"] == "nami"  # context 跟著進 worker thread


def test_run_text_worker_thread_propagates_exceptions(fake_sdk):
    fake_sdk(_assistant(error="billing_error"), _result("no credit", is_error=True))

    async def handler():
        return _call()

    with pytest.raises(SubscriptionExhausted):
        asyncio.run(handler())


def test_run_text_from_plain_thread_keeps_agent_via_spawn_thread(fake_sdk):
    from shared.llm_context import spawn_thread

    fake_sdk(*_ok_stream())
    set_current_agent("franky")
    box = {}
    t = spawn_thread(lambda: box.setdefault("out", _call()))
    t.join(30)
    assert box["out"] == "hello"
    assert _api_rows()[0]["agent"] == "franky"


def test_worker_thread_nested_marker_propagates(fake_sdk, monkeypatch):
    """在 session 裡、又在 async handler 裡呼叫 → worker thread 帶著標記，不另佔名額。"""
    monkeypatch.setattr(agent_sdk, "_LEASE_POLL_S", 0.05)
    fake = fake_sdk(*_ok_stream())
    _insert_foreign_lease("other-process", expires_in=300)

    async def handler():
        return _call(timeout_s=0.5)

    with l1_session(call_class="interactive", ttl_s=60, wait_s=1):
        assert asyncio.run(handler()) == "hello"
    assert fake.session_flags == [True]
    assert fake.active_leases == [2]  # 外層 + 另一個 process；巢狀沒有再多佔
