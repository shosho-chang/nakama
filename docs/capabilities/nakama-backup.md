# Nakama Self-Backup (R2)

**What:** Daily snapshot of Nakama's SQLite state DBs to a dedicated Cloudflare R2 bucket.
**Why:** xCloud only backs up services it deploys; Nakama's own `/home/nakama/data/*.db`
would otherwise have no off-VPS copy. `state.db` holds memories, approval queue,
publish jobs, cost ledger — months of accumulated work that a VPS loss would erase.

## Scope

| File | Backed up | Rationale |
|---|---|---|
| `data/state.db` | yes | Primary: memories / approval_queue / publish_jobs / agent_runs / api_calls |
| `data/nakama.db` | yes (even if 0 bytes) | Phase 1 layout reserves it; future use shouldn't silently bypass backup |
| `data/google_*.json` | **no** | OAuth tokens — sensitive, also re-issuable via OAuth flow |
| `data/scimago_journals.csv` | no | Robin can re-download from Scimago |
| `data/vps_baseline.*` | no | Load-test artifacts, non-essential |

## Layout in bucket

```
nakama-backup/
  state/YYYY/MM/DD/state.db.gz
  nakama/YYYY/MM/DD/nakama.db.gz
```

Date directories make manual inspection (via R2 dashboard) human-readable.

## Mechanism

1. `sqlite3` online `.backup` API on the live DB → atomic snapshot (1 writer + N readers unaffected)
2. `gzip` compress level 6 — balances CPU vs R2 storage
3. `boto3` S3-compatible put to R2
4. **After all uploads succeed**, delete objects older than
   `NAKAMA_BACKUP_RETENTION_DAYS` (default 30) under each prefix

Retention only prunes on a successful run — a failed day never eats into the
previous day's snapshot window.

## Credentials

| Env | Required | Purpose |
|---|---|---|
| `R2_ACCOUNT_ID` | yes | Shared with Franky verify — same Cloudflare account |
| `NAKAMA_R2_BACKUP_BUCKET` | yes | Destination bucket (e.g. `nakama-backup`) |
| `NAKAMA_R2_ACCESS_KEY_ID` | optional | Scope-limited token; falls back to `R2_ACCESS_KEY_ID` |
| `NAKAMA_R2_SECRET_ACCESS_KEY` | optional | Scope-limited secret; falls back to `R2_SECRET_ACCESS_KEY` |
| `NAKAMA_BACKUP_RETENTION_DAYS` | optional | Days to keep; default `30` |
| `NAKAMA_DATA_DIR` | optional | Override source dir (testing); default `/home/nakama/data` |

If `R2_*` credentials are account-wide (can write to any bucket), leaving
`NAKAMA_R2_*` unset is fine. A bucket-scoped write token is safer — set both
`NAKAMA_R2_ACCESS_KEY_ID` and `NAKAMA_R2_SECRET_ACCESS_KEY`.

## Why a separate bucket from xCloud

If Nakama backups lived alongside xCloud snapshots, Franky's `backup-verify`
(which asks "is the newest object in the bucket fresh?") would see the nightly
Nakama upload and always report OK — even if xCloud's weekly WP backup had
silently stopped. Separate buckets keep the two RPO signals independent.

## Cron

```cron
0 4 * * *  cd /home/nakama && /usr/bin/python3 scripts/backup_nakama_state.py \
    >> /var/log/nakama/nakama-backup.log 2>&1
```

04:00 Asia/Taipei — avoids 03:30 Franky backup-verify window and picks a quiet
VPS period before any morning agent work.

## Restore

To restore `state.db` from a given snapshot date:

```bash
# Fetch gz from R2 (aws CLI configured with R2 endpoint, or R2 dashboard)
aws s3 cp s3://nakama-backup/state/2026/04/23/state.db.gz /tmp/state.db.gz \
    --endpoint-url https://<ACCOUNT_ID>.r2.cloudflarestorage.com

gunzip /tmp/state.db.gz                                  # → /tmp/state.db
sudo systemctl stop thousand-sunny nakama-gateway         # avoid overwrite-in-use
mv /home/nakama/data/state.db /home/nakama/data/state.db.pre-restore
cp /tmp/state.db /home/nakama/data/state.db
chown root:root /home/nakama/data/state.db
sudo systemctl start thousand-sunny nakama-gateway
```

## Observed size (2026-04-23)

| DB | Live size | gz size | Daily × 30-day retention |
|---|---|---|---|
| `state.db` | ~312 KiB | ~100 KiB | ~3 MiB |
| `nakama.db` | 0 bytes | — | — |

Well under 1 MiB / day total. R2 storage at $0.015/GB-month means annual cost
is effectively zero; egress on restore is also free.

## Upgrade paths (not in Phase 1)

- **Litestream** — continuous WAL replication → RPO minutes instead of 1 day
  (ADR-006 §SPOF explicitly marks this as Phase 2).
- **Franky probe of `nakama-backup`** — extend `r2_backup_verify` to check the
  self-backup bucket's freshness (new rule, not yet wired).
- **Multi-region replication** — R2 already stores redundantly; cross-region
  only needed if Cloudflare's Asia region goes down for a sustained period.
