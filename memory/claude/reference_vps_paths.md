---
name: VPS 路徑參考
description: VPS 上的關鍵目錄路徑，避免混淆 repo 路徑和資料路徑
type: reference
tags: [vps, deployment, paths]
created: 2026-04-12
updated: 2026-04-17
confidence: high
ttl: permanent
---

- **Repo 根目錄**：`/home/nakama/`
- **Obsidian Vault**：`/home/Shosho LifeOS`
- **state.db**：`/home/nakama/data/state.db`
- **systemd services**：`thousand-sunny`（port 8000）、`cloudflared`（tunnel）
- **cloudflared config**：`/etc/cloudflared/config.yml`

### 系統服務注意事項（2026-04-20）

- 生效的 web 服務是 **`thousand-sunny.service`**（ExecStart `uvicorn thousand_sunny.app:app --host 0.0.0.0 --port 8000`，EnvironmentFile `/home/nakama/.env`）
- 舊的 **`nakama-web.service`** 仍 `enabled` 但指向 2026-04-15 已移除的 `agents.robin.web:app`；一旦重啟會進 restart loop。目前已 `systemctl stop`，但沒 disable —— reboot 會再拋 fail，要徹底關掉需 `systemctl disable nakama-web`（修修授權後執行）
- 重啟正確服務：`systemctl restart thousand-sunny`（不要 restart nakama-web）

Vault 和 Repo 不在同一個父目錄底下。`data/` 已加進 `.gitignore`，不進 git。

**注意：VPS 同時有修修的個人網站（shosho.tw）跑在 LiteSpeed（port 80）** — 不是 Nakama 的服務，不可動。Nakama 走 CF Tunnel 不搶 80 port。詳見 `reference_cloudflare_tunnel.md`。

**驗證來源：** vault_path 來自 `config.yaml` 第一行（`grep vault_path config.yaml`），不是推斷。路徑有疑問時，先查 config 或實際執行，不推斷。
