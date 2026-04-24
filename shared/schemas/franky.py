"""Franky monitoring contracts (ADR-007 §3 / §4 / §10).

Schema 定義順序（依相依性）：
    HealthProbeV1 → HealthzResponseV1
    AlertV1
    VPSResourceSampleV1 → WeeklyDigestV1

所有 schema 遵守 docs/principles/schemas.md：
- extra="forbid" + frozen=True
- 持久化 schema 必須有 schema_version
- AwareDatetime 取代 str timestamps
- Literal 取代 str enums
"""

from __future__ import annotations

from typing import Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    constr,
)

# 探測目標白名單（Phase 1）。Phase 2 加新 target 時升 V2，避免靜默接受未知 target。
ProbeTarget = Literal[
    "vps_resources",
    "wp_shosho",
    "wp_fleet",
    "nakama_gateway",
    "r2_backup_nakama",
]

# 連續失敗門檻：跨過後升 Critical。ADR-007 §8 三連 fail 才升告警。
DEFAULT_FAIL_THRESHOLD: int = 3

# Alert dedup window 預設 15 分鐘（ADR-007 §4）
DEFAULT_DEDUP_WINDOW_S: int = 15 * 60


# ---------------------------------------------------------------------------
# Health probe（ADR-007 §4）
# ---------------------------------------------------------------------------


class HealthProbeV1(BaseModel):
    """單一 target 一次 probe 的結果。

    狀態機（health_probe_state 表）由 health_check.py 維護：
    fail → consecutive_fails += 1；ok → consecutive_fails = 0。
    跨過 fail_threshold 才推 AlertV1 進 router。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    target: ProbeTarget
    status: Literal["ok", "fail"]
    checked_at: AwareDatetime
    latency_ms: int = Field(ge=0)
    error: str | None = None  # status=fail 時的簡短原因（不含 secrets）
    detail: dict[str, str | int | float | bool] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# /healthz endpoint（ADR-007 §3）
# ---------------------------------------------------------------------------


class HealthzCheckEntry(BaseModel):
    """/healthz 內部子 check 的單一條目。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    name: constr(pattern=r"^[a-z_][a-z0-9_]{1,31}$")
    status: Literal["ok", "degraded", "unknown"]


class HealthzResponseV1(BaseModel):
    """`GET /healthz` response（外部 UptimeRobot 探測契約）。

    硬約束（ADR-007 §3 + task prompt §4）：
    - p95 < 200 ms
    - 不查 DB、不打 LLM、不打 Slack
    - 只回 200（status=ok）/ 503（status=degraded）
    - 不需 auth

    Phase 1 只回 process-level 訊號（uptime + 模組是否載入成功）；
    Phase 2 加 in-memory cached 30s 的 DB ping。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    status: Literal["ok", "degraded"]
    service: Literal["nakama-gateway"] = "nakama-gateway"
    version: str
    uptime_seconds: int = Field(ge=0)
    checks: list[HealthzCheckEntry] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Alert event（ADR-007 §4 / §8）
# ---------------------------------------------------------------------------


class AlertV1(BaseModel):
    """alert_router 接受的事件 schema。

    health_check / r2_backup_verify / vps_monitor 各自決定升 alert 的條件，
    產出 AlertV1 推給 router。Router 是 stateless，邏輯只負責 dedup + 分派。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    rule_id: constr(pattern=r"^[a-z_][a-z0-9_]{2,63}$")  # e.g. "wp_shosho_down"
    severity: Literal["critical", "warning", "info"]
    title: constr(min_length=1, max_length=120)
    message: constr(min_length=1, max_length=500)
    fired_at: AwareDatetime
    # dedup_key 預設 = rule_id；多維度告警（per host / per target）可加後綴
    dedup_key: constr(min_length=1, max_length=128)
    dedup_window_seconds: int = Field(default=DEFAULT_DEDUP_WINDOW_S, ge=60, le=24 * 3600)
    operation_id: constr(pattern=r"^op_[0-9a-f]{8}$")
    # 額外結構化欄位供 dashboard / log 顯示（不能含 secrets，observability.md §9）
    context: dict[str, str | int | float | bool] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# VPS resource sample（Slice 2/3 vps_monitor 會寫滿；Slice 1 先定 schema）
# ---------------------------------------------------------------------------


class VPSResourceSampleV1(BaseModel):
    """單次 VPS 資源採樣。對應 ADR-007 §5 vps_metrics 表的 row 形狀。"""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    sampled_at: AwareDatetime
    cpu_pct: float = Field(ge=0, le=100)
    ram_used_mb: int = Field(ge=0)
    ram_total_mb: int = Field(ge=0)
    swap_used_mb: int = Field(ge=0)
    disk_used_pct: float = Field(ge=0, le=100)
    load_1m: float = Field(ge=0)


# ---------------------------------------------------------------------------
# Weekly digest（Slice 3 weekly_digest.py 會填；Slice 1 先凍結 schema）
# ---------------------------------------------------------------------------


class WeeklyDigestV1(BaseModel):
    """週一早上 10:00 Slack DM 週報的結構化內容。

    ADR-007 §10：Phase 1 純 template + 字串格式化，不用 LLM 生成。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    period_start: AwareDatetime
    period_end: AwareDatetime
    # VPS
    vps_cpu_avg_pct: float = Field(ge=0, le=100)
    vps_cpu_p95_pct: float = Field(ge=0, le=100)
    vps_ram_avg_pct: float = Field(ge=0, le=100)
    vps_disk_used_pct: float = Field(ge=0, le=100)
    # cron
    cron_total_runs: int = Field(ge=0)
    cron_success_pct: float = Field(ge=0, le=100)
    cron_slowest_p95_ms: int = Field(ge=0)
    # backup
    r2_backup_days_ok: int = Field(ge=0, le=7)
    r2_backup_latest_size_mb: int = Field(ge=0)
    # alerts
    critical_count: int = Field(ge=0)
    warning_count: int = Field(ge=0)
    # cost
    llm_cost_usd_week: float = Field(ge=0)
    llm_cost_delta_pct: float  # 相對上週 +/- %
