I couldn’t save `docs/research/2026-05-08-codex-v2-signoff.md`: the workspace is read-only and the write was rejected. Verbatim content:

## §1 - Sign off with mods

Verdict: **sign off with mods**. Required edits:

- Promote P1.5 golden chapter fixture to P0, or remove condition 7 from the P0 gate. Prefer promotion.
- Move P1.3 placeholder string invariant into P0.5, or delete the duplicate P1 row after folding it into gate hardening.
- Clarify P1.4 migration scope: mandatory before live Wiki publish and before any run that reads/reuses the 192 staged concept pages.
- Re-check P0.1 file attribution: v2 cites `run_s8_batch.py:382`, while the matrix names `verify_staging.py` / batch spot-check behavior.
- Clean mojibake in title/status/section markers before publishing.

## §2 - Open questions in v2 §5

1. P0 is complete after promoting golden fixture and placeholder invariant. CJK is validly deferred for this English-only textbook path.

2. “Patch P0 -> ship BSE only -> UAT” is the honest pace. Shipping BSE+SN together would skip the 5/7 lesson: panel approval is not Obsidian validation.

3. The 7-condition gate is minimum honest only if computed from files on disk after ingest, not from writer internals. It must independently scan markdown/frontmatter/concept files.

4. Rejecting Gemini Path C as a rewrite is right. v2 adopts the useful substance: canonicalization, graph-aware backlinks, dispatch-result appendix, and golden E2E.

5. No prose-only panel round 3. Next review should be artifact-based: P0 diff, gate output, BSE staging pages, and UAT findings.

## §3 - Final ship recommendation

Approve v2 with the mods above: implement modified P0 from v2 §1, enforce the file-based 7-condition gate from v2 §2, re-ingest BSE only to staging, and send that to UAT. After UAT passes, implement P1 before SN, including migration before live publish, then ingest SN, rerun the final gate, and only then publish to `KB/Wiki/`.