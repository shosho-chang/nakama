# ADR-014: RepurposeEngine Plug-in Interface

**Date:** 2026-05-01
**Status:** Accepted

---

## Context

PRD #283 (Line 1 Podcast Repurpose) established a two-stage LLM pipeline to
transform podcast SRT transcripts into three-channel artifacts (Blog / FB / IG).
User Story 14 explicitly requires the orchestration to be abstracted into a
plug-in interface so that Line 2 (book notes) and Line 3 (literature → science
popularization) can onboard without re-implementing orchestration, parallelism,
retry, cost tracking, or I/O path conventions.

The three content lines share:
- The same output directory scheme (`data/repurpose/<YYYY-MM-DD>-<slug>/`)
- The same fan-out pattern (Stage 1 extract → Stage 2 parallel render)
- The same Bridge UI review surface (`/bridge/repurpose/<run_id>`)

But they differ in:
- Stage 1 JSON schema (podcast narrative arc ≠ book structure ≠ literature claims)
- Which channel renderers are applicable
- Source material format (SRT vs EPUB/notes vs PDF abstract)

## Decision

Slice 2 implements a lightweight plug-in interface in
`agents/brook/repurpose_engine.py`, frozen as follows:

### Protocols

```python
class Stage1Extractor(Protocol):
    def extract(self, source_input: str, metadata: EpisodeMetadata) -> Stage1Result: ...

class ChannelRenderer(Protocol):
    def render(self, stage1: Stage1Result, metadata: EpisodeMetadata) -> list[ChannelArtifact]: ...
```

Both protocols are `@runtime_checkable` so conformance can be asserted in tests.

### Engine wiring

```python
engine = RepurposeEngine(
    extractor=SomeExtractor(),
    renderers={"blog": BlogRenderer(), "fb": FBRenderer(), ...},
)
result = engine.run(source_input, EpisodeMetadata(slug="...", host="張修修"))
```

### I/O path scheme

```
data/repurpose/<YYYY-MM-DD>-<slug>/
    stage1.json
    blog.md
    fb-light.md
    fb-emotional.md
    fb-serious.md
    fb-neutral.md
    ig-cards.json
```

Date is `Asia/Taipei` at run time.  Slug is ASCII-sanitized (special chars and
CJK replaced by `-`).

### Stage 1 schema

Stage 1 JSON schema is **NOT** shared across lines.  Each line's
`Stage1Extractor` defines its own shape because the narrative skeletons differ:

| Line | Stage 1 shape |
|------|--------------|
| Line 1 (podcast) | `{narrative_segments, quotes, titles, meta_description, episode_type}` |
| Line 2 (book notes) | TBD — Line 2 extractor defines at onboard time |
| Line 3 (literature) | TBD — Line 3 extractor defines at onboard time |

Downstream renderers consume only the keys they expect; unknown keys are safe
to ignore.

### Multi-variant renderers

`ChannelRenderer.render()` returns `list[ChannelArtifact]` (not a single
artifact) to support `FBRenderer` which yields 4 tonal variants in one call.
Single-output renderers return a 1-element list.

### Error isolation

`RepurposeEngine.run()` catches per-renderer exceptions and accumulates them in
`ChannelArtifacts.errors` without aborting other renderers.  Stage 1 failure
propagates immediately (no partial result is useful if extraction fails).

### Bridge UI panel

`/bridge/repurpose/<run_id>` provides a read-only 3-panel review surface
(Slice 2 skeleton).  Mutation logic (edit-in-place, per-channel approve, blog →
Usopp WP draft) lands in Slice 10 and follows the PR #140 mutation pattern
(cookie auth + form POST + 303 redirect + native `<dialog>`).

## Consequences

**Positive:**
- Line 2 / Line 3 onboarding requires only implementing `Stage1Extractor` +
  optionally new `ChannelRenderer` implementations — zero changes to the engine.
- Blog / FB / IG renderers written for Line 1 are reusable by other lines.
- I/O path scheme is consistent; Bridge UI works for all lines without changes.

**Constraints:**
- Stage 1 JSON schema drift between lines is the caller's problem — the engine
  does not validate `stage1.data` structure.
- `ChannelRenderer.render()` is synchronous; renderers that need async
  (e.g. streaming SSE) must wrap themselves or use a future interface.
- `max_workers=6` default covers 6 parallel Stage 2 calls (Blog + 4×FB + IG);
  callers with more renderers should raise `max_workers` explicitly.

## Alternatives Considered

**A: Shared Stage 1 schema across lines** — Rejected.  A "super-schema" union
of podcast / book / literature fields would be either under-specified (most
fields empty for each line) or over-engineered.  The open `data: dict` approach
keeps each line's extractor self-contained.

**B: Async engine using `asyncio.gather`** — Deferred.  The current codebase
wraps blocking LLM calls in `asyncio.to_thread` at the router layer.
`ThreadPoolExecutor` achieves the same parallelism for CPU-bound / blocking I/O
without requiring `async def render()` on every renderer.  Can be revisited
if streaming renders are needed.

**C: Plugin registry (entry_points / config file)** — Deferred.  With only
3 planned lines and a single operator, a hard-coded `dict[str, ChannelRenderer]`
passed at construction time is simpler and testable.  A registry adds value when
third-party plugins exist.

## References

- PRD #283 — Line 1 Podcast Repurpose architecture section
- `agents/brook/repurpose_engine.py` — canonical implementation
- `tests/test_repurpose_engine.py` — acceptance tests
- `memory/claude/reference_bridge_ui_mutation_pattern.md` — Slice 10 mutation pattern
- ADR-001 — Brook = Composer role
- ADR-012 — Brook = inward processing boundary
