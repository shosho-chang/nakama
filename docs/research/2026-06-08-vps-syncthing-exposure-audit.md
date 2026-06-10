# VPS Syncthing folder-set + exposure audit (ADR-044 Slice 0, #853 / #850)

Read-only inspection of `nakama-vps` (root@202.182.107.202) on 2026-06-08,
authorized by 修修. No changes were made — `ssh nakama-vps` read-only only.
Grounds ADR-044 Slice 0 issues **#853** (Syncthing folder set) and **#850**
(reverse-proxy auth), and resolves an open question from **#852**.

## 1. Syncthing folder set (#853) — CONFIRMED, no gap

Service: `syncthing@root.service` (active). Config: `/root/.local/state/syncthing/config.xml`.

The vault is shared as a **single folder**, not a per-subdir set:

| Field | Value |
|-------|-------|
| id | `czfdm-go36n` |
| label | `Shosho LifeOS` |
| path | `/home/Shosho LifeOS` |
| type | `sendreceive` (bidirectional peer) |
| `ignorePerms` | **`false`** — file permissions ARE synced |
| `.stignore` | **none** — no subdir is excluded |

Because the whole vault syncs with no ignore patterns, every directory Robin
reads/writes is covered. Verified present on the VPS vault:

- `KB/Wiki` ✓, `KB/Annotations` ✓, `KB/Raw` ✓, `KB/Attachments` ✓
- `Inbox/web` ✓, `Projects` ✓

`Watchlist/youtube` and `Files` do **not** exist yet — only because the VPS has
not yet ingested a video or fetched article images. They are created by Robin on
first use (`thousand_sunny/routers/robin.py:1442`, `agents/robin/image_fetcher.py`)
and, with no `.stignore`, sync automatically. **Not a gap — no action required.**

**Conclusion: the Syncthing folder set is already correct and complete for
ADR-044.** No folder needs to be added.

### Implication for #852 (annotation conflict control)

`ignorePerms="false"` means a `chmod 444` set by Robin on a written annotation
file **would propagate** through Syncthing. The #852 decision (detection-only,
no write-lock) stands — it is robust regardless of perm-sync and avoids false
confidence — but the `chmod 444` defense-in-depth path is **viable** here, not a
dead end. It can be added later without re-litigating the sync model.

### Note: vault-relative promotion state

`thousand_sunny/promotion_wiring.py:103,109` put `.promotion-manifests/` and
`.reading-context-packages/` under `vault_root`. With no `.stignore` these also
sync across machines. Harmless today (promotion commit is gated off until N519,
#851), but worth an `.stignore` entry if cross-machine promotion-state drift ever
appears.

## 2. Exposure (#850) — Cloudflare Tunnel already live; only WEB_PASSWORD guards it

`cloudflared` is installed (`/usr/local/bin/cloudflared`) and active. Tunnel
`7369fe09-60a8-48a2-931f-d554a1e277c7`, ingress (`/etc/cloudflared/config.yml`):

```yaml
ingress:
  - hostname: nakama.shosho.tw
    service: http://localhost:8000
  - service: http_status:404
```

The Thousand Sunny app listens on `0.0.0.0:8000` (python3, pid varies).

**Live risk (not future):** the tunnel connects outbound from `cloudflared` to
`localhost:8000`, so `ufw` (which allows only 22/80/443) does **not** gate it.
`nakama.shosho.tw` is reachable from the internet **today**, guarded only by the
app's `WEB_PASSWORD`. This is exactly the panel's objection — and it is current,
not hypothetical. (No Tailscale is installed; Cloudflare is the established path.)

Other listeners (context, not in scope): litespeed 80/443 (the WordPress
`shosho.tw` stack), nginx + traefik active, redis 6379, mysql 3306/33060 (bound
appropriately), syncthing GUI 127.0.0.1:8384 + sync 22000.

### Fix path for #850

1. **Cloudflare Access** application + policy on `nakama.shosho.tw` (Zero Trust
   dashboard — 修修's account; not visible from the server). Auth happens before
   traffic reaches the tunnel. Server-side: zero change. See runbook
   `docs/runbooks/cloudflare-access-nakama.md`.
2. **Optional hardening** (deploy change, 修修-authorized): bind the app to
   `127.0.0.1:8000` instead of `0.0.0.0:8000` — `cloudflared` only needs
   localhost, so `0.0.0.0` is needless surface (ufw blocks it now, but a localhost
   bind is defense-in-depth if ufw ever changes).

## Provenance

All facts above are from read-only `ssh nakama-vps` commands run 2026-06-08
(systemctl, ss -tlnp, ufw status, and cat of the syncthing + cloudflared config
files). No mutation, no deploy.
