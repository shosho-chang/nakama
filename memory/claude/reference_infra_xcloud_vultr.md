---
name: 基礎設施 — xCloud on Vultr
description: 修修的 WP 站與 Nakama VPS 規格、deploy 平台、升級考量
type: reference
tags: [infra, vps, vultr, xcloud, wordpress]
created: 2026-04-22
---

## 硬體

- **VPS**: Vultr
- **規格**: 2 vCPU / 4 GB RAM / 128 GB NVMe / 3 TB bandwidth
- **管理平台**: xCloud（WordPress deploy panel）
- **SSH alias**: `nakama-vps` → root@202.182.107.202

## 同 VPS 上跑的服務

1. Nakama Python services：thousand-sunny（web）+ nakama-gateway（Slack）
2. Cloudflare Tunnel daemon
3. WordPress blog: shosho.tw
4. WordPress community: fleet.shosho.tw（FluentCommunity + FluentCRM + FluentCart）
5. MariaDB × (預設 xCloud 共用一個 instance，不同 database)
6. PHP-FPM ×2 pool

## 備份

- 備份目的地：**Cloudflare R2**（已設定）
- Nakama state.db 備份狀況待確認（可能要 Franky 納管）

## 升級考量

- 4 GB RAM 跑兩個 WP 站 + 3 個 Python service 會緊，FluentCommunity 活躍度上升時會更吃緊
- **升級門檻建議**：日活會員 >50 或 FluentCRM 名單 >5000 時升 4vCPU/8GB
- Franky 要納管實體資源監控（RAM / disk / CPU）與告警

## How to apply

這是 Franky「系統維護」職責的具體對象。
Franky 寫的監控 code 不要 hardcode server spec，用 `/proc/meminfo` 動態讀，方便未來升級後無痛運作。
