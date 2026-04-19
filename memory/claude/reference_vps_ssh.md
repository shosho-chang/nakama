---
name: VPS SSH Alias
description: VPS 登入方式 — 用 `nakama-vps` alias，IP 202.182.107.202 root user
type: reference
tags: [vps, ssh, deployment]
created: 2026-04-19
updated: 2026-04-19
confidence: high
ttl: permanent
---

**SSH alias：** `nakama-vps`（設定於 `~/.ssh/config`）
**實際：** `ssh root@202.182.107.202`
**Identity：** `~/.ssh/id_ed25519`

**~/.ssh/config：**
```
Host nakama-vps
    HostName 202.182.107.202
    User root
    IdentityFile ~/.ssh/id_ed25519
```

**常用指令：**
- `ssh nakama-vps`
- `scp local-file nakama-vps:/home/nakama/data/`
- `ssh nakama-vps "cd /home/nakama && git pull && systemctl restart nakama-gateway"`

**注意：**
- Claude Code 預設可能 deny ssh/scp 指令 — 修修自己在終端機跑，不要透過我代執行
- 記憶路徑用 `reference_vps_paths.md` 查 repo / data / vault 位置
- VPS 沒裝 `python-is-python3` → **用 `python3` 不用 `python`**（pip 同理改 `pip3`）
