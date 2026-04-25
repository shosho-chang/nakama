"""SQLite FTS5 index over docs/ + memory/ markdown files.

Powers `/bridge/docs` search (Phase 9). Lives in its own SQLite file so it
doesn't pollute `state.db` and can be rebuilt cheaply (full reindex of ~700
markdown files takes <1 second).

Schema:

    CREATE VIRTUAL TABLE docs_fts USING fts5(
        path,           -- relative path from repo root (e.g. "docs/runbooks/deploy-usopp-vps.md")
        title,          -- markdown H1 / frontmatter `name` if present
        body,           -- full file text minus frontmatter
        category,       -- "docs" / "memory" / "decisions" / "runbooks" — from path
        tokenize='porter unicode61'
    );

Tokenizer notes:
- `unicode61` handles Chinese characters as individual tokens (1-char tokens
  match well for CJK content). Not as good as a CJK-aware tokenizer (jieba)
  but built-in and zero-dep.
- `porter` stemming for English ("running" → "run") so EN searches generalize.

Usage:
    from shared.doc_index import DocIndex

    idx = DocIndex.from_repo_root(Path("/Users/shosho/Documents/nakama"))
    idx.rebuild()
    hits = idx.search("R2 backup", limit=10)
    for h in hits:
        print(h.path, h.snippet)
"""

from __future__ import annotations

import html
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from shared.log import get_logger

logger = get_logger("nakama.doc_index")

# Sentinel tokens for FTS5 snippet() — replaced with safe `<mark>`/`</mark>`
# AFTER html.escape() runs over the body. Any literal `<script>` (or other HTML)
# inside an indexed markdown file would otherwise reach the template raw via
# `{{ hit.snippet | safe }}` and execute. ASCII control chars 0x01/0x02 are
# illegal in markdown source, so they can never collide with file content.
_MARK_OPEN = "\x01"
_MARK_CLOSE = "\x02"

# Subdirectories of repo root to index. Reading any other path is intentional
# noise — agents/ source code, scripts/, etc. live elsewhere and don't belong
# in a doc-search index.
_INDEXED_ROOTS = ("docs", "memory/claude")

# Excluded subpaths (archive / generated / templates that aren't useful in search)
_EXCLUDED_DIRS = ("_archive",)

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", flags=re.DOTALL)
_FRONTMATTER_NAME_RE = re.compile(r"^name:\s*(.+?)\s*$", flags=re.MULTILINE)
_H1_RE = re.compile(r"^#\s+(.+?)\s*$", flags=re.MULTILINE)


@dataclass(frozen=True)
class DocHit:
    """One ranked search result.

    `snippet` is safe HTML — already html.escape()'d, with only `<mark>` /
    `</mark>` tags re-introduced. Templates can render with `| safe`.
    """

    path: str
    title: str
    category: str
    snippet: str


def _safe_snippet(raw: str) -> str:
    """Escape FTS5 snippet body, then swap sentinels for `<mark>` tags."""
    return html.escape(raw).replace(_MARK_OPEN, "<mark>").replace(_MARK_CLOSE, "</mark>")


class DocIndex:
    """SQLite FTS5 search index. Re-buildable; no incremental updates yet."""

    def __init__(self, *, repo_root: Path, db_path: Path) -> None:
        self._repo_root = repo_root
        self._db_path = db_path
        # Lazy-init: only open + create schema on first use, so importing this
        # module in tests doesn't create stray DBs in cwd.
        self._conn: sqlite3.Connection | None = None

    @classmethod
    def from_repo_root(cls, repo_root: Path | None = None) -> DocIndex:
        """Default constructor: index lives at `<data_dir>/doc_index.db`.

        Path resolution (first match wins):
          1. `NAKAMA_DOC_INDEX_DB_PATH` env override (full path) — for tests
          2. `NAKAMA_DATA_DIR` env (data dir, file appended) — VPS sets this
          3. `<repo_root>/data/doc_index.db` — local dev fallback
        """
        repo_root = repo_root or Path(__file__).resolve().parent.parent
        override = os.environ.get("NAKAMA_DOC_INDEX_DB_PATH")
        if override:
            return cls(repo_root=repo_root, db_path=Path(override))
        data_dir_env = os.environ.get("NAKAMA_DATA_DIR")
        if data_dir_env:
            return cls(repo_root=repo_root, db_path=Path(data_dir_env) / "doc_index.db")
        return cls(repo_root=repo_root, db_path=repo_root / "data" / "doc_index.db")

    # ---- connection / schema --------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self._init_schema(conn)
            self._conn = conn
        return self._conn

    @staticmethod
    def _init_schema(conn: sqlite3.Connection) -> None:
        try:
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
                    path UNINDEXED,
                    title,
                    body,
                    category UNINDEXED,
                    tokenize='porter unicode61'
                )
                """
            )
            conn.commit()
        except sqlite3.OperationalError as exc:
            # Older SQLite without FTS5 — surface a clean error.
            raise RuntimeError(f"FTS5 not available in this sqlite build: {exc}") from exc

    # ---- file walk + parse ---------------------------------------------------

    def _walk_markdown(self) -> list[Path]:
        """Yield every .md file under indexed roots (skipping excluded dirs)."""
        out: list[Path] = []
        for sub in _INDEXED_ROOTS:
            root = self._repo_root / sub
            if not root.is_dir():
                continue
            for p in root.rglob("*.md"):
                rel_parts = p.relative_to(self._repo_root).parts
                if any(part in _EXCLUDED_DIRS for part in rel_parts):
                    continue
                out.append(p)
        return out

    @staticmethod
    def _extract_title(text: str, fallback: str) -> str:
        """Prefer frontmatter `name:`, then first `# Heading`, then filename."""
        fm_match = _FRONTMATTER_RE.match(text)
        if fm_match:
            name_match = _FRONTMATTER_NAME_RE.search(fm_match.group(1))
            if name_match:
                return name_match.group(1).strip()
        body_after_fm = _FRONTMATTER_RE.sub("", text, count=1) if fm_match else text
        h1_match = _H1_RE.search(body_after_fm)
        if h1_match:
            return h1_match.group(1).strip()
        return fallback

    @staticmethod
    def _strip_frontmatter(text: str) -> str:
        return _FRONTMATTER_RE.sub("", text, count=1)

    @staticmethod
    def _category_for(rel_path: str) -> str:
        """Bucket by top-level subdir. Used for filter dropdown later."""
        parts = rel_path.split("/")
        if parts[0] == "memory":
            return "memory"
        if len(parts) >= 2 and parts[0] == "docs":
            return parts[1]  # "runbooks" / "decisions" / "plans" / "research" / etc.
        return "other"

    # ---- public API ---------------------------------------------------------

    def rebuild(self) -> int:
        """Wipe + repopulate the index. Returns count of indexed files."""
        conn = self._get_conn()
        conn.execute("DELETE FROM docs_fts")
        files = self._walk_markdown()
        for p in files:
            try:
                text = p.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                logger.warning("skip non-utf8 file path=%s err=%s", p, exc)
                continue
            rel = str(p.relative_to(self._repo_root))
            title = self._extract_title(text, fallback=p.stem)
            body = self._strip_frontmatter(text)
            category = self._category_for(rel)
            conn.execute(
                "INSERT INTO docs_fts (path, title, body, category) VALUES (?, ?, ?, ?)",
                (rel, title, body, category),
            )
        conn.commit()
        logger.info("doc index rebuilt files=%d", len(files))
        return len(files)

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
        category: str | None = None,
    ) -> list[DocHit]:
        """FTS5 search; returns ranked DocHits with snippet (50-char context).

        Empty / blank query returns []. Caller should reject before calling.
        """
        if not query.strip():
            return []
        conn = self._get_conn()
        # snippet() emits sentinels (not literal `<mark>`); we html.escape the
        # whole string then swap sentinels back. Otherwise `<script>` literals
        # inside markdown leak to the template raw via `| safe`.
        sql = (
            "SELECT path, title, category, "
            f"       snippet(docs_fts, 2, '{_MARK_OPEN}', '{_MARK_CLOSE}', ' … ', 15) AS snippet "
            "FROM docs_fts WHERE docs_fts MATCH ?"
        )
        params: list = [query]
        if category:
            sql += " AND category = ?"
            params.append(category)
        sql += " ORDER BY bm25(docs_fts) LIMIT ?"
        params.append(limit)

        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as exc:
            # Bad FTS5 query syntax — surface to caller for nicer error message
            logger.warning("fts5 query syntax err query=%r: %s", query, exc)
            return []
        return [
            DocHit(
                path=row["path"],
                title=row["title"],
                category=row["category"],
                snippet=_safe_snippet(row["snippet"]),
            )
            for row in rows
        ]

    def stats(self) -> dict[str, int]:
        """Return {category: count} for the indexed corpus. Used by /bridge/docs to
        show "X docs / Y memory / ..." breakdown next to the search box."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT category, COUNT(*) AS n FROM docs_fts GROUP BY category"
        ).fetchall()
        return {row["category"]: row["n"] for row in rows}

    def close(self) -> None:
        """Optional explicit close — caller's responsibility in long-running procs."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
