# Memory Schema

**Audience:** AI agents (Claude, Codex, future) and human reviewers
**Status:** v2, post-panel-review (2026-05-08)
**Source design doc:** `docs/research/2026-05-08-memory-system-redesign-v2.md`

## Quick reference

```
memory/
├── shared/                     ← Cross-agent. Bilingual frontmatter required.
│   ├── user/                   ← Facts about the user (修修)
│   ├── project/                ← Facts about the nakama project
│   ├── reference/              ← Pointers to external systems
│   └── decision/               ← Crystallized small design decisions
├── claude/feedback/            ← Claude-specific behavioral feedback
├── codex/feedback/             ← Codex-specific behavioral feedback
├── _archive/YYYY-MM/           ← Auto-rotated old memories
├── INDEX.md                    ← GENERATED — do not hand-edit
└── SCHEMA.md                   ← This file
```

**Plus (legacy, transition state until Phase 2 migration):**

```
memory/
├── claude/                     ← Existing 297 files; will gradually migrate to new layout
├── shared.md                   ← Old monofile schema; pre-dates this redesign
└── agents/{robin,franky}.md    ← Old per-agent files; pre-dates this redesign
```

## Read scope per agent

| Agent | Reads | Writes |
|---|---|---|
| Claude | `memory/shared/**` + `memory/claude/**` | `memory/claude/feedback/**` freely; `memory/shared/**` rare-write (curated) |
| Codex | `memory/shared/**` + `memory/codex/**` | `memory/codex/feedback/**` freely; `memory/shared/**` rare-write (curated) |
| Both | NOT `_archive/**` (re-add to active set if needed) | NEVER `INDEX.md` (it's generated) |

## Frontmatter schema

### Required for all files

```yaml
---
type: feedback | user | project | reference | decision   # required
visibility: shared | claude | codex                       # required (must match physical path)
agent: shared | claude | codex                            # required (defensive cross-check; should match visibility)
confidence: high | medium | draft                         # required; default 'medium'
created: YYYY-MM-DD                                       # required; auto-fill on creation
expires: YYYY-MM-DD | permanent                           # required for type=project (default created+30d);
                                                          # default 'permanent' for user/reference/feedback/decision
tags: [worktree, ci, sandcastle]                          # optional; search aid
---
```

### Plus, for `memory/{claude,codex}/**` (monolingual)

```yaml
name: <single-language identifier>
description: <single-language one-line summary>
```

### Plus, for `memory/shared/**` (bilingual REQUIRED)

```yaml
name_zh: 修修偏好簡潔回覆
name_en: User prefers concise responses
description_zh: 自由艦隊長期文化是直接 + 簡明
description_en: Long-standing preference for direct + minimal phrasing
```

The `name` / `description` (single-language) fields are NOT used in `shared/`. The bilingual versions are required so:
- Claude (Chinese-primary in this project) can semantic-match via `name_zh` / `description_zh`
- Codex (English-primary) can semantic-match via `name_en` / `description_en`

If translation is uncertain, the agent must mark with `[needs review]` rather than skip:
```yaml
description_en: "[needs review] Long-standing preference for ..."
```

`validate` lint flags `[needs review]` placeholders persisting >7 days.

## Body content rules

- Body in either Chinese or English (writer's primary language)
- File path identifiers (paths, function names, env vars) stay in English
- For `shared/`: prefer English in body if the topic is technical/code-grounded; Chinese if it's user-preference / cultural

## Schema precedence (when fields disagree)

When `path`, `visibility`, and `agent:` disagree (e.g., file in `memory/claude/` with `visibility: shared`), `validate` resolves in this order:

1. **`path` is canonical** — physical location is the truth
2. If `visibility` doesn't match path, `validate` warns + reindex auto-corrects
3. If `agent:` doesn't match path-implied owner, `validate` errors (likely a buggy `git mv` or copy-paste)

The `agent:` field exists as a defensive cross-check to catch mis-moved files, NOT as an authoritative ownership signal.

## Update protocol for `memory/shared/**`

Most `shared/` writes are CREATE (new file, no conflict possible). Updates to existing `shared/` files happen rarely but require care:

1. Before update: agent checks for sibling `<filename>.lock` file
2. If absent: create lock with payload `{agent, timestamp, pid}` → perform read-modify-write → delete lock
3. If lock exists and is fresh (<5 min old): wait or abort with explicit message ("shared memory currently being updated by {agent}")
4. If lock exists and is stale (>5 min): take over the lock, log warning
5. On `git push` conflict: NEVER silently last-write-wins. Surface to user for manual reconciliation. Especially important for bilingual frontmatter where partial updates can produce silently-corrupted files (one agent updates `name_zh`+body, another updates `name_en` — both YAML-valid but semantically inconsistent).

Lock files (`*.lock`) are gitignored.

## Translation responsibility (`shared/` only)

The agent authoring or updating a `shared/` memory is responsible for generating and validating BOTH language variants of frontmatter. If translation is uncertain, mark with `[needs review]` rather than skip.

## Type definitions

| `type` | Purpose | Typical lifetime | Example |
|---|---|---|---|
| `feedback` | Agent behavioral guidance | permanent | "Claude over-narrates; prefer terse summaries" |
| `user` | Facts about the user (preferences, role, language) | permanent | "User is solo dev in 修修's nakama project" |
| `project` | Project-level decisions, milestones, deadlines | created+30d default | "ADR-021 ships by 5/8" |
| `reference` | Pointers to external systems | permanent | "Bugs tracked in Linear project INGEST" |
| `decision` | Crystallized small design decisions | permanent | "Use bge-m3 1024d embedding" |

`decision` is the smallest tier — for ADR-level decisions, use `docs/decisions/ADR-XXX.md`.

## What NOT to put in memory

(reinforces system prompt's existing rules)

- Code patterns, file paths, project structure — derive from current state
- Git history / blame — `git log` is authoritative
- Debug recipes — fix is in code; commit message has context
- Anything in CLAUDE.md
- Ephemeral state: in-progress work, current conversation context (use `.nakama/session_handoff_{timestamp}.md` for cross-session)
- Session handoff memos (the very thing that produced 114 `project_session_*` files; goes to `.nakama/` instead)

## Maintenance commands

(implemented in `shared/memory_maintenance.py`; Phase 1 work)

| Command | Trigger | Action |
|---|---|---|
| `reindex` | Pre-commit hook + daily cron | Scan frontmatter, regenerate `INDEX.md` |
| `expire` | Daily cron | Move `expires < today` files to `_archive/YYYY-MM/` |
| `compact-sessions` | Manual or weekly | Move `project_session_*` to `_archive/` |
| `validate` | Pre-commit + CI | Schema lint: required fields, frontmatter format, bilingual check |
| `migrate` | Manual one-shot | Walk old `memory/claude/*.md` → propose new layout paths |
| `dedupe` | Manual quarterly | LLM-driven near-duplicate detection on feedback |

## See also

- `docs/research/2026-05-08-memory-system-redesign-v2.md` — full design rationale
- `docs/research/2026-05-08-codex-memory-redesign-audit*.md` — Codex audits (rounds 1+2)
- `docs/research/2026-05-08-gemini-memory-redesign-audit*.md` — Gemini audits (rounds 1+2)
- `docs/research/2026-05-08-panel-integration-matrix.md` — 3-way audit integration
- `memory/claude/feedback_conversation_end.md` — Tier 1 vs Tier 2 trigger rules
