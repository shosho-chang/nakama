---
name: invoke-review-skill-not-handoff
description: When user says "review the PR", invoke the /code-review skill — do not hand off as if user will read the diff themselves
metadata:
  type: feedback
---

When the user says "先 review / 去 review / 幫我 review / 來 review the PR", they mean **invoke the `/code-review` skill on the PR**. Do NOT respond with a "review-ready for you" summary that hands the diff back to the user — the user does not do PR reviews themselves on this project.

**Why:** Repeated correction on 2026-05-27 — user says quote "我說 review 是要你去呼叫 skill 去 review，我已經講超多次了，我自己不做 review". Pattern: I keep finishing a PR push with "ready for review by you" wording, treating the human as the review surface. The user's mental model is: I am the reviewer (via the skill); they are the merger / decision authority on findings. Handoff-style language wastes a round-trip and is a documented annoyance.

**How to apply:**

1. When the user references "review" of a PR, branch, diff, or worktree — call `Skill(skill="code-review", args="#<PR>")` (or `code-review:code-review` if that's the listed variant) **before** any other action. Don't ask permission. Don't describe what the skill will do. Don't summarize the diff first.
2. Trigger phrases (all map to "invoke the skill"): `review`, `code review`, `review the PR`, `先 review`, `去 review`, `來 review`, `幫我 review`, `audit the diff`.
3. When you finish a PR push, the standard close should be: "PR #N pushed. 要不要 invoke `/code-review`?" — offering the skill explicitly. Don't write phrases like "review-ready for you" or "for your review" or "等你 review" — those assume the user is the reviewer.
4. After the skill returns findings, present them ranked + offer 3 options (fix inline / open issue / defer). The user decides the disposition; I act on it.

Related: [[focused-pr-auto-review]] (the Claude-authored side: auto-invoke /review on own focused PRs without asking) · [[auto-merge-after-review]] (post-review action: squash-merge without re-asking permission) · [[smoke-verify-after-pr-chain]] (similar discipline boundary — what to do after pushing code).
