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
# 正式環境沒設 WEB_PASSWORD 會 fail closed（thousand_sunny.auth.open_auth_allowed）。大量
# route 測試靠「不設密碼＝放行」直接打 handler，這裡統一 opt-in；測 fail closed 的測試自己
# monkeypatch.delenv 掉。
os.environ.setdefault("NAKAMA_ALLOW_OPEN_AUTH", "1")


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
def _no_weekly_autotasks(request, monkeypatch):
    """Opening a current/future week on the Weekly page auto-creates that week's
    「WXX 電子報」 task (shared.weekly_newsletter). Hundreds of dashboard tests GET
    that page against a tmp vault and assert on its task list — keep it off unless
    a test opts in with ``@pytest.mark.weekly_autotask``."""
    if request.node.get_closest_marker("weekly_autotask"):
        return
    monkeypatch.setattr("shared.weekly_newsletter.ENABLED", False)


# 這些模組用 ``from ... import probe_stock_video`` 綁了自己的 reference，
# 要跟 highlight_broll 本身一起換掉。新增 consumer 時同步補（漏補只會變慢，不會錯）。
_FFPROBE_CONSUMER_MODULES = (
    "agents.brook.script_video.highlight_broll",
    "agents.brook.script_video.highlight_candidate_renderer",
    "agents.brook.script_video.highlight_visual_pipeline",
    "shared.shortform_broll",
)


@pytest.fixture(autouse=True)
def _memoize_ffprobe_per_test(request, monkeypatch):
    """同一個測試內，對「同路徑＋同 bytes」的影片只跑一次真的 ffprobe。

    highlight visual pipeline 每次 fresh-load 都會把歷史上每支影片重新 ffprobe
    一遍；一個測試走完整條 Director → DP → audit 流程會 spawn 四千多次 ffprobe
    （CI 上 test_highlight_visual_pipeline.py 一個檔就 ~9 分鐘）。ffprobe 對同一份
    bytes 的輸出是確定的，所以 key 用 (resolved path, sha256(bytes))：檔案被竄改或
    換掉，hash 就不同、照樣重跑真的 ffprobe——fail-closed 的測試語意不變。

    - 只快取成功結果；失敗（BrollContractError 等）每次都重跑，錯誤訊息不會串味。
    - cache 每個測試一份（function scope），不跨測試。
    - 測試自己 monkeypatch ``probe_stock_video`` 時，它的 patch 在這之後套用、優先。
    - 要測「每次都真的呼叫 ffprobe」的測試標 ``@pytest.mark.real_ffprobe`` 跳過。
    """
    broll = sys.modules.get("agents.brook.script_video.highlight_broll")
    if broll is None or request.node.get_closest_marker("real_ffprobe"):
        return
    import copy
    import hashlib

    original = broll.probe_stock_video
    cache: dict[tuple[str, str], dict] = {}

    def probe(path):
        resolved = Path(path).resolve()
        try:
            digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
        except OSError:
            return original(path)
        key = (str(resolved), digest)
        if key not in cache:
            cache[key] = original(path)
        return copy.deepcopy(cache[key])

    for name in _FFPROBE_CONSUMER_MODULES:
        module = sys.modules.get(name)
        if module is not None and getattr(module, "probe_stock_video", None) is original:
            monkeypatch.setattr(module, "probe_stock_video", probe)


@pytest.fixture(autouse=True)
def _memoize_opencc_per_test(monkeypatch):
    """同一個測試內，OpenCC 字典只 parse 一次、converter 只建一次。

    podcast_subtitles 的 orthographic projection 每次投影／replay 都重新 parse
    數 MB 的 OpenCC 字典、重建 converter（verified-projection 測試一半時間在這）。

    為什麼放測試端而不改 ``orthographic_projection.py``：那個檔案的 bytes hash
    就是投影 identity 的 ``adapter_code_hash``，``verify_orthographic_projection``
    replay 時要求完全一致——改檔會讓正式環境既有的投影全部驗不過。

    - 字典 cache key = (路徑, sha256(bytes))：每次照樣讀檔，bytes 變了就重新 parse。
    - converter cache 每個測試一份（安裝的 OpenCC 套件在測試期間不會變）。
    """
    projection = sys.modules.get("agents.brook.podcast_subtitles.orthographic_projection")
    if projection is None:
        return
    import hashlib

    original_read = projection._read_dictionary
    original_opencc = projection.OpenCC
    dictionaries: dict[tuple[str, str], dict] = {}
    converters: dict[str, object] = {}

    def read_dictionary(path):
        raw = Path(path).read_bytes()
        key = (str(path), hashlib.sha256(raw).hexdigest())
        if key not in dictionaries:
            dictionaries[key] = original_read(path)
        return dictionaries[key]

    def opencc(conversion):
        if conversion not in converters:
            converters[conversion] = original_opencc(conversion)
        return converters[conversion]

    monkeypatch.setattr(projection, "_read_dictionary", read_dictionary)
    monkeypatch.setattr(projection, "OpenCC", opencc)


@pytest.fixture(autouse=True)
def _isolated_incidents_pending(tmp_path: Path, monkeypatch):
    """Route shared.incident_archive default dir to tmp.

    Without this, any test that fires `shared.alerts.alert("error", ...)` or
    `agents.franky.alert_router.dispatch(critical_alert)` writes a real .md
    into the repo's `data/incidents-pending/` and pollutes git status.
    """
    monkeypatch.setenv("NAKAMA_INCIDENTS_PENDING_DIR", str(tmp_path / "_incidents-pending"))


@pytest.fixture(autouse=True)
def _isolated_runtime_group(monkeypatch):
    """ADR-070：runtime group 是 process 全域值（``shared.llm_context``）。

    Thousand Sunny 的 lifespan 會把它設成 ``bridge``；TestClient 一驅動 lifespan，
    後面所有測試都會看到 ``bridge``。每個測試開始時重設成預設值、結束時還原，
    facade 的 L1 分派測試才不會因為執行順序而變。
    """
    from shared import llm_context

    monkeypatch.setattr(llm_context, "_runtime_group", llm_context.DEFAULT_RUNTIME_GROUP)


@pytest.fixture(autouse=True)
def _lane_authority_is_vps(monkeypatch):
    """ADR-070 D5：``shared.llm_lane._is_lane_authority`` 依 ``sys.platform`` 判斷
    （VPS Linux = 權威、Windows 桌機 = 唯讀）。測試預設模擬 VPS，讓結果不隨執行
    平台改變（CI 是 Linux、修修的桌機是 Windows）；要測桌機行為的測試自己
    ``monkeypatch.setattr(llm_lane, "_is_lane_authority", lambda: False)`` 覆寫。
    """
    from shared import llm_lane

    monkeypatch.setattr(llm_lane, "_is_lane_authority", lambda: True)


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
