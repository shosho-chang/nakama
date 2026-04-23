"""Franky router.

Two public surfaces:

1. `GET /healthz` — UptimeRobot external probe (ADR-007 §3).
   - p95 < 200 ms, no DB / LLM / Slack, no auth, only 200/503.

2. `GET /bridge/franky` — minimal status dashboard (ADR-007 Slice 3 / direction A).
   - 4 probe cards (vps_resources / wp_shosho / wp_fleet / nakama_gateway)
   - Recent alerts list (last 24h window from alert_state)
   - Latest R2 backup check
   - Current VPS snapshot
   - Uses Direction B design tokens (matches /bridge/cost + /bridge/memory).
   - Cookie auth via `check_auth` (same pattern as bridge.page_router).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Cookie, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from shared.schemas.franky import HealthzCheckEntry, HealthzResponseV1
from thousand_sunny.auth import check_auth

router = APIRouter(tags=["franky"])

# HTML 頁面走 cookie → /login redirect，不跟 API 共用 403 行為（對齊 bridge.page_router）
page_router = APIRouter(prefix="/bridge", tags=["franky-bridge"])
_templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates" / "bridge")
)

# Process start time（module import 時 freeze），uptime 計算用
_PROCESS_START_MONOTONIC: float = time.monotonic()

# 版本字串。沒有正式 version pin，先以 phase tag 標示，未來接 importlib.metadata。
_GATEWAY_VERSION: str = "phase-1-franky-slice-1"


def _build_healthz() -> HealthzResponseV1:
    """Compose the response payload from in-memory state only.

    Phase 1：純 process-level 訊號。能 import schema、能算 uptime 就視為 ok。
    Phase 2：可加 in-memory cached 30s 的 DB ping（不可在 hot path 實際 query）。
    """
    uptime_seconds = int(time.monotonic() - _PROCESS_START_MONOTONIC)
    checks = [
        HealthzCheckEntry(name="process", status="ok"),
    ]
    return HealthzResponseV1(
        status="ok",
        version=_GATEWAY_VERSION,
        uptime_seconds=uptime_seconds,
        checks=checks,
    )


@router.get(
    "/healthz",
    response_model=HealthzResponseV1,
    status_code=status.HTTP_200_OK,
    summary="Liveness probe (UptimeRobot)",
)
def healthz(response: Response) -> HealthzResponseV1:
    """Returns 200 + HealthzResponseV1 when the gateway is alive.

    Returns 503 only if the response object cannot be assembled (e.g. schema import broken).
    The 503 path is intentionally minimal — the failure mode is "this process is broken
    enough that it cannot describe itself".
    """
    try:
        payload = _build_healthz()
    except Exception:  # noqa: BLE001 — degraded path must not raise into stack
        # Bypass FastAPI response_model coercion to ensure a 503 reaches the probe.
        return JSONResponse(  # type: ignore[return-value]
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "schema_version": 1,
                "status": "degraded",
                "service": "nakama-gateway",
                "version": _GATEWAY_VERSION,
                "uptime_seconds": 0,
                "checks": [{"name": "process", "status": "degraded"}],
            },
        )
    # Cache-Control: external probes don't need cached responses
    response.headers["Cache-Control"] = "no-store"
    return payload


# ---------------------------------------------------------------------------
# /bridge/franky dashboard (Slice 3)
# ---------------------------------------------------------------------------

_PROBE_TARGETS: tuple[str, ...] = ("nakama_gateway", "vps_resources", "wp_shosho", "wp_fleet")
_PROBE_LABELS: dict[str, str] = {
    "nakama_gateway": "Nakama Gateway",
    "vps_resources": "VPS Resources",
    "wp_shosho": "WordPress · shosho.tw",
    "wp_fleet": "WordPress · fleet.shosho.tw",
}


def _gather_dashboard_context() -> dict[str, Any]:
    """Read everything the dashboard needs from state.db + one psutil sample.

    Kept deliberately close to the template (server-side render, no JS fetch) so
    page load is cheap and `/bridge/franky` stays readable without browser JS.
    """
    # Imports here instead of top-level so this module stays importable without
    # psutil during unit tests that just probe /healthz.
    import psutil

    from shared.state import _get_conn

    conn = _get_conn()
    now = datetime.now(timezone.utc)
    day_ago_iso = (now - timedelta(hours=24)).isoformat()

    # 1. Probe status per target
    probe_rows = {
        row["target"]: dict(row)
        for row in conn.execute(
            """SELECT target, consecutive_fails, last_check_at, last_status, last_error
               FROM health_probe_state"""
        ).fetchall()
    }
    probe_cards = []
    for target in _PROBE_TARGETS:
        row = probe_rows.get(target)
        if row is None:
            probe_cards.append(
                {
                    "target": target,
                    "label": _PROBE_LABELS[target],
                    "status": "unknown",
                    "consecutive_fails": 0,
                    "last_check_at": None,
                    "last_error": None,
                }
            )
        else:
            probe_cards.append(
                {
                    "target": target,
                    "label": _PROBE_LABELS[target],
                    "status": row["last_status"],
                    "consecutive_fails": row["consecutive_fails"],
                    "last_check_at": row["last_check_at"],
                    "last_error": row["last_error"],
                }
            )

    # 2. Recent alerts (last 24h from alert_state)
    alert_rows = conn.execute(
        """SELECT rule_id, last_fired_at, suppress_until, state, last_message, fire_count
           FROM alert_state
           WHERE last_fired_at >= ?
           ORDER BY last_fired_at DESC
           LIMIT 25""",
        (day_ago_iso,),
    ).fetchall()
    alerts = []
    for row in alert_rows:
        still_firing = False
        if row["state"] == "firing":
            try:
                still_firing = datetime.fromisoformat(row["suppress_until"]) > now
            except ValueError:
                still_firing = True
        alerts.append(
            {
                "rule_id": row["rule_id"],
                "last_fired_at": row["last_fired_at"],
                "state": row["state"],
                "still_firing": still_firing,
                "message": row["last_message"],
                "fire_count": row["fire_count"],
            }
        )

    # 3. Latest R2 backup check
    latest_backup = conn.execute(
        """SELECT checked_at, latest_object_key, latest_object_size, status, detail
           FROM r2_backup_checks
           ORDER BY checked_at DESC
           LIMIT 1"""
    ).fetchone()
    r2 = dict(latest_backup) if latest_backup else None

    # 4. Current VPS snapshot
    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()
    disk = psutil.disk_usage("/")
    vps = {
        "cpu_pct": float(psutil.cpu_percent(interval=None)),
        "ram_pct": float(vm.percent),
        "ram_used_mb": int(vm.used // (1024 * 1024)),
        "ram_total_mb": int(vm.total // (1024 * 1024)),
        "swap_pct": float(sw.percent),
        "disk_pct": float(disk.percent),
    }

    return {
        "generated_at": now.isoformat(),
        "probe_cards": probe_cards,
        "alerts": alerts,
        "r2_backup": r2,
        "vps": vps,
    }


@page_router.get("/franky", response_class=HTMLResponse)
def bridge_franky_page(request: Request, nakama_auth: str | None = Cookie(None)):
    if not check_auth(nakama_auth):
        return RedirectResponse("/login?next=/bridge/franky", status_code=302)
    ctx = _gather_dashboard_context()
    return _templates.TemplateResponse(request, "franky.html", ctx)
