---
name: user vault access pattern
description: 修修在 Obsidian 內只 access 自己手 maintain 的時間軸 + Project 頁面；其他 LLM-maintained 內容預期走 Web UI 不走 Obsidian
type: user
---

修修在 Obsidian Vault 內**只主動 access 這些目錄**：

- 時間軸頁面：daily / weekly / quarterly / yearly journals
- Project 頁面（手寫心得 stage 4 的主場）

其他 LLM-maintained 目錄（`KB/Wiki/*`、`KB/Annotations/`、`AgentBriefs/`、`AgentReports/`、`Schemas/`、`Incidents/`、`Dashboards/` 等）**不期望在 Obsidian 內逛**。修修對 vault 檔案數爆炸明確表達不開心（2026-05-07 grill）。

**理想終局**：Web UI 統一作為「閱讀 + retrieve KB / annotation」的入口（Reader UI 已是這條路徑的 prototype），Obsidian 退回為「人類手寫內容的編輯器」。

**設計含義**：

- 任何「替 LLM-maintained 內容增加 Obsidian-friendly view」的提議要先質疑是否該由 Web UI 取代
- 新增 vault 物件（檔 / 目錄）要過比過去更高的門檻 — vault 簡潔性是 first-class concern
- KB / Annotation / Wiki schema 不需要為 Obsidian 瀏覽 UX 妥協（例如 ADR-017 JSON in markdown 是合理的，因為 canonical store 不期望被人類在 Obsidian 內直接讀）
- Stage 4 retrieve 的 UX 應該是 Web UI，不是 Obsidian search

**Vault 整體重整**（root 24+ 項分組、agent-facing vs 人類-facing 分群）是獨立議題，需另開 grill / ADR — 不該夾帶在其他 feature 設計裡。
