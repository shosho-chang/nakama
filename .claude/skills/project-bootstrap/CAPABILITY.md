# Capability Card — project-bootstrap

## What it does

Given a topic, create one LifeOS Project file plus 3 default Task files with
bidirectional wikilinks, ready to work on in Obsidian. Supports 4 content
types (youtube, blog, research, podcast) with type-specific body skeletons
that wire into in-vault widgets (KB Research button, Keyword Research button).

## What it does NOT do

- Create a single task (use Nami's `create_task`)
- Modify existing projects or tasks
- Integrate with Templater / QuickAdd / other Obsidian plugins directly
  (skeleton is self-contained markdown)
- Fill in `One Sentence` / `專案描述` content (placeholder only — user fills)
- Trigger the downstream Keyword Research or KB Research (those are buttons
  inside the written project that the user clicks in Obsidian)

## Inputs

Through the skill (interactive, via Claude Code or Slack thread):
- Topic (required, becomes Project title)
- content_type (4 choices; asked if unclear)
- area, priority, search_topic (sensible defaults, only asked on ambiguity)
- Optional: custom task names instead of the hardcoded 3-task default

Through the CLI (`scripts/run_project_bootstrap.py`, scriptable):
- `--title`, `--content-type` required
- `--tasks`, `--area`, `--priority`, `--status`, `--search-topic`,
  `--estimated-pomodoros`, `--vault` optional

## Outputs

**Files written** (no overwrite — conflict raises `ProjectExistsError`):
- `<vault>/Projects/<title>.md` — Project file with LifeOS-standard frontmatter
  (`type: project`, `content_type`, `area`, `tags: [project, <content_type>]`, etc.)
- `<vault>/TaskNotes/Tasks/<title> - <taskname>.md` × 3 — Task files with
  TaskNotes-plugin frontmatter (`projects: ["[[<title>]]"]` wikilink array,
  `status: to-do`, etc.)

**Stdout** (JSON, machine-readable for downstream):
```json
{
  "project_path": "Projects/<title>.md",
  "task_paths": ["TaskNotes/Tasks/<title> - <task>.md", ...],
  "content_type": "<type>",
  "vault_abs_project": "<absolute-path>",
  "obsidian_uri": "obsidian://open?vault=<name>&file=..."
}
```

## Cost (measured 2026-04-19)

Per happy-path run (user confirms without content_type question):
- Wall time: ~1–2 seconds (subprocess + write ~15 files incl. templates)
- Claude API (Haiku parse_project): ~300 input + ~100 output tokens
  ≈ **$0.0003** per flow (input $1/M, output $5/M for Haiku 4.5)

Per run with content_type clarification (one extra turn):
- Wall time: ~2–4 seconds
- Claude API: same as above (only one Haiku parse upfront)
- User-side latency: dominated by user response time in the thread

No LLM is called after the initial parse — Nami's state machine handles
content_type reply + confirm deterministically.

## Failure modes

| Condition | Exit code | User-visible message |
|---|---|---|
| Same-titled project / task already exists | 2 | "已有同名 project 或 task。要改標題..." |
| Claude parse returns non-JSON | 0 (falls through) | Uses raw text as title, asks content_type |
| Vault path does not exist | 1 (unhandled) | Python traceback — tell user to check `config.yaml` |
| Subprocess timeout (>30s) | n/a | "⚠️ 建立 project 超時" |

## Open-source extractability

- **LifeOS-specific**: body skeletons in `shared/lifeos_templates/*.md.tpl`
  embed Obsidian Bases filters + dataviewjs widgets tied to修修's LifeOS
  (uses Robin KB Research API, Zoro keyword research API)
- **Generic core**: `shared/lifeos_writer.py` is a pure renderer — an
  open-source user can replace the templates with their own Obsidian
  conventions and reuse the conflict-safe writer + CLI
- **Dependency on config.yaml**: vault path comes from `shared/config.py`;
  `--vault` CLI arg allows override for testing / extraction

## Test coverage

- `tests/test_lifeos_writer.py` — 28 tests (frontmatter schema, wikilink
  format, conflict handling, blank-None rendering, per-content-type skeletons)
- `tests/test_gateway_handlers.py` — 8 new tests for Nami `create_project`
  intent + `continue_flow` state machine (mocked Claude + subprocess)
- `tests/test_conversation_state.py` — 7 tests for thread store TTL /
  isolation / eviction

All 340 existing + new tests pass; ruff check + format clean.
