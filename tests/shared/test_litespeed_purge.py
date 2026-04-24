"""Tests for shared.litespeed_purge — noop-only after Day 1 (2026-04-24).

See docs/runbooks/litespeed-purge.md Day 1 決策紀錄：WP `save_post` hook 自動
處理 cache invalidation，explicit purge call 不需要；REST endpoint 不存在。
因此本模組只保留 noop path，其他舊 method（rest / admin_ajax）的測試已刪除。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from shared.litespeed_purge import purge_url


@pytest.fixture
def no_env(monkeypatch):
    """Clear inherited LITESPEED_* env vars so tests are deterministic."""
    for var in ("LITESPEED_PURGE_METHOD", "LITESPEED_PURGE_TIMEOUT"):
        monkeypatch.delenv(var, raising=False)


def test_default_returns_false_without_calling_client(no_env):
    """No env, no explicit method — returns False, never touches wp_client."""
    client = MagicMock()
    result = purge_url(
        "https://shosho.tw/post/1",
        wp_client=client,
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


def test_explicit_noop_method_arg(no_env):
    client = MagicMock()
    result = purge_url(
        "https://shosho.tw/post/1",
        wp_client=client,
        method="noop",
        operation_id="op_12345678",
    )
    assert result is False
    client._request.assert_not_called()


def test_legacy_env_value_falls_back_to_noop(monkeypatch, caplog):
    """Stale .env with LITESPEED_PURGE_METHOD=rest / admin_ajax / anything else
    must not crash — logs WARNING and behaves as noop."""
    import logging

    for legacy_value in ("rest", "admin_ajax", "carrier-pigeon", ""):
        monkeypatch.setenv("LITESPEED_PURGE_METHOD", legacy_value)
        client = MagicMock()
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="nakama.litespeed_purge"):
            result = purge_url(
                "https://shosho.tw/post/1",
                wp_client=client,
                operation_id="op_12345678",
            )
        assert result is False, f"legacy env value {legacy_value!r} should still noop"
        client._request.assert_not_called()
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("not supported" in r.message for r in warnings), (
            f"expected 'not supported' WARNING for {legacy_value!r}, "
            f"got {[r.message for r in warnings]}"
        )


def test_wp_client_can_be_none(no_env):
    """wp_client is accepted but ignored; None must not raise."""
    result = purge_url(
        "https://shosho.tw/post/1",
        wp_client=None,
        operation_id="op_12345678",
    )
    assert result is False
