"""KB vault 索引器 — vault walker → H2 chunker → FTS5 + vec0 writer.

`index_vault(vault_path, db)` 接受已初始化的 SQLite connection
（sqlite-vec 已 load，kb_* tables 已存在），
掃 KB/Wiki/{Sources,Concepts,Entities}（recursive，含 nested Books/{book_id}/）
+ KB/Annotations/，按 H2 切 chunk，mtime_ns 增量跳過未改檔案。

Annotation 檔（KB/Annotations/{slug}.md，ADR-021 §2）走獨立路徑：
parse JSON code block 為 v1/v2/v3 items，每個 highlight.text / annotation.note /
reflection.body 各成一個 chunk，metadata 帶 source_slug + item_type + chapter_ref。

Typical usage:
    from shared.kb_hybrid_search import make_conn
    from shared.kb_indexer import index_vault

    db = make_conn()
    stats = index_vault(vault_path, db)
    print(stats.files_indexed, "files,", stats.chunks_added, "chunks")
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from shared import kb_embedder
from shared.annotation_store import upgrade_to_v3
from shared.schemas.annotations import (
    AnnotationSetV3,
    AnnotationV3,
    HighlightV3,
    ReflectionV3,
)
from shared.utils import extract_frontmatter

# H2 heading marker (e.g. "## 定義")
_H2_RE = re.compile(r"^## (.+)$", re.MULTILINE)

# Wikilink capture (e.g. [[Concepts/overtraining]])
_WIKILINK_RE = re.compile(r"\[\[([^\[\]]+)\]\]")

# H2 sections that are structural boilerplate and not useful for retrieval
_SKIP_SECTIONS = frozenset(
    {"Related", "See Also", "References", "延伸閱讀", "參考資料", "See also"}
)

_MIN_CHUNK_CHARS = 30

# Wiki subdirectories scanned recursively for H2-chunked content.
# ADR-021 §2: do NOT scan KB/Wiki/Syntheses (path doesn't exist). Annotations
# are handled via the dedicated `_index_annotations` path, not as a Wiki subdir.
_KB_SUBDIRS = frozenset({"Sources", "Concepts", "Entities"})


def _normalize_wikilink(raw: str) -> str | None:
    """Convert a raw wikilink target to a canonical KB path, or None if not KB.

    Examples:
      "Concepts/overtraining"    → "KB/Wiki/Concepts/overtraining"
      "Sources/paper-name"       → "KB/Wiki/Sources/paper-name"
      "KB/Wiki/Entities/foo"     → "KB/Wiki/Entities/foo"  (already canonical)
      "external-note"            → None  (not a KB wiki path)
    """
    # Strip display alias: [[Target|Display]] → "Target"
    raw = raw.split("|")[0].strip()
    if raw.startswith("KB/Wiki/"):
        return raw
    # Match Concepts/X, Sources/X, Entities/X
    for subdir in _KB_SUBDIRS:
        if raw.startswith(f"{subdir}/"):
            return f"KB/Wiki/{raw}"
    return None


@dataclass
class IndexStats:
    files_indexed: int = 0
    files_skipped: int = 0  # mtime_ns unchanged — fast path
    chunks_added: int = 0
    chunks_removed: int = 0
    wikilinks: list[str] = field(default_factory=list)


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _split_h2_chunks(body: str, page_title: str, page_path: str) -> list[dict]:
    """Split a markdown page body into H2-level chunks.

    Returns a list of dicts:
      chunk_text     — text of this section
      section        — H2 heading text (empty string for preamble)
      heading_context — page title (constant across all chunks for this page)
      path            — page path
    """
    chunks: list[dict] = []
    parts = _H2_RE.split(body)
    # _H2_RE.split produces: [before_h2, h2_text, after_h2, h2_text, after_h2, ...]

    # Preamble: text before the first ##
    preamble = parts[0].strip()
    if len(preamble) >= _MIN_CHUNK_CHARS:
        chunks.append(
            {
                "chunk_text": preamble,
                "section": "",
                "heading_context": page_title,
                "path": page_path,
            }
        )

    # H2 sections (pairs of heading + body)
    i = 1
    while i + 1 < len(parts):
        heading = parts[i].strip()
        section_body = parts[i + 1].strip()
        i += 2

        if heading in _SKIP_SECTIONS:
            continue
        if len(section_body) < _MIN_CHUNK_CHARS:
            continue

        chunks.append(
            {
                "chunk_text": section_body,
                "section": heading,
                "heading_context": page_title,
                "path": page_path,
            }
        )

    return chunks


def index_vault(vault_path: Path, db: sqlite3.Connection) -> IndexStats:
    """Scan KB/Wiki vault and write chunks + embeddings into `db`.

    Args:
        vault_path: Obsidian vault root (KB/Wiki/{Sources,Concepts,Entities} live here).
        db:         SQLite connection with sqlite-vec loaded and kb_* tables initialized.
                    Obtain via ``shared.kb_hybrid_search.make_conn()`` or
                    ``shared.kb_hybrid_search.get_kb_conn()``.

    Returns:
        IndexStats with files_indexed / files_skipped / chunks_added / chunks_removed.
    """
    stats = IndexStats()
    wiki_path = vault_path / "KB" / "Wiki"
    now = datetime.now(timezone.utc).isoformat()

    # ADR-021 §2 + Codex amendment: recursive `rglob("*.md")` so nested files
    # like `KB/Wiki/Sources/Books/{book_id}/notes.md` get indexed too.
    for subdir in sorted(_KB_SUBDIRS):
        dir_path = wiki_path / subdir
        if not dir_path.exists():
            continue

        for md_file in sorted(dir_path.rglob("*.md")):
            # Build canonical page_path from path-relative-to-Wiki, strip ".md".
            # e.g. KB/Wiki/Sources/Books/atomic-habits/notes
            rel = md_file.relative_to(wiki_path).with_suffix("").as_posix()
            page_path = f"KB/Wiki/{rel}"
            mtime_ns = md_file.stat().st_mtime_ns

            # Incremental shortcut: skip if mtime_ns unchanged
            meta_row = db.execute(
                "SELECT mtime_ns FROM kb_index_meta WHERE path = ?",
                (page_path,),
            ).fetchone()
            if meta_row is not None and meta_row[0] == mtime_ns:
                stats.files_skipped += 1
                continue

            try:
                content = md_file.read_text(encoding="utf-8")
            except Exception:
                continue

            fhash = _file_hash(md_file)
            fm, body = extract_frontmatter(content)
            page_title: str = fm.get("title") or md_file.stem

            # Collect wikilinks and persist to kb_wikilinks
            raw_wikilinks = _WIKILINK_RE.findall(content)
            for wl in raw_wikilinks:
                stats.wikilinks.append(wl)

            # Remove stale wikilinks and chunks for this page
            db.execute("DELETE FROM kb_wikilinks WHERE src_path = ?", (page_path,))
            for raw_wl in raw_wikilinks:
                dst = _normalize_wikilink(raw_wl)
                if dst and dst != page_path:
                    db.execute(
                        "INSERT INTO kb_wikilinks(src_path, dst_path) VALUES (?, ?)",
                        (page_path, dst),
                    )

            # Remove stale chunks for this page
            old_rowids: list[int] = [
                r[0]
                for r in db.execute(
                    "SELECT rowid FROM kb_chunks WHERE path = ?",
                    (page_path,),
                ).fetchall()
            ]
            if old_rowids:
                placeholders = ",".join("?" * len(old_rowids))
                db.execute(
                    f"DELETE FROM kb_vectors WHERE rowid IN ({placeholders})",
                    old_rowids,
                )
                db.execute("DELETE FROM kb_chunks WHERE path = ?", (page_path,))
                stats.chunks_removed += len(old_rowids)

            # Chunk + embed + insert
            chunks = _split_h2_chunks(body, page_title, page_path)
            if chunks:
                embeddings = kb_embedder.embed_batch([c["chunk_text"] for c in chunks])
                for chunk, emb in zip(chunks, embeddings):
                    cur = db.execute(
                        "INSERT INTO kb_chunks (chunk_text, section, heading_context, path) "
                        "VALUES (?, ?, ?, ?)",
                        (
                            chunk["chunk_text"],
                            chunk["section"],
                            chunk["heading_context"],
                            chunk["path"],
                        ),
                    )
                    rowid: int = cur.lastrowid  # type: ignore[assignment]
                    db.execute(
                        "INSERT INTO kb_vectors(rowid, embedding) VALUES (?, ?)",
                        (rowid, emb.tobytes()),
                    )
                    stats.chunks_added += 1

            # Update incremental-index bookmark
            db.execute(
                """INSERT OR REPLACE INTO kb_index_meta (path, mtime_ns, file_hash, indexed_at)
                   VALUES (?, ?, ?, ?)""",
                (page_path, mtime_ns, fhash, now),
            )
            stats.files_indexed += 1

        db.commit()

    _index_annotations(vault_path, db, stats, now)
    db.commit()

    return stats


def _annotation_chunks(ann_set: AnnotationSetV3, page_path: str) -> list[dict]:
    """Convert v3 annotation items into chunk dicts.

    ADR-021 §2 chunk shape:
      - HighlightV3   → text  (the highlighted content)
      - AnnotationV3  → note  (user's short note tied to a span)
      - ReflectionV3  → body  (chapter-level long-form reflection)

    Metadata is folded into the existing 4-column FTS schema:
      - chunk_text:     the body text above
      - section:        item_type (with chapter_ref appended if present)
      - heading_context: source slug — falls back to source_filename / book_id / slug
      - path:           KB/Annotations/{slug}  (multiple chunks per page is fine,
                        same as H2 chunks per Wiki page)
    """
    # Source identifier — ADR-021 §2 says "source slug from AnnotationSet.source_filename".
    # v3 sets carry either source_filename (paper) or book_id (book); fall back to slug.
    source_slug = ann_set.source_filename or ann_set.book_id or ann_set.slug

    chunks: list[dict] = []
    for item in ann_set.items:
        if isinstance(item, HighlightV3):
            text = item.text
            chapter_ref: str | None = None
        elif isinstance(item, AnnotationV3):
            text = item.note
            chapter_ref = None
        elif isinstance(item, ReflectionV3):
            text = item.body
            chapter_ref = item.chapter_ref
        else:  # pragma: no cover — discriminated union exhausts above
            continue

        text = (text or "").strip()
        if not text:
            continue

        # Pack item_type + optional chapter_ref into `section`. Down-stream consumers
        # parse on the "|" separator if they need the chapter.
        section = item.type if not chapter_ref else f"{item.type}|{chapter_ref}"

        chunks.append(
            {
                "chunk_text": text,
                "section": section,
                "heading_context": source_slug,
                "path": page_path,
            }
        )
    return chunks


def _index_annotations(
    vault_path: Path,
    db: sqlite3.Connection,
    stats: IndexStats,
    now: str,
) -> None:
    """Scan KB/Annotations/*.md and index each item as one chunk (ADR-021 §2).

    Reuses ``shared.annotation_store._parse`` (via ``upgrade_to_v3``) to read v1/v2/v3
    transparently, so we never re-implement JSON parsing here.
    """
    # Local import to avoid a module-load-time circular: annotation_store imports
    # shared.config (vault path), and we don't want kb_indexer importing config either.
    from shared.annotation_store import _parse  # noqa: PLC0415

    annotations_dir = vault_path / "KB" / "Annotations"
    if not annotations_dir.exists():
        return

    for md_file in sorted(annotations_dir.glob("*.md")):
        slug = md_file.stem
        page_path = f"KB/Annotations/{slug}"
        mtime_ns = md_file.stat().st_mtime_ns

        # Incremental shortcut — same shape as the Wiki loop.
        meta_row = db.execute(
            "SELECT mtime_ns FROM kb_index_meta WHERE path = ?",
            (page_path,),
        ).fetchone()
        if meta_row is not None and meta_row[0] == mtime_ns:
            stats.files_skipped += 1
            continue

        try:
            content = md_file.read_text(encoding="utf-8")
        except Exception:
            continue

        try:
            ann_set = upgrade_to_v3(_parse(content, slug))
        except Exception:
            # Malformed JSON / unknown shape — skip rather than crash full index.
            continue

        fhash = _file_hash(md_file)

        # Remove stale chunks for this page (no wikilinks tracked for annotation pages).
        old_rowids: list[int] = [
            r[0]
            for r in db.execute(
                "SELECT rowid FROM kb_chunks WHERE path = ?",
                (page_path,),
            ).fetchall()
        ]
        if old_rowids:
            placeholders = ",".join("?" * len(old_rowids))
            db.execute(
                f"DELETE FROM kb_vectors WHERE rowid IN ({placeholders})",
                old_rowids,
            )
            db.execute("DELETE FROM kb_chunks WHERE path = ?", (page_path,))
            stats.chunks_removed += len(old_rowids)

        chunks = _annotation_chunks(ann_set, page_path)
        if chunks:
            embeddings = kb_embedder.embed_batch([c["chunk_text"] for c in chunks])
            for chunk, emb in zip(chunks, embeddings):
                cur = db.execute(
                    "INSERT INTO kb_chunks (chunk_text, section, heading_context, path) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        chunk["chunk_text"],
                        chunk["section"],
                        chunk["heading_context"],
                        chunk["path"],
                    ),
                )
                rowid: int = cur.lastrowid  # type: ignore[assignment]
                db.execute(
                    "INSERT INTO kb_vectors(rowid, embedding) VALUES (?, ?)",
                    (rowid, emb.tobytes()),
                )
                stats.chunks_added += 1

        db.execute(
            """INSERT OR REPLACE INTO kb_index_meta (path, mtime_ns, file_hash, indexed_at)
               VALUES (?, ?, ?, ?)""",
            (page_path, mtime_ns, fhash, now),
        )
        stats.files_indexed += 1


# ---------------------------------------------------------------------------
# Rebuild: drop kb_vectors + kb_chunks + kb_index_meta, recreate at current
# embedder dim, full re-embed. ADR-022 — needed when embedder dim changes
# (e.g. potion 256 → bge-m3 1024).
# ---------------------------------------------------------------------------


def rebuild_index(vault_path: Path, db: sqlite3.Connection) -> IndexStats:
    """Drop & recreate kb_vectors at the current embedder dim, full re-embed.

    Wipes kb_chunks / kb_vectors / kb_index_meta / kb_wikilinks rows so the
    follow-up ``index_vault`` walks every page from scratch. The vec0 vtab
    is dropped + recreated at ``kb_embedder.current_dim()``.
    """
    # FTS5 + vec0 tables can't be ALTERed — drop + recreate.
    db.execute("DROP TABLE IF EXISTS kb_vectors")
    db.execute("DELETE FROM kb_chunks")
    db.execute("DELETE FROM kb_index_meta")
    db.execute("DELETE FROM kb_wikilinks")
    target_dim = kb_embedder.current_dim()
    db.execute(f"CREATE VIRTUAL TABLE kb_vectors USING vec0(embedding float[{target_dim}])")
    db.commit()
    return index_vault(vault_path, db)


def _resolve_vault_path() -> Path:
    """Resolve the vault root via the project's canonical config helper.

    Delegates to ``shared.config.get_vault_path()``, which already honors the
    ``VAULT_PATH`` env var ahead of ``config.yaml``. Falls back to ``<repo>/vault``
    only if config loading fails (e.g. running outside the repo).
    """
    try:
        from shared.config import get_vault_path  # noqa: PLC0415

        return get_vault_path()
    except Exception:
        import os as _os  # noqa: PLC0415

        override = _os.environ.get("VAULT_PATH")
        if override:
            return Path(override)
        return Path(__file__).resolve().parent.parent / "vault"


def _main() -> None:
    import argparse  # noqa: PLC0415

    from shared.kb_hybrid_search import get_kb_conn  # noqa: PLC0415

    parser = argparse.ArgumentParser(
        prog="python -m shared.kb_indexer",
        description="KB vault indexer (incremental by default; --rebuild for full re-embed).",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help=(
            "Drop kb_vectors + clear kb_chunks/kb_index_meta/kb_wikilinks, "
            "recreate vec0 at the current embedder dim, full re-embed."
        ),
    )
    parser.add_argument(
        "--vault",
        type=Path,
        default=None,
        help="Vault root (defaults to VAULT_PATH env / config.yaml vault_path / <repo>/vault).",
    )
    args = parser.parse_args()

    vault = args.vault if args.vault is not None else _resolve_vault_path()
    if not vault.exists():
        raise SystemExit(f"Vault path does not exist: {vault}")

    # ADR-022: skip dim assertion on --rebuild — that's the path that fixes
    # the very mismatch the assertion would refuse to open the conn for.
    conn = get_kb_conn(check_dim=not args.rebuild)
    if args.rebuild:
        print(
            f"[rebuild] backend={kb_embedder.current_backend()} "
            f"dim={kb_embedder.current_dim()} vault={vault}"
        )
        stats = rebuild_index(vault, conn)
    else:
        stats = index_vault(vault, conn)
    print(
        f"files_indexed={stats.files_indexed} files_skipped={stats.files_skipped} "
        f"chunks_added={stats.chunks_added} chunks_removed={stats.chunks_removed}"
    )


if __name__ == "__main__":
    _main()
