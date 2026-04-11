---
name: 開發流程偏好
description: 修修希望大型功能開發遵循正規流程（branch → PR → merge），且使用 plan mode 做設計
type: feedback
tags: [workflow, development, process]
created: 2026-04-11
updated: 2026-04-11
confidence: high
ttl: permanent
---

修修在 2026-04-10（Windows 環境）討論過「大型專案的正規開發流程」，但具體內容未記錄（當時 Windows 記憶未同步）。修修表示之後開發都要遵循正規流程。

**已觀察到的偏好：**
- 大型功能用 feature branch → PR → merge（如 ADR-002 用 `feat/adr-002-memory-system` → PR #4）
- 規劃階段使用 plan mode，確認方向後才開始實作
- 每個 Phase 完成後立即 commit，不要一次大 commit
- commit message 格式：`feat:` / `docs:` / `fix:` 前綴，中文描述
- 重要架構決策寫 ADR（`docs/decisions/ADR-XXX-*.md`）

**Why:** 修修希望開發過程有條理、可追蹤，避免混亂。

**How to apply:** 新功能開發前先確認是否需要建分支和 PR，大型變更用 plan mode 設計再實作。
