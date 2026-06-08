# Runbook: put Cloudflare Access in front of nakama.shosho.tw (ADR-044 #850)

**Why:** `nakama.shosho.tw` is served via an existing Cloudflare Tunnel
(`cloudflared` → `http://localhost:8000`). The tunnel bypasses `ufw`, so the
Robin/Bridge app is internet-reachable today guarded only by `WEB_PASSWORD`
(panel REJECT item, ADR-044 §B5). Cloudflare Access adds identity-based auth
**before** traffic reaches the tunnel — defense-in-depth in front of the app
password, no server-side change.

Audit that established the state: `docs/research/2026-06-08-vps-syncthing-exposure-audit.md`.

Prereq: you already run Cloudflare WAF skip rules (see `cf-waf-skip-rules.md`),
so the Zero Trust dashboard + `shosho.tw` zone are set up.

## Part A — Cloudflare Access application (dashboard, ~5 min)

All in the **Zero Trust dashboard** (one.dash.cloudflare.com), not the server.

1. **Zero Trust → Access → Applications → Add an application → Self-hosted.**
2. Application config:
   - **Application name:** `Nakama Bridge`
   - **Session duration:** 24h (or your preference)
   - **Public hostname:** `nakama.shosho.tw` (subdomain `nakama`, domain `shosho.tw`, path empty)
3. **Identity providers:** ensure at least one is configured (Zero Trust → Settings
   → Authentication). One-Time PIN (email) needs no setup; or use Google/GitHub.
4. **Policies → Add a policy:**
   - **Policy name:** `Allow 修修`
   - **Action:** Allow
   - **Include → Emails →** `shosho@shosho.tw` (add any other personal addresses)
   - (Optional, tighter: **Require → Login Methods** if you want a specific IdP.)
5. **Save.** Access is now enforced.

## Part B — Verify (golden path + edge)

- **Golden path:** open an incognito window → `https://nakama.shosho.tw` → you
  should hit the **Cloudflare Access login** (email OTP / IdP), *then* the app's
  own `WEB_PASSWORD` page. Two layers.
- **Edge — non-allowed identity:** sign in with an email NOT in the policy →
  Access denies before the app is reached.
- **API paths:** confirm `https://nakama.shosho.tw/robin/api/books/...` is also
  behind Access (Access applies to the whole hostname incl. `/api/*`). The app's
  own `require_auth_or_key` (PR #858) remains the second layer for programmatic
  callers — issue a **service token** in Access if you need non-interactive API
  access, and send it as the `CF-Access-Client-Id/Secret` headers.

## Part C — Optional server hardening (deploy change — authorize first)

Currently the app binds `0.0.0.0:8000`. `cloudflared` only needs `localhost`, so
bind to loopback to remove needless surface (today `ufw` blocks :8000, but a
loopback bind is defense-in-depth if `ufw` ever changes):

1. Find the unit: `systemctl cat thousand-sunny.service` — locate the
   `ExecStart` uvicorn line with `--host 0.0.0.0`.
2. Change `--host 0.0.0.0` → `--host 127.0.0.1` (keep `--port 8000`).
3. `systemctl daemon-reload && systemctl restart thousand-sunny.service`
4. Verify: `ss -tlnp | grep :8000` shows `127.0.0.1:8000` (not `0.0.0.0`); then
   re-run Part B golden path (tunnel still reaches it via localhost).

This step is a **deploy action** — outward-facing, do it yourself / explicitly
authorize. The audit doc notes it as optional, not blocking #850.

## Done criteria for #850

- [ ] Access application on `nakama.shosho.tw` with an Allow policy for your identity
- [ ] Incognito hits Access login before the app (Part B golden path)
- [ ] (optional) app bound to `127.0.0.1:8000`
