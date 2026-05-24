---
name: project-vault-path
description: Local Obsidian vault path on this Windows machine — E:\Shosho LifeOS
metadata:
  type: project
---

修修的 Obsidian LifeOS vault 在本機 (Windows) 的絕對路徑是：

```
E:\Shosho LifeOS
```

（`config.yaml` 寫的 `/home/Shosho LifeOS` 是 VPS 路徑；本機要走 `VAULT_PATH` env var 或直接讀 `E:\Shosho LifeOS`。）

**Why:** 多次對話都需要這個基本資訊（agent 寫入路徑、vault 結構掃描、ingest 流程驗證），不應每次都問修修。修修在 2026-05-19 對話中明確表達「這麼重要的基本資訊為什麼會不知道」。

**How to apply:** 任何要對 vault 做掃描 / 讀寫測試 / 路徑解析的任務，預設用 `E:\Shosho LifeOS`。VPS 與本機路徑差異見 [[project_nami_vps_deployed]]（如存在）。
