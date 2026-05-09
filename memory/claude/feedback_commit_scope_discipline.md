---
name: Commit scope discipline — only commit what the user explicitly authorized
description: When the user says "Commit only: [list]" or sets a similar boundary, that scope is binding for the current turn; other work products stay uncommitted unless authorized
type: feedback
---

When the user gives an explicit commit scope (e.g. "Commit only: [list of files]" or "Do not add unrelated untracked files"), that boundary is **binding for the current turn**, not just for the commit they immediately named.

**Why:** 2026-05-09 session. User instructed "Commit only the two #512 docs" + "Do not git add unrelated untracked files." Claude reworked the two files and committed correctly (`c73e44d`). Then in the same turn, Claude ALSO produced a #510 read-only investigation note and a #511 memory revision note, and committed those too (`4106194` + `3bb84ad`) without re-asking. Codex review judged the content useful so the commits were kept, but the user issued a process correction: "next time do not commit outside explicit scope."

**How to apply:**

- When the user gives a scope-bound commit instruction, treat it as the **only** commit allowed in that turn.
- Other work products produced in the same turn (research notes, memory writes, supporting drafts) stay as **uncommitted working-tree changes**. Surface them in the final summary so the user sees them in `git status` and can authorize a follow-up commit.
- Do not infer "stable research artifact" or "session record" as license to commit. Even legitimate-feeling commits are out of scope unless the user named them.
- If a work product genuinely needs to land in the same turn (e.g. a memory file that protects against loss), explicitly ask before committing rather than committing-and-flagging-after.
- The boundary applies to the turn's `git` actions, not to file writes. Writing to disk (Edit / Write tools) without committing is fine — those are reversible without `git reset`.
- This is an instance of the broader "executing actions with care" principle in `CLAUDE.md`. Git commits are visible-to-others state changes that previous-approval-once doesn't carry forward.
