# Capability: Franky Monitor

**Status:** Slice 1 + 2 + 3 landed — health checks, alert router, R2 backup verify, weekly digest, and `/bridge/franky` dashboard are all shipped.
**ADR:** [ADR-007 Franky Phase 1 — 基礎設施監控 (slim 版)](../decisions/ADR-007-franky-scope-expansion.md)
**Principles:** [reliability](../principles/reliability.md), [observability](../principles/observability.md), [schemas](../principles/schemas.md)

Franky is Nakama's infrastructure watchdog. It probes VPS resources, WordPress sites, the Nakama gateway, and R2 backups, then dispatches Critical alerts to Slack while suppressing dupes in a 15-minute window.

---

## Scope

| Module | What it does |
|---|---|
| `agents/franky/health_check.py` | 5-min cron probe: VPS / WP×2 / Nakama gateway, 3-consecutive-fail state machine |
| `agents/franky/alert_router.py` | Dedup via `alert_state` table (ADR-007 §4), dispatch to Slack |
| `agents/franky/r2_backup_verify.py` | Daily probe of R2 backups; 連 2 日失敗 → Critical |
| `agents/franky/slack_bot.py` | `slack_sdk` wrapper, DM to `SLACK_USER_ID_SHOSHO`, no-op stub when env missing; `post_alert` + `post_plain` surfaces |
| `agents/franky/weekly_digest.py` | Monday 10:00 Slack DM — 5 sections (VPS / cron / alerts / backup / cost), pure template, no LLM |
| `thousand_sunny/routers/franky.py` | `GET /healthz` (Slice 1) + `GET /bridge/franky` dashboard (Slice 3) |
| `thousand_sunny/templates/bridge/franky.html` | Direction B-styled dashboard: 4 probe cards + 24h alert list + R2 strip |
| `shared/r2_client.py` | Read-only boto3 wrapper for R2 (Cloudflare Object Storage) |
| `shared/schemas/franky.py` | Pydantic contracts (`HealthProbeV1` / `AlertV1` / `HealthzResponseV1` / `WeeklyDigestV1`) |

---

## Inputs (env)

| Env | Required? | Purpose |
|---|---|---|
| `SLACK_FRANKY_BOT_TOKEN` | optional† | Slack bot token (xoxb-...); missing → DM is a logged no-op |
| `SLACK_USER_ID_SHOSHO` | optional† | Target user for DMs (U07XXXXXXX) |
| `NAKAMA_HEALTHZ_URL` | optional | Override loopback probe URL; defaults to `http://127.0.0.1:8000/healthz` |
| `WP_SHOSHO_{BASE_URL,USERNAME,APP_PASSWORD}` | optional | When set, Franky probes shosho.tw health |
| `WP_FLEET_{BASE_URL,USERNAME,APP_PASSWORD}` | optional | When set, Franky probes fleet.shosho.tw health |
| `R2_ACCOUNT_ID` / `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` / `R2_BUCKET_NAME` | optional‡ | Backup verification; missing → status=missing, no alert on first day |
| `FRANKY_R2_PREFIX` | optional | Object prefix filter (e.g. `daily/`); default = "" |
| `FRANKY_R2_STALE_HOURS` | optional | Latest snapshot age threshold; default = 25h |
| `FRANKY_R2_MIN_SIZE_BYTES` | optional | Below this = `too_small`; default = 1 MiB |

† Dev + CI run without Slack env; stub logs the alert.
‡ Dev + CI run without R2 env; verify returns `status=missing` with no alert unless 2 consecutive day failures are in the local DB.

---

## Outputs

### Data

- **SQLite tables** (`shared/state.py`): `health_probe_state`, `alert_state`, `r2_backup_checks`
- **Logs** — structured via `shared/log.py` — on every probe, dispatch, and verify
- **Slack DMs** — only when:
  - Critical or Warning severity alerts that are not suppressed by dedup
  - Resolved info alerts (rule_id ends with `_recovered` / `_resolved`), not deduped

### CLI

```bash
python -m agents.franky health         # one 5-min tick; probes all 4 targets; dispatches alerts
python -m agents.franky alert --test   # synthetic info alert (self-test Slack wiring)
python -m agents.franky backup-verify  # one R2 daily probe; returns non-zero exit when Critical emitted
python -m agents.franky digest         # compose + send weekly digest DM (5 sections)
python -m agents.franky                # legacy weekly report (backward-compat for current cron)
```

### HTTP

```
GET /healthz         →  200 + HealthzResponseV1   (process-level; < 200ms; no DB/Slack/LLM)
                        503 + degraded payload    (only when response cannot be built)

GET /bridge/franky   →  200 + HTML dashboard       (cookie auth; reads health_probe_state
                                                   + alert_state + r2_backup_checks + psutil)
                        302 /login?next=/bridge/franky  (when unauthenticated)
```

---

## Alert contract (AlertV1)

```python
AlertV1(
    rule_id="vps_disk_critical",          # lowercase, [a-z0-9_], 3-64 chars
    severity="critical",                   # critical | warning | info
    title="VPS disk usage critical",      # short human summary
    message="disk at 97.2% (>= 95%)",
    fired_at=<aware UTC>,
    dedup_key="vps_disk_critical",         # defaults to rule_id; add dimensions as needed
    dedup_window_seconds=900,              # 60 ≤ x ≤ 86400; default 15 min
    operation_id="op_a3f2e9b4",
    context={"disk_pct": 97.2},            # structured data, no secrets
)
```

Router state machine (per dedup_key):

```
none ─(first fire)─▶  firing (suppress_until = now + dedup_window)
                          │
                          ├─(repeat within window)──▶ suppressed, fire_count++
                          │
                          ├─(repeat after window)──▶  send again, suppress_until renewed
                          │
                          └─(info + _recovered)───▶ resolved (state='resolved')
```

Corrupt state rows (unparseable `suppress_until`) degrade safely to "send again".

---

## SLO (ADR-007 §10)

| Metric | Target | Measurement |
|---|---|---|
| `run_once()` total wall time | p95 < 30 s | captured in result `duration_ms` |
| `/healthz` response time | p95 < 200 ms | TestClient + uptime probe |
| Alert dedup correctness | same `dedup_key` within 15 min → 1 Slack DM | manual Slack audit |
| R2 backup freshness | daily snapshot ≤ 25h old | `r2_backup_checks.status = 'ok'` ratio |

---

## Cost

- LLM: **zero**. Franky emits no LLM calls in Phase 1 (digest generation uses string templates, not Claude).
- Slack: free workspace messaging; rate-limited to ~1 msg/sec by slack_sdk retry policy.
- R2: list + head only; negligible within free tier.
- SQLite writes: ~10 rows/day (cron_runs scope pending Slice 3).

---

## Testability

- **Zero external network in tests** — `conftest.isolated_db` gives a fresh SQLite per test; slack / psutil / boto3 / httpx are mocked per-test.
- Every module exports a single public entry: `run_once` / `dispatch` / `verify_once` / `post_alert` — so downstream callers depend on a narrow surface.
- State-machine tests (3-fail threshold, dedup window, consecutive-day counter) use explicit DB seeding instead of time travel — deterministic.

---

## Open-source readiness

Per [feedback_open_source_ready.md](../../memory/claude/feedback_open_source_ready.md):

- All external couplings behind factories (`WordPressClient.from_env`, `R2Client.from_env`, `FrankySlackBot.from_env`) — swappable.
- `AlertV1` / `HealthProbeV1` / `HealthzResponseV1` have `schema_version` for wire-compat.
- Capability is self-contained: pull `agents/franky/`, `shared/schemas/franky.py`, `shared/r2_client.py`, `thousand_sunny/routers/franky.py`, and the migration file — works in any FastAPI + SQLite project.
- No Nakama-specific assumptions except the `alert_state` / `health_probe_state` / `r2_backup_checks` tables, which are standard enough to port.

Missing for OSS release:
- A generic `config.yaml` for threshold knobs (currently env-only).
- Pluggable notifier interface (today only Slack; email / PagerDuty / Discord would need new module).
- Phase 2: `vps_monitor.py` time-series sampler (today VPS snapshot is single-point at digest/dashboard time) + `cron_wrapper.py` + charts.

---

## Related runbooks

- [UptimeRobot setup](../runbooks/uptimerobot-setup.md) — 修修 manual; configure external probe after VPS deploy
- WP credentials: [setup-wp-integration-credentials.md](../runbooks/setup-wp-integration-credentials.md)
