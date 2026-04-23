"""Franky router — `GET /healthz` (UptimeRobot 外部 probe 契約 / ADR-007 §3).

Slice 1 範圍：只有 `/healthz`。`/bridge/franky` 儀表板由 Slice 3 接手。

設計約束（task prompt §4 + ADR-007 §3）：
- p95 < 200 ms（任務需求）
- 不查 DB、不打 LLM、不打 Slack
- 不需 auth（外部 probe 要能訪問）
- 只回 200（status=ok）/ 503（status=degraded）
- response 不含 secrets（observability.md §9）
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Response, status
from fastapi.responses import JSONResponse

from shared.schemas.franky import HealthzCheckEntry, HealthzResponseV1

router = APIRouter(tags=["franky"])

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
