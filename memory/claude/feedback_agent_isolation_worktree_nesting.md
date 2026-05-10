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

**How to apply**:
- For Agent dispatches that touch any code, tests, or filesystem state: pre-create sibling worktree at `E:/nakama-<topic>-<purpose>`, dispatch **without** `isolation`. Brief the agent: "Work ONLY in `<sibling-path>` — do NOT use `git worktree add` to create another worktree."
- For read-only research dispatches (Explore, summarisation, file lookups): `isolation` is unnecessary anyway; skip it.
- For Sandcastle / cloud-isolated dispatches: this concern doesn't apply (different mechanism, runs in its own VM).
- When cleaning up after an agent that DID end up nested: prefer waiting for file locks to release rather than `git worktree remove -f -f`, which can hit Permission denied on Windows and leave the worktree state half-removed.

Complements: `feedback_subagent_shared_worktree.md` (parallel sub-agents need worktrees), `feedback_dual_window_worktree.md` (multi-window race), `feedback_worktree_session_hygiene.md` (worktree cleanup discipline).
