"""Tests for the S2 Agent SDK path in gateway/handlers/nami.py.

不打真 API — patch `gateway.handlers.nami.query` 驗證：
- flag 分流（NAMI_USE_AGENT_SDK）
- ClaudeAgentOptions 組裝（tools=[] 紅線 / allowed_tools 27 條 / setting_sources
  明確空 / skills 白名單 / budget env / model 走 router）
- 終態分支：SDK 對 error result 是「先 yield ResultMessage 再 raise」——
  error_max_turns 對映 legacy 訊息、其餘明確報錯、raw exception 不進 Slack
- auto memory settings（2026-07-29 裁決：VPS 獨立目錄；inline JSON 免檔案 race）
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from claude_agent_sdk import ResultMessage
from claude_agent_sdk.types import DeferredToolUse

from gateway.handlers.nami import (
    NAMI_AGENT_FLOW,
    NAMI_TOOLS,
    NamiHandler,
    _format_ask_user_question,
    _make_ask_user_hook,
    _sdk_auth_env,
    _sdk_budget_usd,
    _sdk_settings,
    _sdk_skills,
    _use_agent_sdk,
)


def _result(
    text: str | None = "done",
    subtype: str = "success",
    is_error: bool = False,
    deferred: DeferredToolUse | None = None,
    stop_reason: str | None = None,
) -> ResultMessage:
    return ResultMessage(
        subtype=subtype,
        duration_ms=10,
        duration_api_ms=8,
        is_error=is_error,
        num_turns=2,
        session_id="sess-test",
        total_cost_usd=0.01,
        result=text,
        terminal_reason="completed",
        deferred_tool_use=deferred,
        stop_reason=stop_reason,
    )


def _fake_query(captured: dict, messages: list, raise_after: Exception | None = None):
    async def fake_query(*, prompt, options):
        captured["prompt"] = prompt
        captured["options"] = options
        for m in messages:
            yield m
        if raise_after is not None:
            raise raise_after
        captured["exhausted"] = True  # drain 到自然結束才會設（B1：不准 break）

    return fake_query


@pytest.fixture(autouse=True)
def _sdk_env_hygiene(monkeypatch):
    """統一清 SDK env（有設過的機器跑測試不可漂移）+ 擋掉背景 memory extractor。"""
    for key in (
        "NAMI_USE_AGENT_SDK",
        "NAMI_SDK_MAX_BUDGET_USD",
        "NAMI_AUTO_MEMORY_DIR",
        "NAMI_SKILLS",
    ):
        monkeypatch.delenv(key, raising=False)
    with patch("gateway.handlers.nami.extract_in_background"):
        yield


# ── Flag 分流 ─────────────────────────────────────────────────────────


def test_flag_off_by_default():
    assert _use_agent_sdk() is False


def test_flag_on(monkeypatch):
    monkeypatch.setenv("NAMI_USE_AGENT_SDK", "1")
    assert _use_agent_sdk() is True


def test_handle_dispatches_to_legacy_when_flag_off():
    handler = NamiHandler()
    with (
        patch.object(handler, "_run_loop", return_value="legacy") as legacy,
        patch.object(handler, "_run_loop_sdk") as sdk,
        patch("gateway.handlers.nami._build_context_preamble", return_value=["ctx"]),
    ):
        assert handler.handle("general", "嗨", "U1") == "legacy"
    legacy.assert_called_once()
    sdk.assert_not_called()


def test_handle_dispatches_to_sdk_when_flag_on(monkeypatch):
    monkeypatch.setenv("NAMI_USE_AGENT_SDK", "1")
    handler = NamiHandler()
    with (
        patch.object(handler, "_run_loop") as legacy,
        patch.object(handler, "_run_loop_sdk", return_value="sdk") as sdk,
        patch("gateway.handlers.nami._build_context_preamble", return_value=["ctx"]),
    ):
        assert handler.handle("general", "嗨", "U1") == "sdk"
    # preamble（日期/記憶注入）與使用者原話同一條路組進 prompt
    sdk.assert_called_once_with("ctx\n\n嗨", "U1")
    legacy.assert_not_called()


# ── Options 組裝 ──────────────────────────────────────────────────────


def test_sdk_options_composition():
    captured: dict = {}
    with (
        patch("gateway.handlers.nami.query", _fake_query(captured, [_result("回覆")])),
        patch("gateway.handlers.nami.get_model", return_value="claude-router-model") as gm,
    ):
        resp = NamiHandler()._run_loop_sdk("列出任務", "U1")

    opts = captured["options"]
    assert opts.tools == []  # 安全紅線（S0-Q1）
    assert set(opts.allowed_tools) == {f"mcp__nami__{s['name']}" for s in NAMI_TOOLS}
    assert len(opts.allowed_tools) == 28  # S3 起含 ask_user
    assert opts.max_turns == 15
    assert opts.max_budget_usd == 1.0
    assert opts.setting_sources == []  # None 會載入全部本機設定
    assert opts.skills == []  # None 不是「關」
    assert opts.settings is None
    assert opts.model == "claude-router-model"
    gm.assert_called_once_with(agent="nami", task="default")
    assert "nami" in opts.mcp_servers
    assert opts.resume is None
    assert "PreToolUse" in opts.hooks  # ask_user 的 defer hook（S3）
    assert opts.hooks["PreToolUse"][0].matcher == "mcp__nami__ask_user"
    assert "ask_user 工具暫不可用" not in opts.system_prompt  # S3 起 ask_user 可用
    assert captured["prompt"] == "列出任務"
    assert resp.text == "回覆"
    # S3：thread 續談靠 session resume — success 也要留 continuation
    assert resp.continuation is not None
    assert resp.continuation.flow_name == NAMI_AGENT_FLOW
    assert resp.continuation.state == {
        "sdk": True,
        "session_id": "sess-test",
        "pending_tool_use_id": None,
    }


def test_budget_env_override(monkeypatch):
    monkeypatch.setenv("NAMI_SDK_MAX_BUDGET_USD", "0.25")
    assert _sdk_budget_usd() == 0.25


def test_budget_env_invalid_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("NAMI_SDK_MAX_BUDGET_USD", "abc")
    assert _sdk_budget_usd() == 1.0


def test_skills_whitelist_env(monkeypatch):
    monkeypatch.setenv("NAMI_SKILLS", "project-bootstrap, keyword-research")
    assert _sdk_skills() == ["project-bootstrap", "keyword-research"]
    monkeypatch.delenv("NAMI_SKILLS", raising=False)
    assert _sdk_skills() == []


def test_auth_env_empty_without_token(monkeypatch):
    """未設 token → 空 dict，SDK 子進程沿用繼承的 ANTHROPIC_API_KEY（零行為改變）。"""
    monkeypatch.delenv("NAMI_SDK_OAUTH_TOKEN", raising=False)
    assert _sdk_auth_env() == {}


def test_auth_env_forces_subscription_and_blanks_api_key(monkeypatch):
    """設了 token → 子進程走 OAuth，且 API key 必須被清空。

    留著 API key 會讓「走訂閱」取決於 CLI 未文件化的優先序 —— 這個斷言就是
    防止日後有人「順手」把清空那行拿掉。
    """
    monkeypatch.setenv("NAMI_SDK_OAUTH_TOKEN", "sk-ant-oat01-xxx")
    env = _sdk_auth_env()
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat01-xxx"
    assert env["ANTHROPIC_API_KEY"] == ""


# ── 終態分支（SDK 對 error result：先 yield ResultMessage 再 raise）──


def test_success_empty_result_says_done():
    captured: dict = {}
    msgs = [_result(text=None, subtype="success")]
    with (
        patch("gateway.handlers.nami.query", _fake_query(captured, msgs)),
        patch("gateway.handlers.nami.get_model", return_value="m"),
    ):
        resp = NamiHandler()._run_loop_sdk("hi", "U1")
    assert resp.text == "完成。"


def test_error_max_turns_maps_to_legacy_message():
    """真實形狀：yield error ResultMessage 後 raise —— 對映 legacy 的超限訊息。"""
    captured: dict = {}
    err = Exception("Claude Code returned an error result: error_max_turns")
    msgs = [_result(text=None, subtype="error_max_turns", is_error=True)]
    with (
        patch("gateway.handlers.nami.query", _fake_query(captured, msgs, raise_after=err)),
        patch("gateway.handlers.nami.get_model", return_value="m"),
    ):
        resp = NamiHandler()._run_loop_sdk("hi", "U1")
    assert resp.text == "已達最大迴圈次數，請重新下指令。"


def test_error_budget_gives_explicit_message():
    captured: dict = {}
    err = Exception("Claude Code returned an error result: error_max_budget_usd")
    msgs = [_result(text=None, subtype="error_max_budget_usd", is_error=True)]
    with (
        patch("gateway.handlers.nami.query", _fake_query(captured, msgs, raise_after=err)),
        patch("gateway.handlers.nami.get_model", return_value="m"),
    ):
        resp = NamiHandler()._run_loop_sdk("hi", "U1")
    assert "成本上限" in resp.text


def test_stream_without_result_message_reports_failure():
    """Stream 正常結束但沒有 ResultMessage —— 不准謊報「完成。」。"""
    captured: dict = {}
    with (
        patch("gateway.handlers.nami.query", _fake_query(captured, [])),
        patch("gateway.handlers.nami.get_model", return_value="m"),
    ):
        resp = NamiHandler()._run_loop_sdk("hi", "U1")
    assert "失敗" in resp.text
    assert resp.text != "完成。"


def test_raw_exception_not_leaked_to_slack():
    """crash 內文只進 log，不進 Slack。"""

    async def boom_query(*, prompt, options):
        raise RuntimeError("CLI died with /secret/path")
        yield  # pragma: no cover — 讓函式是 async generator

    with (
        patch("gateway.handlers.nami.query", boom_query),
        patch("gateway.handlers.nami.get_model", return_value="m"),
    ):
        resp = NamiHandler()._run_loop_sdk("hi", "U1")
    assert "失敗" in resp.text
    assert "CLI died" not in resp.text
    assert "RuntimeError" not in resp.text


# ── S3：ask_user defer / resume ──────────────────────────────────────


def test_deferred_ask_user_returns_question_and_pending_state():
    """defer 終態：問題（含選項 bullet，與 legacy 同格式）回 Slack + pending state。"""
    captured: dict = {}
    deferred = DeferredToolUse(
        id="toolu_x1",
        name="mcp__nami__ask_user",
        input={"question": "content_type 要哪種？", "options": ["youtube", "blog"]},
    )
    msgs = [_result(text="", deferred=deferred, stop_reason="tool_deferred")]
    with (
        patch("gateway.handlers.nami.query", _fake_query(captured, msgs)),
        patch("gateway.handlers.nami.get_model", return_value="m"),
    ):
        resp = NamiHandler()._run_loop_sdk("開個 project", "U1")
    assert resp.text == "content_type 要哪種？\n\n  • youtube\n  • blog"
    assert resp.continuation.state == {
        "sdk": True,
        "session_id": "sess-test",
        "pending_tool_use_id": "toolu_x1",
    }


def test_ask_user_hook_defers_without_answer():
    box: dict = {"value": None}
    hook = _make_ask_user_hook(None, box)
    out = asyncio.run(hook({"tool_input": {"question": "q"}}, "toolu_1", None))
    assert out["hookSpecificOutput"]["permissionDecision"] == "defer"
    assert box["value"] is None


def test_ask_user_hook_allows_once_via_box_then_defers():
    """resume：第一次 allow 並把回覆寫進 in-process box（不經 CLI）；同輪追問 → 再 defer。"""
    box: dict = {"value": None}
    hook = _make_ask_user_hook("走 youtube", box)
    first = asyncio.run(hook({"tool_input": {"question": "q1"}}, "toolu_1", None))
    assert first["hookSpecificOutput"]["permissionDecision"] == "allow"
    # updatedInput 只原樣 pass-through —— 回覆不走 CLI（review M1/M2）
    assert first["hookSpecificOutput"]["updatedInput"] == {"question": "q1"}
    assert "使用者回覆：走 youtube" in box["value"]
    assert "現在時間：" in box["value"]  # 跨日 resume 的日期刷新
    second = asyncio.run(hook({"tool_input": {"question": "q2"}}, "toolu_2", None))
    assert second["hookSpecificOutput"]["permissionDecision"] == "defer"


def test_ask_user_hook_survives_none_input_data():
    box: dict = {"value": None}
    hook = _make_ask_user_hook("ok", box)
    out = asyncio.run(hook(None, "toolu_1", None))
    assert out["hookSpecificOutput"]["updatedInput"] == {}


def test_continue_flow_sdk_pending_resumes_with_answer(monkeypatch):
    monkeypatch.setenv("NAMI_USE_AGENT_SDK", "1")
    handler = NamiHandler()
    state = {"sdk": True, "session_id": "sess-9", "pending_tool_use_id": "toolu_x1"}
    with patch.object(handler, "_run_loop_sdk", return_value="resumed") as sdk:
        out = handler.continue_flow(NAMI_AGENT_FLOW, state, "選 youtube", "U1")
    assert out == "resumed"
    sdk.assert_called_once_with("", "U1", resume_session="sess-9", answer="選 youtube")


def test_continue_flow_sdk_followup_resumes_with_dated_prompt(monkeypatch):
    monkeypatch.setenv("NAMI_USE_AGENT_SDK", "1")
    handler = NamiHandler()
    state = {"sdk": True, "session_id": "sess-9", "pending_tool_use_id": None}
    with patch.object(handler, "_run_loop_sdk", return_value="resumed") as sdk:
        handler.continue_flow(NAMI_AGENT_FLOW, state, "再排一個任務", "U1")
    args, kwargs = sdk.call_args
    assert "現在時間：" in args[0]  # session 歷史的「今天」是凍結的 — 重注日期
    assert args[0].endswith("再排一個任務")
    assert kwargs == {"resume_session": "sess-9"}


def test_continue_flow_sdk_without_session_resets(monkeypatch):
    monkeypatch.setenv("NAMI_USE_AGENT_SDK", "1")
    handler = NamiHandler()
    resp = handler.continue_flow(NAMI_AGENT_FLOW, {"sdk": True}, "hi", "U1")
    assert "流程狀態異常" in resp.text


def test_continue_flow_sdk_flag_off_resets():
    """S5 回滾：旗標關掉後飛行中的 SDK conversation 不准繼續走 SDK 路徑（m6）。"""
    handler = NamiHandler()
    state = {"sdk": True, "session_id": "sess-9", "pending_tool_use_id": None}
    with patch.object(handler, "_run_loop_sdk") as sdk:
        resp = handler.continue_flow(NAMI_AGENT_FLOW, state, "hi", "U1")
    sdk.assert_not_called()
    assert "已重置" in resp.text


def test_continue_flow_legacy_state_untouched():
    """state 沒標 sdk → 走 legacy messages 路徑，S3 不影響飛行中的舊 conversation。"""
    handler = NamiHandler()
    state = {"messages": [{"role": "user", "content": "舊對話"}], "pending_tool_use_id": None}
    with (
        patch.object(handler, "_run_loop", return_value="legacy") as legacy,
        patch.object(handler, "_run_loop_sdk") as sdk,
        patch("gateway.handlers.nami._refresh_context_preamble", side_effect=lambda m, u: m),
    ):
        assert handler.continue_flow(NAMI_AGENT_FLOW, state, "繼續", "U1") == "legacy"
    legacy.assert_called_once()
    sdk.assert_not_called()


def test_tool_deferred_unavailable_reports_invalid_state():
    captured: dict = {}
    msgs = [_result(text=None, is_error=True, stop_reason="tool_deferred_unavailable")]
    with (
        patch("gateway.handlers.nami.query", _fake_query(captured, msgs)),
        patch("gateway.handlers.nami.get_model", return_value="m"),
    ):
        resp = NamiHandler()._run_loop_sdk("", "U1", resume_session="sess-9", answer="x")
    assert "已失效" in resp.text
    assert captured["options"].resume == "sess-9"


def test_first_result_message_wins_and_stream_drained():
    """取第一個 ResultMessage 為終態，但 stream 要 drain 到自然結束 ——
    break 會把 CLI 子進程清理丟給 GC（review B1）。"""
    captured: dict = {}
    msgs = [_result(text="真結果"), _result(text="第二個 result 的雜訊")]
    with (
        patch("gateway.handlers.nami.query", _fake_query(captured, msgs)),
        patch("gateway.handlers.nami.get_model", return_value="m"),
    ):
        resp = NamiHandler()._run_loop_sdk("hi", "U1")
    assert resp.text == "真結果"
    assert captured.get("exhausted") is True  # 沒有 break


def test_answer_resume_uses_empty_prompt_stream():
    """answer 路徑不產生新 user message：prompt 是空的 async stream，不是字串。"""
    captured: dict = {}
    with (
        patch("gateway.handlers.nami.query", _fake_query(captured, [_result("好的")])),
        patch("gateway.handlers.nami.get_model", return_value="m"),
    ):
        resp = NamiHandler()._run_loop_sdk("", "U1", resume_session="sess-9", answer="選 youtube")
    assert resp.text == "好的"
    assert captured["options"].resume == "sess-9"
    prompt = captured["prompt"]
    assert not isinstance(prompt, str)
    assert hasattr(prompt, "__anext__")  # async generator（零則訊息）


def test_server_built_with_ask_user_and_shared_answer_box():
    """MCP server 以 include_ask_user=True 建立，且 answer_box 與 hook 同一個物件（n5）。"""
    captured: dict = {}
    with (
        patch("gateway.handlers.nami.query", _fake_query(captured, [_result("ok")])),
        patch("gateway.handlers.nami.get_model", return_value="m"),
        patch("gateway.handlers.nami_tools.build_nami_server", return_value={"type": "sdk"}) as bs,
    ):
        NamiHandler()._run_loop_sdk("hi", "U1")
    _, kwargs = bs.call_args
    assert kwargs["include_ask_user"] is True
    assert isinstance(kwargs["answer_box"], dict)


def test_format_ask_user_question_without_options():
    assert _format_ask_user_question({"question": "何時？"}) == "何時？"


# ── auto memory settings（裁決：VPS 獨立目錄；inline JSON）────────────


def test_settings_none_without_env():
    assert _sdk_settings() is None


def test_settings_inline_json_and_dir_created(monkeypatch, tmp_path):
    target = tmp_path / "nami-auto-memory"
    monkeypatch.setenv("NAMI_AUTO_MEMORY_DIR", str(target))
    settings = _sdk_settings()
    assert settings is not None
    assert json.loads(settings) == {"autoMemoryDirectory": str(target)}
    assert target.is_dir()  # 目錄先建好，CLI 首寫不會撞不存在的路徑
    assert not any(target.iterdir())  # inline JSON：不在目錄裡留設定檔


def test_settings_dir_is_path_object_safe(monkeypatch, tmp_path):
    """env 帶尾斜線等雜訊時，寫進 JSON 的是正規化路徑。"""
    target = tmp_path / "mem"
    monkeypatch.setenv("NAMI_AUTO_MEMORY_DIR", str(target) + "\\")
    settings = _sdk_settings()
    assert json.loads(settings)["autoMemoryDirectory"] == str(Path(str(target) + "\\"))
