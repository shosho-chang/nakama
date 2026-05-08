# Memory System Redesign v2 — Panel-revised

**Status:** Draft v2, second-round panel review pending
**Date:** 2026-05-08
**Author:** Claude Opus 4.7 (1M context) — v1 panel-revised after Codex GPT-5 + Gemini 2.5 Pro audits
**Repository:** nakama (E:\nakama)
**Supersedes:** `2026-05-08-memory-system-redesign-v1.md`
**Audit trail:**
- v1 → v2 changes traced to: Codex audit (`2026-05-08-codex-memory-redesign-audit.md`), Gemini audit (`2026-05-08-gemini-memory-redesign-audit.md`), integration matrix (`2026-05-08-panel-integration-matrix.md`)

## v1 → v2 changeset (verbatim audit attribution)

### Adopted from panel

| v1 had | v2 has | Source |
|--------|--------|--------|
| `memory-trunk` branch with weekly fast-forward to main | **Direct push to main**, CI bypass via `paths-ignore: memory/**` | Codex §4 + Gemini §5 |
| L3 confirm-mode trigger ("Claude lists candidates, asks user") | **Agent judgment + ephemeral handoff file** (`.nakama/session_handoff.md`, gitignored) | Gemini §3 (Blind Spot 1) + §6 |
| `memory/claude/`: 297 files (155+114+4+23) | `memory/claude/`: 296 typed + 1 MEMORY.md = 297 filesystem; 289 git-tracked + 8 untracked | Codex §1 + §3 |
| "Schema is `name / description / type` only" | Schema reality is mixed; some files have `tags/created/updated/confidence/ttl/originSessionId`, some lack `type` entirely. v2 schema upgrade includes `validate` lint to detect drift. | Codex §1 |
| `feedback_conversation_end.md` "is rewritten" | Explicit pending action: rewrite is part of PR A scope | Codex §1 |
| Phase 0 = single PR | Phase 0 split: **PR A** (immediate stop-the-bleeding) + **PR B** (design + scaffolding) | Codex §6 + Gemini §6 |
| `memory/shared/` accessible by both agents (no language constraint) | **`memory/shared/` requires bilingual frontmatter** (`name_zh`/`name_en`/`description_zh`/`description_en`) | Gemini §1 |
| Cleanup of `project_session_*` deferred to Phase 4 | Bulk archive of `project_session_*` files older than 30 days happens in **PR A** | Codex §5 + Gemini §6 |

### Rejected from panel (with reasoning)

| Panel proposed | v2 chose differently | Reason |
|---|---|---|
| Codex: drop multi-agent directory split, use prefix + `agent:` frontmatter only | Keep `memory/{shared,claude,codex}/` directory split | Bilingual concern (Gemini §1) requires per-agent path filter to be language-aware. Prefix-only loses this. Adopt directory split + add `agent:` frontmatter as redundant tag. |
| Codex: minimum viable cleanup (just delete `project_session_*` periodically) | Cleanup + trigger fix together | Cleanup without trigger fix = infinite cleanup loop. Treat root cause not symptom. |
| Gemini: build CLI tool (`nakama memory save`) for tool-driven workflow | Defer CLI to Phase 2+; instruct agent via system prompt instead | Solo dev's agent IS the tool. CLI for memory write adds friction to flow. But adopt Gemini's underlying point: doc-as-reference, not enforcement — agent's judgment is the gate, not human discipline. |
| Gemini: SQLite as source of truth + markdown export | Defer to Phase 4+, evaluate at scale | Premature. Phase 0/1 staying with markdown is simple, debuggable, git-friendly. SQLite hybrid is a viable Phase 4+ migration if scale demands. |
| Gemini: separate `nakama-memory` repo via submodule/clone | Stay in main repo | Codex §5 correctly notes Sandcastle (C3) compatibility breaks with submodule. Solo dev gains < complexity cost. |
| Gemini: `memory/shared/` defaults to read-only with formal "propose" workflow | `memory/shared/` is rare-write (curated), update precedence rules in schema | Read-only is too rigid. Real conflict only emerges on simultaneous-update of same file (rare). Solve via update precedence, not lock. |

---

## Context (compressed from v1)

修修 (solo developer) operates Health & Wellness AI agent system across multiple Claude Code windows on Win+Mac, plus Sandcastle cloud sandboxes. Codex (GPT-5 via API) is being added as a second collaborating agent. Memory must serve both agents across 3 execution surfaces.

**Audit data (Codex-corrected):**
- Filesystem: 297 files in `memory/claude/` (296 typed memories + MEMORY.md index)
- Git-tracked: 289 (8 untracked recent additions awaiting commit)
- Type breakdown: 154 feedback (Codex's count, off by 1 from v1) / 114 project / 23 reference / 4 user; 1 missing `type` (`feedback_test_realism.md`)
- 30-day commit volume on `memory/`: 264 commits
- GHA quota: 90% used (2700/3000), partial cause is memory-related CI

**Key behavioral source:** `feedback_conversation_end.md` instructs Claude to auto-commit+push memory on "清對話". Has not been rewritten yet (PR A scope).

**Constraints (unchanged from v1):**
- C1 cross-platform durability via repo storage
- C2 multi-agent (Claude + Codex)
- C3 Sandcastle compatibility (no submodules)
- C4 solo dev (no human reviewer for memory PRs)
- C5 signal-to-noise (155 feedback files = search noise)
- C6 backward compat (no big-bang migration)

---

## Decision (v2)

### Principle 1 — Memory ≠ session log (unchanged from v1)

Durable cross-session truths only. Session-specific state (debug context, handoff notes, in-progress work) belongs in git commits + GitHub issues + ephemeral files, not memory.

### Principle 2 — Two memory tiers, two access patterns

**v2 introduces explicit dual-tier model:**

```
Tier 1: Durable memory (memory/)         — git-tracked, survives sessions, schema-validated
Tier 2: Ephemeral handoff (.nakama/)     — git-ignored, lives one session boundary, free-form
```

This is the panel's most important contribution. v1 conflated these. Separating them eliminates ~90% of session-handoff PR/CI noise without losing the user's continuity benefit.

#### Tier 2 specification (`.nakama/session_handoff_{timestamp}.md`)

- **Path:** `.nakama/session_handoff_{ISO8601_timestamp}.md` (one file per session-end signal; timestamp prevents overwrites when multiple windows close near-simultaneously). Refinement adopted from Round-2 panel (Codex §3 + Gemini §1).
- **Filename example:** `.nakama/session_handoff_2026-05-08T16-42-15Z.md`
- **Lifecycle:** Written when session about to end OR user signals "清對話" + agent has unsaved continuity context → next session at startup reads ALL matching files in chronological order → deletes them after consumption
- **Format:** Free-form markdown, no schema, no frontmatter (the timestamp filename + content is enough)
- **Git:** Whole `.nakama/` directory in `.gitignore` from PR A (not just the file)
- **Concurrency:** Multi-window safe via timestamps. Last-write-wins is impossible because each writer creates a new file.
- **Stale handoff defense:** if startup finds handoff files older than 24h (agent crashed before deleting), agent logs warning + still reads but flags as stale.
- **Cross-session continuity preserved**: agent at session start globs `.nakama/session_handoff_*.md`, reads in chrono order, deletes each; if directory empty, no continuity loss (new session starts clean).
- **Cross-machine note**: `.nakama/` is local to each machine (not git-synced). Cross-machine continuity (Win → Mac) is not handled by Tier 2; durable cross-machine knowledge belongs in Tier 1.

#### Tier 1 specification: durable knowledge → `memory/`

The redesigned tree:

```
memory/
├── shared/                     # Cross-agent — Claude + Codex both read; both write rarely
│   ├── user/                   # Stable facts about 修修 (rarely change)
│   ├── project/                # Project-level decisions, milestones, deadlines
│   ├── reference/              # External system pointers (Linear, Slack, Grafana...)
│   └── decision/               # Crystallized small design decisions (complement to ADRs)
├── claude/
│   └── feedback/               # Claude-specific behavioral feedback
├── codex/
│   └── feedback/               # Codex-specific behavioral feedback
├── _archive/                   # Auto-rotated old memories
│   └── YYYY-MM/
├── INDEX.md                    # GENERATED — never hand-edited
└── SCHEMA.md                   # Schema documentation
```

**Read scope:**
- Claude reads: `memory/shared/**` + `memory/claude/**`
- Codex reads: `memory/shared/**` + `memory/codex/**`
- Neither reads `_archive/**` by default

**Write scope:**
- Each agent writes its own subdir freely
- `memory/shared/` is **rare-write, curated**: only when knowledge applies cross-agent (e.g., user preference change, project decision)
- **Update protocol for `memory/shared/**` (refined per Round-2 panel, Gemini + Codex):**
  - Before update: agent checks for sibling `.lock` file (`<filename>.lock`) → if absent, creates lock with `{agent, timestamp, pid}` payload → performs read-modify-write → deletes lock
  - If lock exists and is fresh (<5 min old): agent waits or aborts the update with explicit message ("shared memory currently being updated by {agent}")
  - If lock exists and is stale (>5 min): agent logs warning, takes over the lock
  - On `git push` conflict: NEVER silently last-write-wins. Agent surfaces the conflict to user for manual reconciliation (especially important for bilingual frontmatter where partial updates can produce silently-corrupted files — Gemini Round-2 §2)
  - Lock files are gitignored (lock files belong to working tree state, not history)

### Principle 3 — Memory commits skip PR review

**Implementation chosen (revised from v1):** `paths-ignore: memory/**` in CI workflows.

```yaml
# .github/workflows/ci.yml
on:
  push:
    branches: [main]
    paths-ignore:
      - 'memory/**'
      - '**.md'
      - 'docs/**'
```

Memory commits push directly to main. CI does not run on memory-only commits. Branch protection (if exists) needs path-based exemption — but if user's repo doesn't currently have memory-protecting branch protection, just direct push works.

This is **simpler** than v1's `memory-trunk` model. Adopted from Codex §4 + Gemini §5.

### Principle 4 — Cross-agent multilingual support

**v2 mandates bilingual frontmatter for `memory/shared/**`:**

```yaml
---
name_zh: 修修偏好簡潔回覆
name_en: User prefers concise responses
description_zh: 自由艦隊長期文化是直接 + 簡明
description_en: Long-standing preference for direct + minimal phrasing
type: user
visibility: shared
agent: shared              # NEW — explicit cross-agent ownership
confidence: high
created: 2026-05-08
expires: permanent
---

# Body content (in author's primary language; bilingual not required for body)
```

For `memory/{claude,codex}/**`, monolingual is fine (the writing agent and the consuming agent share a primary language).

For `memory/shared/**`:
- Frontmatter `name_zh`/`name_en`/`description_zh`/`description_en` are ALL required
- Body content can be in either language but should reference English path identifiers
- **Translation responsibility (refinement adopted from Round-2 Gemini)**: the agent authoring or updating a `shared/` memory is responsible for generating and validating BOTH language variants of frontmatter. If translation is uncertain, the agent must flag it (`description_en: "[needs review]"`) rather than skip.
- `validate` lint catches missing translation OR `[needs review]` placeholders persisting >7 days

This addresses the Gemini blind spot — Codex's English query for "preferred response style" can match `name_en: User prefers concise responses` even when body is Chinese.

**Phase 1 enhancement** (later): cross-lingual embeddings in reindex.

### Principle 5 — Schema upgrade (revised, validated)

```yaml
---
name: ...                                # required if monolingual; replaced by name_zh + name_en in shared/
description: ...                         # required if monolingual; replaced by bilingual in shared/
type: feedback | user | project | reference | decision   # required
visibility: shared | claude | codex      # required (defaults to inferring from path)
agent: shared | claude | codex           # NEW — explicit ownership tag (redundant w/ path, defensive)
confidence: high | medium | draft        # default: medium
created: YYYY-MM-DD                      # auto-fill on creation
expires: YYYY-MM-DD | permanent          # required for type=project (default created+30d); default permanent for other types
tags: [...]                              # optional
---
```

**Backward compat:** existing 297 files keep working with old frontmatter. `validate` lint identifies drift, `migrate` tool batch-upgrades when user requests.

**`feedback_test_realism.md`** (no `type` field): caught by `validate`, flagged for cleanup.

**Schema precedence (refinement adopted from Round-2 Codex)**: when `path`, `visibility`, and `agent:` disagree (e.g., file in `memory/claude/` with `visibility: shared` and `agent: codex`), `validate` MUST resolve in this order:
1. **`path` is canonical** — physical location is the truth. `visibility` and `agent` are derived/cached.
2. If `visibility` doesn't match path, `validate` warns + auto-corrects on next reindex
3. If `agent:` doesn't match path-implied owner, `validate` errors (likely a buggy mv or copy-paste)
4. The `agent:` field exists as a defensive cross-check (catches mis-moved files), not as an authoritative signal

### Principle 6 — Trigger reform (v2 final)

Replaces v1's L3 confirm-mode entirely:

| Layer | Signal | Action |
|-------|--------|--------|
| **L1 — Explicit user trigger** | User says "記下這個 / save / remember X" | Agent writes to durable memory immediately. Inline confirms what was written. |
| **L2 — Strong-signal auto trigger** | User explicit correction ("不要 X，要 Y") <br> OR strong validation ("對，繼續這方向") <br> OR new stable user/project fact discovered | Agent writes to durable memory. Inline confirms. |
| **L3 — Session boundary signal** | "清對話 / 對話結束 / 收工" alone, OR session about to end | Agent writes session continuity to **`.nakama/session_handoff.md`** (ephemeral, gitignored). NO durable memory write triggered by this signal alone. |

**The agent's judgment is the gate**. Training the agent on what counts as durable (L1-L2 contexts) vs ephemeral (L3) is the actual leverage point — not adding a confirmation prompt (which user will rubber-stamp anyway, per Gemini §3).

This trigger model is documented in CLAUDE.md, but the actual behavior is shaped by the model's instruction-following. The doc is a reference, not enforcement.

### Principle 7 — Maintenance (file-based, not database-based for now)

Extend `shared/memory_maintenance.py` (currently SQLite-only) with file-based commands:

| Command | Trigger | Action |
|---|---|---|
| `reindex` | Pre-commit hook on `memory/**` + daily cron | Scan frontmatter, regenerate `INDEX.md`. **Transition-aware**: indexes both old `memory/claude/*` and new `memory/{shared,claude,codex}/**` (per Gemini §4). |
| `expire` | Daily cron | Move expired (`expires < today`) files to `_archive/YYYY-MM/`. |
| `compact-sessions` | Manual or weekly cron | Move `project_session_*` to `_archive/`. **Initial bulk run in PR A** for files >30 days old. |
| `validate` | Pre-commit hook + CI workflow | Schema lint: required fields, frontmatter format, bilingual check for `shared/` |
| `migrate` | Manual one-shot | Walk old `memory/claude/*.md` → propose new layout paths → apply on confirm |
| `dedupe` | Manual quarterly | LLM-driven near-duplicate detection on feedback files |

### Principle 8 — Worktree discipline (separate concern)

Memory writes happen in worktree on `main` branch (or whatever feature branch the agent is on; memory commits will simply be cherry-pickable to main if needed). Memory commits **never** go on long-lived feature branches.

Memory writes never happen in `E:\nakama` (the bare-ish primary repo) — but this is part of the larger Worktree discipline rule.

---

## CLAUDE.md restructure (compressed scope)

**Add 3 new sections** (`## Memory 寫入紀律`, `## 工作面紀律 (Worktree)`, `## Multi-agent 協作`).

**Slim down** AFK/Sandcastle (3 lines), move Vault rules to `docs/agents/vault-writes.md` (1-line pointer).

**Keep** the original "Claude 記憶系統（跨平台）" pointer to memory/, but update the path to reflect new layout.

Estimated final length: ~150 lines (vs 195 baseline). Net signal density increases.

---

## Phased rollout (v2)

### Phase 0 — Stop the bleeding (split into PR A + PR B)

#### PR A — Immediate ship (today)

Branch: `chore/memory-and-ci-immediate-fix`

1. **CI workflow improvements** (already drafted, in `stash@{1}`):
   - `.github/workflows/ci.yml` — `paths-ignore: memory/**, **.md, docs/**` + pip/npm cache
   - `.github/workflows/external-probe.yml` — cron 5m → 15m
2. **Rewrite `memory/claude/feedback_conversation_end.md`**:
   - Remove auto-write/commit/push behavior
   - Define ephemeral memo path: `.nakama/session_handoff.md`
   - Define durable memory criteria (L1/L2/L3)
3. **Add `.gitignore`** entry: `.nakama/session_handoff.md`
4. **Bulk archive**: move `memory/claude/project_session_*.md` files **older than 30 days** to `memory/_archive/2026-04/` (git mv to preserve history). Files <30 days stay (recent enough to still serve as continuity).
   - **PR description requirement (refinement adopted from Round-2 Codex)**: include dry-run output listing all files that will move, with sample of 5 file titles, so reviewer can sanity-check the cutoff before merge.

**No** schema changes in PR A. **No** new directory structure. **No** CLAUDE.md changes.

PR A purpose: stop the bleeding. Smallest possible change to halt CI cost + memory pollution.

#### PR B — Design + scaffolding (after PR A merges)

Branch: `chore/memory-design-and-scaffolding`

1. **Research artifacts** → `docs/research/`:
   - This v2 design doc + v1 + 3 audits + integration matrix
2. **`memory/SCHEMA.md`**: schema docs with bilingual rules for `shared/`
3. **Empty directory shells**: `memory/{shared/{user,project,reference,decision}, codex/feedback}/.gitkeep`
4. **CLAUDE.md edits**:
   - Add `## Memory 寫入紀律`
   - Add `## 工作面紀律 (Worktree)` (subsumes today's worktree learnings)
   - Add `## Multi-agent 協作 (Claude + Codex)` (with bilingual rule)
   - Slim AFK/Sandcastle section
   - Move Vault rules pointer
5. **GitHub issues opened** for Phase 1 work (not implemented in PR B):
   - `memory_maintenance.py reindex` extension
   - `memory_maintenance.py validate` lint
   - `memory_maintenance.py migrate` migration tool
   - daily cron setup
   - pre-commit hook for `validate`

### Phase 1 — Infrastructure (1-2 weeks after PR B)

1. Implement `reindex` (transition-aware, indexes both old + new layouts)
2. Implement `validate` (schema lint)
3. Implement `expire` for file-based memories
4. Set daily cron on nakama VPS
5. Pre-commit hook on `memory/**` runs `validate`

### Phase 2 — Migration (weeks 3-4 after Phase 1)

1. Implement `migrate` tool
2. User-driven incremental migration (file-by-file or in batches)
3. Bilingual frontmatter walk for `memory/shared/` files
4. Old `memory/claude/MEMORY.md` deprecated, `INDEX.md` is canonical

### Phase 3 — Codex onboarding (when Codex active)

1. Codex's equivalent system prompt or config reads `memory/SCHEMA.md`
2. Codex writes only to `memory/codex/` and (rarely) `memory/shared/`
3. Monitor inter-agent friction first 2 weeks; iterate

### Phase 4 — Cleanup + optional architecture upgrade (month 2+)

1. `dedupe` on feedback files
2. `compact-sessions` rolls up remaining old session memos
3. INDEX.md final state: ~80-100 high-signal active entries
4. **Optional**: evaluate SQLite-as-source + markdown-export hybrid (Gemini §5) at scale. Defer until Phase 1-3 prove the file model insufficient.

---

## Open questions for user (final sign-off)

### Q1 — `paths-ignore` + direct push to main

3-way panel + Claude v2 agree. Acceptable risk: memory commits land in main without review. For solo dev with no second human reviewer this is a no-op.

### Q2 — Ephemeral handoff via `.nakama/session_handoff.md`

Lose: git-history of session memo (you cannot `git log -- .nakama/session_handoff.md` to see past sessions; the file is git-ignored).
Gain: zero CI commits for session-end pings, true ephemeral semantics, no memory pollution.

### Q3 — Bilingual frontmatter for `memory/shared/`

Cost: every `shared/` memory write is 2x size in frontmatter.
Gain: Codex (English-primary) can semantically retrieve Chinese memories via English `name_en`/`description_en`.

### Q4 — Bulk archive of `project_session_*` >30 days old in PR A

Files moved (not deleted) to `memory/_archive/2026-04/`. git mv preserves history. Reversible by `git mv` back. Estimated ~80-100 files moved.

### Q5 — Schema upgrade: additive (mid-migration files have mixed frontmatter)

`validate` lint identifies drift but doesn't fail builds during transition. After Phase 2 migration completes, `validate` becomes a hard gate.

---

## Specific items I want second-round panel to challenge

1. **Is the dual-tier (Tier 1 durable + Tier 2 ephemeral via `.nakama/`) the right cut?** Or is there a third tier I'm missing (e.g., "cross-window state" that lives < 1 day but > 1 conversation turn)?

2. **Is `agent:` frontmatter field redundant with directory path?** Codex v1 said yes; v2 keeps both as defensive redundancy. Justified or just clutter?

3. **Is `project default expires: created+30d` too aggressive?** Some project decisions last entire ADR cycle (1-3 months). Gemini didn't push back here; should they have?

4. **Does `memory/shared/decision/` introduce harmful overlap with `docs/decisions/` (ADRs)?** Where's the boundary? My current heuristic: ADR for cross-cutting design (>10 files affected), shared/decision for crystallized small choices (1-3 files). Defensible?

5. **Can `validate` + `reindex` actually be transition-aware safely?** Gemini §4 said yes; but reindex producing single INDEX.md from old+new locations may confuse agents during read. Need to verify.

6. **Is PR A's "bulk archive 30-day-old `project_session_*`" the right cutoff?** Some 6-month-old session memos might still be load-bearing; some 2-week-old ones might already be junk. Does cutoff matter or just batch them all?

7. **Does dropping the `memory-trunk` branch lose the "main log stays clean of memory noise" benefit Claude v1 cared about?** Or is `paths-ignore` enough — main log will have memory commits but CI won't run, and `git log --grep memory` filters them out for review?

---

## Failure modes (revised, narrower)

1. **`feedback_conversation_end.md` rewrite is mis-implemented** → agent keeps auto-committing. Mitigation: PR A includes test that searches for `git push` or `commit & push` patterns in feedback files.

2. **`.nakama/session_handoff.md` not deleted by next session** → handoff stays stale, conflicts with current state. Mitigation: hardcode "delete after read" in agent instruction; include timestamp in handoff so old ones are detectable.

3. **Bilingual frontmatter walk gets out of sync** (zh updated, en stale). Mitigation: `validate` includes `bilingual_consistency` check (compares semantic similarity of zh+en using small embedding); flags drift.

4. **Old + new layout coexist for >2 months** because user gets pulled away mid-migration. Mitigation: transition-aware `reindex` makes coexistence safe; add periodic reminder via daily cron output.

5. **Phase 0 PR A merges but PR B never lands** → repo has fixed CI but no schema/structure docs. Mitigation: PR B's GitHub issue is opened in PR A; visible reminder.

---

## Inversion test (revised)

If the **opposite** of v2 were proposed:
- "Combine Tier 1 + Tier 2 into single memory system": dilutes signal-to-noise; rejected based on observed pollution
- "Make `shared/` write-locked per agent": too rigid for low-frequency cross-agent updates; rejected based on flexibility need
- "Ban all auto memory writes; user must explicitly write everything": too rigid, loses agent's first-pass judgment value; rejected.

v2 stays robust to its inversions.
