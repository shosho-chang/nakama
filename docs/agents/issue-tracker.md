# Issue tracker: GitHub

Issues and PRDs for this repo live as GitHub issues. Use the `gh` CLI for all operations.

## Conventions

- **Create an issue**: `gh issue create --title "..." --body "..."`. Use a heredoc for multi-line bodies.
- **Read an issue**: `gh issue view <number> --comments`, fetching labels alongside.
- **List issues**: `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'` with `--label` and `--state` filters as needed.
- **Comment on an issue**: `gh issue comment <number> --body "..."`
- **Apply / remove labels**: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **Close**: `gh issue close <number> --comment "..."`

The repo is inferred from `git remote -v` — `gh` does this automatically when run inside a clone.

## Repo-specific notes (nakama)

- **PR-first culture**: most work lands as PRs (often without a corresponding issue) — see recent commits like `#252`, `#253`. Issues are reserved for cross-cutting work that needs triage state, ADR-level decisions, or external-reporter inputs.
- **Branch protection limitation**: this is a private repo on GH free tier — branch protection rules cannot be enforced via API (`reference_github_plan_branch_protection.md`). Don't assume merge gates are blocking; reviewers and CI checks are advisory.
- **Decision artefacts ≠ issues**: ADRs live in `docs/decisions/ADR-NNN-*.md`, plans in `docs/plans/YYYY-MM-DD-*.md`, research in `docs/research/`. When an issue's resolution is "we decided X," cross-link the ADR/plan rather than burying the decision in issue comments.

## When a skill says "publish to the issue tracker"

Create a GitHub issue.

## When a skill says "fetch the relevant ticket"

Run `gh issue view <number> --comments`.
