"""Franky weekly digest — Monday 10:00 Slack DM (ADR-007 §10).

Composes a 5-section digest from data already in state.db + a live psutil sample:

1. VPS snapshot      — psutil at digest-time (no vps_metrics table in Phase 1)
2. Cron success rate — `agent_runs` WHERE agent='franky' in last 7 days
3. Alert stats       — `alert_state` WHERE last_fired_at in last 7 days
4. R2 backup status  — `r2_backup_checks` in last 7 days
5. LLM week cost     — `api_calls` for this week + last week, via shared.pricing

Phase 1 rule (ADR-007 §10): pure string templating, NOT LLM-generated. The digest
is structured enough that template formatting beats LLM narration on both cost
and predictability.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import psutil

from shared.log import get_logger
from shared.pricing import calc_cost
from shared.state import _get_conn

logger = get_logger("nakama.franky.weekly_digest")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VPSSnapshot:
    cpu_pct: float
    ram_pct: float
    ram_used_mb: int
    ram_total_mb: int
    swap_pct: float
    disk_pct: float


@dataclass(frozen=True)
class CronSummary:
    total: int
    done: int
    failed: int
    running: int
    success_pct: float  # done / (done + failed), 0-100; 100 if no terminal runs


@dataclass(frozen=True)
class AlertSummary:
    critical_count: int
    warning_count: int
    info_count: int
    firing_now: int  # rows with state='firing' and suppress_until > now


@dataclass(frozen=True)
class BackupSummary:
    days_checked: int
    days_ok: int
    latest_status: str | None
    latest_size_mb: int | None
    latest_checked_at: str | None  # ISO


@dataclass(frozen=True)
class CostSummary:
    this_week_usd: float
    last_week_usd: float
    delta_pct: float  # (this - last) / last * 100; 0 if last week was 0


@dataclass(frozen=True)
class DigestBundle:
    period_start: datetime
    period_end: datetime
    vps: VPSSnapshot
    cron: CronSummary
    alerts: AlertSummary
    backup: BackupSummary
    cost: CostSummary
    operation_id: str


# ---------------------------------------------------------------------------
# Data gathering
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_op_id() -> str:
    return f"op_{uuid.uuid4().hex[:8]}"


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def sample_vps() -> VPSSnapshot:
    cpu = float(psutil.cpu_percent(interval=None))
    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()
    disk = psutil.disk_usage("/")
    return VPSSnapshot(
        cpu_pct=cpu,
        ram_pct=float(vm.percent),
        ram_used_mb=int(vm.used // (1024 * 1024)),
        ram_total_mb=int(vm.total // (1024 * 1024)),
        swap_pct=float(sw.percent),
        disk_pct=float(disk.percent),
    )


def summarise_cron(*, since: datetime, agent: str = "franky") -> CronSummary:
    conn = _get_conn()
    rows = conn.execute(
        """SELECT status, COUNT(*) AS n FROM agent_runs
           WHERE agent = ? AND started_at >= ?
           GROUP BY status""",
        (agent, _iso(since)),
    ).fetchall()
    counts = {r["status"]: int(r["n"]) for r in rows}
    done = counts.get("done", 0)
    failed = counts.get("failed", 0)
    running = counts.get("running", 0)
    total = sum(counts.values())
    terminal = done + failed
    success_pct = 100.0 if terminal == 0 else round(done / terminal * 100, 1)
    return CronSummary(
        total=total, done=done, failed=failed, running=running, success_pct=success_pct
    )


def summarise_alerts(*, since: datetime) -> AlertSummary:
    """Read from alert_state. Since alert_state keeps latest-per-dedup_key, this
    counts distinct alerting rules (not individual fires) — plus per-row fire_count
    can be surfaced if needed. For digest we just count rows by severity hint.

    alert_state has no explicit severity column (it's derived from the AlertV1 at
    dispatch time). We proxy by rule_id suffix — rules ending with _critical / _unhealthy
    / _missing are Critical; _warning is Warning; resolved (state='resolved') is info.
    """
    conn = _get_conn()
    rows = conn.execute(
        """SELECT rule_id, state, suppress_until
           FROM alert_state
           WHERE last_fired_at >= ?""",
        (_iso(since),),
    ).fetchall()
    critical = 0
    warning = 0
    info = 0
    firing_now = 0
    now = _now()
    for row in rows:
        rule = row["rule_id"]
        if row["state"] == "resolved":
            info += 1
        elif rule.endswith("_warning"):
            warning += 1
        else:
            critical += 1  # default: treat unknown / unhealthy / missing / critical as Critical
        # firing_now counts rows whose window hasn't expired
        if row["state"] == "firing":
            try:
                su = datetime.fromisoformat(row["suppress_until"])
                if su > now:
                    firing_now += 1
            except ValueError:
                firing_now += 1  # corrupt row — count as firing, conservative
    return AlertSummary(
        critical_count=critical,
        warning_count=warning,
        info_count=info,
        firing_now=firing_now,
    )


def summarise_backup(*, since: datetime) -> BackupSummary:
    conn = _get_conn()
    rows = conn.execute(
        """SELECT checked_at, status, latest_object_size
           FROM r2_backup_checks
           WHERE checked_at >= ?
           ORDER BY checked_at DESC""",
        (_iso(since),),
    ).fetchall()
    if not rows:
        return BackupSummary(
            days_checked=0,
            days_ok=0,
            latest_status=None,
            latest_size_mb=None,
            latest_checked_at=None,
        )
    # Count distinct days
    days_seen: set[str] = set()
    days_ok: set[str] = set()
    for row in rows:
        day = row["checked_at"][:10]
        days_seen.add(day)
        if row["status"] == "ok":
            days_ok.add(day)
    latest = rows[0]
    latest_size = latest["latest_object_size"]
    return BackupSummary(
        days_checked=len(days_seen),
        days_ok=len(days_ok),
        latest_status=latest["status"],
        latest_size_mb=int(latest_size) // (1024 * 1024) if latest_size else None,
        latest_checked_at=latest["checked_at"],
    )


def _week_cost(*, since: datetime, until: datetime) -> float:
    """Sum api_calls rows between since and until, convert to USD via shared.pricing."""
    conn = _get_conn()
    rows = conn.execute(
        """SELECT model,
                  SUM(input_tokens)       AS input_tokens,
                  SUM(output_tokens)      AS output_tokens,
                  SUM(cache_read_tokens)  AS cache_read_tokens,
                  SUM(cache_write_tokens) AS cache_write_tokens
           FROM api_calls
           WHERE called_at >= ? AND called_at < ?
           GROUP BY model""",
        (_iso(since), _iso(until)),
    ).fetchall()
    total = 0.0
    for row in rows:
        total += calc_cost(
            row["model"],
            input_tokens=int(row["input_tokens"] or 0),
            output_tokens=int(row["output_tokens"] or 0),
            cache_read_tokens=int(row["cache_read_tokens"] or 0),
            cache_write_tokens=int(row["cache_write_tokens"] or 0),
        )
    return total


def summarise_cost(*, period_end: datetime) -> CostSummary:
    this_start = period_end - timedelta(days=7)
    last_start = period_end - timedelta(days=14)
    this_week = _week_cost(since=this_start, until=period_end)
    last_week = _week_cost(since=last_start, until=this_start)
    if last_week == 0.0:
        delta_pct = 0.0
    else:
        delta_pct = round((this_week - last_week) / last_week * 100, 1)
    return CostSummary(
        this_week_usd=round(this_week, 4),
        last_week_usd=round(last_week, 4),
        delta_pct=delta_pct,
    )


# ---------------------------------------------------------------------------
# Compose
# ---------------------------------------------------------------------------


def gather(*, now: datetime | None = None) -> DigestBundle:
    """Pure data-gathering — no side effects on external services."""
    now = now or _now()
    since = now - timedelta(days=7)
    return DigestBundle(
        period_start=since,
        period_end=now,
        vps=sample_vps(),
        cron=summarise_cron(since=since),
        alerts=summarise_alerts(since=since),
        backup=summarise_backup(since=since),
        cost=summarise_cost(period_end=now),
        operation_id=_new_op_id(),
    )


def _format_delta(delta_pct: float) -> str:
    if delta_pct == 0.0:
        return "（與上週持平）"
    sign = "+" if delta_pct > 0 else ""
    return f"（vs 上週 {sign}{delta_pct:.1f}%）"


def render_slack_text(bundle: DigestBundle) -> str:
    """Render the digest bundle into Slack markdown."""
    period_start_tpe = bundle.period_start.astimezone(timezone(timedelta(hours=8)))
    period_end_tpe = bundle.period_end.astimezone(timezone(timedelta(hours=8)))

    vps = bundle.vps
    cron = bundle.cron
    alerts = bundle.alerts
    backup = bundle.backup
    cost = bundle.cost

    backup_line = (
        f"{backup.days_ok}/{backup.days_checked} 天 OK" if backup.days_checked else "尚無紀錄"
    )
    backup_latest = (
        f"最新 {backup.latest_size_mb} MB · {backup.latest_status}"
        if backup.latest_size_mb is not None
        else "—"
    )

    return "\n".join(
        [
            f"*🤖 Franky Weekly Digest · {period_end_tpe:%Y-%m-%d}*",
            f"_週期：{period_start_tpe:%Y-%m-%d} → {period_end_tpe:%Y-%m-%d}（台北時區）_",
            "",
            "*1. VPS 快照（當下）*",
            (
                f"• CPU `{vps.cpu_pct:.1f}%` · RAM `{vps.ram_pct:.1f}%` "
                f"({vps.ram_used_mb}/{vps.ram_total_mb} MB) · "
                f"Swap `{vps.swap_pct:.1f}%` · Disk `{vps.disk_pct:.1f}%`"
            ),
            "",
            "*2. Franky Cron 成功率（過去 7 天）*",
            (
                f"• 總 {cron.total} 次 · done {cron.done} · failed {cron.failed} · "
                f"running {cron.running} · success_pct `{cron.success_pct:.1f}%`"
            ),
            "",
            "*3. Alert 統計（過去 7 天）*",
            (
                f"• Critical `{alerts.critical_count}` · Warning `{alerts.warning_count}` · "
                f"Info `{alerts.info_count}` · 目前 firing `{alerts.firing_now}`"
            ),
            "",
            "*4. R2 備份（過去 7 天）*",
            f"• {backup_line} · {backup_latest}",
            "",
            "*5. LLM 花費（本週 vs 上週）*",
            f"• 本週 `${cost.this_week_usd:.2f}` {_format_delta(cost.delta_pct)}",
            "",
            f"_op=`{bundle.operation_id}`_",
        ]
    )


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


def build_digest_text(*, now: datetime | None = None) -> tuple[DigestBundle, str]:
    """Convenience — gather + render in one call."""
    bundle = gather(now=now)
    return bundle, render_slack_text(bundle)


def send_digest(*, slack_bot: Any | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Compose the digest and send via Slack DM.

    Returns:
        dict(operation_id, slack_ts, text_preview)
    """
    bundle, text = build_digest_text(now=now)
    if slack_bot is None:
        from agents.franky.slack_bot import FrankySlackBot

        slack_bot = FrankySlackBot.from_env()
    slack_ts = slack_bot.post_plain(text, context="weekly_digest")
    logger.info(
        "weekly_digest sent op=%s slack_ts=%s chars=%s",
        bundle.operation_id,
        slack_ts,
        len(text),
    )
    return {
        "operation_id": bundle.operation_id,
        "slack_ts": slack_ts,
        "text_preview": text[:300],
    }
