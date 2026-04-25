---
name: LifeOS Templates drift 已修正
description: tpl-project / tpl-action 已對齊 gold standard（2026-04-24 Mac session，2026-04-25 re-scan 再次確認）
type: project
tags: [lifeos, templates, resolved]
created: 2026-04-19
updated: 2026-04-25
confidence: high
ttl: 180d
originSessionId: 64ccfe1b-b7a7-4f86-8964-5a458e6eba6f
---
## Status: RESOLVED 2026-04-24（2026-04-25 re-scan 確認）

### 2026-04-25 re-scan 結果

桌機 auto-mode session（修修 Mac 出門期間）對 vault `Templates/` 與 `shared/lifeos_templates/` + `shared/lifeos_writer.py:render_task` 跑完整 diff：

- ✅ `tpl-project.md`（dispatcher）→ tp.system.suggester 收 4 個 content_type、emit frontmatter 後 include partial
- ✅ `tpl-project-body-{youtube,blog,research,podcast}.md` 4 份 body partial 與 Nami source `shared/lifeos_templates/project_*.md.tpl` 1:1 對齊（modulo `__TITLE__` → `<% tp.file.title %>` substitution，functionally equivalent）
- ✅ `tpl-action.md` 9-key frontmatter 與 `render_task()` 輸出 1:1 對齊（title / status / priority / projects / tags / dateCreated / dateModified / 預估🍅 / ✅）

### ⚠️ 留下的 vault 雜物（drift 之外）

vault `Templates/` 還留著 3 份 dispatcher 遷移前的 legacy templates，**不在 active path 上**（dispatcher 不會 include），但 Templater 的 quick-switcher 會列出來，修修不小心選到會建出舊 frontmatter shape：

- `tpl-new-project.md` — 老 mega template（含 typeChoice / status `on-hold` 等遷移前選項）
- `tpl-project-youtube.md` — 老 youtube-specific（有 `estimated_pomodoros` 但沒 `area` / `search_topic`）
- `tpl-project-podcast.md` — 老 podcast-specific（同上）

**建議**：修修可考慮 archive（移到 `Templates/_archive/`）或直接送回收桶。本 session 不自動處理（vault 不該由 Claude 自動刪/搬）。

### 做了什麼

Mac session 2026-04-24 把 `Templates/tpl-project.md` + `Templates/tpl-action.md` 對齊 gold standard `Projects/肌酸的妙用.md` + Nami `shared/lifeos_writer.py` 的 output。

架構：**dispatcher + 4 body partials**（drift memory 原本就預先驗證過這個拆法）：
- `tpl-project.md` — Templater 提示 content_type / priority / area / search_topic，emit frontmatter 後 `tp.file.include([[tpl-project-body-{content_type}]])`
- `tpl-project-body-{youtube,blog,research,podcast}.md` — 4 份 body partial，內容來自 `shared/lifeos_templates/project_*.md.tpl`（把 `__TITLE__` → `<% tp.file.title %>`）
- `tpl-action.md` — 9-key frontmatter，對齊 Nami render_task（gold standard 多的 3 個 `scheduled/created/updated` 是 TaskNotes plugin metadata，非初始 frontmatter）

### 為什麼走 dispatcher + 4 partials

- 單一 mega template with nested conditionals 會是 850+ 行，KB Research 大塊 dataviewjs 四倍重複，不可維護
- Drift memory 明示「建議可拆成 tpl-project-youtube.md / tpl-project-research.md 等」
- Nami & 手動路徑現在共用 body 定義（Nami 讀 `shared/lifeos_templates/`，手動走 `Templates/tpl-project-body-*`，但結構完全對齊）

### 未驗證的一件事

Obsidian 實體 smoke test 沒跑 — CLI 動不了 Templater。修修需在 Obsidian 建一個測試 project 驗 frontmatter 跟 Nami output 是否 1:1。
