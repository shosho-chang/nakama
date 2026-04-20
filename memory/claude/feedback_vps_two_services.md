---
name: VPS 兩個獨立 service
description: thousand-sunny 和 nakama-gateway 是獨立 systemd service，部署時要各自重啟
type: feedback
---

VPS 上有兩個 service：

- `thousand-sunny.service` — FastAPI web server（Uvicorn）
- `nakama-gateway.service` — Slack Socket Mode gateway（bot.py）

**Why:** 2026-04-20 部署 PR #55 時只重啟了 thousand-sunny，gateway 還在跑舊 code，Sanji bot 沒啟動，花了一段時間才找到原因。

**How to apply:** 每次部署涉及 gateway 相關改動（`gateway/`、agent handler、router）時，兩個 service 都要重啟，或至少明確確認哪個需要重啟。確認指令：`journalctl -u nakama-gateway -n 5` 看啟動時間。
