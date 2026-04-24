"""gateway/bot.py — multi-bot registry 測試。

重點：`_discover_bots` 從 env 抓 agent bot 組合正確、缺 token 不啟動、
`run()` 無任何 token 時 fail fast。

不實打 Slack SDK — `create_app` 需要真 token pass validation，我們測
`_discover_bots` + `_create_bot_app` 的註冊層。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _clean_slack_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """每個測試清掉 *_SLACK_BOT_TOKEN / *_SLACK_APP_TOKEN，避免本機 .env 污染。"""
    import os

    for key in list(os.environ.keys()):
        if key.endswith("_SLACK_BOT_TOKEN") or key.endswith("_SLACK_APP_TOKEN"):
            monkeypatch.delenv(key, raising=False)


def test_discover_bots_empty_when_no_tokens():
    from gateway.bot import _discover_bots

    assert _discover_bots() == []


def test_discover_bots_picks_up_nami(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NAMI_SLACK_BOT_TOKEN", "xoxb-nami")
    monkeypatch.setenv("NAMI_SLACK_APP_TOKEN", "xapp-nami")

    from gateway.bot import _discover_bots

    bots = _discover_bots()
    assert bots == [("nami", "xoxb-nami", "xapp-nami")]


def test_discover_bots_multi_agent_order(monkeypatch: pytest.MonkeyPatch):
    """多 agent 按 agent_name 字母排序 — 確保 log/啟動順序穩定。"""
    monkeypatch.setenv("NAMI_SLACK_BOT_TOKEN", "xoxb-nami")
    monkeypatch.setenv("NAMI_SLACK_APP_TOKEN", "xapp-nami")
    monkeypatch.setenv("SANJI_SLACK_BOT_TOKEN", "xoxb-sanji")
    monkeypatch.setenv("SANJI_SLACK_APP_TOKEN", "xapp-sanji")

    from gateway.bot import _discover_bots

    bots = _discover_bots()
    assert [a for a, _, _ in bots] == ["nami", "sanji"]


def test_discover_bots_skips_half_set_token(monkeypatch: pytest.MonkeyPatch):
    """只設 bot_token 沒設 app_token 的 agent 不算上線。"""
    monkeypatch.setenv("NAMI_SLACK_BOT_TOKEN", "xoxb-nami")
    monkeypatch.setenv("NAMI_SLACK_APP_TOKEN", "xapp-nami")
    monkeypatch.setenv("SANJI_SLACK_BOT_TOKEN", "xoxb-sanji")
    # SANJI_SLACK_APP_TOKEN 故意不設

    from gateway.bot import _discover_bots

    bots = _discover_bots()
    assert [a for a, _, _ in bots] == ["nami"]


def test_discover_bots_ignores_whitespace_only(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NAMI_SLACK_BOT_TOKEN", "   ")
    monkeypatch.setenv("NAMI_SLACK_APP_TOKEN", "xapp-nami")

    from gateway.bot import _discover_bots

    assert _discover_bots() == []


def test_discover_bots_skips_agent_without_handler(monkeypatch: pytest.MonkeyPatch):
    """Token 齊了但 gateway/handlers registry 沒註冊 → skip，不啟動空殼 bot。"""
    monkeypatch.setenv("BROOK_SLACK_BOT_TOKEN", "xoxb-brook")
    monkeypatch.setenv("BROOK_SLACK_APP_TOKEN", "xapp-brook")

    from gateway.bot import _discover_bots

    assert _discover_bots() == []


def test_run_raises_when_no_bots_configured():
    from gateway import bot

    with (
        patch("gateway.bot.load_config"),
        pytest.raises(RuntimeError, match="沒有任何 agent 的 Slack token"),
    ):
        bot.run()


def test_create_bot_app_registers_agent_specific_slash_command(monkeypatch: pytest.MonkeyPatch):
    """每個 bot 至少註冊自己的 /<agent> slash command；Nami 繼承早期全家。"""
    import sys
    import types

    registered_commands: list[str] = []

    class FakeApp:
        def __init__(self, *a, **kw):
            pass

        def command(self, cmd):
            registered_commands.append(cmd)

            def _decorator(fn):
                return fn

            return _decorator

        def event(self, name):
            def _decorator(fn):
                return fn

            return _decorator

    fake_module = types.ModuleType("slack_bolt")
    fake_module.App = FakeApp  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "slack_bolt", fake_module)

    from gateway import bot

    # Sanji bot — 只應註冊 /sanji
    registered_commands.clear()
    bot._create_bot_app("sanji", "xoxb-sanji")
    assert registered_commands == ["/sanji"]

    # Nami bot — 繼承早期全掛的 slash commands + 自己的 /nami
    registered_commands.clear()
    bot._create_bot_app("nami", "xoxb-nami")
    assert "/nami" in registered_commands
    assert "/nakama" in registered_commands
    assert "/zoro" in registered_commands  # 尚未搬到 Zoro app
    assert "/brook" in registered_commands
