---
name: Never scp .env over VPS without first diffing key names — append only new keys
description: Local .env is rarely the superset of VPS — overwriting silently loses VPS-only keys like UNPAYWALL_EMAIL
type: feedback
tags: [env, vps, deploy, scp, secret-management]
---
Do **not** `scp .env nakama-vps:/home/nakama/.env` as the default way to push env changes. Diff key names first; if VPS has keys local doesn't, append only the new keys instead of overwriting.

**Why:** 2026-04-24 session — 修修 asked "help me push NAKAMA_R2_* to VPS." A naive `scp` would have overwritten VPS `.env` and silently dropped 4 VPS-only keys: `DISABLE_ROBIN`, `FRANKY_R2_STALE_HOURS` (just set 1h earlier), `MODEL_ROBIN`, `UNPAYWALL_EMAIL` (PubMed OA fallback — would start 403'ing). Local `.env` accumulates dev-machine keys (AUPHONIC_*, XAI_*, transcribe config) that VPS doesn't need; VPS accumulates prod-only tweaks that local doesn't see. Neither side is strictly a superset in practice. The failure mode is silent: agents that were working start falling back to defaults or stub modes, and nothing raises.

**How to apply:**
```bash
# 1. Extract key names (not values) from both sides
grep -oE '^[A-Z][A-Z0-9_]+=' .env | sort -u > /tmp/local_keys.txt
ssh nakama-vps "grep -oE '^[A-Z][A-Z0-9_]+=' /home/nakama/.env | sort -u" > /tmp/vps_keys.txt

# 2. Show what would be LOST by overwrite
comm -23 /tmp/vps_keys.txt /tmp/local_keys.txt

# 3a. If output is empty → safe to scp
# 3b. If output is non-empty → append mode instead:
ssh nakama-vps 'cp /home/nakama/.env /home/nakama/.env.bak.$(date +%Y%m%d_%H%M%S)'
grep -E '^(NEW_KEY_1|NEW_KEY_2|...)=' .env | ssh nakama-vps 'cat >> /home/nakama/.env'
```

Always back up VPS `.env` with a dated suffix before any change. After the push, ask 修修 whether to pull VPS-only keys back into local `.env` so the two sides converge over time.
