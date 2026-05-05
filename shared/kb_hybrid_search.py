"""KB hybrid retrieval — BM25 + dense-vec + RRF k=60 fusion.

DB schema (canonical reference: migrations/012_kb_hybrid.sql):
  kb_chunks   — FTS5(chunk_text, section, heading_context, path UNINDEXED)
  kb_vectors  — vec0(embedding float[256])
  kb_index_meta — (path, mtime_ns, file_hash, indexed_at)

The index DB lives in kb_index.db (separate from state.db) because sqlite-vec
must be loaded as a SQLite extension.  The module-level `get_kb_conn()` manages
a single lazy-opened connection.  Tests inject their own in-memory connection
via the `db=` parameter in `search()`.

Path resolution for kb_index.db (first match wins):
  1. NAKAMA_KB_INDEX_DB_PATH env override (full path) — for tests / CI
  2. NAKAMA_DATA_DIR env (data dir, file appended) — VPS sets this
  3. <repo_root>/data/kb_index.db — local dev fallback
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import sqlite_vec

from shared import kb_embedder

_RRF_K = 60
_CANDIDATES_PER_LANE = 30
_TOKEN_BUDGET_CHARS = 2000  # ~512 tokens at ~4 chars/token

_conn: sqlite3.Connection | None = None


# ---------------------------------------------------------------------------
# DB connection + schema
# ---------------------------------------------------------------------------


def _get_kb_db_path() -> Path:
    override = os.environ.get("NAKAMA_KB_INDEX_DB_PATH")
    if override:
        return Path(override)
    data_dir_env = os.environ.get("NAKAMA_DATA_DIR")
    if data_dir_env:
        return Path(data_dir_env) / "kb_index.db"
    return Path(__file__).resolve().parent.parent / "data" / "kb_index.db"


def _open_conn(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    """Create kb_* tables if they don't exist yet."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kb_index_meta (
            path       TEXT PRIMARY KEY,
            mtime_ns   INTEGER NOT NULL,
            file_hash  TEXT    NOT NULL,
            indexed_at TEXT    NOT NULL
        )
    """)
    # FTS5 virtual tables don't support IF NOT EXISTS — use try/except
    try:
        conn.execute("""
            CREATE VIRTUAL TABLE kb_chunks USING fts5(
                chunk_text,
                section,
                heading_context,
                path UNINDEXED,
                tokenize='porter unicode61'
            )
        """)
    except sqlite3.OperationalError:
        pass  # already exists
    try:
        conn.execute("CREATE VIRTUAL TABLE kb_vectors USING vec0(embedding float[256])")
    except sqlite3.OperationalError:
        pass  # already exists
    conn.commit()


def get_kb_conn() -> sqlite3.Connection:
    """Return the module-level kb_index DB connection (lazy-opened)."""
    global _conn
    if _conn is None:
        _conn = _open_conn(_get_kb_db_path())
    return _conn


def make_conn(db_path: str | Path = ":memory:") -> sqlite3.Connection:
    """Create and initialize a fresh connection — for tests or CLI use.

    Passing ":memory:" creates an in-memory DB (no file, lost on close).
    """
    if str(db_path) == ":memory:":
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        _init_schema(conn)
        return conn
    return _open_conn(Path(db_path))


# ---------------------------------------------------------------------------
# Search result schema
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SearchHit:
    chunk_id: int  # FTS5 rowid (same as kb_vectors rowid)
    path: str  # e.g. "KB/Wiki/Concepts/overtraining"
    heading: str  # H2 section heading; empty string for preamble chunks
    page_title: str  # page title from frontmatter (heading_context column)
    chunk_text: str  # body, truncated to TOKEN_BUDGET_CHARS
    rrf_score: float
    lane_ranks: dict  # e.g. {"bm25": 1, "vec": 3}


# ---------------------------------------------------------------------------
# Core search
# ---------------------------------------------------------------------------


def search(
    query: str,
    top_k: int = 10,
    *,
    lanes: tuple[str, ...] = ("bm25", "vec"),
    db: sqlite3.Connection | None = None,
) -> list[SearchHit]:
    """BM25 + dense-vec dual-lane RRF-k=60 hybrid search.

    Args:
        query:  free-text query (supports both Latin and CJK text).
        top_k:  maximum number of results to return.
        lanes:  active retrieval lanes; subset of ("bm25", "vec").
                Pass lanes=("bm25",) or lanes=("vec",) to run a single lane.
        db:     connection override for tests; uses module-level conn if None.

    Returns:
        List of SearchHit sorted by RRF score descending (best first).
    """
    conn = db if db is not None else get_kb_conn()
    candidates: dict[int, dict[str, int]] = {}  # rowid → {lane: rank}

    if "bm25" in lanes:
        try:
            rows = conn.execute(
                """SELECT rowid FROM kb_chunks
                   WHERE kb_chunks MATCH ?
                   ORDER BY bm25(kb_chunks, 1.0, 0.5, 0.3)
                   LIMIT ?""",
                (query, _CANDIDATES_PER_LANE),
            ).fetchall()
        except sqlite3.OperationalError:
            # Bad FTS5 query syntax or empty index
            rows = []
        for rank, row in enumerate(rows):
            candidates.setdefault(row[0], {})["bm25"] = rank + 1

    if "vec" in lanes:
        emb = kb_embedder.embed(query)
        rows = conn.execute(
            """SELECT rowid, distance FROM kb_vectors
               WHERE embedding MATCH ?
               AND k = ?""",
            (emb.tobytes(), _CANDIDATES_PER_LANE),
        ).fetchall()
        for rank, row in enumerate(rows):
            candidates.setdefault(row[0], {})["vec"] = rank + 1

    # Reciprocal Rank Fusion: score = Σ 1/(k + rank_in_lane)
    scored: list[tuple[int, float, dict[str, int]]] = []
    for rowid, lane_ranks in candidates.items():
        score = sum(1.0 / (_RRF_K + r) for r in lane_ranks.values())
        scored.append((rowid, score, lane_ranks))
    scored.sort(key=lambda x: -x[1])

    results: list[SearchHit] = []
    for rowid, rrf_score, lane_ranks in scored[:top_k]:
        row = conn.execute(
            "SELECT chunk_text, section, heading_context, path FROM kb_chunks WHERE rowid = ?",
            (rowid,),
        ).fetchone()
        if row is None:
            continue
        results.append(
            SearchHit(
                chunk_id=rowid,
                path=row["path"],
                heading=row["section"],
                page_title=row["heading_context"],
                chunk_text=row["chunk_text"][:_TOKEN_BUDGET_CHARS],
                rrf_score=rrf_score,
                lane_ranks=lane_ranks,
            )
        )

    return results
