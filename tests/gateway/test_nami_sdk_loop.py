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

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from claude_agent_sdk import ResultMessage

from gateway.handlers.nami import (
    NAMI_TOOLS,
    NamiHandler,
    _sdk_budget_usd,
    _sdk_settings,
    _sdk_skills,
    _use_agent_sdk,
)


def _result(
    text: str | None = "done",
    subtype: str = "success",
    is_error: bool = False,
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
    )


def _fake_query(captured: dict, messages: list, raise_after: Exception | None = None):
    async def fake_query(*, prompt, options):
        captured["prompt"] = prompt
        captured["options"] = options
        for m in messages:
            yield m
        if raise_after is not None:
            raise raise_after

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
    assert set(opts.allowed_tools) == {
        f"mcp__nami__{s['name']}" for s in NAMI_TOOLS if s["name"] != "ask_user"
    }
    assert len(opts.allowed_tools) == 27
    assert opts.max_turns == 15
    assert opts.max_budget_usd == 1.0
    assert opts.setting_sources == []  # None 會載入全部本機設定
    assert opts.skills == []  # None 不是「關」
    assert opts.settings is None
    assert opts.model == "claude-router-model"
    gm.assert_called_once_with(agent="nami", task="default")
    assert "nami" in opts.mcp_servers
    assert "ask_user 工具暫不可用" in opts.system_prompt
    assert captured["prompt"] == "列出任務"
    assert resp.text == "回覆"
    assert resp.continuation is None  # S3 前不留 continuation


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
