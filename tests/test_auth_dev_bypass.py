"""Tests for NAKAMA_DEV_AUTH_BYPASS env-gated auth short-circuit."""

import importlib

import pytest


def _reload_auth():
    import thousand_sunny.auth as auth_module

    importlib.reload(auth_module)
    return auth_module


def test_bypass_off_by_default(monkeypatch):
    monkeypatch.delenv("NAKAMA_DEV_AUTH_BYPASS", raising=False)
    monkeypatch.setenv("WEB_PASSWORD", "x")
    monkeypatch.setenv("WEB_SECRET", "y")
    auth = _reload_auth()
    assert auth.check_auth(None) is False
    assert auth.check_auth("garbage") is False
    assert auth.check_key(None) is False
    assert auth.check_key("nope") is False


@pytest.mark.parametrize("flag", ["1", "true", "yes", "TRUE", "Yes"])
def test_bypass_short_circuits_check_auth(monkeypatch, flag):
    monkeypatch.setenv("NAKAMA_DEV_AUTH_BYPASS", flag)
    monkeypatch.setenv("WEB_PASSWORD", "x")
    monkeypatch.setenv("WEB_SECRET", "y")
    auth = _reload_auth()
    assert auth.check_auth(None) is True
    assert auth.check_auth("anything") is True
    assert auth.check_key(None) is True
    assert auth.check_key("anything") is True


@pytest.mark.parametrize("flag", ["", "0", "false", "no", "random"])
def test_bypass_falsey_values_dont_trigger(monkeypatch, flag):
    monkeypatch.setenv("NAKAMA_DEV_AUTH_BYPASS", flag)
    monkeypatch.setenv("WEB_PASSWORD", "x")
    monkeypatch.setenv("WEB_SECRET", "y")
    auth = _reload_auth()
    assert auth.check_auth(None) is False
