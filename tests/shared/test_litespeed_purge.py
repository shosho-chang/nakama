"""Tests for shared.litespeed_purge (ADR-005b §5)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from shared.litespeed_purge import purge_url
from shared.wordpress_client import WPAuthError, WPClientError, WPServerError


@pytest.fixture
def no_env(monkeypatch):
    """Clear any inherited LITESPEED_* env vars so tests are deterministic."""
    for var in ("LITESPEED_PURGE_METHOD", "LITESPEED_PURGE_TIMEOUT"):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# method=noop
# ---------------------------------------------------------------------------


def test_noop_returns_false_without_calling_client(no_env):
    client = MagicMock()
    result = purge_url(
        "https://shosho.tw/post/1",
        wp_client=client,
        method="noop",
        operation_id="op_12345678",
    )
    assert result is False
    client._request.assert_not_called()


def test_env_noop_is_honored(monkeypatch):
    monkeypatch.setenv("LITESPEED_PURGE_METHOD", "noop")
    client = MagicMock()
    result = purge_url(
        "https://shosho.tw/post/1",
        wp_client=client,
        operation_id="op_12345678",
    )
    assert result is False
    client._request.assert_not_called()


# ---------------------------------------------------------------------------
# method=admin_ajax (documented but not implemented in Phase 1)
# ---------------------------------------------------------------------------


def test_admin_ajax_returns_false_without_calling_client(no_env):
    client = MagicMock()
    result = purge_url(
        "https://shosho.tw/post/1",
        wp_client=client,
        method="admin_ajax",
        operation_id="op_12345678",
    )
    assert result is False
    client._request.assert_not_called()


# ---------------------------------------------------------------------------
# method=rest
# ---------------------------------------------------------------------------


def test_rest_happy_path(no_env):
    client = MagicMock()
    client._request.return_value = {"ok": True}
    result = purge_url(
        "https://shosho.tw/post/1",
        wp_client=client,
        method="rest",
        operation_id="op_12345678",
    )
    assert result is True
    client._request.assert_called_once()
    args, kwargs = client._request.call_args
    assert args[0] == "POST"
    assert args[1] == "litespeed/v1/purge"
    assert kwargs["json"] == {"url": "https://shosho.tw/post/1"}


@pytest.mark.parametrize(
    "exc",
    [
        WPAuthError("401"),
        WPClientError("404 not found"),
        WPServerError("503 unavailable"),
    ],
)
def test_rest_swallows_expected_wp_errors(no_env, exc):
    client = MagicMock()
    client._request.side_effect = exc
    result = purge_url(
        "https://shosho.tw/post/1",
        wp_client=client,
        method="rest",
        operation_id="op_12345678",
    )
    assert result is False


def test_rest_requires_wp_client(no_env):
    # No wp_client provided → log + False, no crash.
    result = purge_url(
        "https://shosho.tw/post/1",
        method="rest",
        operation_id="op_12345678",
    )
    assert result is False


# ---------------------------------------------------------------------------
# Env resolution
# ---------------------------------------------------------------------------


def test_env_default_is_rest(monkeypatch):
    monkeypatch.delenv("LITESPEED_PURGE_METHOD", raising=False)
    client = MagicMock()
    client._request.return_value = {"ok": True}
    result = purge_url(
        "https://shosho.tw/post/1",
        wp_client=client,
        operation_id="op_12345678",
    )
    assert result is True
    client._request.assert_called_once()


def test_env_unknown_method_falls_back_to_noop(monkeypatch):
    monkeypatch.setenv("LITESPEED_PURGE_METHOD", "carrier-pigeon")
    client = MagicMock()
    result = purge_url(
        "https://shosho.tw/post/1",
        wp_client=client,
        operation_id="op_12345678",
    )
    assert result is False
    client._request.assert_not_called()
