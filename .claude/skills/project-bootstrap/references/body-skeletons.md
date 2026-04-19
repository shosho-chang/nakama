# Body Skeletons

What the skill writes into each content_type's Project file.

All 4 types share a header:
- `# <title>`
- `## 🎯 對應 OKR` — dataview refs to `quarter` / `parent_kr` frontmatter
- `## ✅ Tasks` — Obsidian Bases filter that auto-lists the 3 tasks
- `## 📊 番茄統計` — dataviewjs summary of pomodoro tracking
- `---`

The sections after `---` differ per type.

## youtube

- `## 👄 One Sentence About This Video` — single sentence summarizing the video,
  used as query source by the KB Research button's dataviewjs
- `## 📚 KB Research` — button: POSTs `/kb/research` on Robin (VPS) with the
  one-sentence as query, renders clickable results table, caches in localStorage
- `## 🗝️ Keyword Research & Title Ideas` — button: POSTs `/zoro/keyword-research`
  with `search_topic` frontmatter field + `content_type=youtube`, replaces
  `%%KW-START%% / %%KW-END%%` block with returned markdown
- `%%KW-START%% / %%KW-END%%` — empty anchor block on first write; keyword
  research fills it later
- `## Script / Outline` — where the actual video script goes
- `## 專案筆記` — free-form notes

## blog

- `## 專案描述` — short project description, used as KB Research query source
- `## 預期成果` — target deliverable
- `## 📚 KB Research` — same button as youtube, but reads `## 專案描述` instead
  of `👄 One Sentence`
- `## 🗝️ Keyword Research & SEO` — same pattern but `content_type=blog` (changes
  title prompt at Zoro side; emits blog_titles instead of youtube_titles)
- `%%KW-START%% / %%KW-END%%` — keyword research anchor
- `## Draft Outline` — draft placement
- `## 專案筆記`

## research

- `## 專案描述`, `## 預期成果`
- `## 📚 KB Research` — same pattern, reads `## 專案描述`
- **No keyword research** — research projects aren't for publishing
- `## Literature Notes` — reading notes / citations
- `## Synthesis` — distilled understanding
- `## 專案筆記`

## podcast

- `## 👄 Episode Sentence` — single sentence about the episode
- `## 來賓 / 大綱` — guest info + episode outline
- `## 📚 KB Research`
- `## Show Notes`
- `## 專案筆記`

## Why No "Updated" Field

Unlike `shared/obsidian_writer.py` which always stamps `updated: <today>`,
`lifeos_writer.py` **deliberately omits** `updated`. The LifeOS gold standard
(e.g. `Projects/肌酸的妙用.md`) doesn't have `updated`, and Task files use the
TaskNotes plugin's `dateModified` (ISO8601) which the plugin manages itself.

## Title Substitution

All 4 templates contain one user-facing placeholder: `__TITLE__`, which the
writer replaces with the project title. The only substitution point is the
Bases filter's `projects.contains(link("__TITLE__"))` line — everything else
is Obsidian dataviewjs / base filter logic that resolves at view time from
`dv.current()`.
