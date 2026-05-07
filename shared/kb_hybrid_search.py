"""KB hybrid retrieval — BM25 + dense-vec + RRF k=60 fusion.

DB schema (canonical reference: migrations/012_kb_hybrid.sql):
  kb_chunks   — FTS5(chunk_text, section, heading_context, path UNINDEXED)
  kb_vectors  — vec0(embedding float[1024])  # ADR-022: BGE-M3 default
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


def _open_conn(db_path: Path, *, dim: int = kb_embedder.DIM_BGE_M3) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    _init_schema(conn, dim=dim)
    return conn


def _init_schema(conn: sqlite3.Connection, *, dim: int = kb_embedder.DIM_BGE_M3) -> None:
    """Create kb_* tables if they don't exist yet. `dim` controls vec0 column width."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kb_index_meta (
            path       TEXT PRIMARY KEY,
            mtime_ns   INTEGER NOT NULL,
            file_hash  TEXT    NOT NULL,
            indexed_at TEXT    NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kb_wikilinks (
            src_path TEXT NOT NULL,
            dst_path TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_wikilinks_src ON kb_wikilinks(src_path)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_wikilinks_dst ON kb_wikilinks(dst_path)")
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
        conn.execute(f"CREATE VIRTUAL TABLE kb_vectors USING vec0(embedding float[{dim}])")
    except sqlite3.OperationalError:
        pass  # already exists
    conn.commit()


def kb_vectors_dim(conn: sqlite3.Connection) -> int:
    """Inspect kb_vectors vec0 schema → declared embedding dim."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='kb_vectors'"
    ).fetchone()
    if row is None or row[0] is None:
        raise RuntimeError("kb_vectors table not present in DB")
    sql = row[0]
    # Format: "... vec0(embedding float[1024])"
    import re as _re

    m = _re.search(r"float\[(\d+)\]", sql)
    if m is None:
        raise RuntimeError(f"Cannot parse kb_vectors dim from SQL: {sql!r}")
    return int(m.group(1))


def assert_dim_alignment(conn: sqlite3.Connection) -> None:
    """ADR-022: fail loudly if embedder dim != kb_vectors dim.

    Catches the silent miscompare where kb_vectors was built with a different
    backend than the one currently active. Run this once at connection open.
    """
    table_dim = kb_vectors_dim(conn)
    model_dim = kb_embedder.current_dim()
    if model_dim != table_dim:
        raise RuntimeError(
            f"Embedding dim mismatch: kb_embedder backend "
            f"'{kb_embedder.current_backend()}' produces {model_dim}-d vectors but "
            f"kb_vectors table is float[{table_dim}]. Re-index with "
            f"`python -m shared.kb_indexer --rebuild` or set "
            f"NAKAMA_EMBED_BACKEND to match the table dim."
        )


def get_kb_conn() -> sqlite3.Connection:
    """Return the module-level kb_index DB connection (lazy-opened).

    Asserts ``kb_embedder.current_dim() == kb_vectors_dim`` on first open
    (ADR-022). Mismatch raises RuntimeError immediately.
    """
    global _conn
    if _conn is None:
        _conn = _open_conn(_get_kb_db_path())
        assert_dim_alignment(_conn)
    return _conn


def make_conn(
    db_path: str | Path = ":memory:", *, dim: int = kb_embedder.DIM_BGE_M3
) -> sqlite3.Connection:
    """Create and initialize a fresh connection — for tests or CLI use.

    Passing ":memory:" creates an in-memory DB (no file, lost on close).
    `dim` controls the vec0 embedding column width (default 1024 for BGE-M3;
    legacy potion tests pass dim=256).
    """
    if str(db_path) == ":memory:":
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        _init_schema(conn, dim=dim)
        return conn
    return _open_conn(Path(db_path), dim=dim)


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


def _wikilink_lane(
    conn: sqlite3.Connection,
    candidates: dict[int, dict[str, int]],
) -> None:
    """Expand candidates with 1-hop wikilink neighbors (both directions).

    For each path already in `candidates`, find all pages that this path
    links to (outgoing) and all pages that link to this path (incoming).
    Those neighbor pages' chunks are added to `candidates` with a "wikilink"
    rank (1 = most edges to existing candidates, ties broken alphabetically).
    Modifies `candidates` in place.
    """
    if not candidates:
        return

    # Resolve paths for current candidates
    base_paths: set[str] = set()
    for rowid in candidates:
        row = conn.execute("SELECT path FROM kb_chunks WHERE rowid = ?", (rowid,)).fetchone()
        if row:
            base_paths.add(row["path"])

    if not base_paths:
        return

    ph = ",".join("?" * len(base_paths))
    base_list = list(base_paths)

    # Outgoing edges: pages that base_paths link to
    out_rows = conn.execute(
        f"SELECT dst_path FROM kb_wikilinks WHERE src_path IN ({ph})",
        base_list,
    ).fetchall()
    # Incoming edges: pages that link to base_paths
    in_rows = conn.execute(
        f"SELECT src_path FROM kb_wikilinks WHERE dst_path IN ({ph})",
        base_list,
    ).fetchall()

    neighbor_paths = {r[0] for r in out_rows} | {r[0] for r in in_rows}
    neighbor_paths -= base_paths

    if not neighbor_paths:
        return

    # Rank neighbors by edge count to base candidates (most connected = rank 1)
    conn_count: dict[str, int] = {}
    for path in neighbor_paths:
        out_cnt = conn.execute(
            f"SELECT COUNT(*) FROM kb_wikilinks WHERE src_path = ? AND dst_path IN ({ph})",
            [path] + base_list,
        ).fetchone()[0]
        in_cnt = conn.execute(
            f"SELECT COUNT(*) FROM kb_wikilinks WHERE dst_path = ? AND src_path IN ({ph})",
            [path] + base_list,
        ).fetchone()[0]
        conn_count[path] = out_cnt + in_cnt

    sorted_neighbors = sorted(neighbor_paths, key=lambda p: (-conn_count[p], p))

    for rank, neighbor_path in enumerate(sorted_neighbors):
        chunk_rows = conn.execute(
            "SELECT rowid FROM kb_chunks WHERE path = ?", (neighbor_path,)
        ).fetchall()
        for chunk_row in chunk_rows:
            rowid = chunk_row[0]
            candidates.setdefault(rowid, {}).setdefault("wikilink", rank + 1)


def search(
    query: str,
    top_k: int = 10,
    *,
    lanes: tuple[str, ...] = ("bm25", "vec"),
    db: sqlite3.Connection | None = None,
) -> list[SearchHit]:
    """BM25 + dense-vec + wikilink RRF-k=60 hybrid search.

    Args:
        query:  free-text query (supports both Latin and CJK text).
        top_k:  maximum number of results to return.
        lanes:  active retrieval lanes; subset of ("bm25", "vec", "wikilink").
                "wikilink" expands BM25/vec hits with 1-hop structural neighbors.
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

    if "wikilink" in lanes:
        _wikilink_lane(conn, candidates)

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
