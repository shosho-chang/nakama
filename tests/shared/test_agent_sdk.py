"""Tests for shared/agent_sdk.py (merger SDK migration S1 + ADR-070 S0 取證).

行為契約比照 tests/gateway/test_nami_sdk_loop.py 的 _sdk_auth_env 測試 —
這兩組測試共同鎖住「訂閱覆寫必須同時清空 API key」的語意，S4 收斂時
nami 版 delegate 到 shared 版，兩組測試都不可刪。

ADR-070 S0 的部分鎖住兩件事：
1. ``log_sdk_message`` / ``log_sdk_exception`` 把 F15 的額度訊號、錯誤與成功摘要
   原樣寫進 ``nakama.llm_lane``，而且對舊版 SDK（缺欄位、缺類別）不會炸。
2. 四個呼叫點（Nami、merger、Sanji judge、``claude -p``）真的有接上，且控制流程
   與回傳值不變。全部用假 stream / 假 subprocess，不打任何網路或 LLM。
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import subprocess
import sys
import types

import claude_agent_sdk
import pytest
from claude_agent_sdk import (
    AssistantMessage,
    RateLimitEvent,
    RateLimitInfo,
    ResultMessage,
    SystemMessage,
    TextBlock,
)

from shared import agent_sdk
from shared.agent_sdk import (
    LANE_LOGGER_NAME,
    describe_sdk_message,
    log_sdk_exception,
    log_sdk_message,
    sdk_message_kind,
    subscription_env,
)


def test_empty_without_token(monkeypatch):
    """未設 token → 空 dict，SDK 子進程沿用繼承環境（行為零改變）。"""
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    assert subscription_env() == {}


def test_forces_subscription_and_blanks_api_key(monkeypatch):
    """設了 token → 子進程走 OAuth，且 API key 必須被清空。

    CLI 實測優先序是 ANTHROPIC_API_KEY 壓過 CLAUDE_CODE_OAUTH_TOKEN
    （2026-08-18，findings §操作性發現）——這個斷言防止日後有人「順手」
    把清空那行拿掉，讓「走訂閱」變成取決於未文件化優先序的賭局。
    """
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-test")
    env = subscription_env()
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat01-test"
    assert env["ANTHROPIC_API_KEY"] == ""


def test_exactly_two_keys(monkeypatch):
    """覆寫範圍鎖死兩個 key —— 不准偷渡其他環境變數進子進程覆寫。"""
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-test")
    assert set(subscription_env().keys()) == {"CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"}


# ── ADR-070 S0：lane log helper ─────────────────────────────────────────


def _lane(caplog) -> list[tuple[str, dict, int]]:
    """caplog 裡 ``nakama.llm_lane`` 的紀錄 → ``[(event, payload, levelno)]``。"""
    out = []
    for rec in caplog.records:
        if rec.name != LANE_LOGGER_NAME or rec.levelno < logging.INFO:
            continue
        event, _, payload = rec.getMessage().partition(" ")
        out.append((event, json.loads(payload), rec.levelno))
    return out


@pytest.fixture
def lane_caplog(caplog):
    caplog.set_level(logging.DEBUG, logger=LANE_LOGGER_NAME)
    return caplog


@pytest.fixture(autouse=True)
def _fresh_model_state():
    """同步呼叫的測試會把 model 暫存留在 pytest thread 的 context；每個測試歸零。"""
    token = agent_sdk._seen_models.set(None)
    yield
    agent_sdk._seen_models.reset(token)


def _rate_limit_event(status="rejected", **info_kwargs) -> RateLimitEvent:
    info = RateLimitInfo(
        status=status,
        resets_at=info_kwargs.pop("resets_at", 1790000000),
        rate_limit_type=info_kwargs.pop("rate_limit_type", "five_hour"),
        utilization=info_kwargs.pop("utilization", 1.0),
        overage_status=info_kwargs.pop("overage_status", "rejected"),
        overage_resets_at=info_kwargs.pop("overage_resets_at", None),
        overage_disabled_reason=info_kwargs.pop("overage_disabled_reason", "out_of_credits"),
        raw=info_kwargs.pop("raw", {"status": status, "rateLimitType": "five_hour"}),
    )
    return RateLimitEvent(rate_limit_info=info, uuid="u-1", session_id="sess-1")


def _assistant(model="claude-haiku-4-5-20251001", error=None, text="hi") -> AssistantMessage:
    return AssistantMessage(content=[TextBlock(text=text)], model=model, error=error)


def _result(subtype="success", is_error=False, **kw) -> ResultMessage:
    return ResultMessage(
        subtype=subtype,
        duration_ms=kw.pop("duration_ms", 1200),
        duration_api_ms=kw.pop("duration_api_ms", 900),
        is_error=is_error,
        num_turns=kw.pop("num_turns", 1),
        session_id=kw.pop("session_id", "sess-1"),
        total_cost_usd=kw.pop("total_cost_usd", 0.0123),
        usage=kw.pop(
            "usage",
            {
                "input_tokens": 10,
                "output_tokens": 2,
                "cache_read_input_tokens": 0,
                "service_tier": "standard",
                "server_tool_use": {"web_search_requests": 0},
            },
        ),
        result=kw.pop("result", "ok"),
        model_usage=kw.pop("model_usage", {"claude-haiku-4-5-20251001": {"costUSD": 0.0123}}),
        **kw,
    )


def test_rate_limit_event_logs_every_field(lane_caplog):
    log_sdk_message("nami", _rate_limit_event())
    [(event, payload, level)] = _lane(lane_caplog)
    assert event == "sdk_rate_limit"
    assert level == logging.WARNING
    assert payload["site"] == "nami"
    assert payload["status"] == "rejected"
    assert payload["rate_limit_type"] == "five_hour"
    assert payload["resets_at"] == 1790000000
    assert payload["resets_at_iso"].startswith("2026-")
    assert payload["utilization"] == 1.0
    assert payload["overage_status"] == "rejected"
    assert payload["overage_disabled_reason"] == "out_of_credits"
    assert payload["raw"] == {"status": "rejected", "rateLimitType": "five_hour"}
    assert payload["session_id"] == "sess-1"


def test_rate_limit_allowed_is_info(lane_caplog):
    log_sdk_message("nami", _rate_limit_event(status="allowed"))
    [(_, payload, level)] = _lane(lane_caplog)
    assert payload["status"] == "allowed"
    assert level == logging.INFO


def test_rate_limit_event_old_flat_shape_without_newer_fields(lane_caplog):
    """舊版 SDK 的假物件：欄位平鋪在 event 上、沒有 rate_limit_info / raw / uuid。"""

    class RateLimitEvent:  # noqa: N801 — 名稱要跟 SDK 類別一樣才會被辨識
        status = "allowed_warning"

    log_sdk_message("sanji.judge", RateLimitEvent())
    [(event, payload, level)] = _lane(lane_caplog)
    assert event == "sdk_rate_limit"
    assert payload["status"] == "allowed_warning"
    assert payload["rate_limit_type"] is None
    assert payload["resets_at"] is None
    assert payload["raw"] is None
    assert level == logging.WARNING


def test_assistant_error_logs_error_model_and_text(lane_caplog):
    log_sdk_message(
        "nami",
        _assistant(model="claude-sonnet-5", error="rate_limit", text="You've hit your limit"),
    )
    [(event, payload, level)] = _lane(lane_caplog)
    assert event == "sdk_assistant_error"
    assert payload["error"] == "rate_limit"
    assert payload["model"] == "claude-sonnet-5"
    assert payload["text"] == "You've hit your limit"
    assert level == logging.WARNING


def test_plain_assistant_not_logged_but_model_reaches_result_line(lane_caplog):
    """成功的 AssistantMessage 不單獨記，但它的 model 會出現在 ResultMessage 那一行。"""

    async def run():
        log_sdk_message("robin.merger", _assistant(model="claude-opus-5"))
        log_sdk_message("robin.merger", _assistant(model="claude-opus-5"))
        log_sdk_message("robin.merger", _result())

    asyncio.run(run())
    [(event, payload, level)] = _lane(lane_caplog)
    assert event == "sdk_result_ok"
    assert level == logging.INFO
    assert payload["models"] == ["claude-opus-5"]
    assert payload["model_usage_models"] == ["claude-haiku-4-5-20251001"]
    assert payload["total_cost_usd"] == 0.0123
    # 成功那一行只留數值 token 欄位 + service_tier
    assert payload["usage"] == {
        "input_tokens": 10,
        "output_tokens": 2,
        "cache_read_input_tokens": 0,
        "service_tier": "standard",
    }


def test_result_error_logs_full_fields(lane_caplog):
    long_result = "x" * 5000
    msg = _result(
        subtype="success",
        is_error=True,
        result=long_result,
        errors=["Credit balance is too low"],
        api_error_status=429,
        terminal_reason="completed",
    )
    log_sdk_message("nami", msg)
    [(event, payload, level)] = _lane(lane_caplog)
    assert event == "sdk_result_error"
    assert level == logging.WARNING
    assert payload["subtype"] == "success"
    assert payload["is_error"] is True
    assert payload["api_error_status"] == 429
    assert payload["errors"] == ["Credit balance is too low"]
    assert payload["terminal_reason"] == "completed"
    assert payload["result"].startswith("x" * 2000)
    assert len(payload["result"]) < 2100  # 截斷到 2000 字 + 標記
    assert payload["usage"]["server_tool_use"] == {"web_search_requests": 0}  # 失敗記完整 usage
    assert payload["model_usage"] == {"claude-haiku-4-5-20251001": {"costUSD": 0.0123}}


def test_non_success_subtype_counts_as_error(lane_caplog):
    log_sdk_message("nami", _result(subtype="error_max_turns", is_error=False))
    [(event, payload, _)] = _lane(lane_caplog)
    assert event == "sdk_result_error"
    assert payload["subtype"] == "error_max_turns"


def test_old_result_message_without_newer_attributes(lane_caplog):
    """0.2.128 可能沒有 api_error_status / terminal_reason / errors / model_usage。"""

    class ResultMessage:  # noqa: N801
        subtype = "success"
        is_error = True
        num_turns = 1
        session_id = "s-old"
        result = "API Error: 400"

    log_sdk_message("nami", ResultMessage())
    [(event, payload, _)] = _lane(lane_caplog)
    assert event == "sdk_result_error"
    assert payload["api_error_status"] is None
    assert payload["terminal_reason"] is None
    assert payload["errors"] is None
    assert payload["model_usage"] is None
    assert payload["result"] == "API Error: 400"


@pytest.mark.parametrize(
    "msg",
    [
        SystemMessage(subtype="init", data={}),
        object(),
        None,
        "text",
    ],
)
def test_other_messages_are_ignored(lane_caplog, msg):
    log_sdk_message("nami", msg)
    assert _lane(lane_caplog) == []


def test_never_raises_even_if_attribute_access_explodes(lane_caplog):
    class ResultMessage:  # noqa: N801
        @property
        def subtype(self):
            raise RuntimeError("boom")

    log_sdk_message("nami", ResultMessage())  # 不 raise 就是通過
    assert _lane(lane_caplog) == []


def test_models_do_not_leak_between_calls(lane_caplog):
    """每次 asyncio.run 各自一份 context；同一 context 裡 ResultMessage 會清掉暫存。"""

    async def crashed_call():
        log_sdk_message("nami", _assistant(model="claude-opus-5"))  # 沒等到 result 就掛

    async def good_call(model):
        log_sdk_message("nami", _assistant(model=model))
        log_sdk_message("nami", _result())

    asyncio.run(crashed_call())
    asyncio.run(good_call("claude-sonnet-5"))

    async def two_in_a_row():
        await good_call("claude-haiku-4-5")
        await good_call("claude-sonnet-5")

    asyncio.run(two_in_a_row())
    models = [p["models"] for e, p, _ in _lane(lane_caplog) if e == "sdk_result_ok"]
    assert models == [["claude-sonnet-5"], ["claude-haiku-4-5"], ["claude-sonnet-5"]]


def test_state_left_in_parent_context_does_not_leak_into_new_call(lane_caplog):
    """同步 context 留下的 model（例如沒等到 result 的呼叫）不可混進之後的 asyncio.run。"""
    log_sdk_message("nami", _assistant(model="claude-stale-1"))

    async def good_call():
        log_sdk_message("nami", _assistant(model="claude-sonnet-5"))
        log_sdk_message("nami", _result())

    asyncio.run(good_call())
    [(_, payload, _)] = _lane(lane_caplog)
    assert payload["models"] == ["claude-sonnet-5"]


def test_log_sdk_exception_records_text_and_models(lane_caplog):
    async def run():
        log_sdk_message("nami", _assistant(model="claude-sonnet-5"))
        log_sdk_exception(
            "nami", Exception("Claude Code returned an error result: Credit balance is too low")
        )

    asyncio.run(run())
    [(event, payload, level)] = _lane(lane_caplog)
    assert event == "sdk_exception"
    assert level == logging.WARNING
    assert payload["exc_type"] == "Exception"
    assert payload["exc_text"].endswith("Credit balance is too low")
    assert payload["models"] == ["claude-sonnet-5"]


def test_log_sdk_exception_keeps_process_error_details(lane_caplog):
    class ProcessError(Exception):
        exit_code = 1
        stderr = "fatal: not logged in"

    log_sdk_exception("robin.merger", ProcessError("Command failed"))
    [(_, payload, _)] = _lane(lane_caplog)
    assert payload["exit_code"] == 1
    assert payload["stderr"] == "fatal: not logged in"


def test_describe_and_kind_are_pure():
    assert sdk_message_kind(_rate_limit_event()) == "RateLimitEvent"
    assert sdk_message_kind(_assistant()) == "AssistantMessage"
    assert sdk_message_kind(_result()) == "ResultMessage"
    assert sdk_message_kind(SystemMessage(subtype="init", data={})) is None
    assert describe_sdk_message(_assistant()) is None
    event, fields = describe_sdk_message(_result())
    assert event == "sdk_result_ok"
    assert "models" not in fields  # models 只在 log_sdk_message 補


def test_module_imports_on_sdk_without_rate_limit_event(monkeypatch):
    """VPS 的 0.2.128 若沒有 RateLimitEvent：本模組 import 不可炸，改用類別名稱辨識。"""
    stub = types.ModuleType("claude_agent_sdk")
    stub.AssistantMessage = AssistantMessage
    stub.ResultMessage = ResultMessage  # 故意沒有 RateLimitEvent
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", stub)
    spec = importlib.util.spec_from_file_location("agent_sdk_old_sdk_copy", agent_sdk.__file__)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod._RateLimitEvent is None

    class RateLimitEvent:  # noqa: N801
        status = "rejected"
        rate_limit_type = "seven_day_opus"

    assert mod.sdk_message_kind(RateLimitEvent()) == "RateLimitEvent"
    event, fields = mod.describe_sdk_message(RateLimitEvent())
    assert (event, fields["rate_limit_type"]) == ("sdk_rate_limit", "seven_day_opus")


def test_module_imports_without_sdk_installed(monkeypatch):
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", None)  # import 會丟 ImportError
    spec = importlib.util.spec_from_file_location("agent_sdk_no_sdk_copy", agent_sdk.__file__)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod._RateLimitEvent is None and mod._ResultMessage is None


# ── ADR-070 S0：呼叫點接線（控制流程不變）─────────────────────────────


def _fake_stream(messages, raise_after: Exception | None = None):
    async def fake_query(*, prompt, options):
        for m in messages:
            yield m
        if raise_after is not None:
            raise raise_after

    return fake_query


def test_nami_sdk_loop_logs_every_message_and_keeps_reply(lane_caplog, monkeypatch):
    import gateway.handlers.nami as nami  # noqa: PLC0415

    msgs = [_rate_limit_event(status="allowed"), _assistant(model="claude-sonnet-5"), _result()]
    monkeypatch.setattr(nami, "query", _fake_stream(msgs))
    monkeypatch.setattr(nami, "get_model", lambda **kw: "sonnet")
    resp = nami.NamiHandler()._run_loop_sdk("hi", "U1")
    assert resp.text == "ok"  # 回覆與改動前一樣
    events = [(e, p["site"]) for e, p, _ in _lane(lane_caplog)]
    assert events == [("sdk_rate_limit", "nami"), ("sdk_result_ok", "nami")]
    assert _lane(lane_caplog)[1][1]["models"] == ["claude-sonnet-5"]


def test_nami_sdk_loop_logs_exception_and_still_reports_failure(lane_caplog, monkeypatch):
    import gateway.handlers.nami as nami  # noqa: PLC0415

    err = Exception("Claude Code returned an error result: You've hit your limit")
    msgs = [
        _assistant(model="claude-sonnet-5", error="rate_limit", text="You've hit your limit"),
        _result(is_error=True, api_error_status=429, errors=["You've hit your limit"]),
    ]
    monkeypatch.setattr(nami, "query", _fake_stream(msgs, raise_after=err))
    monkeypatch.setattr(nami, "get_model", lambda **kw: "sonnet")
    resp = nami.NamiHandler()._run_loop_sdk("hi", "U1")
    assert resp.text == "Nami 執行失敗，請稍後再試。（細節已記錄在 log）"
    events = [e for e, _, _ in _lane(lane_caplog)]
    assert events == ["sdk_assistant_error", "sdk_result_error", "sdk_exception"]
    assert _lane(lane_caplog)[2][1]["exc_text"].endswith("You've hit your limit")


def test_merger_sdk_logs_and_reraises_same_exception(lane_caplog, monkeypatch):
    from agents.robin import annotation_merger as merger  # noqa: PLC0415

    err = Exception("Claude Code returned an error result: billing")
    msgs = [_assistant(model="claude-opus-5"), _result(is_error=True)]
    monkeypatch.setattr(claude_agent_sdk, "query", _fake_stream(msgs, raise_after=err))
    with pytest.raises(Exception) as excinfo:
        merger._sdk_merge_once("p", "opus")
    assert excinfo.value is err  # 例外原樣往上拋
    events = [(e, p["site"]) for e, p, _ in _lane(lane_caplog)]
    assert events == [("sdk_result_error", "robin.merger"), ("sdk_exception", "robin.merger")]
    assert _lane(lane_caplog)[0][1]["models"] == ["claude-opus-5"]


def test_merger_sdk_success_return_value_unchanged(lane_caplog, monkeypatch):
    from agents.robin import annotation_merger as merger  # noqa: PLC0415

    final = _result()
    monkeypatch.setattr(claude_agent_sdk, "query", _fake_stream([_assistant(), final]))
    mapping, result_msg = merger._sdk_merge_once("p", "opus")
    assert mapping is None and result_msg is final
    assert [e for e, _, _ in _lane(lane_caplog)] == ["sdk_result_ok"]


def test_sanji_judge_logs_and_still_degrades_to_provisional(lane_caplog, monkeypatch):
    from agents.sanji import judge  # noqa: PLC0415

    err = Exception("Claude Code returned an error result: rate limited")
    monkeypatch.setattr(judge, "query", _fake_stream([_rate_limit_event()], raise_after=err))
    monkeypatch.setattr(judge, "get_model", lambda **kw: "haiku")
    text = "今天睡前做了十分鐘的身體掃描，肩膀放鬆很多，記錄一下感受。"
    d = judge.judge_feed({"message": text, "media": []}, "睡眠")
    assert (d.action, d.note) == ("provisional", "haiku:error:Exception")
    events = [(e, p["site"]) for e, p, _ in _lane(lane_caplog)]
    assert events == [("sdk_rate_limit", "sanji.judge"), ("sdk_exception", "sanji.judge")]


def test_sanji_judge_success_path_unchanged(lane_caplog, monkeypatch):
    from agents.sanji import judge  # noqa: PLC0415

    ok = _result(result='{"verdict": "pass", "reason": "有練習"}')
    monkeypatch.setattr(judge, "query", _fake_stream([_assistant(), ok]))
    monkeypatch.setattr(judge, "get_model", lambda **kw: "haiku")
    text = "今天睡前做了十分鐘的身體掃描，肩膀放鬆很多，記錄一下感受。"
    d = judge.judge_feed({"message": text, "media": []}, "睡眠")
    assert (d.action, d.note) == ("approve", "haiku:pass:有練習")
    assert [e for e, _, _ in _lane(lane_caplog)] == ["sdk_result_ok"]


@pytest.fixture
def cli_mod(monkeypatch):
    from shared import claude_cli_client as cli  # noqa: PLC0415

    monkeypatch.setenv("NAKAMA_CLAUDE_CLI", "C:/fake/claude.exe")  # 不會真的執行
    monkeypatch.setattr(cli, "with_retry", lambda fn, **kw: fn())
    return cli


def _completed(stdout: str, returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(
        args=["claude"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_cli_is_error_payload_logged_then_raises_as_before(lane_caplog, cli_mod, monkeypatch):
    payload = {
        "type": "result",
        "subtype": "success",
        "is_error": True,
        "api_error_status": 429,
        "result": "You've hit your limit · resets 3pm",
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _completed(json.dumps(payload)))
    with pytest.raises(cli_mod.ClaudeCliError, match="is_error=true"):
        cli_mod.ask_via_cli("hi", model="claude-sonnet-4-6")
    [(event, fields, level)] = _lane(lane_caplog)
    assert event == "cli_result_error"
    assert level == logging.WARNING
    assert fields["site"] == "claude_cli"
    assert fields["model"] == "claude-sonnet-4-6"
    assert fields["subtype"] == "success"
    assert fields["api_error_status"] == 429
    assert fields["result"] == "You've hit your limit · resets 3pm"
    assert fields["usage"] == {"input_tokens": 0, "output_tokens": 0}


def test_cli_nonzero_exit_with_json_stdout_is_parsed(lane_caplog, cli_mod, monkeypatch):
    payload = {"type": "result", "subtype": "success", "is_error": True, "api_error_status": 400}
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: _completed(json.dumps(payload), returncode=1, stderr="x"),
    )
    with pytest.raises(cli_mod.ClaudeCliError, match="exited 1"):
        cli_mod.ask_via_cli("hi", model="m")
    [(event, fields, _)] = _lane(lane_caplog)
    assert (event, fields["returncode"], fields["api_error_status"]) == (
        "cli_result_error",
        1,
        400,
    )


def test_cli_nonzero_exit_without_json_logs_raw_text(lane_caplog, cli_mod, monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: _completed("", returncode=1, stderr="boom")
    )
    with pytest.raises(cli_mod.ClaudeCliError, match="exited 1"):
        cli_mod.ask_via_cli("hi", model="m")
    [(event, fields, _)] = _lane(lane_caplog)
    assert (event, fields["stderr"], fields["stdout"]) == ("cli_exit_error", "boom", "")


def test_cli_success_logs_nothing(lane_caplog, cli_mod, monkeypatch):
    payload = {"type": "result", "subtype": "success", "is_error": False, "result": "HELLO"}
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _completed(json.dumps(payload)))
    monkeypatch.setattr(cli_mod, "record_call", lambda **kw: None)
    assert cli_mod.ask_via_cli("hi", model="m") == "HELLO"
    assert _lane(lane_caplog) == []
