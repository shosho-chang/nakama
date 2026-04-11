---
name: VPS 路徑參考
description: VPS 上的關鍵目錄路徑，避免混淆 repo 路徑和資料路徑
type: reference
tags: [vps, deployment, paths]
created: 2026-04-12
updated: 2026-04-12
confidence: high
ttl: permanent
---

- **Repo 根目錄**：`/home/nakama/`
- **Obsidian Vault**：`/home/Shosho LifeOS`
- **state.db**：`/home/nakama/data/state.db`

Vault 和 Repo 不在同一個父目錄底下。`data/` 已加進 `.gitignore`，不進 git。

**驗證來源：** vault_path 來自 `config.yaml` 第一行（`grep vault_path config.yaml`），不是推斷。路徑有疑問時，先查 config 或實際執行，不推斷。
