# Spike: EPUB → Markdown Converter Comparison

**Date:** 2026-05-06  
**Issue:** #442  
**ADR:** ADR-020 Phase 0  
**Outcome:** ebooklib + markdownify selected

---

## Scope

Compare three candidate tools on the same input (synthetic EPUB 3 fixture matching BSE structure) across five quality dimensions:

| Dimension | Why it matters for Phase 0 |
|---|---|
| Heading preservation | Phase 1 chapter-boundary detection relies on `# H1` markers |
| Paragraph + inline formatting | Bold/italic are semantic signals in academic text |
| Table output quality | Nutrition tables are a primary content type in BSE/SN4E |
| Image link handling | Images must be extractable to `Attachments/Books/{book_id}/` |
| Programmatic control | Path rewriting and spine-order iteration must happen in Python |

---

## Candidates

### 1. pandoc (via pypandoc-binary)

Tested with `pypandoc.convert_file(epub_path, 'markdown', extra_args=['--wrap=none'])`.

**Input:**
```html
<h1>Chapter 1: Introduction</h1>
<p>This is <strong>bold</strong> and <em>italic</em> text.</p>
<table><tr><th>Col 1</th><th>Col 2</th></tr><tr><td>A</td><td>B</td></tr></table>
<img src="fig1.png" alt="Figure 1.1: Test figure"/>
```

**Output:**
```markdown
[]{#ch1.xhtml}

# Chapter 1: Introduction

This is **bold** and *italic* text.

  Col 1   Col 2
  ------- -------
  A       B

![Figure 1.1: Test figure](fig1.png)
```

**Assessment:**

| Dimension | Result |
|---|---|
| Headings | ✅ ATX `#` |
| Bold/italic | ✅ `**` / `*` |
| Tables | ⚠️ Space-aligned grid format (non-GFM, non-pipe) |
| Image links | ✅ Standard `![alt](src)` |
| Programmatic control | ❌ Subprocess only; requires tmp file I/O for path rewriting |
| Spurious output | ⚠️ Inserts `[]{#ch1.xhtml}` anchor stubs from EPUB internal IDs |
| Dependency | ❌ External binary (pypandoc-binary adds ~34 MB to image) |
| Already in requirements.txt | ❌ No |

---

### 2. ebooklib + markdownify (Python)

Tested with `ebooklib.epub.read_epub()` → `BeautifulSoup` preprocessing → `markdownify(heading_style="ATX")`.

**Output (same input):**
```markdown
# Chapter 1: Introduction

This is **bold** and *italic* text.

| Col 1 | Col 2 |
| --- | --- |
| A | B |

![Figure 1.1: Test figure](Attachments/Books/test-book/fig1.png)
```

**Assessment:**

| Dimension | Result |
|---|---|
| Headings | ✅ ATX `#` |
| Bold/italic | ✅ `**` / `*` |
| Tables | ✅ GFM pipe syntax (GitHub-compatible, Obsidian-compatible) |
| Image links | ✅ Standard `![alt](src)`; vault-relative path rewriting done in Python before conversion |
| Programmatic control | ✅ Full Python: spine order via `book.spine`, image bytes via `item.get_content()` |
| Spurious output | ✅ None — BeautifulSoup strips XML declarations and `<head>` before conversion |
| Dependency | ✅ Pure Python |
| Already in requirements.txt | ✅ `ebooklib>=0.18` (markdownify added as companion) |

**Spine order access:** `book.spine` returns `[(idref, linear)]` tuples in reading order. Iterating via manifest lookup guarantees correct chapter sequence — critical for Phase 1 chapter boundary detection.

**Image path rewriting:** Before calling markdownify, a BeautifulSoup pass rewrites all `<img src="...">` to vault-relative paths using `_resolve_epub_href()` (handles `../Images/fig.png` relative paths). The rewritten path appears directly in the markdown output.

---

### 3. Calibre `ebook-convert`

Not available in this environment (requires ~100 MB system install, no pip package). Characteristics based on documentation:

| Dimension | Expected |
|---|---|
| Headings | ✅ Configurable |
| Bold/italic | ✅ Preserved |
| Tables | ⚠️ Depends on output format; HTML intermediate needed |
| Image handling | ✅ Best-in-class extract; external path rewriting needed |
| Programmatic control | ❌ CLI subprocess only |
| Dependency | ❌ ~100 MB system package; not pip-installable |
| Already in requirements.txt | ❌ No |

Calibre would require a post-processing pass to rewrite image paths and fix table markdown. Its primary advantage (figure extraction completeness) is superseded by ebooklib's direct binary access to EPUB manifest items.

---

## Comparison Matrix

| Dimension | pandoc | **ebooklib + markdownify** | Calibre |
|---|---|---|---|
| Table format | ⚠️ space-grid | ✅ GFM pipe | ⚠️ post-process |
| Image path rewrite | ❌ subprocess | ✅ Python in-process | ❌ subprocess |
| Spine order | ✅ (implicit) | ✅ explicit `book.spine` | ✅ (implicit) |
| Pure Python | ❌ | ✅ | ❌ |
| In requirements.txt | ❌ | ✅ | ❌ |
| Spurious output | ⚠️ anchor stubs | ✅ clean | ❌ verbose |
| Binary size | ~34 MB | 0 (pure Python) | ~100 MB |

---

## Decision: ebooklib + markdownify

**Selected tool:** `ebooklib` (spine/manifest reader) + `markdownify` (HTML→Markdown) + `BeautifulSoup` (preprocessing)

**Version at spike time:**
- ebooklib: 0.20 (from `pip list`)
- markdownify: 1.2.2
- BeautifulSoup4: 4.14.3 (already in requirements.txt)

**Rationale:**

1. **GFM pipe tables** — pandoc's space-aligned grid format is non-standard and will break any downstream markdown parser. Pipe tables work in Obsidian, GitHub, and every standard renderer.
2. **In-process path rewriting** — vault-relative image paths (`Attachments/Books/{book_id}/fig.png`) are set during the `<img>` preprocessing pass, before markdownify runs. No subprocess round-trip needed.
3. **Pure Python, zero binary dep** — ebooklib + markdownify add 0 binary weight to the container. pypandoc-binary adds ~34 MB; Calibre ~100 MB.
4. **Already in `requirements.txt`** — `ebooklib>=0.18` was already required (ADR-010 EPUB path). `markdownify` is the only new addition.
5. **Explicit spine order** — `book.spine` exposes reading order as `[(idref, linear)]`. Manifest lookup via `{item.id: item}` gives deterministic chapter sequence, critical for Phase 1 boundary detection.

---

## BSE EPUB Pilot Run

The unit test suite uses synthetic in-memory EPUB fixtures. To run against the actual BSE EPUB (`biochemistry-sport-exercise-2024.epub`):

```python
from shared.raw_ingest import epub_to_raw_markdown
from pathlib import Path

result = epub_to_raw_markdown(
    epub_path=Path("path/to/BSE.epub"),
    book_id="biochemistry-sport-exercise-2024",
    attachments_dir=Path("E:/Shosho LifeOS/Attachments/Books"),
)

# Write to vault
out = Path("E:/Shosho LifeOS/KB/Raw/Books/biochemistry-sport-exercise-2024.md")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(result.markdown, encoding="utf-8")

print(f"Title: {result.title}")
print(f"Images extracted: {len(result.images_extracted)}")
print(f"Output size: {len(result.markdown):,} chars")
```

Expected output location: `KB/Raw/Books/biochemistry-sport-exercise-2024.md`
Expected attachments: `Attachments/Books/biochemistry-sport-exercise-2024/{fig-name}.{ext}`

---

## Limitations and Known Edge Cases

1. **Inline math** — EPUB math is typically MathML. markdownify does not convert MathML to LaTeX; equations render as raw MathML tags. Phase 1 LLM pass can handle LaTeX extraction from the raw content. Mitigation: preserve `<math>` tags as-is in the output (markdownify strips unknown tags by default — set `strip=[]` for math or use a custom converter).

2. **SVG images** — SVG files are extracted as binaries but not inlined. They render as standard image links. Acceptable for Phase 0.

3. **Complex multi-stylesheet EPUBs** — some EPUBs embed CSS classes that produce layout elements (side-bars, callout boxes). BeautifulSoup strips the class attributes; layout intent is lost. This is acceptable for a raw lossless text layer — Phase 0 goal is text + structure, not visual layout.

4. **Image filename collisions** — if two chapters reference images with the same basename but different paths, only one survives (last-write wins). Real BSE EPUB image naming should be checked; if collisions exist, prefix with chapter index.
