# Vault CLAUDE.md — Post-ADR-028 Replacement Draft

This file is the proposed replacement content for `E:\Shosho LifeOS\CLAUDE.md`.

**It is NOT applied to the vault by this PR.** It is included as an ADR-028 attachment for review. Application happens as ADR-028 Phase 3 PR-9 (PR-Vault-CLAUDE-Update), after panel review approves the overall plan.

The draft replaces the current §2 Directory Model section with a ~30-line cheat sheet pointing at the repo canonical `docs/VAULT-LAYOUT.md`. The remaining sections of vault CLAUDE.md (Mission, Permissions, Page Types, Metadata Schema, Workflows) are kept verbatim — they remain useful and accurate post-ADR-028.

---

## Proposed new §2 Directory Model

```markdown
## 2. Directory Model

> **Canonical authoritative reference:** `E:\nakama\docs\VAULT-LAYOUT.md`
> This section is a cheat sheet only. When in conflict with the canonical doc, the canonical doc wins.

### Top-level cheat sheet

```
E:\Shosho LifeOS\
├── Journals/         🔒 你寫 (Daily / Weekly / Quarterly / Yearly)
├── OKRs/             🔒 你寫 (年度 / 季度)
├── Projects/         🟡 協作頁面 (你 + agent assist sections)
├── TaskNotes/        🟡 TaskNotes plugin + 你
├── Dashboards/       🔒 你寫 (dataviewjs queries)
├── Inbox/            🤖 capture cache，可清空
│   ├── web/            ← News Coo FSA pick 這層
│   ├── books/          ← .epub
│   ├── papers/         ← .pdf
│   └── snapshots/      ← .mhtml
├── KB/               🤖 agent 主要工作區（PARA Resources）
│   ├── Raw/            禁改 body
│   ├── Wiki/           可改 — Sources, Concepts, Entities, Digests, _alias_map
│   ├── Annotations/    Robin Reader 註解（ADR-017）
│   ├── Attachments/{slug}/  flat per-source
│   ├── index.md / log.md
├── Attachments/
│   ├── Books/{book-id}/ch{n}/    textbook 圖
│   └── journal-pasted/{YYYY-MM}/ Obsidian paste 預設目錄
├── AgentOutputs/     🤖 agent 任務輸出
│   ├── nami/{briefs,notes,research}/
│   ├── brook/seo-audit/
│   └── franky/{weekly/,dev-backlog.md}
├── Templates/        🔒 你寫 (Templater plugin)
└── Scripts/          🔒 你寫 (Templater scripts + nakama-config.md)
```

### 三層紅線 (3-tier ownership)
- 🔒 **Human only** — agent 不可寫文字內容 (Journals, OKRs, Dashboards, Templates, Scripts; Project 內部 `## 專案描述/預期成果/Draft Outline/專案筆記` sections)
- 🤖 **Agent only** — 你不該手寫 (KB/, AgentOutputs/, Inbox/)
- 🟡 **協作** — section-level 分工 (今天只有 `Projects/{title}.md`)

### 寫入規則
- `Journals/`、`OKRs/`、`Dashboards/`、`Templates/`、`Scripts/` — agent 禁寫
- `KB/Raw/` — 不可改 body，只可補 frontmatter metadata
- `KB/Wiki/`、`KB/Annotations/` — agent 主要工作區
- `KB/index.md` — 每次 Wiki 更新後同步
- `KB/log.md` — append-only

### 協作頁面 marker convention
Agent 寫進 `Projects/{title}.md` body 必須用：
```
%%agent-{agent_name}-{section_id}-start%%
...
%%agent-{agent_name}-{section_id}-end%%
```
詳見 `docs/VAULT-LAYOUT.md` §4。

### Vault 跟 repo 邊界
「如果把 Nakama 整個砍掉重寫，這個檔案還有意義嗎？」
- 有 = vault（你的生活/工作素材）
- 沒有 = repo（Nakama 開發 artifact，住 `E:\nakama\docs\`）
```

---

## What stays unchanged

Vault CLAUDE.md §0 Memory, §1 Mission, §3 Permissions (current table is consistent with ADR-028 §1 three-tier), §4 Page Types, §5 Metadata Schema, §6 Workflows — all kept verbatim.

Only the §2 Directory Model block (currently lines 22-65 in the existing vault CLAUDE.md) is replaced.
