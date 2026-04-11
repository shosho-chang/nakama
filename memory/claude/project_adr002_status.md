---
name: ADR-002 記憶系統實作進度
description: 三層記憶架構的實作狀態——Phase 1-3 已完成並 merge，Phase 4 等 MemPalace 中文支援
type: project
tags: [adr-002, memory-system, architecture]
created: 2026-04-11
updated: 2026-04-11
confidence: high
ttl: permanent
---

ADR-002 三層記憶架構，2026-04-11 在 PR #4 完成並 merge。

**已完成：**
- Phase 1: CLAUDE.md 壓縮（93→49 行）、memory/ 目錄重整（agents/ + claude/）、frontmatter schema 統一、shared/memory.py 升級（parse_frontmatter + get_context）
- Phase 2: state.db memories 表 + FTS5 全文索引、remember() / search_memory() / list_memories() API、Robin 和 Franky 自動記錄 episodic
- Phase 3: BaseAgent.execute() 自動 record_episodic()（子類別可 override）、memory_maintenance.py（expire / archive / stats）
- 文件: docs/memory-system.md

**未完成：**
- Phase 4: MCP Memory Server 整合（等 MemPalace 中文支援，或評估 Mem0 / Basic Memory）
- get_context() 的 task 參數尚未實作 tag 篩選（預留介面）
- max_tokens 壓縮機制尚未實作（預留介面）

**VPS 同步狀態：** 2026-04-11 已 pull + pip install。
