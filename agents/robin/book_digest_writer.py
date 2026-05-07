"""Write KB/Wiki/Sources/Books/{book_id}/digest.md from AnnotationSetV2.

Full-replace semantics: each call overwrites the file entirely. Idempotent.

Surfaces related KB pages via hybrid search (purpose="book_review") for each
H/A/C item. Wikilinks are reverse-surfaced from concept pages that have
annotation-from: {book_id} boundary markers written by annotation_merger v2.

S4: each KB hit renders two Obsidian checkboxes (👍/👎). On re-sync,
existing marks are parsed back, persisted to kb_search_feedback, and
re-rendered as [x] so the state survives the full-replace.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from agents.robin.kb_search import search_kb
from shared.annotation_store import get_annotation_store
from shared.config import get_vault_path
from shared.vault_rules import assert_reader_can_write

# Regex patterns for parsing feedback checkboxes embedded in digest.md.
_FB_UP_RE = re.compile(r"- \[([ x])\] 👍 相關 <!-- fb: cfi=([^\s>]+) path=([^\s>]+) -->")
_FB_DOWN_RE = re.compile(r"- \[([ x])\] 👎 不相關 <!-- fb: cfi=([^\s>]+) path=([^\s>]+) -->")


@dataclass
class DigestReport:
    """Summary of a write_digest() run."""

    book_id: str
    chapters_rendered: int
    items_rendered: dict  # {"h": int, "a": int, "c": int}
    hits_per_item_avg: float
    render_duration_ms: int
    errors: list[str] = field(default_factory=list)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_existing_feedback(
    digest_path: Path,
) -> list[tuple[str, str, str | None]]:
    """Parse (item_cfi, hit_path, signal) tuples from an existing digest.md.

    signal is "up", "down", or None (neither checkbox checked).
    Returns an entry for every (cfi, path) pair found in the file.
    """
    text = digest_path.read_text(encoding="utf-8")

    up_states: dict[tuple[str, str], bool] = {}
    for m in _FB_UP_RE.finditer(text):
        up_states[(m.group(2), m.group(3))] = m.group(1) == "x"

    down_states: dict[tuple[str, str], bool] = {}
    for m in _FB_DOWN_RE.finditer(text):
        down_states[(m.group(2), m.group(3))] = m.group(1) == "x"

    all_keys = set(up_states) | set(down_states)
    results: list[tuple[str, str, str | None]] = []
    for key in sorted(all_keys):
        cfi, path = key
        is_up = up_states.get(key, False)
        is_down = down_states.get(key, False)
        signal: str | None = "up" if is_up else ("down" if is_down else None)
        results.append((cfi, path, signal))
    return results


def _extract_chapter_ref(cfi: str) -> str:
    """Derive chapter identifier from EPUB CFI string.

    Prefers the explicit ID from epubcfi(/6/N[id]!/...). Falls back to the
    spine index number so items from the same chapter still group together.
    """
    m = re.search(r"/6/\d+\[([^\]]+)\]!", cfi)
    if m:
        return m.group(1)
    m = re.search(r"/6/(\d+)!", cfi)
    if m:
        return f"spine-{m.group(1)}"
    return "unknown"


def _surface_wikilinks(book_id: str, vault_path: Path) -> list[str]:
    """Return concept slugs whose pages carry annotation-from: {book_id} markers.

    Reverse-surfaces annotation_merger v2 results at zero extra LLM cost.
    """
    concepts_dir = vault_path / "KB" / "Wiki" / "Concepts"
    if not concepts_dir.exists():
        return []
    marker = f"<!-- annotation-from: {book_id} -->"
    slugs: list[str] = []
    for p in sorted(concepts_dir.glob("*.md")):
        try:
            if marker in p.read_text(encoding="utf-8"):
                slugs.append(p.stem)
        except Exception:  # noqa: BLE001
            continue
    return slugs


def _render_item_block(
    item,
    book_id: str,
    vault_path: Path,
    wikilinks_line: str,
    errors: list[str],
    existing_feedback: dict[tuple[str, str], str] | None = None,
) -> tuple[str, int]:
    """Render a single annotation item as a markdown block.

    Returns (block_text, hits_count). Each KB hit renders two Obsidian
    checkboxes (👍/👎) whose state is restored from existing_feedback when
    available, keyed by (item_cfi, hit_path).
    """
    if item.type == "highlight":
        query = item.text_excerpt
        label = "H"
        body_text = item.text_excerpt
        cfi = item.cfi
    elif item.type == "annotation":
        query = f"{item.text_excerpt}\n{item.note}"
        label = "A"
        body_text = f"{item.text_excerpt}\n\n> {item.note}"
        cfi = item.cfi
    else:  # comment (v2) / reflection (v3) — same shape, renamed in ADR-021 §1
        query = item.body[:500]
        label = "C"
        body_text = item.body
        cfi = item.cfi_anchor or ""

    try:
        hits = search_kb(
            query[:500],
            vault_path,
            top_k=3,
            purpose="book_review",
            engine="hybrid",
        )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"search_kb failed ({label}): {exc}")
        hits = []

    deep_link = f"/books/{book_id}#cfi={cfi}" if cfi else f"/books/{book_id}"

    fb = existing_feedback or {}
    if hits:
        hit_parts: list[str] = []
        for h in hits:
            path = h["path"]
            reason = h.get("relevance_reason", "")
            sig = fb.get((cfi, path)) if cfi else None
            up_check = "[x]" if sig == "up" else "[ ]"
            down_check = "[x]" if sig == "down" else "[ ]"
            hit_parts.append(
                f"  - [[{path}]] — {reason}\n"
                f"  - {up_check} 👍 相關 <!-- fb: cfi={cfi} path={path} -->\n"
                f"  - {down_check} 👎 不相關 <!-- fb: cfi={cfi} path={path} -->"
            )
        hits_lines = "\n".join(hit_parts)
    else:
        hits_lines = "  _(no KB hits)_"

    block = (
        f"**{label}** {body_text}\n\n"
        f"🔗 {wikilinks_line}\n\n"
        f"📚 KB 相關：\n{hits_lines}\n\n"
        f"📖 [開回 Reader]({deep_link})"
    )
    return block, len(hits)


def write_digest(book_id: str) -> DigestReport:
    """Generate and write KB/Wiki/Sources/Books/{book_id}/digest.md.

    Loads AnnotationSetV2 for book_id, groups items by chapter, calls
    search_kb(engine="hybrid", purpose="book_review", top_k=3) per item,
    and renders a chapter-structured markdown digest. Full-replace.

    S4: before rendering, parses any existing 👍/👎 checkboxes from the
    current file, upserts non-null signals to kb_search_feedback, and
    preserves checked state in the newly written digest.

    Returns a DigestReport describing what was rendered.
    """
    start_ms = int(time.monotonic() * 1000)
    errors: list[str] = []
    vault_path = get_vault_path()

    ann_set = get_annotation_store().load(book_id)
    if ann_set is None or not hasattr(ann_set, "book_id"):
        return DigestReport(
            book_id=book_id,
            chapters_rendered=0,
            items_rendered={"h": 0, "a": 0, "c": 0},
            hits_per_item_avg=0.0,
            render_duration_ms=int(time.monotonic() * 1000) - start_ms,
            errors=[f"no annotations found for book_id={book_id!r}"],
        )

    # S4: parse existing feedback before overwriting the file.
    relative = f"KB/Wiki/Sources/Books/{book_id}/digest.md"
    dest = vault_path / relative
    existing_feedback: dict[tuple[str, str], str] = {}
    if dest.exists():
        raw = parse_existing_feedback(dest)
        # Build cfi→query map for DB storage (query derived from annotation items).
        cfi_to_query: dict[str, str] = {}
        for item in ann_set.items:
            if item.type == "highlight":
                cfi_to_query[item.cfi] = item.text_excerpt
            elif item.type == "annotation":
                cfi_to_query[item.cfi] = f"{item.text_excerpt}\n{item.note}"
            elif item.type in ("comment", "reflection") and item.cfi_anchor:
                cfi_to_query[item.cfi_anchor] = item.body[:500]

        from shared.kb_search_feedback_store import upsert_feedback

        for cfi, hit_path, signal in raw:
            if signal is not None:
                existing_feedback[(cfi, hit_path)] = signal
                try:
                    upsert_feedback(
                        book_id=book_id,
                        item_cfi=cfi,
                        query_text=cfi_to_query.get(cfi, ""),
                        hit_path=hit_path,
                        signal=signal,
                    )
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"upsert_feedback failed: {exc}")

    # Group items by chapter ref, preserving first-occurrence order.
    chapters: dict[str, list] = {}
    for item in ann_set.items:
        if item.type in ("comment", "reflection"):
            # ADR-021 §1: v3 reflections use the same chapter_ref as v2 comments;
            # fall back to the cfi_anchor's spine index if chapter_ref is None.
            anchor = getattr(item, "cfi_anchor", None)
            ch = item.chapter_ref or (
                _extract_chapter_ref(anchor) if anchor else "unknown"
            )
        else:
            ch = _extract_chapter_ref(item.cfi)
        chapters.setdefault(ch, []).append(item)

    # Within each chapter: sort H/A by CFI, comments by chapter_ref (stable).
    for ch_items in chapters.values():
        ch_items.sort(key=lambda i: getattr(i, "cfi", "") or getattr(i, "chapter_ref", ""))

    # Reverse-surface concept wikilinks written by annotation_merger v2.
    wikilinks = _surface_wikilinks(book_id, vault_path)
    wikilinks_line = (
        " ".join(f"[[Concepts/{slug}]]" for slug in wikilinks)
        if wikilinks
        else "_none yet — run KB sync first_"
    )

    h_count = a_count = c_count = 0
    total_hits = 0
    total_items = 0
    sections: list[str] = []

    for ch_idx, (ch_ref, ch_items) in enumerate(chapters.items(), start=1):
        heading = f"## Ch{ch_idx} {ch_ref}"
        blocks: list[str] = []
        for item in ch_items:
            block, hit_count = _render_item_block(
                item, book_id, vault_path, wikilinks_line, errors, existing_feedback
            )
            blocks.append(block)
            total_hits += hit_count
            total_items += 1
            if item.type == "highlight":
                h_count += 1
            elif item.type == "annotation":
                a_count += 1
            else:
                c_count += 1
        sections.append(heading + "\n\n" + "\n\n---\n\n".join(blocks))

    frontmatter = (
        f"---\n"
        f"type: book_digest\n"
        f"book_id: {book_id}\n"
        f'book_entity: "[[Sources/Books/{book_id}]]"\n'
        f"schema_version: 1\n"
        f'updated_at: "{_now_iso()}"\n'
        f"---\n"
    )
    content = frontmatter + "\n" + "\n\n".join(sections) + ("\n" if sections else "")

    assert_reader_can_write(relative)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")

    duration_ms = int(time.monotonic() * 1000) - start_ms
    hits_per_item_avg = total_hits / total_items if total_items > 0 else 0.0

    return DigestReport(
        book_id=book_id,
        chapters_rendered=len(chapters),
        items_rendered={"h": h_count, "a": a_count, "c": c_count},
        hits_per_item_avg=hits_per_item_avg,
        render_duration_ms=duration_ms,
        errors=errors,
    )
