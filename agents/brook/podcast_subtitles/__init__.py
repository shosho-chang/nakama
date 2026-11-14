"""Memo evidence helpers used by the podcast subtitle release line (ADR-063).

The Podcast Subtitle V2 deep machinery (ADR-056: Generation store, checkpoints,
Canonical Generation, Semantic Units, Verified Projection) was removed in
2026-11.  What remains here is what Memo Dual-Audit Release V1 executes:
normalized-audio handoff, Memo recognition and cue-boundary evidence, the Memo
bundled runner, and the Memo SRT repair receipts.

Import from the submodules directly; this package deliberately re-exports
nothing so that importing one helper never loads the others.
"""
