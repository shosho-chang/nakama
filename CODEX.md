# Nakama — Codex Operating Rules

This file is the repo-level entry point for Codex and other generic coding agents.

Read `CLAUDE.md` for the full project rules. The rules below are mandatory and exist to prevent multi-window contamination when Claude, Codex, Sandcastle, and local agents work in parallel.

## 0. Worktree Isolation — Main Is Control Plane

`E:\nakama` / the main worktree is a **control plane**, not a write surface.

Allowed in the main worktree:

- inspect status, logs, diffs, and files;
- fetch and fast-forward main;
- create or remove sibling worktrees;
- merge already-reviewed PRs.

Forbidden in the main worktree unless the user explicitly authorizes this exact exception:

- editing code, docs, or memory;
- generating files;
- committing;
- writing durable memory;
- running implementation work.

Any file-producing task must first create a task-specific worktree. **All worktrees live under `E:\nakama-worktrees\`** — never at the `E:\` root, never inside the main repo.

```powershell
git switch main
git fetch --prune
git pull --ff-only
git worktree add E:\nakama-worktrees\<task-name> -b <branch-name> origin/main
```

Examples:

- `E:\nakama-worktrees\source-map-builder`
- `E:\nakama-worktrees\toast-inbox-importer`
- `E:\nakama-worktrees\memory-update`

Drop the `nakama-` prefix from the folder name; the parent directory already says it.

The old `E:\nakama-<task-name>` convention was retired on 2026-09-20 — it had accumulated 33 folders at the `E:\` root, mixed in with video footage and downloads. `E:\nakama\worktrees\` is retired for the same reason plus a sharper one: a repo full of copies of itself makes ripgrep, pytest and Syncthing recurse into them, and one `git clean -xdf` in the main repo wipes the lot. `E:\nakama\.claude\worktrees\` is managed automatically by the Claude Code desktop app — leave it alone, and do not put anything there by hand.

Reclaim a worktree once its PR merges or closes: `git worktree remove <path>`. If the working tree still holds uncommitted work you are not ready to lose, commit it to a `wip/<topic>` branch and push before removing.

Never use `git add .`. Stage explicit paths only.

Reason: multiple Claude/Codex/Sandcastle windows may operate concurrently. Writing in the main worktree can leak unrelated memory drafts, review artifacts, screenshots, or generated files into unrelated PRs.

## 1. Memory

Durable memory writes must not happen in `E:\nakama`. Use a sibling task worktree or a dedicated memory worktree, then stage only the intended memory files and index updates.

## 2. Handoff

For any implementation task, record the exact worktree path and branch in the task prompt, PR body, or handoff note. If the current directory is `E:\nakama` and the task will write files, stop and create a sibling worktree first.

