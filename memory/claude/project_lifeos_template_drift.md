---
name: LifeOS Templates 已與實際 Project/Task 檔案脫節
description: tpl-project.md / tpl-action.md 過時，gold standard 是實際 Projects/ 下的檔案
type: project
tags: [lifeos, templates, tech-debt]
created: 2026-04-19
updated: 2026-04-19
confidence: high
ttl: 180d
---

## 事實

`F:\Shosho LifeOS\Templates\tpl-project.md` + `tpl-action.md` 已經**過時**，跟實際
LifeOS 使用的 Project/Task 結構**不一致**。修修手動建 project 時如果跑 template
會產出跟現有檔案結構不同的檔案。

## Gold Standard 在哪裡

以實際檔案為準，不要讀 template：
- Project gold standard: `F:\Shosho LifeOS\Projects\肌酸的妙用.md`
- Task gold standard: `F:\Shosho LifeOS\TaskNotes\Tasks\肌酸的妙用 - Pre-production.md`

## 主要差異

**Project frontmatter**：
- Template 固定 `content_type: project`；實際支援 `youtube / blog / research / podcast`
- Template 缺欄位：實際有 `search_topic`、`publish_date`，template 沒有
- Template body 用普通 dataview 查 `Vault/Actions`；實際用 Obsidian **Bases** filter + 內嵌 KB Research 按鈕（dataviewjs）+ 關鍵字研究按鈕 + `%%KW-START%%/%%KW-END%%` anchor（給 Zoro 更新）

**Task frontmatter**：
- Template: `type: action`, `project:`（單數）, `🍅_Estimated`, `tags: [action]`
- 實際: `title`, `status: to-do`, `projects: ["[[..]]"]`（複數 wikilink）, `預估🍅`, `✅: false`, `dateCreated/dateModified` ISO8601, `tags: [task]`
- 實際的 body 幾乎空白（由 TaskNotes plugin 管理）

## Nami 的 project-bootstrap 怎麼處理

`shared/lifeos_writer.py` + `shared/lifeos_templates/*.md.tpl` 已對齊 gold standard，
**不讀** `F:\Shosho LifeOS\Templates\*` 裡的檔案。所以 Nami 走的路徑是對的。

## 待辦（獨立工作）

修修想同步手動建 project 體驗時，需要把 `Templates/tpl-project.md` +
`tpl-action.md` 更新到跟實際格式一致（移除 Templater suggester、或對齊
新欄位）。建議可拆成 `tpl-project-youtube.md` / `tpl-project-research.md` 等。

這件事**不阻塞**任何目前工作，優先級低。
