# Memory System Redesign v1 — Cross-platform, Multi-agent (Claude + Codex)

**Status:** Draft v1, panel review pending
**Date:** 2026-05-08
**Author:** Claude Opus 4.7 (1M context) — single-LLM draft, panel review by Codex GPT-5 + Gemini 2.5 Pro pending
**Repository:** nakama (E:\nakama)
**Related:**
- Existing infrastructure: `shared/memory_maintenance.py` (SQLite-backed, applies to old schema only)
- Triggering memory: `memory/claude/feedback_conversation_end.md`
- Schema parallel: `memory/shared.md` + `memory/agents/{robin,franky}.md` (old) vs `memory/claude/*` (new)
- Surrounding constraint: ADR-021 + ADR-022 just shipped (memory commit volume peaked 5/7-5/8)

---

## Context

### What memory is for in this repo

The user (修修, solo developer) operates a Health & Wellness AI agent system across multiple Claude Code windows on Windows + Mac, plus Sandcastle cloud sandboxes for parallel sub-agent execution. Memory exists to give cross-session continuity to AI assistants — facts about the user, project decisions, behavioral feedback, references to external systems.

A separate Codex (GPT-5 via ChatGPT auth) is being integrated as an additional collaborating agent on this repo. So memory must serve **at minimum 2 distinct AI agents** (Claude + Codex) across **2 desktop platforms** (Win + Mac) and **cloud sandbox executions**.

### Problem statement (data from 2026-05-08 audit)

**Scale:**
- `memory/claude/`: 297 files (feedback: 155, project: 114, user: 4, reference: 23)
- `memory/claude/MEMORY.md`: 295 lines (system prompt warns 200-line truncate threshold — already over)
- 264 commits touching `memory/` in past 30 days (avg 9/day)
- All commits go through PR + squash-merge → CI runs each time
- GHA Actions quota at 90% used in current billing cycle (2700/3000 min); memory-related CI is a non-trivial contributor

**Two parallel schemas exist:**
- *Old schema* (`memory/shared.md`, `memory/agents/{robin,franky}.md`): frontmatter `type / agent / tags / created / updated / confidence / ttl` — has SQLite-backed `expire` / `archive` / `stats` infrastructure in `shared/memory_maintenance.py`.
- *New schema* (`memory/claude/*`): frontmatter `name / description / type` (3 fields only) — **no expire, no confidence, no TTL, no maintenance**.

The new file-based system is what AI agents primarily read/write. It has no decay mechanism, so accumulation is unbounded.

**Behavioral problem (the trigger):**
Existing feedback memory `feedback_conversation_end.md` instructs Claude that the phrase "清對話" (or "對話結束" or similar) should:
1. Pick out worth-saving info from session
2. Write to `memory/claude/`
3. Update MEMORY.md
4. `git commit && git push`
5. Reply "記好了"

This trigger has produced:
- One PR per session-end
- ~1-3 session-end memos per day across all windows
- All on whatever branch the worktree happened to be on (no branch discipline)
- 90%+ of session-end memos are `project_session_*` handoff notes that decay rapidly (git log + GitHub issues already preserve this info)

**Current PR pattern for memory commits (sample):**
```
17cef58 docs(memory): 5/8 ADR-021 complete + design system v1 ship handoff (#494)
636a2c3 docs(memory): 早報 2026-05-06 overnight (#439)
6872ef0 docs(memory): 收工 2026-05-05 late (#435)
99f8d4a docs(memory): 收工 2026-05-05 evening (#425)
13d5332 docs(memory): 收工 2026-05-05 (#416)
4cdce43 docs(memory): 收工 2026-05-04 夜 + overnight (#375)
b28343e docs(memory): 收工 2026-05-04 晚 (#364)
... [continues for >20 in past month]
```

These commits **do not deliver knowledge that affects code**. They are session journals. PR review and CI on them is overhead with zero quality signal.

**Multi-window stash drift (observed today):**
While running this redesign session, a different Claude window stashed `wip-mid-bench-2026-05-07` work, pushing the user's CI-fix stash from `stash@{0}` to `stash@{1}`. The auto-trigger to "stash before clear" combined with "pop stash@{0}" assumption produced an accidental wrong-stash pop event. The same pattern of "shared mutable state assuming single-window" affects MEMORY.md edits.

### Constraints

- **C1 — Cross-platform durability**: memory must sync between Windows + Mac without manual intervention. Currently achieved by storing in repo + git sync. **Repo storage is non-negotiable.**
- **C2 — Multi-agent**: Codex coming online; existing memory should not be polluted by agent-specific feedback (e.g. "Claude tends to over-narrate" doesn't apply to Codex; "Codex skips error handling" doesn't apply to Claude).
- **C3 — Sandcastle**: parallel sub-agents in cloud sandboxes write memory; merges back to main repo via git. Conflict resolution must work without manual reconciliation in 90%+ of cases.
- **C4 — Solo developer**: no second human reviewer. PR-review-on-memory adds zero quality signal. Branch protection on main exists (so direct push currently blocked).
- **C5 — Aesthetic / signal-to-noise**: 297 files including 155 feedback is search-degrading. Low-signal `project_session_*` memos drown durable knowledge.
- **C6 — Backward compat**: existing 297 files must continue to function during transition. No big-bang migration.

---

## Decision

### Principle 1 — Memory ≠ session log

Memory captures **durable cross-session truths**: who the user is, what the project decided, what external systems exist, what behavioral patterns to repeat or avoid. Session logs (what happened today, in-progress debug context, handoff notes) belong in **git commits + GitHub issues**, not memory files. This single conceptual cut justifies most downstream choices.

**Implication**: `project_session_*` files are anti-pattern. They will be archived (not deleted — git history preserves them) and forbidden going forward.

### Principle 2 — Append-only files, generated index

Each memory is a single file with frontmatter. `MEMORY.md` becomes a **generated artifact** rebuilt by `memory_maintenance.py reindex` from frontmatter scans. No agent ever edits `MEMORY.md` directly.

**Implication**: removes the primary multi-window conflict point (parallel edits to MEMORY.md). Conflicts only on individual memory files, which is rare since each agent typically writes its own files.

### Principle 3 — Memory commits bypass PR review

Memory is documentation. PR review on docs adds no quality signal in a solo-dev repo. CI on memory-only commits is pure overhead.

**Implementation chosen — Option A (memory-trunk branch with periodic fast-forward):**

```
memory commits go on `memory-trunk` branch
  → push directly (no PR, no CI on workflow files)
  → weekly: `git checkout main && git merge --ff-only memory-trunk`
  → main stays as the canonical reference; memory-trunk is the write surface
```

Why over Option B (path-based branch protection exemption): GitHub path-based exemptions exist but are configured per-rule and brittle. memory-trunk is simpler.

Why over Option C (in-repo path filter on CI only): doesn't solve PR-review noise, only CI cost. CI cost partially solved by `paths-ignore` already (in this PR).

**Note**: this means memory commits do NOT appear in `main` git log until weekly fast-forward. Trade-off: cleaner main log vs slightly delayed visibility. Acceptable for solo dev.

### Principle 4 — Cross-agent directory layout

```
memory/
├── shared/                     # Cross-agent — Claude + Codex both read & write
│   ├── user/                   # Facts about 修修 (preferences, role, languages)
│   ├── project/                # Facts about nakama project (decisions, scope, deadlines)
│   ├── reference/              # Pointers to external systems (Linear, Slack, Grafana, GitHub)
│   └── decision/               # Crystallized design decisions (complement to ADRs — smaller scope)
├── claude/
│   └── feedback/               # Claude-specific behavioral guidance ("Claude over-narrates")
├── codex/
│   └── feedback/               # Codex-specific behavioral guidance ("Codex skips error handling")
├── _archive/                   # Auto-rotated old files (project_session_*, expired memories)
│   └── YYYY-MM/                # Year-month buckets
├── INDEX.md                    # GENERATED — do not hand-edit
└── SCHEMA.md                   # Schema documentation, read by both agents
```

**Read rules:**
- Claude reads: `shared/**` + `claude/**`
- Codex reads: `shared/**` + `codex/**`
- Neither reads `_archive/**` by default

**Write rules:**
- Claude writes: `claude/**` freely; `shared/**` with care (cross-agent impact)
- Codex writes: `codex/**` freely; `shared/**` with care
- Neither writes `_archive/**` (only `memory_maintenance.py` does)
- Neither writes `INDEX.md` (only `memory_maintenance.py reindex` does)

**Backward compat:** existing `memory/claude/*.md` files stay readable during transition. New writes go to new layout. Migration is incremental — `memory_maintenance.py migrate` walks old files, suggests target paths, applies on confirm.

### Principle 5 — Schema upgrade (additive, backward-compat)

```yaml
---
name: ...                                  # required (existing)
description: ...                           # required (existing)
type: feedback | user | project | reference | decision   # existing + 'decision'
visibility: shared | claude | codex        # NEW — required for new files; defaults to inferring from path
confidence: high | medium | draft          # NEW — default 'medium'
created: 2026-05-08                        # NEW — auto-fill on creation
expires: 2026-08-08 | permanent            # NEW — required for type=project (default created+30d)
                                           #     ; default 'permanent' for user/reference/feedback/decision
tags: [worktree, multi-agent]              # NEW — optional, search aid
---
```

`SCHEMA.md` documents this with examples and rules. Both Claude's CLAUDE.md and Codex's equivalent reference it.

### Principle 6 — Trigger reform

Three layers, replacing the single auto-trigger from `feedback_conversation_end.md`:

| Layer | When | Action |
|-------|------|--------|
| **L1 — Explicit user trigger** | User says "記下這個 / save / remember X / update memory" | Write immediately to relevant file. Specify path explicitly. |
| **L2 — Strong-signal auto trigger** | User explicit correction ("不要 X，要 Y") <br>OR strong validation ("對，就這樣 / 這個方向繼續") <br>OR new user/project fact discovered (not session detail) | Write to feedback / shared file. Inline confirm what you wrote. |
| **L3 — Forbidden auto trigger** | "清對話 / session 結束 / 對話結束" alone | Do **NOT** auto-write. Instead: list candidate memories ("學到 3 件可記的事: …"), ask user which to save. User decision gates write. |

`feedback_conversation_end.md` is rewritten to capture L3 explicitly.

### Principle 7 — Maintenance cadence

Extend `memory_maintenance.py` (currently SQLite-only) with file-based commands:

| Command | Trigger | Action |
|---|---|---|
| `reindex` | Pre-commit hook on `memory/**` + daily cron | Rebuild `INDEX.md` from frontmatter scan |
| `expire` | Daily cron | Move files past `expires` to `_archive/YYYY-MM/` |
| `compact-sessions` | Manual or weekly cron | Move all `project_session_*` files to `_archive/` (legacy cleanup) |
| `dedupe` | Manual quarterly | LLM-driven near-duplicate detection on feedback files |
| `migrate-old-to-new-layout` | Manual one-shot | Walk old `memory/claude/*` → propose paths in new layout → apply with confirm |

### Principle 8 — Worktree discipline (separate concern, but related)

Memory writes happen in **either**:
- A dedicated `E:\nakama-memory` worktree on `memory-trunk` branch (long-lived), OR
- The current task's worktree if memory write is critical-path to current task — but commit must be cherry-picked / rebased onto memory-trunk before push.

Memory writes **never** happen in `E:\nakama` (the bare-ish primary repo).

This is part of the larger "Worktree discipline" addition to CLAUDE.md (separate sub-decision, surveyed later in this doc).

---

## CLAUDE.md restructure

Current CLAUDE.md (5/7 baseline, 195 lines): structure is reasonable but has accumulated cruft. Proposed changes (delta only — full file in `docs/research/2026-05-08-claude-md-v2-draft.md` if user agrees):

### Add 3 new sections

1. **`## Memory 寫入紀律`** — replace the buried `feedback_conversation_end.md` trigger with explicit rules:
   - Three-layer trigger (L1 / L2 / L3 above)
   - Schema reference (`memory/SCHEMA.md`)
   - Branch rule (commit to `memory-trunk`, never feature branches)
   - Worktree rule (never write memory from `E:\nakama`)

2. **`## 工作面紀律 (Worktree)`** — codify the lessons from 2026-05-08 cleanup session:
   - `E:\nakama` is metadata-only (never the work surface)
   - Each task gets a sibling worktree (`E:\nakama-<topic>`)
   - Subagent dispatch via Sandcastle (default) or local `isolation: worktree`
   - Stash discipline (use `-m`, never `pop stash@{0}` — find by message)

3. **`## Multi-agent 協作 (Claude + Codex)`** — set expectations for shared state:
   - Read scope per agent (Claude: shared+claude; Codex: shared+codex)
   - Write scope per agent (own subdir + shared with care)
   - Conflict resolution (append-only files + `reindex` regenerates INDEX.md)
   - Schema source of truth: `memory/SCHEMA.md`

### Slim down

- **Lines 114-121 (AFK / Sandcastle)** — currently references 2 memory files repetitively. Compress to 3 lines:
  > Default for parallel/AFK dispatch = Sandcastle (cloud isolation). Local `isolation: worktree` only for single short tasks watched live. See `memory/shared/decision/sandcastle-default.md`.

- **Lines 100-110 (Vault 寫入規則)** — moved to `docs/agents/vault-writes.md`. CLAUDE.md keeps a 1-line pointer.

### Remove

- Repeated mention of ADR-001 (link once, not three times).
- `Agent skills` section (lines 176-190) — relevant routing for engineering skills lives in those skills' own metadata; CLAUDE.md doesn't need to enumerate. Keep "PR-first culture" as a 1-liner under `## 工作方法論`.

### Result

CLAUDE.md goes from 195 lines → estimated ~140-160 lines, but with **3 new substantive sections**. Net signal-density improves substantially.

---

## Phased rollout

### Phase 0 — Stop the bleeding (today, this PR)

Land in `chore/memory-design-and-ci-fix`:
1. CI workflow improvements (already drafted) — `paths-ignore: memory/**` blocks future memory CI cost
2. This design doc → `docs/research/`
3. Codex + Gemini panel audits (this PR includes audits as research artifacts)
4. Updated `feedback_conversation_end.md` (L3 forbidden trigger)
5. New CLAUDE.md sections (3 added)
6. New `memory/SCHEMA.md`
7. Empty `memory/shared/{user,project,reference,decision}/` and `memory/codex/feedback/` directory shells (with `.gitkeep`)

**Does not touch:** existing 297 files in `memory/claude/*` (zero migration risk during transition window).

### Phase 1 — Infrastructure (next 1-2 weeks)

1. Extend `memory_maintenance.py` with file-based `reindex` / `expire` / `compact-sessions` commands
2. Set up daily cron (existing nakama VPS) to run `reindex` + `expire`
3. Pre-commit hook: `memory_maintenance.py reindex` on memory commits
4. Set up `memory-trunk` branch with weekly fast-forward to main

### Phase 2 — Migration (weeks 3-4)

1. `memory_maintenance.py migrate` walks old files, suggests target paths
2. User confirms in batches; files moved to new layout
3. `MEMORY.md` regenerated from new structure
4. Old `memory/claude/MEMORY.md` becomes alias / deprecation pointer

### Phase 3 — Onboarding Codex (when Codex integration starts)

1. Codex equivalent of CLAUDE.md (or system prompt) reads `memory/SCHEMA.md`
2. Codex writes only to `memory/codex/feedback/` and (carefully) `memory/shared/`
3. Inter-agent friction monitored for first 2 weeks; adjust as needed

### Phase 4 — Cleanup (month 2+)

1. `dedupe` on feedback files (155 → estimated 60-80 after dedup)
2. `compact-sessions` archives all `project_session_*` from past
3. MEMORY.md final state: ~80-100 lines of high-signal active memories

---

## Open decisions (panel: please push back on these)

### Decision A — `memory-trunk` branch vs path-based exemption

Chosen: `memory-trunk` branch. Alternative: GitHub branch protection rule with path-based exemption for `memory/**`.

Push-back invited on: complexity vs maintenance cost; potential confusion of dual-trunk model; failure modes if `memory-trunk` diverges from main beyond fast-forward.

### Decision B — Trigger L3 ("清對話" → confirm-only)

Chosen: Claude lists candidates, asks user to confirm. Alternative: completely forbid auto-write (only L1 explicit triggers + L2 corrections during conversation).

Push-back invited on: whether the confirm step actually reduces noise vs adds friction; whether users will rubber-stamp the confirm anyway.

### Decision C — Cross-agent shared/ subdirectory

Chosen: `memory/shared/` accessible by both Claude and Codex. Alternative: complete agent isolation (no shared memory; cross-agent context passed via explicit docs only).

Push-back invited on: whether shared writes will create implicit coupling; whether one agent's interpretation pollutes the other's context.

### Decision D — Schema additive vs replace

Chosen: additive (existing files stay, new fields optional initially). Alternative: schema-version field; hard cutover at v2.

Push-back invited on: whether soft-additive creates long-term schema entropy; vs whether hard cutover risks breaking 297 existing files.

### Decision E — Project memory expiry default (30 days)

Chosen: `type=project` defaults to `expires: created+30d`. Alternative: per-file judgment; no automatic expiry.

Push-back invited on: whether 30 days is too aggressive (some project memories last entire ADR cycle ~weeks-months) or too lax (most are session-handoff junk).

### Decision F — `_archive/` retention

Chosen: keep forever (git history is canonical, archive is just out-of-active-search). Alternative: hard delete after 6 months (smaller repo).

Push-back invited on: implications of growing `_archive/` over years; whether retention period should be type-dependent.

### Decision G — Codex specifics

Open: How exactly does Codex (GPT-5 via ChatGPT) integrate? CLI invocation? Subagent dispatch? File-watch model? This design assumes a configurable agent that can be told "read these paths, write to these paths". If Codex's actual integration model differs, the cross-agent layout may need adjustment.

Push-back invited on: realism of multi-agent assumption; concrete Codex integration model.

---

## Failure modes worth flagging upfront

1. **`memory-trunk` divergence beyond fast-forward**: if someone accidentally merges from main → memory-trunk (or commits non-memory work to memory-trunk), the weekly fast-forward fails. Mitigation: pre-commit hook on memory-trunk that rejects commits touching outside `memory/**`.

2. **Stale agent reading old layout while migration in progress**: during Phase 2, an old session may still read `memory/claude/*.md` while new writes go to `memory/shared/*`. Mitigation: leave symlinks during transition? Or have CLAUDE.md explicitly say "read both paths during transition". User-facing complexity nontrivial.

3. **Reindex race condition**: two parallel agents both run `reindex` → both produce slightly different MEMORY.md → conflict on push. Mitigation: reindex uses lockfile; if locked, just skip (next run picks up).

4. **Schema entropy if backward-compat too lax**: 6 months from now, files may have inconsistent frontmatter. Mitigation: `memory_maintenance.py validate` linter that runs in cron and reports drift to a status memory; pre-commit hook can warn on drift.

5. **Sandcastle write back conflicts**: parallel sub-agents in different sandboxes write memory; merge back. With append-only files this is mostly OK except for shared/ writes (rare). Mitigation: shared/ writes go through a "propose-then-commit" pattern requiring user mediation.

---

## Inversion test

If the **opposite** of this design were proposed, would it sound reasonable?

- **Inversion 1**: "Memory should include session logs because they preserve fragile recent context AI agents need to bootstrap quickly in a new session." → Plausible if you're optimizing for AI bootstrap context. But experience shows 90%+ of session logs are read once at the next session-start and never again. Git log + open issues do this job better.

- **Inversion 2**: "Don't separate cross-agent memory; keep one pool — agents benefit from cross-pollination." → Plausible if agents are similar in profile. But Claude-specific corrections ("don't over-narrate") confuse Codex; Codex-specific corrections ("don't skip errors") confuse Claude. The shared/ directory captures genuinely cross-agent facts (about user, project) without polluting agent-specific behavioral guidance.

- **Inversion 3**: "PRs on memory commits ensure quality." → Plausible in team settings. In solo dev, no second human reviews — PR adds zero quality signal, only CI cost + flow friction.

The inversions don't dominate. Design seems robust to its main alternatives.

---

## Specific items I want the panel to challenge

1. **Is `memory-trunk` overengineered?** Could path-based branch protection exemption (Decision A alt) achieve same outcome with less custom infrastructure?

2. **Is L3 confirm-mode actually different from "don't auto-write"?** Will user just rubber-stamp every confirm? Should it be hard-disabled instead?

3. **Are 297 files actually the problem, or is the problem 155 feedback files specifically?** Maybe project + reference are fine, and only feedback needs dedup. Targeted fix vs systemic redesign.

4. **Is the multi-agent assumption (Codex coming) actually load-bearing?** If Codex turns out to integrate via a totally different model (e.g. as a Claude subagent rather than independent agent), does shared/codex/claude split still make sense?

5. **Is `_archive/` adding value or just deferring deletion?** If memories never come back from archive, why not just delete? Git history preserves anyway.

6. **Does `shared/decision/` overlap unhelpfully with `docs/decisions/` (ADRs)?** Where does a small design decision go — small ADR, or shared/decision memory? This boundary is blurry.

7. **Is Phase 0 (this PR) too ambitious?** Could land in 2 PRs instead — one for CI fix, one for design. The dual-purpose PR violates "one concern per PR" lightly.

8. **Are the new CLAUDE.md sections going to get read?** CLAUDE.md grows constantly; new sections often glossed by AI agents. Should the new content go in linked docs instead?

---

## Acknowledgments

This design was shaped by today's actual debugging session — observing the wrong-stash pop, the multi-window MEMORY.md modification, the 13 zombie agent worktrees, and the user's existing `feedback_conversation_end.md` trigger pattern. Codex and Gemini audits will be appended as separate documents.
