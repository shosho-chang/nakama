# Nakama Self-Backup (R2)

**What:** Multi-tier snapshots of Nakama's SQLite state DBs to a dedicated Cloudflare R2 bucket.
**Why:** xCloud only backs up services it deploys; Nakama's own `/home/nakama/data/*.db`
would otherwise have no off-VPS copy. `state.db` holds memories, approval queue,
publish jobs, cost ledger — months of accumulated work that a VPS loss would erase.
The 3-tier layout (daily / weekly / monthly) means older recovery points survive
even after the daily 30d window has rolled over.

## Scope

| File | Backed up | Rationale |
|---|---|---|
| `data/state.db` | yes | Primary: memories / approval_queue / publish_jobs / agent_runs / api_calls |
| `data/nakama.db` | yes (even if 0 bytes) | Phase 1 layout reserves it; future use shouldn't silently bypass backup |
| `data/google_*.json` | **no** | OAuth tokens — sensitive, also re-issuable via OAuth flow |
| `data/scimago_journals.csv` | no | Robin can re-download from Scimago |
| `data/vps_baseline.*` | no | Load-test artifacts, non-essential |

## Layout in bucket (3 tiers)

```
nakama-backup/
  state/YYYY/MM/DD/state.db.gz          # daily,   retention 30d
  state-weekly/YYYY-WNN/state.db.gz     # weekly,  retention 12w (Sundays only)
  state-monthly/YYYY-MM/state.db.gz     # monthly, retention 12m (1st of month only)
  nakama/...                             # same 3-tier layout
  nakama-weekly/...
  nakama-monthly/...
```

Date directories use Asia/Taipei calendar dates (matches operator perception
even when cron fires across UTC day boundary). Weekly uses ISO year-week
(`%G-W%V`) so year-boundary edge cases are handled correctly.

## Mechanism

1. `sqlite3` online `.backup` API on the live DB → atomic snapshot (1 writer + N readers unaffected)
2. `gzip` compress level 6 — balances CPU vs R2 storage
3. Tier plan: daily always; weekly added on Sundays; monthly added on day-of-month=1
4. Same `.gz` uploaded to all applicable tier prefixes
5. **After all uploads succeed**, prune each tier's prefix per its retention window

Retention only prunes on a successful run — a failed day never eats into the
previous day's snapshot window in any tier.

## Credentials (read/write mode separation)

| Env | Required | Purpose |
|---|---|---|
| `R2_ACCOUNT_ID` | yes | Shared with Franky verify — same Cloudflare account |
| `NAKAMA_R2_BACKUP_BUCKET` | yes | Destination bucket (e.g. `nakama-backup`) |
| `NAKAMA_R2_WRITE_ACCESS_KEY_ID` | optional | Write-scoped token (mode="write") |
| `NAKAMA_R2_WRITE_SECRET_ACCESS_KEY` | optional | — |
| `NAKAMA_R2_READ_ACCESS_KEY_ID` | optional | Read-scoped token (mode="read"; restore + integrity verify) |
| `NAKAMA_R2_READ_SECRET_ACCESS_KEY` | optional | — |
| `NAKAMA_R2_ACCESS_KEY_ID` | optional | Mode-agnostic fallback |
| `NAKAMA_R2_SECRET_ACCESS_KEY` | optional | Mode-agnostic fallback |
| `NAKAMA_BACKUP_RETENTION_DAYS` | optional | Daily tier; default `30` |
| `NAKAMA_BACKUP_WEEKLY_RETENTION_WEEKS` | optional | Weekly tier; default `12` |
| `NAKAMA_BACKUP_MONTHLY_RETENTION_MONTHS` | optional | Monthly tier; default `12` |
| `NAKAMA_DATA_DIR` | optional | Override source dir (testing); default `/home/nakama/data` |

**Recommended setup**: create two bucket-scoped R2 tokens for `nakama-backup`:
- Write token → `NAKAMA_R2_WRITE_*` (compromise = attacker can write garbage backup)
- Read token → `NAKAMA_R2_READ_*` (compromise = attacker can exfil snapshot, which contains memories)

Lookup chain ensures backwards compatibility: if neither write nor read scoped
tokens are set, the script falls back to mode-agnostic `NAKAMA_R2_*`, then to
base `R2_*`. Existing single-token deployments keep working.

## Why a separate bucket from xCloud

If Nakama backups lived alongside xCloud snapshots, Franky's `backup-verify`
(which asks "is the newest object in the bucket fresh?") would see the nightly
Nakama upload and always report OK — even if xCloud's weekly WP backup had
silently stopped. Separate buckets keep the two RPO signals independent.

## Cron (unchanged from Phase 1)

```cron
0 4 * * *  cd /home/nakama && /usr/bin/python3 scripts/backup_nakama_state.py \
    >> /var/log/nakama/nakama-backup.log 2>&1
```

04:00 Asia/Taipei — avoids 03:30 Franky backup-verify window and picks a quiet
VPS period before any morning agent work. Multi-tier logic is internal to the
script — no cron changes needed.

## Restore

Use `scripts/restore_from_r2.py` (added in Phase 1 DR runbook). For the
multi-tier paths above, future Phase 2B will add a `--tier` flag; until then,
restore reads the daily tier by default (which has 30d retention covering
the same window as before).

Manual restore via aws CLI (R2 endpoint configured), if script unavailable:

```bash
# Daily tier
aws s3 cp s3://nakama-backup/state/2026/04/23/state.db.gz /tmp/state.db.gz \
    --endpoint-url https://<ACCOUNT_ID>.r2.cloudflarestorage.com

# Weekly tier (older than 30 days but within 12 weeks)
aws s3 cp s3://nakama-backup/state-weekly/2026-W10/state.db.gz /tmp/state.db.gz \
    --endpoint-url https://<ACCOUNT_ID>.r2.cloudflarestorage.com

gunzip /tmp/state.db.gz                                  # → /tmp/state.db
sudo systemctl stop thousand-sunny nakama-gateway         # avoid overwrite-in-use
mv /home/nakama/data/state.db /home/nakama/data/state.db.pre-restore
cp /tmp/state.db /home/nakama/data/state.db
chown root:root /home/nakama/data/state.db
sudo systemctl start thousand-sunny nakama-gateway
```

## Observed size (2026-04-23)

| DB | Live size | gz size | Daily × 30d | + Weekly × 12w | + Monthly × 12m | Annual cost |
|---|---|---|---|---|---|---|
| `state.db` | ~312 KiB | ~100 KiB | ~3 MiB | +1.2 MiB | +1.2 MiB | <$0.01 |
| `nakama.db` | 0 bytes | — | — | — | — | $0 |

Total under 6 MiB across all tiers — R2 storage at $0.015/GB-month is
effectively free at this scale. Egress on restore is also free.

## Upgrade paths (not in Phase 2)

- **Litestream** — continuous WAL replication → RPO minutes instead of 1 day
  (ADR-006 §SPOF explicitly marks this as Phase 2 of approval queue work).
- **Secondary off-site target** — mirror to B2 / GCS for vendor-redundancy
  beyond R2's own multi-region replication. Ships in Phase 2B per
  `docs/plans/quality-bar-uplift-2026-04-25.md`.
- **Integrity check cron** — weekly verify that snapshots gunzip + open + pass
  `PRAGMA integrity_check`. Ships in Phase 2B as `scripts/verify_backup_integrity.py`.
- **Multi-region replication** — R2 already stores redundantly; cross-region
  only needed if Cloudflare's Asia region goes down for a sustained period.
