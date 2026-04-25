# Branch Protection Setup — `main` (Solo Workflow)

**Scope:** GitHub branch protection rule for `main` so accidental direct push, force push, or untested merge is impossible. Tuned for **solo developer** — the standard "require approval" setting doesn't fit, so we use linear-history + status-checks-required as the gate.
**Owner:** 修修 console-only setup (this can't be code-config'd without a GitHub PAT + REST call, which is more friction than the one-time UI setup).
**Cadence:** One-time setup; revisit if a second contributor joins.

---

## 1. Why these rules

| Rule | Catches |
|------|---------|
| Require pull request before merging | accidental `git push origin main` from feature work-in-progress |
| Require status checks (CI) to pass | merging while ruff red / tests broken |
| Require linear history | random merge commits clutter `git log`; squash/rebase only |
| Block force pushes | rewriting `main` history erases other people's clones (or your own from another machine) |
| Block deletions | typing `git push --delete origin main` would actually work otherwise |

What we **can't** require for a solo workflow:
- "Require approvals" — there's no second reviewer; ultrareview + local 3-agent review is the substitute
- "Require code owners" — same reason
- "Require signed commits" — adds keychain friction without much marginal safety here

---

## 2. Steps (~5 min)

### 2.1 Open the rule editor

GitHub → repo `shosho-chang/nakama` → **Settings** → **Branches** (left sidebar) → **Add branch protection rule**.

### 2.2 Set the branch pattern

**Branch name pattern**: `main`

(Single branch — not a glob. Future `release/*` branches can get their own rule.)

### 2.3 Required checks

Tick the following:

- ☑ **Require a pull request before merging**
  - ☑ Require approvals: **0** (solo workflow — set to 0 explicitly)
  - ☐ Dismiss stale pull request approvals when new commits are pushed (irrelevant at 0 approvals)
  - ☐ Require review from Code Owners (no CODEOWNERS file)
  - ☐ Require approval of the most recent reviewable push
- ☑ **Require status checks to pass before merging**
  - ☑ Require branches to be up to date before merging
  - **Status checks that are required**: search and add:
    - `lint-and-test` (from `.github/workflows/ci.yml`)
    - `lint-pr-title` (from `.github/workflows/conventional-commits-lint.yml`)
- ☑ **Require linear history**
- ☐ Require conversation resolution before merging (no PR comments tooling in solo flow)
- ☐ Require signed commits
- ☐ Require deployments to succeed before merging
- ☐ Lock branch (read-only)

### 2.4 Push restrictions

- ☑ **Block force pushes**
- ☑ **Block deletions**
- ☐ Restrict who can push to matching branches (no need — only you have push access)

### 2.5 Bypass list

- **Allow specified actors to bypass required pull requests**: leave empty
- **Do not allow bypassing the above settings**: ☑ tick (admin can't override silently — forces explicit re-toggle if you ever need to)

### 2.6 Save

Click **Create** at the bottom.

---

## 3. Verify

Open a fresh terminal and try a direct push (replace dummy file with whatever):

```bash
git checkout main
echo "test" >> /tmp/test.md
git add /tmp/test.md
git commit -m "chore: direct push test"
git push origin main
```

Expected: `remote: error: GH013: Repository rule violations found ...` — push rejected.

To merge: open a PR, wait for CI green, squash-merge.

---

## 4. Daily flow under protection

```bash
# Feature branch (worktree if doing parallel sessions per feedback_dual_window_worktree.md)
git checkout -b feat/whatever-name
# work, commit, lint, test
git push -u origin feat/whatever-name
gh pr create --title "feat(scope): summary" --body "..."

# Wait for CI green (~1-2 min)
gh pr merge --squash --delete-branch

# Pull main locally
git checkout main && git pull
```

`gh pr merge --squash` is the only path. Anything that looks like a fast-forward direct push is now blocked.

---

## 5. If you ever need to bypass

Real cases (rare):
- Initial branch protection setup itself (chicken-and-egg — temporarily disable, push, re-enable)
- Recovering from a broken main (force-push a fixed commit) — disable protection, fix, re-enable
- One-off doc typo that's not worth a PR — still better to PR; bypass is a slippery slope

To bypass:
1. Settings → Branches → edit `main` rule → uncheck the relevant box → Save
2. Do the operation
3. Re-enable the rule

`gh api` flow exists but the UI is faster for one-offs.

---

## 6. Why no auto-merge / auto-deploy here

`/auto-merge` is a separate decision (Phase 8 — CI/CD auto-deploy on main). Branch protection just gates **what gets to main**; CI/CD gates **what happens after main**. Keep them separate so a bad protection config doesn't break deploy and vice versa.

---

## 相關

- [`docs/plans/quality-bar-uplift-2026-04-25.md`](../plans/quality-bar-uplift-2026-04-25.md) — Phase 9 scope
- [`feedback_branch_workflow.md`](../../memory/claude/feedback_branch_workflow.md) — feature-branch + PR convention
- [`feedback_dual_window_worktree.md`](../../memory/claude/feedback_dual_window_worktree.md) — worktree for parallel sessions
- [`feedback_pr_review_merge_flow.md`](../../memory/claude/feedback_pr_review_merge_flow.md) — auto review / squash flow
