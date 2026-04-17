---
name: Cloudflare Tunnel 部署架構
description: VPS 對外透過 cloudflared tunnel 走 outbound 連到 CF edge，不開 inbound port
type: reference
tags: [vps, deployment, https, cloudflare]
created: 2026-04-17
updated: 2026-04-17
confidence: high
ttl: permanent
---

**外部入口：** `https://nakama.shosho.tw` （Cloudflare proxy → tunnel → VPS）

**VPS 端設定：**
- systemd service：`cloudflared`（官方 deb 安裝）
- 設定檔：`/etc/cloudflared/config.yml`
- Tunnel 名：`nakama`，credentials file 在 `/etc/cloudflared/<UUID>.json`
- Ingress rule：`nakama.shosho.tw` → `http://localhost:8000`
- CF dashboard DNS：`nakama` 是 CNAME 指向 tunnel（不是 A record）
- CF SSL mode 不用設（tunnel 自動處理 edge TLS）

**關鍵決策（為什麼不用 nginx/Caddy）：**
- VPS 的 80 port 被 LiteSpeed 佔著，那是修修個人網站（shosho.tw）的 web server，不能停
- Tunnel 走 outbound，不需要搶 80 port，也不用申請 LE cert
- VPS IP 被 CF 隱藏，攻擊面縮小；ufw 把 8000 對外關了

**VPS 防火牆（ufw）：**
- 對外只開 22 (SSH)、80 (LiteSpeed)、443、22000 (Syncthing)
- 8000 (Thousand Sunny) 只 listen 127.0.0.1，ufw 對外也關

**要改什麼時看哪裡：**
- Tunnel routing / ingress：`/etc/cloudflared/config.yml`
- Uvicorn bind host/port：`/etc/systemd/system/thousand-sunny.service`（目前 `--host 127.0.0.1`，只對 tunnel 可見）
- CF-level security（Bot Fight Mode、WAF）：CF dashboard → Security

**驗證 tunnel 有通：**
`sudo systemctl status cloudflared` → 應看到 `Registered tunnel connection`。
外部打 `curl -sI -H "User-Agent: Mozilla/5.0..." https://nakama.shosho.tw/`（不加 UA 會被 Bot Fight Mode 擋成 403）。
