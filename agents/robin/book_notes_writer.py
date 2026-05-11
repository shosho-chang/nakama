"""Write KB/Wiki/Sources/Books/{book_id}/notes.md from CommentV2 items.

Full-replace semantics: each call overwrites the file entirely.
Comments are grouped by chapter_ref under H2 headings.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from shared.config import get_vault_path
from shared.schemas.annotations import CommentV2
from shared.vault_rules import assert_reader_can_write


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_notes(book_id: str, comments: list[CommentV2]) -> None:
    """Write KB/Wiki/Sources/Books/{book_id}/notes.md with comments grouped
    by chapter_ref under H2 headings. Full-replace: existing notes.md
    contents are overwritten. Empty comments → no-op (or frontmatter-only
    stub, either works). Idempotent: same input → same output."""
    relative = f"KB/Wiki/Sources/Books/{book_id}/notes.md"
    assert_reader_can_write(relative)

    dest: Path = get_vault_path() / relative
    dest.parent.mkdir(parents=True, exist_ok=True)

    if not comments:
        return

    # Group by chapter_ref, preserving first-occurrence order
    chapters: dict[str, list[str]] = {}
    for c in comments:
        chapters.setdefault(c.chapter_ref, []).append(c.body)

    frontmatter = (
        f"---\n"
        f"type: book_notes\n"
        f"book_id: {book_id}\n"
        f'book_entity: "[[Sources/Books/{book_id}]]"\n'
        f"schema_version: 1\n"
        f'updated_at: "{_now_iso()}"\n'
        f"---\n"
    )

    sections: list[str] = []
    for chapter_ref, bodies in chapters.items():
        heading = f"## {chapter_ref}"
        body_text = "\n\n".join(bodies)
        sections.append(f"{heading}\n\n{body_text}")

    content = frontmatter + "\n" + "\n\n".join(sections) + "\n"
    dest.write_text(content, encoding="utf-8")
