"""Tests for shared/agent_sdk.py (merger SDK migration S1).

行為契約比照 tests/gateway/test_nami_sdk_loop.py 的 _sdk_auth_env 測試 —
這兩組測試共同鎖住「訂閱覆寫必須同時清空 API key」的語意，S4 收斂時
nami 版 delegate 到 shared 版，兩組測試都不可刪。
"""

from __future__ import annotations

from shared.agent_sdk import subscription_env


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
