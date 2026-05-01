# Context

## Repo

You are working in **nakama** — a Python AI Agent team for Health & Wellness content. Read these orientation docs before deciding:

- `CLAUDE.md` — three red lines (閉環/事實驅動/窮盡一切), P7 completion format, vault rules, file-deletion rules
- `docs/agents/issue-tracker.md` — GitHub Issues via `gh` CLI, PR-first culture
- `docs/agents/triage-labels.md` — 5 canonical labels (needs-triage / needs-info / ready-for-agent / ready-for-human / wontfix)
- `docs/agents/domain.md` — multi-context layout. **ADRs live in `docs/decisions/` (NOT `docs/adr/`)**.

## Open `sandcastle`-labeled issues

!`gh issue list --label sandcastle --state open --json number,title,labels --jq '.[] | "#\(.number) \(.title) — labels: \([.labels[].name] | join(","))"'`

## Recent commits on main (last 10)

!`git log --oneline -10 origin/main`

# Task

You are an autonomous coding agent. Solve **one** issue per iteration.

## Pick an issue

Choose the highest-priority open `sandcastle`-labeled issue that is NOT blocked.

- Read each candidate's body via `gh issue view <N>`. Check the `Blocked by` section.
- Skip issues labeled `needs-info` (waiting on external input).
- If all candidates are blocked or `needs-info`, output `<promise>COMPLETE</promise>` and exit.

## Workflow

1. **Explore** — `gh issue view <N>` for the full body. Read referenced files. If acceptance criteria are vague or you cannot find a referenced ADR/file, leave a comment requesting clarification, label `needs-info`, and exit (do NOT guess).

2. **Plan** — read the relevant ADR (`docs/decisions/ADR-*.md`) and existing patterns in the codebase. Keep the change scope minimal. If 3+ unrelated files would change, the issue is too big for sandcastle — comment, remove the `sandcastle` label, exit.

3. **Execute (TDD red→green)**:
   - Write a failing test FIRST (`tests/...` mirrors `agents/...` / `shared/...` / `thousand_sunny/...`)
   - Implement the minimum to pass
   - Re-run until green
   - Refactor only when readability suffers

4. **Verify** — all three must pass before commit:
   ```bash
   pytest tests/<changed-area>/ -q
   ruff check .
   ruff format --check .
   ```
   (No `.venv/` in this sandbox — Python deps are user-pip-installed system-wide via Dockerfile.)

5. **Commit** — ONE commit per issue, on the current branch. Sandcastle handles branching + merge-back to host HEAD; do NOT push, do NOT open a PR.

   Conventional Commits format:
   ```
   <type>(<scope>): <summary>

   - <key change 1>
   - <key change 2>

   Closes #<issue-number>
   ```
   `<type>` ∈ feat / fix / chore / docs / test / refactor. Do NOT include `Co-Authored-By` lines (host adds them when opening the PR).

   `Closes #<N>` in the commit body — when the host operator merges the eventual PR, GitHub auto-closes the issue.

## Rules (from `CLAUDE.md`, do not violate)

- **No backwards-compat hacks** — delete unused code; do not leave `// removed` or `# TODO removed` markers.
- **No `--no-verify`** / no skipping pre-commit hooks. If a hook fails, fix the underlying issue.
- **No `rm`/`rmdir`** — destructive ops require user authorization (project rule).
- **Trust internal code** — do not add error handling for scenarios that cannot happen. Validate at system boundaries only.
- **No new files unless required** — prefer editing existing files.
- **No comments explaining WHAT** — well-named identifiers do that. Only comment WHY when non-obvious.
- **Aesthetic surfaces stay out of scope** — UI / Bridge UI / Brook templates are NOT sandcastle-eligible.

## Escalation

If blocked at any point (missing test fixtures, unclear acceptance, external dependency you cannot install, requires UI/aesthetic judgment):

1. `gh issue comment <N> --body "Sandcastle blocked: <reason>. Escalating to human."`
2. `gh issue edit <N> --add-label needs-info --remove-label sandcastle`
3. Continue to next iteration (do NOT commit a half-done state).

# Done

When all `sandcastle`-labeled issues have been processed (closed, blocked, or escalated), output:

<promise>COMPLETE</promise>
