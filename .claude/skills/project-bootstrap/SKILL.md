---
name: project-bootstrap
description: >
  Interactive bootstrap for a new Obsidian LifeOS Project — collects topic,
  content_type (youtube/blog/research/podcast), area, priority, then writes one
  Project file plus 3 default Task files with two-way wikilinks, ready to work
  on. Trigger when the user says "幫我建立一個關於 X 的 project / 開個 <topic>
  專案 / start a project on X / new project about X / 建一個 <topic> 的新專案",
  or asks Nami to bootstrap a project in Obsidian. Defaults are sensible so
  the user can usually confirm in one turn. Writes a frontmatter block that
  downstream skills (keyword-research, Brook article-compose) already consume.
---

# Project Bootstrap — LifeOS Project + 3 Task Skeleton

You are the interactive wrapper for `scripts/run_project_bootstrap.py`. Your
job is to turn a user intent like "幫我建立一個關於 X 的 project" into a
ready-to-work Obsidian LifeOS Project file + 3 Tasks — making judgment calls
on content_type, area, priority, and default tasks so the user usually
confirms in one turn.

You do NOT re-implement the write logic. You shell out to
`scripts/run_project_bootstrap.py` and relay the result.

## When to Use This Skill

Trigger on intent like:
- "幫我建立一個關於 <topic> 的 project"
- "開個 <topic> 專案"
- "start a project on <topic>"
- "new project about <topic>"
- "建一個 <topic> 的新專案"
- "Nami 幫我開 <topic> 的 project"
- "我想研究 <topic>，幫我建個 project"

Do NOT trigger for:
- Creating a single task (not a full project) → Nami task handler
- Creating a KB wiki page → `kb-ingest`
- Writing an article for an existing project → `article-compose`
- Keyword research without a project file → `keyword-research`

## Workflow

The skill has 5 short steps. Steps 1-4 collect fields; step 5 writes.

```
Step 1. Extract topic + any explicitly stated fields
Step 2. Resolve content_type                    [ASK if unclear]
Step 3. Resolve area, priority, search_topic    [ASK only if unsure]
Step 4. Show default 3 tasks + full plan        [CONFIRM, never skip]
Step 5. Invoke run_project_bootstrap.py + report
```

---

## Step 1: Extract Topic + Stated Fields

From the user's message, pull out:
- `topic` — the project subject (required, becomes filename)
- `content_type` (optional) — any of `youtube / blog / research / podcast`
- `area` (optional) — `work / health / family / self-growth / play / visibility`
- `priority` (optional) — `first / high / medium / low`
- `search_topic` (optional) — SEO hint; only meaningful for `youtube` / `blog`

Keywords that hint content_type:
- "影片 / YouTube / 拍 / 短片" → `youtube`
- "部落格 / blog / 文章 / 長文 / SEO" → `blog`
- "研究 / 論文 / literature / 深入研究" → `research`
- "podcast / 錄音 / 訪談 / 來賓" → `podcast`

Do NOT guess if ambiguous — Step 2 will ask.

## Step 2: Resolve content_type

If content_type was not explicitly stated and cannot be confidently inferred,
ask **once** with a compact choice:

```
「<topic>」要做成哪一類？
  - youtube（拍影片）
  - blog（寫文章 / SEO）
  - research（深度研究，不直接產出）
  - podcast（錄音 / 訪談）
```

Default recommendation if the user seems uncertain: `research` for abstract
concept topics, `youtube` for anything mentioned alongside "影片/拍/youtube".

## Step 3: Resolve area / priority / search_topic

These all have strong defaults. Skip asking unless the user's topic clearly
conflicts with the defaults.

**Defaults**:
- `area` = `work`
- `priority` = `medium`
- `status` = `active`
- `search_topic` = same as `topic` (for youtube/blog only)
- `estimated_pomodoros` = 4 per task

Adjust proactively without asking when obvious:
- Health / wellness / 身心 topic → `area: health`
- Family / relationship topic → `area: family`
- Learning a new skill → `area: self-growth`

Only ask the user when there is genuine ambiguity or a high-stakes project.

## Step 4: Show Plan + Confirm (NEVER SKIP)

Always show the full plan before writing. This is the single confirm gate.

Default task names per content_type:

| content_type | Task 1 | Task 2 | Task 3 |
|---|---|---|---|
| youtube | Pre-production | Filming | Post-production |
| blog | Research | Draft | Publish |
| research | Literature Review | Synthesis | Write-up |
| podcast | Prep & Booking | Recording | Edit & Publish |

Present (example for research type):

```
要建立的 Project：
  標題：超加工食品
  類型：research
  領域：health
  優先級：medium

預設 3 個 Task：
  1. 超加工食品 - Literature Review
  2. 超加工食品 - Synthesis
  3. 超加工食品 - Write-up

確認建立？（或想改 task 名稱 / 領域 / 優先級）
```

Accept: "確認" / "go" / "yes" / "ok" / "建立吧" / "好" / "對".
Adjust: if user provides new task names ("改成 調研 / 寫稿 / 校稿"), replace the
list. If they change area/priority, update. Re-confirm.

## Step 5: Invoke run_project_bootstrap.py

Build the command:

```bash
python scripts/run_project_bootstrap.py \
    --title "<topic>" \
    --content-type <youtube|blog|research|podcast> \
    --tasks "<task_1>" "<task_2>" "<task_3>" \
    --area <area> \
    --priority <priority> \
    [--search-topic "<search_topic>"] \
    [--status active]
```

Run with `Bash`. The script writes JSON to stdout. Parse it.

**Exit codes:**
- `0` — success, JSON has `project_path`, `task_paths`, `obsidian_uri`
- `2` — `ProjectExistsError` (project file already exists); JSON has `error` field.
  Tell the user: "已經有同名 project，要改標題還是刪舊的？"

Do NOT auto-retry on any error. Report and wait for user decision.

## Step 6: Report Result

On success, present:

```
✅ Project 建好了
  📄 <project_path>
  ✅ 3 個 Tasks 已建立

直接開啟：<obsidian_uri>

下一步建議：
  → 打開 Project 檔填「One Sentence / 專案描述」讓 KB Research 有查詢
  → （youtube / blog）按「🗝️ 關鍵字研究」讓 Zoro 抓搜尋潛力 + 標題建議
```

For `youtube` / `blog` types, the "下一步" hint should specifically mention the
`keyword-research` skill or the in-project 🗝️ button.

For `research` / `podcast`, replace the keyword-research line with:
  → 打開 `📚 KB Research` 按鈕讓 Robin 從 KB 抓相關已知素材

---

## Fast Mode Behavior

Triggered by "用 default" / "快速建" / "go with defaults" / "照建議建立".

- Step 2 (content_type) → **still ask if unclear**; skip only if explicitly
  stated in the original message
- Step 3 (area/priority) → skipped, use defaults
- Step 4 (plan confirm) → **still shown, still requires confirmation**
  (the never-skip gate)

The never-skip gate is the plan confirmation in Step 4.

## Output Contract (Downstream Composability)

When the skill reports back, future agents (Brook, downstream orchestrators)
should be able to pick up the project. The script stdout JSON schema:

```json
{
  "project_path": "Projects/超加工食品.md",
  "task_paths": [
    "TaskNotes/Tasks/超加工食品 - Literature Review.md",
    "TaskNotes/Tasks/超加工食品 - Synthesis.md",
    "TaskNotes/Tasks/超加工食品 - Write-up.md"
  ],
  "content_type": "research",
  "vault_abs_project": "/home/Shosho LifeOS/Projects/超加工食品.md",
  "obsidian_uri": "obsidian://open?vault=Shosho%20LifeOS&file=Projects%2F超加工食品"
}
```

The created Project file's frontmatter is the real hand-off contract (has
`type: project`, `content_type`, `area`, `search_topic`, etc.), consumed by
`article-compose`, `keyword-research`, and in-vault dataviewjs widgets.

## Open-Source Friendliness

This skill is LifeOS-specific (writes Obsidian-plugin-specific body like
dataviewjs + Bases filters). Extracting requires:
- Replacing body templates in `shared/lifeos_templates/*.md.tpl` with user's
  own Obsidian vault conventions
- Adjusting `DEFAULT_TASKS` in `shared/lifeos_writer.py` for different content
  workflows
- `shared/config.py` vault path resolution is already generic

## References

See `references/` for details when you need them:

| File | When to read |
|------|--------------|
| `content-type-guide.md` | Step 2 — how to choose between the 4 types |
| `body-skeletons.md` | Understanding what each content_type produces (when user asks "裡面會有什麼？") |
| `error-recovery.md` | Step 5 on failure |
