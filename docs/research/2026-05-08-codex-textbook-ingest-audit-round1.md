---
title: Codex round 1 audit — textbook ingest v3 3-patch review
date: 2026-05-08
model: codex (gpt-5 / o-pro tier — exact model not surfaced by codex CLI)
context_artifacts:
  - commit feee4d8 (3 patches: walker EPUB strip + slug relax + appendix redesign)
  - 3-chapter Obsidian-equivalent validation by 3 Explore subagents (ch1/3/6)
preserved_verbatim: true
---

# Verbatim Codex audit response (round 1)

> Saved per multi-agent-panel skill protocol — verbatim preservation matters.

---

**A. Patch correctness**

Patch 1 is directionally correct but has regex gaps. `_RE_EPUB_FILE_LINK` and `_RE_EPUB_ANCHOR_LINK` use `[^\]]+` / `[^)]*` which can span newlines, and the negative lookbehind `(?<!!)` fails on nested bracket text like `[Fig [A]](...)` (outer `[` is not immediately preceded by `!`). Tests at `tests/shared/test_source_ingest_strip_epub_links.py:12-66` cover the happy path but miss nested brackets, escaped `\]`, and multi-line links.

Patch 2 mostly correct. Blocks literal `..`, Windows-illegal ASCII, controls, leading/trailing whitespace, leading dot (`kb_writer.py:98-106`). Missing: no NFKC normalization before validation, so Unicode confusables and RTL override chars pass unchecked. Windows reserved device filenames (`CON`, `AUX`, etc.) also pass — not traversal but a filename hazard on Windows hosts.

Patch 3 correct for 0/1/N sections and tested (`test_assemble_body.py:53-123`). One silent regression: empty `concept_map_md` still emits empty `### anchor` H3 blocks in the appendix (`run_s8_preflight.py:294-309`). The appendix stripper uses literal `\n\n---\n\n## Section Concept Maps` as split marker (`run_s8_preflight.py:708-715`); any textbook content after that exact string would be silently dropped from the verbatim compare.

---

**B. Pure-red fix**

Pick C. The task states root cause is `route_concept` returning "skip," but `concept_classifier.py:9-12` and `:88-93` document that no-rule-fires falls through to L1 (alias), not skip. The real root cause is that L1 only appends `_alias_map.md` — no concept page is written — so `[[term]]` in the appendix still links to nothing. Option A therefore does not help (L1 is already the behavior). Option B is structurally impossible as written because `_assemble_body` runs before Phase 2 dispatch (`run_s8_preflight.py:503-535, 563-578`).

C sketch: after `run_phase2_dispatch` returns its dispatch log, rewrite the appendix in-place:

```python
page_terms = {e["term"] for e in log if e["action"] in {"create","exists-skip","update_merge"}}
alias_terms = {e["term"] for e in log if e["action"] == "alias"}
# Replace "## Wikilinks Introduced" block: emit [[term]] only for page_terms
# Replace with "## Aliases Recorded" plain-text list for alias_terms
# Terms with dispatch-error / ingest-fail: emit as plain text with warning comment
```

This makes red links structurally impossible without changing the classifier or adding an expensive pre-pass.

---

**C. Acceptance gate**

Not sufficient. The 4 rules check verbatim body, H2 anchors, figure count, and frontmatter `wikilinks_introduced` count (`run_s8_preflight.py:763-831`). The frontmatter count is compared against the frontmatter field, not against the actual visible `[[...]]` in the appendix body — these are assembled independently (`run_s8_preflight.py:304-312` vs `:825-831`) so they can diverge silently.

Add two checks:
1. Dispatch health: fail if any dispatch log entry has action `dispatch-error`, `ingest-fail`, or classifier `skipped` (`run_s8_preflight.py:579-582, 619-628`).
2. Wikilink resolve: every `[[term]]` in the appendix must exist as a concept page or alias entry on disk (cheap glob check post-dispatch).

---

**D. Pre-scale checkpoint**

Do not ship 28 chapters yet. The ch3 ground-truth sample (`ch3.md:967-1375`) shows a clean body and appendix structure, confirming Patch 3 works visually. But the current gate does not prove appendix wikilinks resolve — the 3-chapter validation already found 7 pure-red in ch1. One more step: run the post-dispatch appendix reconciliation (fix C above) on ch1 + ch3, confirm zero red links, then authorize scale batch. Cost: one re-ingest of ch1 (already-patched ch3 just needs the appendix rewrite pass).

---

**E. Other findings**

- `concept_classifier.py:9-12`: docstring says no-rule → L1 alias; the task's stated "skip" root cause conflicts. The actual behavior is L1 but L1 is incomplete (no concept page). Comment should clarify what L1 means for wikilink resolution.
- `concept_dispatch.py:9-12` vs `run_s8_preflight.py:603-624`: dispatch comment describes 4-action routing but the script only uses `create` / `exists-skip`. ADR-020 contract drift — needs comment correction or actual implementation.
- `run_s8_preflight.py:538-544`: `_slug_from_term` comment says "replace whitespace + path separators" but code does not replace internal whitespace (intentional post-Patch-2 but comment is stale).
- `tests/scripts/test_assemble_body.py`: no test for empty `concept_map_md` → silent empty H3 blocks.

---

**Concrete fix list**

1. `scripts/run_s8_preflight.py:547` — Add post-dispatch appendix/frontmatter reconciliation (C above). Rewrite `## Wikilinks Introduced` to page-terms only; add `## Aliases Recorded` plain-text list.
2. `scripts/run_s8_preflight.py:807` — Add dispatch-health and wikilink-resolve checks to acceptance gate.
3. `shared/kb_writer.py:98` — Prepend `unicodedata.normalize("NFKC", value)` before forbidden-char checks; reject if normalized form differs by adding a forbidden char. Add Windows reserved-name check.
4. `tests/shared/test_source_ingest_strip_epub_links.py` — Add nested bracket, escaped `\]`, and multi-line link cases (document current behavior).
5. `shared/concept_dispatch.py:9` / `run_s8_preflight.py:603` — Correct ADR-facing comments to reflect actual 2-action routing, or implement the missing 2 actions.

---

**Ship recommendation** — patch-4-then-go.
