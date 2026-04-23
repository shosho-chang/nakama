"""Tests for agents/franky/health_check.py.

Coverage (task prompt §5 verification):
- 3-consecutive-fail state machine: fail x2 no alert, fail x3 Critical, fail x4 no re-alert
- Recovery emits resolved AlertV1 only when prior fails crossed threshold
- Single fail then ok → no alerts (flap suppression)
- VPS Critical thresholds (disk >= 95%, RAM full + swap > 80%) emit inline
- WP probe skips gracefully when env missing (dev safety)
- Nakama gateway probe classifies timeout / non-200 correctly
- run_once is the single-tick entry contract
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import httpx
import pytest

from agents.franky import health_check
from agents.franky.health_check import (
    _record_and_maybe_alert,
    probe_nakama_gateway,
    probe_vps_resources,
    probe_wp_site,
    run_once,
)
from shared.schemas.franky import (
    DEFAULT_FAIL_THRESHOLD,
    AlertV1,
    HealthProbeV1,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# 3-consecutive-fail state machine
# ---------------------------------------------------------------------------


def _fail_probe(target: str = "wp_shosho") -> HealthProbeV1:
    return HealthProbeV1(
        target=target,  # type: ignore[arg-type]
        status="fail",
        checked_at=_now(),
        latency_ms=100,
        error="boom",
    )


def _ok_probe(target: str = "wp_shosho") -> HealthProbeV1:
    return HealthProbeV1(
        target=target,  # type: ignore[arg-type]
        status="ok",
        checked_at=_now(),
        latency_ms=50,
    )


def test_single_fail_does_not_emit_alert():
    sink = MagicMock()
    result = _record_and_maybe_alert(_fail_probe(), operation_id="op_aaaaaaaa", alert_sink=sink)
    assert result is None
    sink.assert_not_called()


def test_two_consecutive_fails_do_not_emit():
    sink = MagicMock()
    for _ in range(DEFAULT_FAIL_THRESHOLD - 1):
        result = _record_and_maybe_alert(_fail_probe(), operation_id="op_bbbbbbbb", alert_sink=sink)
        assert result is None
    sink.assert_not_called()


def test_third_consecutive_fail_emits_critical():
    sink = MagicMock()
    for _ in range(DEFAULT_FAIL_THRESHOLD):
        _record_and_maybe_alert(_fail_probe(), operation_id="op_cccccccc", alert_sink=sink)
    assert sink.call_count == 1
    emitted: AlertV1 = sink.call_args.args[0]
    assert emitted.severity == "critical"
    assert emitted.rule_id == "wp_shosho_unhealthy"
    assert emitted.context["consecutive_fails"] == DEFAULT_FAIL_THRESHOLD


def test_fourth_consecutive_fail_does_not_re_emit():
    sink = MagicMock()
    for _ in range(DEFAULT_FAIL_THRESHOLD + 1):
        _record_and_maybe_alert(_fail_probe(), operation_id="op_dddddddd", alert_sink=sink)
    # Only one alert total — router-side dedup gets the same guarantee but we prove
    # the probe itself doesn't spam.
    assert sink.call_count == 1


def test_recovery_after_threshold_emits_resolved():
    sink = MagicMock()
    for _ in range(DEFAULT_FAIL_THRESHOLD):
        _record_and_maybe_alert(_fail_probe(), operation_id="op_eeeeeeee", alert_sink=sink)
    sink.reset_mock()

    _record_and_maybe_alert(_ok_probe(), operation_id="op_eeeeeeef", alert_sink=sink)
    assert sink.call_count == 1
    emitted: AlertV1 = sink.call_args.args[0]
    assert emitted.severity == "info"
    assert emitted.rule_id == "wp_shosho_recovered"
    assert emitted.context["prior_consecutive_fails"] >= DEFAULT_FAIL_THRESHOLD


def test_single_fail_then_ok_emits_nothing():
    """Flap suppression — one transient fail without crossing threshold: silent."""
    sink = MagicMock()
    _record_and_maybe_alert(_fail_probe(), operation_id="op_ffffffff", alert_sink=sink)
    _record_and_maybe_alert(_ok_probe(), operation_id="op_ffffffff", alert_sink=sink)
    sink.assert_not_called()


# ---------------------------------------------------------------------------
# VPS resource probe
# ---------------------------------------------------------------------------


def _mock_psutil(*, cpu: float, ram_pct: float, swap_pct: float, disk_pct: float):
    """Patch psutil.* with controllable values.

    Returns the patcher stack (enter manually via `with` in the test).
    """
    vm = MagicMock(percent=ram_pct, used=int(ram_pct * 10**6), total=10**9)
    sw = MagicMock(percent=swap_pct)
    disk = MagicMock(percent=disk_pct)
    return [
        patch.object(health_check.psutil, "cpu_percent", return_value=cpu),
        patch.object(health_check.psutil, "virtual_memory", return_value=vm),
        patch.object(health_check.psutil, "swap_memory", return_value=sw),
        patch.object(health_check.psutil, "disk_usage", return_value=disk),
    ]


def test_vps_probe_happy_path_no_alerts():
    for p in _mock_psutil(cpu=15.0, ram_pct=40.0, swap_pct=0.0, disk_pct=30.0):
        p.start()
    try:
        probe, alerts = probe_vps_resources()
    finally:
        patch.stopall()
    assert probe.status == "ok"
    assert alerts == []
    assert probe.detail["cpu_pct"] == 15.0
    assert probe.detail["disk_pct"] == 30.0


def test_vps_probe_disk_critical_emits_inline_alert():
    for p in _mock_psutil(cpu=10.0, ram_pct=50.0, swap_pct=0.0, disk_pct=96.5):
        p.start()
    try:
        _, alerts = probe_vps_resources()
    finally:
        patch.stopall()
    assert any(a.rule_id == "vps_disk_critical" for a in alerts)


def test_vps_probe_ram_full_plus_swap_emits_alert():
    for p in _mock_psutil(cpu=10.0, ram_pct=96.0, swap_pct=85.0, disk_pct=40.0):
        p.start()
    try:
        _, alerts = probe_vps_resources()
    finally:
        patch.stopall()
    assert any(a.rule_id == "vps_ram_swap_critical" for a in alerts)


def test_vps_probe_ram_high_but_swap_low_does_not_alert():
    """RAM alone crossing critical is not enough — must also saturate swap."""
    for p in _mock_psutil(cpu=10.0, ram_pct=96.0, swap_pct=20.0, disk_pct=40.0):
        p.start()
    try:
        _, alerts = probe_vps_resources()
    finally:
        patch.stopall()
    assert all(a.rule_id != "vps_ram_swap_critical" for a in alerts)


# ---------------------------------------------------------------------------
# WP probe — env skip + success/failure paths
# ---------------------------------------------------------------------------


def test_wp_probe_skips_when_env_missing(monkeypatch):
    for k in ("WP_SHOSHO_BASE_URL", "WP_SHOSHO_USERNAME", "WP_SHOSHO_APP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    probe = probe_wp_site("wp_shosho")
    assert probe.status == "ok"
    assert probe.detail.get("skipped") is True
    assert probe.detail.get("reason") == "missing_env"


def test_wp_probe_success_path(monkeypatch):
    monkeypatch.setenv("WP_SHOSHO_BASE_URL", "http://wp.test")
    monkeypatch.setenv("WP_SHOSHO_USERNAME", "u")
    monkeypatch.setenv("WP_SHOSHO_APP_PASSWORD", "p")

    fake_client = MagicMock()
    fake_client.health_check.return_value = True

    with patch("shared.wordpress_client.WordPressClient.from_env", return_value=fake_client):
        probe = probe_wp_site("wp_shosho")
    assert probe.status == "ok"


def test_wp_probe_failure_path(monkeypatch):
    monkeypatch.setenv("WP_SHOSHO_BASE_URL", "http://wp.test")
    monkeypatch.setenv("WP_SHOSHO_USERNAME", "u")
    monkeypatch.setenv("WP_SHOSHO_APP_PASSWORD", "p")

    fake_client = MagicMock()
    fake_client.health_check.return_value = False

    with patch("shared.wordpress_client.WordPressClient.from_env", return_value=fake_client):
        probe = probe_wp_site("wp_shosho")
    assert probe.status == "fail"
    assert "health_check returned False" in (probe.error or "")


def test_wp_probe_exception_becomes_fail(monkeypatch):
    monkeypatch.setenv("WP_SHOSHO_BASE_URL", "http://wp.test")
    monkeypatch.setenv("WP_SHOSHO_USERNAME", "u")
    monkeypatch.setenv("WP_SHOSHO_APP_PASSWORD", "p")

    def _boom(_site):
        raise RuntimeError("credential rotated")

    with patch("shared.wordpress_client.WordPressClient.from_env", side_effect=_boom):
        probe = probe_wp_site("wp_shosho")
    assert probe.status == "fail"
    assert "RuntimeError" in (probe.error or "")


# ---------------------------------------------------------------------------
# Nakama gateway probe
# ---------------------------------------------------------------------------


class _MockHttpxClient:
    """Minimal context manager to stub httpx.Client for probe_nakama_gateway."""

    def __init__(self, *, status_code: int | None = None, exc: Exception | None = None):
        self._status_code = status_code
        self._exc = exc

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, _url):
        if self._exc is not None:
            raise self._exc
        resp = MagicMock()
        resp.status_code = self._status_code
        return resp


def test_nakama_probe_200_ok():
    with patch.object(
        health_check.httpx,
        "Client",
        return_value=_MockHttpxClient(status_code=200),
    ):
        probe = probe_nakama_gateway(url="http://test/healthz")
    assert probe.status == "ok"
    assert probe.detail["status_code"] == 200


def test_nakama_probe_503_fail():
    with patch.object(
        health_check.httpx,
        "Client",
        return_value=_MockHttpxClient(status_code=503),
    ):
        probe = probe_nakama_gateway(url="http://test/healthz")
    assert probe.status == "fail"
    assert "503" in (probe.error or "")


def test_nakama_probe_timeout_fail():
    with patch.object(
        health_check.httpx,
        "Client",
        return_value=_MockHttpxClient(exc=httpx.TimeoutException("timed out")),
    ):
        probe = probe_nakama_gateway(url="http://test/healthz")
    assert probe.status == "fail"
    assert "TimeoutException" in (probe.error or "")


def test_nakama_probe_connect_error_fail():
    with patch.object(
        health_check.httpx,
        "Client",
        return_value=_MockHttpxClient(exc=httpx.ConnectError("refused")),
    ):
        probe = probe_nakama_gateway(url="http://test/healthz")
    assert probe.status == "fail"
    assert "ConnectError" in (probe.error or "")


# ---------------------------------------------------------------------------
# run_once end-to-end
# ---------------------------------------------------------------------------


@pytest.fixture
def _mock_ok_env(monkeypatch):
    """Make probes pass — env missing (wp skip) + nakama 200 + vps healthy."""
    for k in ("WP_SHOSHO_BASE_URL", "WP_FLEET_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    for p in _mock_psutil(cpu=10.0, ram_pct=40.0, swap_pct=0.0, disk_pct=30.0):
        p.start()
    with patch.object(
        health_check.httpx,
        "Client",
        return_value=_MockHttpxClient(status_code=200),
    ):
        yield
    patch.stopall()


def test_run_once_returns_all_four_probes(_mock_ok_env):
    result = run_once(alert_sink=lambda _a: None)
    targets = {p.target for p in result["probes"]}
    assert targets == {"vps_resources", "wp_shosho", "wp_fleet", "nakama_gateway"}
    assert result["operation_id"].startswith("op_")
    assert result["duration_ms"] >= 0


def test_run_once_healthy_emits_no_alerts(_mock_ok_env):
    result = run_once(alert_sink=lambda _a: None)
    assert result["alerts"] == []


def test_run_once_collects_alerts_from_sink(_mock_ok_env):
    collected: list[AlertV1] = []
    # Trigger nakama_gateway to emit by forcing 3 consecutive fails via threshold=1
    with patch.object(
        health_check.httpx,
        "Client",
        return_value=_MockHttpxClient(exc=httpx.ConnectError("refused")),
    ):
        result = run_once(alert_sink=collected.append, fail_threshold=1)
    assert any(a.rule_id == "nakama_gateway_unhealthy" for a in collected)
    assert collected == result["alerts"]
