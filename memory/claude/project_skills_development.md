---
name: Skills 開發體系建立
description: prior-art-research skill 完成、skill-creator fork 版安裝、find-skills 安裝、開發前先調研的工作流程
type: project
created: 2026-04-14
updated: 2026-04-14
confidence: high
ttl: permanent
---

## 已建立的 Claude Code Skills

### prior-art-research（自建）
- 位置：`~/.claude/skills/prior-art-research/SKILL.md`
- 功能：開發前自動搜尋 6 個通道（本地 → skills.sh → Marketplaces → MCP → GitHub → PyPI），產出 adopt/extend/build 報告
- 經過 2 輪 eval 迭代，pass rate 95%
- 關鍵特性：
  - Step 0 先查本地 codebase（避免重複開發）
  - 自適應深度（quick scan / standard / deep dive）
  - 強制閘門：報告完成後使用者確認才進開發
- Reference file：`references/search-channels.md`（deep dive 時按需載入）

### find-skills（手動安裝）
- 位置：`~/.claude/skills/find-skills.md`
- 來源：vercel-labs/skills
- 功能：搜尋 skills.sh 市場
- 注意：環境沒有 Node.js，`npx skills` 無法使用，改用 WebFetch/WebSearch

## 開發工作流程（更新）

```
使用者提出開發需求
  → prior-art-research 自動觸發
    → Step 0: 查本地 codebase
    → 搜尋外部生態系
    → 產出 adopt/extend/build 報告
  → 使用者確認方向
    → Adopt: 安裝現成工具
    → Extend: fork 改造
    → Build: 用 /skill-creator 建立（含 eval 迭代循環）
```

**Why:** 避免重複造輪子。Eval 2 測試中，without-skill agent 直接寫了 384 行未經授權的程式碼進生產檔案，with-skill agent 則先搜尋再報告。
**How to apply:** 每次收到開發需求時，先讓 prior-art-research skill 跑完再動手。
