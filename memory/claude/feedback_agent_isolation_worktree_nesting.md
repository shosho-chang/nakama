---
name: agent_isolation_worktree_nesting
description: Agent tool's isolation:worktree creates a NESTED worktree at .claude/worktrees/ inside the main repo; this violates the main-as-control-plane rule and leaks .env via find_dotenv() walk-up. Pre-create a sibling worktree manually before dispatching code-writing agents.
type: feedback
---

When dispatching a sub-agent for non-trivial implementation work (code edits, test runs, file writes), **do NOT pass `isolation: "worktree"` to the Agent tool**. The harness creates the temporary worktree at `<main-repo>/.claude/worktrees/agent-<id>/` — i.e. **inside the main worktree**. This violates Nakama's "main is control plane, never write surface" rule (CLAUDE.md §工作面紀律). It also has a concrete operational consequence: pytest's `find_dotenv()` walks up the directory tree from the nested worktree and picks up `<main-repo>/.env`, polluting environment variables (e.g. `R2_*` from unrelated branches) for the agent's test runs.

Instead: pre-create a sibling worktree manually before dispatch:

```
git worktree add E:/nakama-<topic>-<purpose> -b feat/<topic> origin/main
# or, attaching an existing branch:
git worktree add E:/nakama-<topic>-fix <existing-branch>
```

Then dispatch the Agent **without** `isolation`, and brief it to `cd` to that sibling path as its first action. The dispatch prompt must explicitly say "Do NOT use `git worktree add` to create another worktree" so the agent doesn't re-introduce the nested pattern.

**Why**: Concrete incident on 2026-05-10 (N518a impl agent for ADR-024). Agent was dispatched with `isolation: "worktree"`. Work landed at `E:\nakama\.claude\worktrees\agent-a69ce6a97f44a0c1c`. Agent's own self-report flagged the issue: *"Pre-existing test pollution caused by my worktree being nested inside /e/nakama (not a sibling). tests/scripts/test_run_repurpose.py calls load_dotenv() with no args, which find_dotenv() walks up and picks up /e/nakama/.env, polluting R2_* env vars for later tests. This is environmental and unrelated to my code; it does NOT happen on a sibling worktree."* Cleanup later required `git worktree remove -f -f` (the agent process held file locks even after marking complete), and even that hit "Permission denied" on Windows until the locks released — at which point a *different* git operation got blocked by stale-locked branch refs.

**Second failure mode — absolute-path edits hit the main worktree (2026-05-21).** During the Design System v0.1 migration, ~10 agents were dispatched *with* `isolation: "worktree"`. Beyond the nesting issue above, a new problem surfaced: the dispatch prompt referenced files by absolute path (`E:/nakama/thousand_sunny/...`) for the agent to read as reference. Agents then defaulted to those same `E:/nakama/...` absolute paths when **editing**, so their Write/Edit calls landed in the *main* worktree instead of their isolated one — 3 of the first 4 agents did this. Each had to self-recover (capture as patch → apply to its own worktree → `git restore` main), and the main worktree was left with stray modified files + untracked artifacts that blocked later `git pull --ff-only`. The fix that worked for the remaining batches: the dispatch prompt must explicitly say (a) "run `git rev-parse --show-toplevel` first — that IS your worktree; ALL edits target paths inside it", (b) "NEVER write/edit any path beginning with `E:\nakama\`", (c) "read reference files via `git show <sha>:<path>`, not absolute reads of E:/nakama". Batches 2 and 3 with the hardened prompt had zero contamination.

**How to apply**:
- For Agent dispatches that touch any code, tests, or filesystem state: pre-create sibling worktree at `E:/nakama-<topic>-<purpose>`, dispatch **without** `isolation`. Brief the agent: "Work ONLY in `<sibling-path>` — do NOT use `git worktree add` to create another worktree."
- If you nonetheless use `isolation: "worktree"` (e.g. parallel AFK fleet): the prompt MUST harden against absolute-path edits — see the 2026-05-21 fix above. Never give the agent `E:/nakama/...` paths to edit; have it resolve its own toplevel and read references via `git show`.
- For read-only research dispatches (Explore, summarisation, file lookups): `isolation` is unnecessary anyway; skip it.
- For Sandcastle / cloud-isolated dispatches: this concern doesn't apply (different mechanism, runs in its own VM).
- When cleaning up after an agent that DID end up nested: prefer waiting for file locks to release rather than `git worktree remove -f -f`, which can hit Permission denied on Windows and leave the worktree state half-removed.

Complements: `feedback_subagent_shared_worktree.md` (parallel sub-agents need worktrees), `feedback_dual_window_worktree.md` (multi-window race), `feedback_worktree_session_hygiene.md` (worktree cleanup discipline).
