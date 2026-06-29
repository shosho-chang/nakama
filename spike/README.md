# Zoro Coach — Phase 0 spike instrumentation

Throwaway scripts to settle the **empirical** half of the Phase 0 spike
(v2 §6). They are deliberately isolated from production: nothing here is
imported by `agents/` or `shared/`, and the dep is **not** in
`requirements.txt` / `pyproject.toml`. Findings flow back into
`docs/research/2026-06-29-zoro-coach-implementation-plan-v2.md` §7/§10.

> **Why these exist:** the research-able half of Phase 0 (Q1 Tredict landing,
> Q2 schema shape, Q4 token mechanism, Q3 watch capability, Q5 typical latency)
> is **done and verified against live primary sources** — see the v2 §10 "Phase 0
> 結果回填" block. What remains can only be produced from **your personal Garmin
> account + your watch + real elapsed VPS time**. These scripts produce exactly
> those numbers. They do **not** run in CI and have **not** been executed against
> a live account (no credentials in repo). What HAS been verified here: the dep
> installs + imports on Python 3.14, all five scripts `py_compile`, and the token
> APIs they call (`Garmin.login(tokenstore)`, `g.client.dump(path)`,
> `get_activities_by_date`, `get_activity_exercise_sets`) were confirmed against
> the **installed** garminconnect 0.3.6 (the Garmin object has no `.dump`/`.garth`
> — persistence is on the inner DI client). What remains is a live login to
> produce YOUR numbers.

## Prerequisites

- **Python ≥ 3.12** (garminconnect 0.3.x dropped 3.11; it replaced the dead
  `garth` with a native DI OAuth client).
- A throwaway venv (keep the spike dep out of the project env). **Already created
  + verified** at `.venv-spike/` (garminconnect 0.3.6 on Python 3.14, imports OK);
  it is local-only and not committed. To recreate elsewhere:
  ```powershell
  python -m venv .venv-spike
  .venv-spike\Scripts\activate
  pip install -r spike/requirements-spike.txt
  ```
- Your Garmin Connect login via env vars (only for the first interactive login):
  ```powershell
  $env:GARMIN_EMAIL="you@example.com"
  $env:GARMIN_PASSWORD="..."
  ```

## What each script answers

| Spike Q | Script | Produces |
|---|---|---|
| **Q4** first login | `garmin_auth_spike.py` | Writes `data/garmin/garmin_tokens.json` (gitignored) + prints the scp-to-VPS handoff. **Run once, locally** (handles MFA). |
| **Q2** exerciseSets | `dump_exercise_sets.py --since 8w` | Dumps each strength activity's raw `exerciseSets` JSON to `spike/samples/` + a `_report.json` (set counts, weight-null rate, gram sanity, category/name enums). |
| **Q4** token survival | `garmin_token_probe.py` | Daily cron on the VPS. Appends `token_probe.jsonl`. First `ok:false` minus login date = real non-interactive survival window. |
| **Q5** sync latency | `sync_latency_probe.py --watch 15` | After a workout, polls until it appears in the cloud + reports lag since activity end. |

## Run order

```powershell
# 1. ONCE, on your trusted local machine (handles password + MFA):
python spike/garmin_auth_spike.py
#    -> copy data/garmin to the VPS as printed.

# 2. Dump your real strength data (Q2 — locks the schema adapter):
python spike/dump_exercise_sets.py --since 8w
#    -> inspect spike/samples/_report.json. Confirm: weight in grams? reps/weight
#       null on REST? any non-null exercises[].name? gram range looks like real kg?

# 3. On the VPS, add a daily cron line (Q4 — measures token TTL over weeks):
#    30 7 * * *  cd /home/nakama && GARMINTOKENS=/home/nakama/data/garmin \
#      /home/nakama/.venv-spike/bin/python spike/garmin_token_probe.py \
#      >> /var/log/nakama/garmin-token-probe.log 2>&1

# 4. After a real workout, measure sync latency (Q5):
python spike/sync_latency_probe.py --watch 15
```

## Token model (Q4 — corrects the v2 draft)

`garth` is dead (deprecated 2026-03-27). garminconnect **0.3.0+** ships a native
DI OAuth client; the token store is a **directory** holding `garmin_tokens.json`
(`di_token` = short-lived access JWT, `di_refresh_token`, `di_client_id`).
Refresh is silent (`grant_type=refresh_token`, no MFA), so non-interactive
survival = the **di_refresh_token lifetime** — which Garmin/the library do **not
publish**. Hence step 3's longitudinal probe. Re-MFA is forced by: refresh-token
expiry/revoke, password change, sign-out-all-devices, a future Garmin SSO change,
or VPS-IP geo-anomaly. The local→VPS token copy mirrors `scripts/google_calendar_auth.py`.

## Out of scope here

- **Tredict (Q1)** is Phase 2 and the headless question is already resolved
  (direct HTTP `/api/oauth/v2/plan` with a Personal API Token — no MCP client —
  but a manual "apply plan to calendar" tap is required to reach the watch).
  A power-step plan body and the endpoint are documented in v2 §10; a Tredict
  push script is only worth building once you have a (US$49/yr) account.
- **Q3 watch target-weight** needs your **watch model** + an on-device test; no
  script can substitute for pressing buttons on the watch.
