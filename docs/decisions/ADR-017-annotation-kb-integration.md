# ADR-017: Annotation KB Integration

**Status:** Accepted  
**Date:** 2026-05-04  
**Deciders:** shosho-chang  
**Related:** Slice 1 (#338), Reader route (`thousand_sunny/routers/robin.py`)

---

## Context

The Reader (Thousand Sunny / Robin UI) lets 修修 highlight text (`==text==`) and
add annotations (`> [!annotation]`) while reading Inbox or Sources files.

Before this ADR, annotations were stored **inside the source file** by the
`/save-annotations` endpoint.  This had two problems:

1. **Lifecycle coupling** — when a source file is ingested (moved from Inbox →
   KB/Raw → KB/Wiki/Sources) the annotation markers move with it but the file
   hash changes, breaking downstream idempotence checks in Robin.
2. **Source corruption** — ingest prompts are designed for clean markdown; embedded
   `> [!annotation]` callouts interfere with heading / concept extraction.

## Decision

Annotations are stored in a **separate file per source** at
`KB/Annotations/{slug}.md`, decoupled from the source file lifecycle.

Key choices:

| Question | Decision | Rationale |
|---|---|---|
| Storage location | `KB/Annotations/` (vault-side) | Survives source ingest/translation; visible in Obsidian |
| File format | YAML frontmatter + JSON code block | Frontmatter for human-readable metadata; JSON body for structured items (avoids YAML quoting edge cases with CJK / special chars) |
| Slug derivation | frontmatter `title` → filename stem | Already-ingested sources have stable titles; inbox fallback is filename |
| Source file mutation | Prohibited on every save | Source files must stay clean for Robin ingest pipeline |
| Lock strategy | Per-slug `threading.Lock` | Single-process uvicorn; prevents concurrent-save lost-update within one process |
| mark_synced | No-op stub | Future Obsidian sync hook; not needed for MVP |

## Schema

```python
class Highlight(BaseModel):
    type: Literal["highlight"]
    text: str
    created_at: str  # ISO-8601

class Annotation(BaseModel):
    type: Literal["annotation"]
    ref: str   # first 60 chars of highlighted text
    note: str
    created_at: str

class AnnotationSet(BaseModel):
    slug: str
    source_filename: str
    base: str          # "inbox" | "sources"
    items: list[Highlight | Annotation]
    updated_at: str
```

Stored at `KB/Annotations/{slug}.md`:

```
---
slug: deep-sleep-research
source: deep-sleep-research.md
base: inbox
updated_at: "2026-05-04T00:00:00Z"
---

```json
[
  {"type": "highlight", "text": "...", "created_at": "..."},
  {"type": "annotation", "ref": "...", "note": "...", "created_at": "..."}
]
```
```

## API Changes

| Endpoint | Before | After |
|---|---|---|
| `POST /save-annotations` | `Form(filename, content, base)` — writes full file | `Body(AnnotationSet JSON)` — writes KB/Annotations/{slug}.md; source untouched |
| `GET /read` | Returns `content` (body only) | Also returns `slug` + `annotations` (list of items) |

## Consequences

- **Reader UI** renders annotations by overlaying `annotationsData` onto pure
  `mdSource` at render time (client-side `buildAnnotatedMd()`); `mdSource` is
  never mutated.
- **Vault rules** updated: `KB/Annotations/` added to `READER_WRITE_WHITELIST`
  in `shared/vault_rules.py` and documented in `CLAUDE.md`.
- **Existing files** that already contain `==text==` / `> [!annotation]` markers
  are not migrated.  The Reader displays them inline (marked.js renders
  them as-is), but future saves overwrite only the annotation store.
- **Phase 2 upgrade points**: cross-process file locking (fcntl), Obsidian sync
  via `mark_synced`, migration tool for existing annotated sources.
