"""Tests for the S2 Agent SDK path in gateway/handlers/nami.py.

不打真 API — patch `gateway.handlers.nami.query` 驗證：
- flag 分流（NAMI_USE_AGENT_SDK）
- ClaudeAgentOptions 組裝（tools=[] 紅線 / allowed_tools 27 條 / setting_sources
  明確空 / skills 白名單 / budget env / model 走 router）
- ResultMessage → HandlerResponse 的萃取與錯誤路徑
- auto memory settings 檔生成（2026-07-29 裁決：VPS 獨立目錄）
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
    _sdk_settings_path,
    _sdk_skills,
    _use_agent_sdk,
)


def _result(text: str | None = "done", subtype: str = "success") -> ResultMessage:
    return ResultMessage(
        subtype=subtype,
        duration_ms=10,
        duration_api_ms=8,
        is_error=False,
        num_turns=2,
        session_id="sess-test",
        total_cost_usd=0.01,
        result=text,
        terminal_reason="completed",
    )


def _fake_query(captured: dict, messages: list):
    async def fake_query(*, prompt, options):
        captured["prompt"] = prompt
        captured["options"] = options
        for m in messages:
            yield m

    return fake_query


@pytest.fixture(autouse=True)
def _no_background_extractor():
    with patch("gateway.handlers.nami.extract_in_background"):
        yield


# ── Flag 分流 ─────────────────────────────────────────────────────────


def test_flag_off_by_default(monkeypatch):
    monkeypatch.delenv("NAMI_USE_AGENT_SDK", raising=False)
    assert _use_agent_sdk() is False


def test_flag_on(monkeypatch):
    monkeypatch.setenv("NAMI_USE_AGENT_SDK", "1")
    assert _use_agent_sdk() is True


def test_handle_dispatches_to_legacy_when_flag_off(monkeypatch):
    monkeypatch.delenv("NAMI_USE_AGENT_SDK", raising=False)
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
    # preamble 與使用者原話同一條路組進 prompt
    sdk.assert_called_once_with("ctx\n\n嗨", "U1")
    legacy.assert_not_called()


# ── Options 組裝 ──────────────────────────────────────────────────────


def test_sdk_options_composition(monkeypatch):
    monkeypatch.delenv("NAMI_SDK_MAX_BUDGET_USD", raising=False)
    monkeypatch.delenv("NAMI_AUTO_MEMORY_DIR", raising=False)
    monkeypatch.delenv("NAMI_SKILLS", raising=False)
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
    captured: dict = {}
    with (
        patch("gateway.handlers.nami.query", _fake_query(captured, [_result()])),
        patch("gateway.handlers.nami.get_model", return_value="m"),
    ):
        NamiHandler()._run_loop_sdk("hi", "U1")
    assert captured["options"].max_budget_usd == 0.25


def test_skills_whitelist_env(monkeypatch):
    monkeypatch.setenv("NAMI_SKILLS", "project-bootstrap, keyword-research")
    assert _sdk_skills() == ["project-bootstrap", "keyword-research"]
    monkeypatch.delenv("NAMI_SKILLS", raising=False)
    assert _sdk_skills() == []


# ── Result 萃取與錯誤路徑 ─────────────────────────────────────────────


def test_error_subtype_falls_back_to_default_text(monkeypatch):
    captured: dict = {}
    msgs = [_result(text=None, subtype="error_during_execution")]
    with (
        patch("gateway.handlers.nami.query", _fake_query(captured, msgs)),
        patch("gateway.handlers.nami.get_model", return_value="m"),
    ):
        resp = NamiHandler()._run_loop_sdk("hi", "U1")
    assert resp.text == "完成。"


def test_query_exception_returns_handler_response(monkeypatch):
    async def boom_query(*, prompt, options):
        raise RuntimeError("CLI died")
        yield  # pragma: no cover — 讓函式是 async generator

    with (
        patch("gateway.handlers.nami.query", boom_query),
        patch("gateway.handlers.nami.get_model", return_value="m"),
    ):
        resp = NamiHandler()._run_loop_sdk("hi", "U1")
    assert "Agent SDK 路徑失敗" in resp.text
    assert "RuntimeError" in resp.text


# ── auto memory settings 檔（裁決：VPS 獨立目錄）──────────────────────


def test_settings_path_none_without_env(monkeypatch):
    monkeypatch.delenv("NAMI_AUTO_MEMORY_DIR", raising=False)
    assert _sdk_settings_path() is None


def test_settings_file_written_inside_auto_memory_dir(monkeypatch, tmp_path):
    target = tmp_path / "nami-auto-memory"
    monkeypatch.setenv("NAMI_AUTO_MEMORY_DIR", str(target))
    path = _sdk_settings_path()
    assert path is not None
    assert Path(path).parent == target
    content = json.loads(Path(path).read_text(encoding="utf-8"))
    assert content == {"autoMemoryDirectory": str(target)}
    # 第二次呼叫 idempotent（內容相同不重寫，回同一路徑）
    assert _sdk_settings_path() == path
