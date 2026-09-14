"""Shared test fixtures."""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# Module-level: disable the SQLite log sink BEFORE any test module imports
# `shared.log` and triggers handler setup. Tests that exercise the handler
# itself (`test_log_handler.py`) explicitly opt out via monkeypatch.
os.environ.setdefault("NAKAMA_LOG_DB_DISABLE", "1")


@pytest.fixture(autouse=True)
def _prevent_real_memory_extraction(request, monkeypatch):
    """Stop background Haiku calls during tests.

    The gateway handler triggers ``extract_in_background`` on ``end_turn``.
    Without this fixture, any test that reaches end_turn would spawn a daemon
    thread making real API calls (costing money + flaky).

    Tests that specifically exercise the real extractor should mark themselves
    with ``@pytest.mark.real_extractor``.
    """
    if request.node.get_closest_marker("real_extractor"):
        return
    monkeypatch.setattr(
        "shared.memory_extractor.extract_in_background",
        MagicMock(return_value=MagicMock(is_alive=lambda: False)),
    )
    try:
        import gateway.handlers.nami as nami

        monkeypatch.setattr(nami, "extract_in_background", MagicMock())
    except ImportError:
        pass


@pytest.fixture(autouse=True)
def _prevent_real_slack_alerts(request, monkeypatch):
    """Stop ``shared.alerts.alert("error", ...)`` from DMing real Slack.

    ``_send_slack`` lazy-imports ``FrankySlackBot.from_env``. On dev machines
    where ``SLACK_FRANKY_BOT_TOKEN`` + ``SLACK_USER_ID_SHOSHO`` are set, any
    failure-path test that exercises a backup/cron script silently posts to
    the user's production DM (e.g. pytest tmp_path leaking via "data dir
    missing: ...pytest-of-Shosho/...").

    Default: redirect ``from_env`` to the no-op stub. Tests that want to
    assert post arguments stack their own patch (see
    ``tests/shared/test_alerts.py::fake_slack``).

    Tests that need real Slack delivery should mark themselves with
    ``@pytest.mark.real_slack``.
    """
    if request.node.get_closest_marker("real_slack"):
        return
    from agents.franky.slack_bot import _NoopSlackStub

    monkeypatch.setattr(
        "agents.franky.slack_bot.FrankySlackBot.from_env",
        MagicMock(return_value=_NoopSlackStub()),
    )


@pytest.fixture(autouse=True)
def _prevent_real_google_calendar(request, monkeypatch):
    """Stop tests from touching the REAL Google Calendar.

    All calendar CRUD (create/update/delete/list_events) goes through the one
    chokepoint ``shared.google_calendar._get_service``. On a box WITHOUT a token
    that raised ``GoogleCalendarAuthError`` → ``schedule_entry`` returned
    UNAVAILABLE, so unmocked tests were side-effect-free *by accident*. The
    moment a real token lands in ``data/`` (e.g. copied in to run the dev
    server) those same unmocked tests start writing real events — observed:
    all-day "測試任務" events appearing on the user's calendar on the test
    dates. Patch the service chokepoint to reproduce the safe no-token
    behaviour regardless of whether a token file exists.

    Tests that mock ``create_event``/``list_events`` directly are unaffected
    (their patch is applied after this and wins). Tests that genuinely need the
    live API mark themselves ``@pytest.mark.real_calendar``.
    """
    if request.node.get_closest_marker("real_calendar"):
        return
    from shared.google_calendar import GoogleCalendarAuthError

    def _no_service(*_a, **_k):
        raise GoogleCalendarAuthError("calendar disabled in tests (no real_calendar marker)")

    monkeypatch.setattr("shared.google_calendar._get_service", _no_service)


@pytest.fixture(autouse=True)
def _no_subscription_dispatch_in_tests(request, monkeypatch):
    """Stop tests from dispatching real ``claude -p`` subprocesses.

    ADR-026 Amendment 2026-08-19 後 ``DEFAULT_AUTH["default"]`` 是
    ``subscription_preferred``。dev 機器上 ``~/.claude/.credentials.json`` 與
    ``claude`` binary 通常都在 → 未 mock 到 dispatch 層的測試會真的起 CLI
    子進程（v3 Implementation deviation 記載的原始痛點，當年為此把預設留在
    api）。這裡強制測試環境「無訂閱條件」→ ``subscription_preferred`` 軟降
    api，測試行為與 flip 前逐位元相同。

    CLI / dispatch 路徑自身的測試（test_claude_cli_client、
    test_anthropic_auth_dispatch 等）在測試內自行 patch 這兩個函式 —— 測試層
    的 patch 在本 fixture 之後套用、會蓋過它，不受影響（同
    ``_prevent_real_google_calendar`` 的分層語意）。真要打訂閱的測試標
    ``@pytest.mark.real_subscription``。
    """
    if request.node.get_closest_marker("real_subscription"):
        return
    monkeypatch.setattr("shared.anthropic_client._oauth_token_available", lambda: False)


@pytest.fixture(autouse=True)
def _isolated_incidents_pending(tmp_path: Path, monkeypatch):
    """Route shared.incident_archive default dir to tmp.

    Without this, any test that fires `shared.alerts.alert("error", ...)` or
    `agents.franky.alert_router.dispatch(critical_alert)` writes a real .md
    into the repo's `data/incidents-pending/` and pollutes git status.
    """
    monkeypatch.setenv("NAKAMA_INCIDENTS_PENDING_DIR", str(tmp_path / "_incidents-pending"))


_LLM_FACADE_FUNCS = ("ask", "ask_multi", "ask_with_tools", "ask_with_audio")

# caller-binding sweep 只掃 repo 自己的 top-level packages（全部
# `from shared.llm import ...` 的檔案都在這些底下 — 加新 top-level package
# 且其中有 facade caller 時要同步補）。全掃 sys.modules（~1400 模組）每次
# install 要 ~7ms，這個 filter 砍掉 ~98% 純浪費。
_LLM_SWEEP_TOP_PACKAGES = ("agents", "gateway", "scripts", "shared", "tests", "thousand_sunny")


@pytest.fixture
def mock_llm_response(monkeypatch):
    """Facade-level LLM mock — patch ``shared.llm.ask*`` 而非 provider client 內部。

    2026-07-03 架構審計：業務測試 mock 在 ``shared.anthropic_client.get_client``
    （實作層）會讓 LLM stack 內部 refactor（router / auth dispatch / transport）
    弄壞不相干的測試，而該測的 wiring 反而測不到。``shared.llm`` facade 才是
    test surface — 業務邏輯測試一律用這個 fixture；只有刻意測 client / router
    層本身的測試（test_llm_*, test_*_client*）才 mock provider 內部。

    用法::

        def test_x(mock_llm_response):
            llm = mock_llm_response("回應文字")
            run_production_code()
            llm.ask.assert_called_once()
            assert "關鍵詞" in llm.ask.call_args.args[0]          # prompt
            assert llm.ask.call_args.kwargs["model"] == "..."

    - ``text``：``ask`` / ``ask_multi`` / ``ask_with_audio`` 的回傳值；
      ``ask_with_tools`` 每次呼叫回傳一個「新的」stub Message
      （content=[text block]、stop_reason="end_turn"、usage 全 0）—
      per-call fresh 避免 production code mutate 後污染同測試內後續呼叫。
    - ``side_effect``：exception（或 callable / list）模擬 LLM 失敗，
      四個介面共用。
    - ``from shared.llm import ask`` 的 caller 持有自己的 module-level
      binding（feedback_facade_mock_caller_binding.md）— 這裡以 identity
      掃 ``sys.modules`` 一併 patch；install 之後才 import 的模組拿到的
      是已 patch 的 ``shared.llm`` attr，兩種順序都安全。
    """
    import shared.llm as llm_module

    def _install(text: str = "", *, side_effect=None) -> SimpleNamespace:
        originals = {name: getattr(llm_module, name) for name in _LLM_FACADE_FUNCS}

        def _fresh_tool_message(*_args, **_kwargs) -> SimpleNamespace:
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text=text)],
                stop_reason="end_turn",
                usage=SimpleNamespace(
                    input_tokens=0,
                    output_tokens=0,
                    cache_read_input_tokens=0,
                    cache_creation_input_tokens=0,
                ),
            )

        mocks = SimpleNamespace(
            ask=MagicMock(return_value=text, side_effect=side_effect),
            ask_multi=MagicMock(return_value=text, side_effect=side_effect),
            ask_with_tools=MagicMock(
                side_effect=side_effect if side_effect is not None else _fresh_tool_message
            ),
            ask_with_audio=MagicMock(return_value=text, side_effect=side_effect),
        )
        for name in _LLM_FACADE_FUNCS:
            monkeypatch.setattr(llm_module, name, getattr(mocks, name))
        for mod_name, module in list(sys.modules.items()):
            if module is None or module is llm_module:
                continue
            if mod_name.split(".", 1)[0] not in _LLM_SWEEP_TOP_PACKAGES:
                continue
            module_dict = getattr(module, "__dict__", None)
            if not module_dict:
                continue
            for attr, value in list(module_dict.items()):
                for name in _LLM_FACADE_FUNCS:
                    if value is originals[name]:
                        monkeypatch.setattr(module, attr, getattr(mocks, name))
        return mocks

    return _install


@pytest.fixture(autouse=True)
def isolated_db(tmp_path: Path, monkeypatch):
    """Route shared.state to a temporary SQLite DB per test.

    Autouse so every test hits a tmp DB — real config points to
    /home/nakama/data/state.db which doesn't exist on CI runners.
    """
    db_path = tmp_path / "test.db"
    import shared.state as state

    monkeypatch.setattr(state, "get_db_path", lambda: db_path)

    if state._conn is not None:
        try:
            state._conn.close()
        except sqlite3.Error:
            pass
    state._conn = None

    from shared import agent_memory, candidate_inbox, episodic_memory, pushed_topics

    agent_memory._SCHEMA_INITIALIZED = False
    candidate_inbox._SCHEMA_INITIALIZED = False
    episodic_memory._SCHEMA_INITIALIZED = False
    pushed_topics._SCHEMA_INITIALIZED = False

    yield db_path

    if state._conn is not None:
        try:
            state._conn.close()
        except sqlite3.Error:
            pass
        state._conn = None
