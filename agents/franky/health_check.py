"""Franky health check — 5-min cron 的核心 probe 模組（ADR-007 §4）。

職責：
- 對 4 個 target 跑 probe（vps_resources / wp_shosho / wp_fleet / nakama_gateway）
- 維護 health_probe_state 表的 N-fail 計數器
- 連續 N 次（預設 3）fail 才推 AlertV1 給 alert_sink（Slice 2 接 alert_router）
- 從 fail → ok 過渡時推 resolved AlertV1（不 dedup，明確發一次「已恢復」）

設計原則（ADR-007 §4）：
- 此模組維護 N-fail 狀態，alert_router 是 stateless dedup
- 單次網路抖動不刷屏（threshold = 3）
- VPS 資源達門檻直接告警（不需連續 3 次，因為這是 sustained state）

執行方式：
    from agents.franky.health_check import run_once
    alerts = run_once(alert_sink=lambda a: print(a))
    # alerts 是該次 cron tick 推給 router 的所有 AlertV1（含升 / 降）

CLI: `python -m agents.franky health`
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import httpx
import psutil

from shared.log import get_logger
from shared.r2_client import R2Client, R2Unavailable
from shared.schemas.franky import (
    DEFAULT_FAIL_THRESHOLD,
    AlertV1,
    HealthProbeV1,
    ProbeTarget,
)
from shared.state import _get_conn

logger = get_logger("nakama.franky.health_check")

# ---------------------------------------------------------------------------
# Thresholds（ADR-007 §8 Critical 條件）
# ---------------------------------------------------------------------------

VPS_DISK_CRITICAL_PCT: float = 95.0
VPS_DISK_WARNING_PCT: float = 85.0
VPS_RAM_CRITICAL_PCT: float = 95.0  # RAM full
VPS_SWAP_CRITICAL_PCT: float = 80.0  # swap > 80%
VPS_RAM_WARNING_PCT: float = 85.0  # warning level (Bridge dashboard)

# HTTP probe timeouts（reliability.md §7 — 本機 < 3s）
NAKAMA_PROBE_TIMEOUT_S: float = 3.0

# 預設本機 gateway URL；env override 讓 dev/CI 各自指向
DEFAULT_NAKAMA_HEALTHZ_URL: str = "http://127.0.0.1:8000/healthz"

# nakama-backup bucket freshness threshold. 超過此小時數 → sustained-state Critical（inline）。
# 預設 48h：Nakama state.db daily 04:00 Taipei 備份，48h 代表連兩日都沒跑就報警。
NAKAMA_BACKUP_STALE_THRESHOLD_HOURS: float = float(
    os.getenv("FRANKY_NAKAMA_BACKUP_STALE_HOURS", "48")
)


AlertSink = Callable[[AlertV1], None]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso(now: datetime | None = None) -> str:
    return (now or _now()).isoformat()


def _new_operation_id() -> str:
    return f"op_{uuid.uuid4().hex[:8]}"


def _log_alert_only(alert: AlertV1) -> None:
    """Default sink — Slice 1 沒 alert_router，只 log。Slice 2 替換為真路由。"""
    logger.warning(
        "alert severity=%s rule=%s dedup=%s msg=%s op=%s",
        alert.severity,
        alert.rule_id,
        alert.dedup_key,
        alert.message,
        alert.operation_id,
    )


# ---------------------------------------------------------------------------
# Probe state machine — health_probe_state table
# ---------------------------------------------------------------------------


def _get_probe_state(target: ProbeTarget) -> dict[str, Any] | None:
    conn = _get_conn()
    row = conn.execute(
        "SELECT consecutive_fails, last_status, last_check_at, last_error "
        "FROM health_probe_state WHERE target = ?",
        (target,),
    ).fetchone()
    return dict(row) if row else None


def _upsert_probe_state(probe: HealthProbeV1, consecutive_fails: int) -> None:
    conn = _get_conn()
    conn.execute(
        """INSERT INTO health_probe_state
              (target, consecutive_fails, last_check_at, last_status, last_error)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(target) DO UPDATE SET
              consecutive_fails = excluded.consecutive_fails,
              last_check_at     = excluded.last_check_at,
              last_status       = excluded.last_status,
              last_error        = excluded.last_error""",
        (
            probe.target,
            consecutive_fails,
            probe.checked_at.isoformat(),
            probe.status,
            probe.error,
        ),
    )
    conn.commit()


def _record_and_maybe_alert(
    probe: HealthProbeV1,
    *,
    operation_id: str,
    alert_sink: AlertSink,
    fail_threshold: int = DEFAULT_FAIL_THRESHOLD,
    severity_on_threshold: str = "critical",
) -> AlertV1 | None:
    """Update health_probe_state and emit AlertV1 on threshold cross / recovery.

    Returns the AlertV1 emitted (if any), so the caller can collect them for the cron summary.
    """
    prev = _get_probe_state(probe.target)
    prev_fails: int = prev["consecutive_fails"] if prev else 0
    prev_status: str = prev["last_status"] if prev else "ok"

    if probe.status == "fail":
        new_fails = prev_fails + 1
        _upsert_probe_state(probe, new_fails)

        # 跨過門檻才升告警；超過門檻後不重複推（router 自己 dedup，但這樣連 router 都省了）
        if new_fails == fail_threshold:
            alert = AlertV1(
                rule_id=f"{probe.target}_unhealthy",
                severity=severity_on_threshold,  # type: ignore[arg-type]
                title=f"{probe.target} unhealthy",
                message=(
                    f"{probe.target} failed {new_fails} consecutive probes. "
                    f"Last error: {probe.error or 'unknown'}"
                ),
                fired_at=probe.checked_at,
                dedup_key=f"{probe.target}_unhealthy",
                operation_id=operation_id,
                context={
                    "consecutive_fails": new_fails,
                    "latency_ms": probe.latency_ms,
                },
            )
            alert_sink(alert)
            return alert
        return None

    # status == "ok"
    _upsert_probe_state(probe, 0)
    # 從 fail 過渡回 ok（且之前真的有越過門檻）→ 推 resolved
    if prev_status == "fail" and prev_fails >= fail_threshold:
        alert = AlertV1(
            rule_id=f"{probe.target}_recovered",
            severity="info",
            title=f"{probe.target} recovered",
            message=f"{probe.target} probe ok again after {prev_fails} consecutive fails.",
            fired_at=probe.checked_at,
            dedup_key=f"{probe.target}_recovered",
            dedup_window_seconds=60,  # resolved 不需要長 window，避免下次掛掉的 resolved 被吞
            operation_id=operation_id,
            context={"prior_consecutive_fails": prev_fails},
        )
        alert_sink(alert)
        return alert
    return None


# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------


def probe_vps_resources(now: datetime | None = None) -> tuple[HealthProbeV1, list[AlertV1]]:
    """Sample CPU / RAM / disk / swap via psutil; emit Critical-level alerts inline.

    VPS resource alerts are sustained-state (already 95% disk = already bad). We don't
    require 3-consecutive-fail like network probes; instead each threshold breach pushes
    its own AlertV1 (router dedup keeps the noise down).

    Returns: (probe, list_of_inline_alerts)
        probe — pass to _record_and_maybe_alert for state tracking
        list_of_inline_alerts — already-emitted resource alerts (caller adds to summary)
    """
    now = now or _now()
    started = time.monotonic()
    inline_alerts: list[AlertV1] = []

    cpu = psutil.cpu_percent(interval=None)  # non-blocking; uses last reading
    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()
    disk = psutil.disk_usage("/")

    detail: dict[str, str | int | float | bool] = {
        "cpu_pct": float(cpu),
        "ram_pct": float(vm.percent),
        "ram_used_mb": int(vm.used // (1024 * 1024)),
        "ram_total_mb": int(vm.total // (1024 * 1024)),
        "swap_pct": float(sw.percent),
        "disk_pct": float(disk.percent),
    }

    op = _new_operation_id()
    if disk.percent >= VPS_DISK_CRITICAL_PCT:
        inline_alerts.append(
            AlertV1(
                rule_id="vps_disk_critical",
                severity="critical",
                title="VPS disk usage critical",
                message=f"disk at {disk.percent:.1f}% (>= {VPS_DISK_CRITICAL_PCT}%)",
                fired_at=now,
                dedup_key="vps_disk_critical",
                operation_id=op,
                context={"disk_pct": float(disk.percent)},
            )
        )
    if vm.percent >= VPS_RAM_CRITICAL_PCT and sw.percent >= VPS_SWAP_CRITICAL_PCT:
        inline_alerts.append(
            AlertV1(
                rule_id="vps_ram_swap_critical",
                severity="critical",
                title="VPS RAM full + swap saturating",
                message=(
                    f"RAM at {vm.percent:.1f}% AND swap at {sw.percent:.1f}% "
                    f"(both above critical thresholds)"
                ),
                fired_at=now,
                dedup_key="vps_ram_swap_critical",
                operation_id=op,
                context={"ram_pct": float(vm.percent), "swap_pct": float(sw.percent)},
            )
        )

    latency_ms = int((time.monotonic() - started) * 1000)
    probe = HealthProbeV1(
        target="vps_resources",
        status="ok",  # sampling itself doesn't fail; resource alerts surface via inline
        checked_at=now,
        latency_ms=latency_ms,
        detail=detail,
    )
    return probe, inline_alerts


def probe_wp_site(site: str, now: datetime | None = None) -> HealthProbeV1:
    """Probe a WordPress site via WordPressClient.health_check().

    site must be one of: "wp_shosho", "wp_fleet".
    Returns HealthProbeV1; never raises (failures are encoded as status="fail").
    Skips and returns ok if env credentials missing (local dev / CI).
    """
    now = now or _now()
    started = time.monotonic()
    target: ProbeTarget = "wp_shosho" if site == "wp_shosho" else "wp_fleet"

    prefix = site.upper()  # e.g. WP_SHOSHO
    required = [f"{prefix}_BASE_URL", f"{prefix}_USERNAME", f"{prefix}_APP_PASSWORD"]
    if any(not os.getenv(k) for k in required):
        logger.info(
            "wp probe skipped (missing env) site=%s; reporting ok to avoid false-positive on dev",
            site,
        )
        return HealthProbeV1(
            target=target,
            status="ok",
            checked_at=now,
            latency_ms=0,
            detail={"skipped": True, "reason": "missing_env"},
        )

    try:
        from shared.wordpress_client import WordPressClient

        client = WordPressClient.from_env(site)
        ok = client.health_check(operation_id=_new_operation_id())
        latency_ms = int((time.monotonic() - started) * 1000)
        if ok:
            return HealthProbeV1(
                target=target,
                status="ok",
                checked_at=now,
                latency_ms=latency_ms,
            )
        return HealthProbeV1(
            target=target,
            status="fail",
            checked_at=now,
            latency_ms=latency_ms,
            error="health_check returned False",
        )
    except Exception as exc:  # noqa: BLE001 — probe must never raise
        latency_ms = int((time.monotonic() - started) * 1000)
        return HealthProbeV1(
            target=target,
            status="fail",
            checked_at=now,
            latency_ms=latency_ms,
            error=f"{type(exc).__name__}: {exc}"[:200],
        )


def probe_nakama_gateway(
    url: str | None = None,
    *,
    now: datetime | None = None,
    timeout_s: float = NAKAMA_PROBE_TIMEOUT_S,
) -> HealthProbeV1:
    """Probe local Nakama gateway via HTTP GET /healthz.

    Loopback probe detects "process alive but stuck" cases. The external UptimeRobot
    probe covers "VPS down" cases (ADR-007 §2).
    """
    now = now or _now()
    started = time.monotonic()
    url = url or os.getenv("NAKAMA_HEALTHZ_URL", DEFAULT_NAKAMA_HEALTHZ_URL)

    try:
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.get(url)
        latency_ms = int((time.monotonic() - started) * 1000)
        if resp.status_code == 200:
            return HealthProbeV1(
                target="nakama_gateway",
                status="ok",
                checked_at=now,
                latency_ms=latency_ms,
                detail={"status_code": resp.status_code},
            )
        return HealthProbeV1(
            target="nakama_gateway",
            status="fail",
            checked_at=now,
            latency_ms=latency_ms,
            error=f"unexpected status_code={resp.status_code}",
            detail={"status_code": resp.status_code},
        )
    except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPError) as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        return HealthProbeV1(
            target="nakama_gateway",
            status="fail",
            checked_at=now,
            latency_ms=latency_ms,
            error=f"{type(exc).__name__}: {exc}"[:200],
        )


def probe_r2_backup_nakama(
    now: datetime | None = None,
) -> tuple[HealthProbeV1, list[AlertV1]]:
    """Check nakama-backup R2 bucket freshness; emit Critical inline if >48h stale.

    Sustained-state probe pattern (like probe_vps_resources): sampling itself never "fails";
    inline Critical alerts fire directly when the backup is stale / empty — router dedup
    controls repetition. Bypasses the 3-consecutive-fail gate because 48h is already its
    own confirmation window (not a transient blip).

    Transient network errors on list_objects return status='fail' WITHOUT an inline alert,
    so _record_and_maybe_alert's 3-fail gate can still escalate persistent infra issues.

    Env missing → skip with status='ok' + detail.skipped=True (dev / CI safety), mirrors
    probe_wp_site's behavior.

    Returns: (probe, list_of_inline_alerts) — same contract as probe_vps_resources.
    """
    now = now or _now()
    started = time.monotonic()
    inline_alerts: list[AlertV1] = []

    try:
        # Probe is read-only (list_objects). Use mode="read" so this surface
        # never needs the WRITE token — least-privilege.
        client = R2Client.from_nakama_backup_env(mode="read")
    except R2Unavailable as exc:
        logger.info("r2_backup_nakama probe skipped (missing env): %s", exc)
        return (
            HealthProbeV1(
                target="r2_backup_nakama",
                status="ok",
                checked_at=now,
                latency_ms=int((time.monotonic() - started) * 1000),
                detail={"skipped": True, "reason": "missing_env"},
            ),
            [],
        )

    try:
        objects = client.list_objects(prefix="", max_keys=50)
    except R2Unavailable as exc:
        latency_ms = int((time.monotonic() - started) * 1000)
        return (
            HealthProbeV1(
                target="r2_backup_nakama",
                status="fail",
                checked_at=now,
                latency_ms=latency_ms,
                error=f"list_objects failed: {exc}"[:200],
                detail={"bucket": client.bucket},
            ),
            [],
        )

    op = _new_operation_id()
    if not objects:
        detail_msg = f"bucket={client.bucket} empty — daily state.db backup job failing?"
        inline_alerts.append(
            AlertV1(
                rule_id="r2_backup_nakama_empty",
                severity="critical",
                title="nakama-backup bucket empty",
                message=detail_msg,
                fired_at=now,
                dedup_key="r2_backup_nakama_empty",
                operation_id=op,
                context={"bucket": client.bucket},
            )
        )
        return (
            HealthProbeV1(
                target="r2_backup_nakama",
                status="fail",
                checked_at=now,
                latency_ms=int((time.monotonic() - started) * 1000),
                error="bucket empty",
                detail={"bucket": client.bucket},
            ),
            inline_alerts,
        )

    latest = max(objects, key=lambda o: o.last_modified)
    age_h = (now - latest.last_modified).total_seconds() / 3600
    latency_ms = int((time.monotonic() - started) * 1000)
    base_detail: dict[str, str | int | float | bool] = {
        "bucket": client.bucket,
        "latest_key": latest.key,
        "latest_size": latest.size,
        "age_hours": round(age_h, 2),
    }

    if age_h > NAKAMA_BACKUP_STALE_THRESHOLD_HOURS:
        msg = (
            f"latest={latest.key} is {age_h:.1f}h old "
            f"(> {NAKAMA_BACKUP_STALE_THRESHOLD_HOURS}h threshold)"
        )
        inline_alerts.append(
            AlertV1(
                rule_id="r2_backup_nakama_stale",
                severity="critical",
                title="nakama-backup is stale",
                message=msg,
                fired_at=now,
                dedup_key="r2_backup_nakama_stale",
                operation_id=op,
                context=base_detail,
            )
        )
        return (
            HealthProbeV1(
                target="r2_backup_nakama",
                status="fail",
                checked_at=now,
                latency_ms=latency_ms,
                error=f"stale {age_h:.1f}h",
                detail=base_detail,
            ),
            inline_alerts,
        )

    return (
        HealthProbeV1(
            target="r2_backup_nakama",
            status="ok",
            checked_at=now,
            latency_ms=latency_ms,
            detail=base_detail,
        ),
        [],
    )


# ---------------------------------------------------------------------------
# Public entry — single cron tick
# ---------------------------------------------------------------------------


def run_once(
    *,
    alert_sink: AlertSink | None = None,
    fail_threshold: int = DEFAULT_FAIL_THRESHOLD,
    nakama_healthz_url: str | None = None,
) -> dict[str, Any]:
    """Run all probes once. Suitable as the body of a 5-min systemd-timer / cron job.

    Args:
        alert_sink: callable that consumes each AlertV1. Default = log only (Slice 1).
                    Slice 2 will inject the real alert_router.dispatch.
        fail_threshold: consecutive fails before threshold-cross AlertV1 is emitted.
        nakama_healthz_url: override default loopback URL (for tests).

    Returns:
        Dict with keys:
          - probes: list[HealthProbeV1] for every target
          - alerts: list[AlertV1] emitted this tick
          - operation_id: cron-tick operation id (observability.md §2)
          - duration_ms: total wall time
    """
    sink: AlertSink = alert_sink or _log_alert_only
    operation_id = _new_operation_id()
    started = time.monotonic()
    now = _now()

    probes: list[HealthProbeV1] = []
    alerts: list[AlertV1] = []

    # 1. VPS resources（每次都採樣，Critical 直接 inline）
    vps_probe, vps_alerts = probe_vps_resources(now=now)
    probes.append(vps_probe)
    for a in vps_alerts:
        sink(a)
        alerts.append(a)
    a = _record_and_maybe_alert(
        vps_probe,
        operation_id=operation_id,
        alert_sink=sink,
        fail_threshold=fail_threshold,
    )
    if a is not None:
        alerts.append(a)

    # 2. WP × 2
    for site in ("wp_shosho", "wp_fleet"):
        probe = probe_wp_site(site, now=now)
        probes.append(probe)
        a = _record_and_maybe_alert(
            probe,
            operation_id=operation_id,
            alert_sink=sink,
            fail_threshold=fail_threshold,
        )
        if a is not None:
            alerts.append(a)

    # 3. Nakama gateway loopback
    nakama_probe = probe_nakama_gateway(url=nakama_healthz_url, now=now)
    probes.append(nakama_probe)
    a = _record_and_maybe_alert(
        nakama_probe,
        operation_id=operation_id,
        alert_sink=sink,
        fail_threshold=fail_threshold,
    )
    if a is not None:
        alerts.append(a)

    # 4. nakama-backup R2 freshness（inline Critical + 3-fail gate for transient errors only）
    r2_nakama_probe, r2_nakama_alerts = probe_r2_backup_nakama(now=now)
    probes.append(r2_nakama_probe)
    for inline in r2_nakama_alerts:
        sink(inline)
        alerts.append(inline)
    # Skip the N-fail gate when inline already fired (stale/empty path) — the explicit
    # r2_backup_nakama_stale / _empty alert is already the right signal; the generic
    # _unhealthy rule would just double-page 15 min later with a different dedup key.
    # Still upsert state so the Bridge dashboard sees consecutive_fails accurately.
    if r2_nakama_alerts:
        _upsert_probe_state(r2_nakama_probe, consecutive_fails=fail_threshold)
    else:
        a = _record_and_maybe_alert(
            r2_nakama_probe,
            operation_id=operation_id,
            alert_sink=sink,
            fail_threshold=fail_threshold,
        )
        if a is not None:
            alerts.append(a)

    duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "health_check tick op=%s duration_ms=%s probes=%s alerts=%s",
        operation_id,
        duration_ms,
        len(probes),
        len(alerts),
    )
    return {
        "operation_id": operation_id,
        "probes": probes,
        "alerts": alerts,
        "duration_ms": duration_ms,
    }
