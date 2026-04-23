"""Schema contracts for shared/schemas/franky.py (ADR-007 §3 / §4)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from shared.schemas.franky import (
    AlertV1,
    DEFAULT_DEDUP_WINDOW_S,
    DEFAULT_FAIL_THRESHOLD,
    HealthProbeV1,
    HealthzCheckEntry,
    HealthzResponseV1,
    VPSResourceSampleV1,
    WeeklyDigestV1,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# HealthProbeV1
# ---------------------------------------------------------------------------


def test_health_probe_accepts_ok_minimal():
    p = HealthProbeV1(target="vps_resources", status="ok", checked_at=_now(), latency_ms=12)
    assert p.status == "ok"
    assert p.error is None
    assert p.detail == {}


def test_health_probe_rejects_unknown_target():
    with pytest.raises(ValidationError):
        HealthProbeV1(
            target="shosho_birdsite",  # not in ProbeTarget whitelist
            status="ok",
            checked_at=_now(),
            latency_ms=0,
        )


def test_health_probe_rejects_negative_latency():
    with pytest.raises(ValidationError):
        HealthProbeV1(target="wp_shosho", status="ok", checked_at=_now(), latency_ms=-1)


def test_health_probe_forbids_extra_fields():
    with pytest.raises(ValidationError):
        HealthProbeV1(
            target="wp_shosho",
            status="ok",
            checked_at=_now(),
            latency_ms=10,
            undocumented_field="surprise",  # type: ignore[call-arg]
        )


def test_health_probe_is_frozen():
    p = HealthProbeV1(target="wp_shosho", status="ok", checked_at=_now(), latency_ms=10)
    with pytest.raises(ValidationError):
        p.status = "fail"  # type: ignore[misc]


def test_health_probe_requires_aware_datetime():
    # Naive datetime → pydantic v2 AwareDatetime rejects
    naive = datetime(2026, 4, 23, 12, 0, 0)
    with pytest.raises(ValidationError):
        HealthProbeV1(target="wp_shosho", status="ok", checked_at=naive, latency_ms=10)


# ---------------------------------------------------------------------------
# HealthzResponseV1
# ---------------------------------------------------------------------------


def test_healthz_response_minimal_ok():
    resp = HealthzResponseV1(
        status="ok",
        version="phase-1",
        uptime_seconds=42,
        checks=[HealthzCheckEntry(name="process", status="ok")],
    )
    assert resp.service == "nakama-gateway"
    assert resp.schema_version == 1


def test_healthz_check_name_pattern():
    with pytest.raises(ValidationError):
        HealthzCheckEntry(name="Bad-Name", status="ok")  # uppercase/dash rejected
    with pytest.raises(ValidationError):
        HealthzCheckEntry(name="1process", status="ok")  # must start with [a-z_]


def test_healthz_response_version_is_accepted_as_str():
    resp = HealthzResponseV1(status="ok", version="0.0.1", uptime_seconds=0)
    assert resp.version == "0.0.1"


# ---------------------------------------------------------------------------
# AlertV1
# ---------------------------------------------------------------------------


def test_alert_dedup_defaults_to_15_min():
    alert = AlertV1(
        rule_id="wp_shosho_unhealthy",
        severity="critical",
        title="x",
        message="y",
        fired_at=_now(),
        dedup_key="wp_shosho_unhealthy",
        operation_id="op_12345678",
    )
    assert alert.dedup_window_seconds == DEFAULT_DEDUP_WINDOW_S
    assert DEFAULT_FAIL_THRESHOLD == 3  # guard against accidental threshold change


def test_alert_rejects_short_rule_id():
    with pytest.raises(ValidationError):
        AlertV1(
            rule_id="ab",  # must be >= 3 chars after first [a-z_]
            severity="info",
            title="x",
            message="y",
            fired_at=_now(),
            dedup_key="x",
            operation_id="op_12345678",
        )


def test_alert_rejects_bad_operation_id():
    with pytest.raises(ValidationError):
        AlertV1(
            rule_id="disk_critical",
            severity="warning",
            title="x",
            message="y",
            fired_at=_now(),
            dedup_key="k",
            operation_id="operation-12345",  # wrong prefix format
        )


def test_alert_dedup_window_bounds():
    # Too low (< 60s) rejected
    with pytest.raises(ValidationError):
        AlertV1(
            rule_id="disk_critical",
            severity="warning",
            title="x",
            message="y",
            fired_at=_now(),
            dedup_key="k",
            dedup_window_seconds=10,
            operation_id="op_12345678",
        )
    # Too high (> 24h) rejected
    with pytest.raises(ValidationError):
        AlertV1(
            rule_id="disk_critical",
            severity="warning",
            title="x",
            message="y",
            fired_at=_now(),
            dedup_key="k",
            dedup_window_seconds=24 * 3600 + 1,
            operation_id="op_12345678",
        )


# ---------------------------------------------------------------------------
# VPSResourceSampleV1 / WeeklyDigestV1 — schema freeze only
# ---------------------------------------------------------------------------


def test_vps_resource_sample_bounds():
    with pytest.raises(ValidationError):
        VPSResourceSampleV1(
            sampled_at=_now(),
            cpu_pct=120.0,  # > 100 rejected
            ram_used_mb=100,
            ram_total_mb=1000,
            swap_used_mb=0,
            disk_used_pct=40.0,
            load_1m=0.5,
        )


def test_weekly_digest_accepts_negative_cost_delta():
    """Week-over-week cost can decrease (delta < 0), schema must allow it."""
    d = WeeklyDigestV1(
        period_start=_now(),
        period_end=_now(),
        vps_cpu_avg_pct=14.0,
        vps_cpu_p95_pct=41.0,
        vps_ram_avg_pct=62.0,
        vps_disk_used_pct=26.0,
        cron_total_runs=2016,
        cron_success_pct=99.8,
        cron_slowest_p95_ms=18400,
        r2_backup_days_ok=7,
        r2_backup_latest_size_mb=142,
        critical_count=0,
        warning_count=2,
        llm_cost_usd_week=3.21,
        llm_cost_delta_pct=-4.5,
    )
    assert d.llm_cost_delta_pct == -4.5
