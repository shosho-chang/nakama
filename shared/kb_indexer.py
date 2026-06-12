"""KB vault 索引器 — vault walker → H2 chunker → FTS5 writer.

ADR-042: dense-vector (vec0 / embeddings) writes removed — FTS5 BM25 is the
only retrieval lane now. The indexer writes `kb_chunks` (FTS5) + `kb_wikilinks`
+ `kb_index_meta` only.

`index_vault(vault_path, db)` 接受已初始化的 SQLite connection
（kb_* tables 已存在），
掃 KB/Wiki/{Sources,Concepts,Entities}（recursive，含 nested Books/{book_id}/）
+ KB/Annotations/ + KB/Permanent/，按 H2 切 chunk，mtime_ns 增量跳過未改檔案。

Centaur N520：KB/Permanent/（人寫永久卡）走獨立 `_index_permanent` 路徑——卡片
正文進 FTS5（可被檢索、排序置頂），typed edges（支持/反駁/延伸）進結構化
`kb_typed_edges` 表（directed graph，不靠 CJK text tokenization）。

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

from shared.annotation_store import upgrade_to_v3
from shared.log import get_logger
from shared.schemas.annotations import (
    AnnotationSetV3,
    AnnotationV3,
    HighlightV3,
    ReflectionV3,
)
from shared.utils import extract_frontmatter

logger = get_logger("nakama.shared.kb_indexer")

# H2 heading marker (e.g. "## 定義")
_H2_RE = re.compile(r"^## (.+)$", re.MULTILINE)

# Wikilink capture (e.g. [[Concepts/overtraining]])
_WIKILINK_RE = re.compile(r"\[\[([^\[\]]+)\]\]")

# Centaur N520 — typed edges in KB/Permanent/ card bodies (Dataview inline field).
# Format (v0.2 §3):  支持:: [[卡片標題]] — 理由
# Direction is always 本卡 → 對方 (v0.2 §3「方向定死」); reverse comes free from
# kb_typed_edges(dst_path) / Obsidian backlinks, so we never mirror-write.
_TYPED_EDGE_RE = re.compile(
    r"^(支持|反駁|延伸)::\s*\[\[([^\[\]]+)\]\]\s*(?:[—–-]\s*(.*?))?\s*$",
    re.MULTILINE,
)
_EDGE_TYPE_MAP = {"支持": "support", "反駁": "refute", "延伸": "extend"}

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
    annotation_conflicts: int = 0  # Syncthing *.sync-conflict-* files seen (ADR-044 §B8)
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
    """Scan KB/Wiki vault and write FTS5 chunks into `db` (ADR-042: no embeddings).

    Args:
        vault_path: Obsidian vault root (KB/Wiki/{Sources,Concepts,Entities} live here).
        db:         SQLite connection with kb_* tables initialized.
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
                db.execute("DELETE FROM kb_chunks WHERE path = ?", (page_path,))
                stats.chunks_removed += len(old_rowids)

            # Chunk + insert (FTS5 only; ADR-042 removed the dense-vec lane)
            chunks = _split_h2_chunks(body, page_title, page_path)
            for chunk in chunks:
                db.execute(
                    "INSERT INTO kb_chunks (chunk_text, section, heading_context, path) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        chunk["chunk_text"],
                        chunk["section"],
                        chunk["heading_context"],
                        chunk["path"],
                    ),
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

    _index_permanent(vault_path, db, stats, now)
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
    from shared.annotation_store import (  # noqa: PLC0415
        ANNOTATION_SYNC_CONFLICT_RE,
        _parse,
    )

    annotations_dir = vault_path / "KB" / "Annotations"
    if not annotations_dir.exists():
        return

    for md_file in sorted(annotations_dir.glob("*.md")):
        # Syncthing conflict copies are reported, not indexed as real annotation
        # files (ADR-044 §B8) — their stem would otherwise become a junk slug.
        if ANNOTATION_SYNC_CONFLICT_RE.match(md_file.name):
            stats.annotation_conflicts += 1
            logger.warning(
                "annotation sync-conflict file detected — needs manual merge: %s",
                md_file.name,
            )
            continue

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
            db.execute("DELETE FROM kb_chunks WHERE path = ?", (page_path,))
            stats.chunks_removed += len(old_rowids)

        chunks = _annotation_chunks(ann_set, page_path)
        for chunk in chunks:
            db.execute(
                "INSERT INTO kb_chunks (chunk_text, section, heading_context, path) "
                "VALUES (?, ?, ?, ?)",
                (
                    chunk["chunk_text"],
                    chunk["section"],
                    chunk["heading_context"],
                    chunk["path"],
                ),
            )
            stats.chunks_added += 1

        db.execute(
            """INSERT OR REPLACE INTO kb_index_meta (path, mtime_ns, file_hash, indexed_at)
               VALUES (?, ?, ?, ?)""",
            (page_path, mtime_ns, fhash, now),
        )
        stats.files_indexed += 1


def _normalize_permanent_link(raw: str) -> str:
    """Normalize a typed-edge target wikilink to a canonical KB path.

    Permanent cards mostly link to other Permanent cards by title (filename =
    declaration sentence), e.g. ``[[好系統讓你不需要意志力]]`` →
    ``KB/Permanent/好系統讓你不需要意志力``. A target that already carries a Wiki
    subdir prefix (``Concepts/X``) or ``KB/`` is normalized via the Wiki rule.
    """
    raw = raw.split("|")[0].strip()
    wiki = _normalize_wikilink(raw)
    if wiki is not None:
        return wiki
    if raw.startswith("KB/"):
        return raw
    # Bare title → Permanent sibling card
    return f"KB/Permanent/{raw}"


def _index_permanent(
    vault_path: Path,
    db: sqlite3.Connection,
    stats: IndexStats,
    now: str,
) -> None:
    """Scan KB/Permanent/*.md — FTS-index card bodies + extract typed edges.

    Two complementary writes per card (Centaur N520):
      1. card body → ``kb_chunks`` (FTS5), so永久卡 are keyword-searchable and can
         be ranked first by ``kb_hybrid_search.search`` (handoff fork 2).
      2. ``支持::`` / ``反駁::`` / ``延伸::`` lines → ``kb_typed_edges`` (structured),
         so directed-graph queries don't depend on CJK text tokenization
         (panel Gemini §2).

    KB/Permanent/ is NOT a KB/Wiki/ subdir, so this is a dedicated path (same
    shape as ``_index_annotations``), never added to ``_KB_SUBDIRS``.
    """
    permanent_dir = vault_path / "KB" / "Permanent"
    if not permanent_dir.exists():
        return

    for md_file in sorted(permanent_dir.rglob("*.md")):
        rel = md_file.relative_to(vault_path).with_suffix("").as_posix()
        page_path = rel  # already "KB/Permanent/..."
        mtime_ns = md_file.stat().st_mtime_ns

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

        # Refresh structured typed edges for this card
        db.execute("DELETE FROM kb_typed_edges WHERE src_path = ?", (page_path,))
        for m in _TYPED_EDGE_RE.finditer(body):
            edge_type = _EDGE_TYPE_MAP[m.group(1)]
            dst = _normalize_permanent_link(m.group(2))
            reason = (m.group(3) or "").strip() or None
            db.execute(
                "INSERT INTO kb_typed_edges(src_path, edge_type, dst_path, reason) "
                "VALUES (?, ?, ?, ?)",
                (page_path, edge_type, dst, reason),
            )

        # Refresh FTS chunks for this card (cards are short — usually 1 preamble chunk)
        old_rowids: list[int] = [
            r[0]
            for r in db.execute(
                "SELECT rowid FROM kb_chunks WHERE path = ?",
                (page_path,),
            ).fetchall()
        ]
        if old_rowids:
            db.execute("DELETE FROM kb_chunks WHERE path = ?", (page_path,))
            stats.chunks_removed += len(old_rowids)

        chunks = _split_h2_chunks(body, page_title, page_path)
        for chunk in chunks:
            db.execute(
                "INSERT INTO kb_chunks (chunk_text, section, heading_context, path) "
                "VALUES (?, ?, ?, ?)",
                (
                    chunk["chunk_text"],
                    chunk["section"],
                    chunk["heading_context"],
                    chunk["path"],
                ),
            )
            stats.chunks_added += 1

        db.execute(
            """INSERT OR REPLACE INTO kb_index_meta (path, mtime_ns, file_hash, indexed_at)
               VALUES (?, ?, ?, ?)""",
            (page_path, mtime_ns, fhash, now),
        )
        stats.files_indexed += 1


# ---------------------------------------------------------------------------
# Rebuild: wipe kb_chunks + kb_index_meta + kb_wikilinks and re-walk from
# scratch. ADR-042 — also drops the legacy kb_vectors vtab if a pre-removal
# DB still carries it (the dense-vec lane is gone; the table is never recreated).
# ---------------------------------------------------------------------------


def rebuild_index(vault_path: Path, db: sqlite3.Connection) -> IndexStats:
    """Wipe FTS5 chunks + bookkeeping and re-walk every page from scratch.

    Clears kb_chunks / kb_index_meta / kb_wikilinks so the follow-up
    ``index_vault`` reindexes everything. Drops the legacy ``kb_vectors`` vtab
    if present (ADR-042) — it is no longer recreated.
    """
    db.execute("DROP TABLE IF EXISTS kb_vectors")  # legacy dense-vec lane (ADR-042)
    db.execute("DELETE FROM kb_chunks")
    db.execute("DELETE FROM kb_index_meta")
    db.execute("DELETE FROM kb_wikilinks")
    db.execute("DELETE FROM kb_typed_edges")  # Centaur N520 typed edges
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
            "Clear kb_chunks/kb_index_meta/kb_wikilinks (and drop any legacy "
            "kb_vectors vtab), then re-walk every page from scratch."
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

    conn = get_kb_conn()
    if args.rebuild:
        print(f"[rebuild] FTS5-only (ADR-042) vault={vault}")
        stats = rebuild_index(vault, conn)
    else:
        stats = index_vault(vault, conn)
    print(
        f"files_indexed={stats.files_indexed} files_skipped={stats.files_skipped} "
        f"chunks_added={stats.chunks_added} chunks_removed={stats.chunks_removed} "
        f"annotation_conflicts={stats.annotation_conflicts}"
    )


if __name__ == "__main__":
    _main()
