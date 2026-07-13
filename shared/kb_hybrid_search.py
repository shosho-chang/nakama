"""KB retrieval — BM25 (FTS5) + wikilink expansion, RRF k=60 fusion.

ADR-042: the dense-vector lane (vec0 / sqlite-vec / BGE-M3 embeddings) was
removed when the textbook corpus left the vault — the small card-box corpus
is well served by keyword search, so BM25 is the only ranking lane now. The
``wikilink`` lane still expands hits with 1-hop structural neighbours. RRF
fusion is retained (it degenerates gracefully to a single lane) so the
``SearchHit`` shape — including ``rrf_score`` and ``lane_ranks`` — is
unchanged for downstream evidence-card builders (Brook synthesize).

DB schema (canonical reference: migrations/012_kb_hybrid.sql):
  kb_chunks   — FTS5(chunk_text, section, heading_context, path UNINDEXED)
  kb_index_meta — (path, mtime_ns, file_hash, indexed_at)
  kb_wikilinks  — (src_path, dst_path)

The index DB lives in kb_index.db (separate from state.db).  The module-level
`get_kb_conn()` manages a single lazy-opened connection.  Tests inject their
own in-memory connection via the `db=` parameter in `search()`.

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
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    """Create kb_* tables if they don't exist yet (FTS5 + bookkeeping only)."""
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
    # Centaur N520: typed edges (支持/反駁/延伸) from KB/Permanent/ cards live in a
    # structured table, NOT only in FTS text. The edges form a directed knowledge
    # graph; storing them structurally keeps "show all cards that 反駁 X" a cheap
    # path query instead of a fragile CJK text-parse (panel Gemini §2). The card
    # *body* is still FTS-indexed for keyword search — the two are complementary.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kb_typed_edges (
            src_path  TEXT NOT NULL,
            edge_type TEXT NOT NULL,   -- 'support' | 'refute' | 'extend'
            dst_path  TEXT NOT NULL,
            reason    TEXT             -- 人的判斷理由（edge 行 '—' 之後）
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_typed_edges_src ON kb_typed_edges(src_path)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_typed_edges_dst ON kb_typed_edges(dst_path)")
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
        _init_schema(conn)
        return conn
    return _open_conn(Path(db_path))


# ---------------------------------------------------------------------------
# Search result schema
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SearchHit:
    chunk_id: int  # FTS5 rowid
    path: str  # e.g. "KB/Wiki/Concepts/overtraining"
    heading: str  # H2 section heading; empty string for preamble chunks
    page_title: str  # page title from frontmatter (heading_context column)
    chunk_text: str  # body, truncated to TOKEN_BUDGET_CHARS
    rrf_score: float
    lane_ranks: dict  # e.g. {"bm25": 1, "wikilink": 3}


# ---------------------------------------------------------------------------
# Core search
# ---------------------------------------------------------------------------


def _is_permanent_path(path: str) -> bool:
    """True if a chunk path lives under KB/Permanent/ (canonical guard, N520)."""
    from shared.permanent_layer import is_permanent_path

    return bool(path) and is_permanent_path(path)


def _resolve_candidate_paths(conn: sqlite3.Connection, rowids: list[int]) -> dict[int, str]:
    """Fetch path for each candidate rowid in one query (for Permanent tiering)."""
    if not rowids:
        return {}
    ph = ",".join("?" * len(rowids))
    rows = conn.execute(
        f"SELECT rowid, path FROM kb_chunks WHERE rowid IN ({ph})", rowids
    ).fetchall()
    return {r[0]: r["path"] for r in rows}


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
    lanes: tuple[str, ...] = ("bm25",),
    db: sqlite3.Connection | None = None,
) -> list[SearchHit]:
    """BM25 + wikilink RRF-k=60 search (ADR-042: dense-vec lane removed).

    Args:
        query:  free-text query (supports both Latin and CJK text).
        top_k:  maximum number of results to return.
        lanes:  active retrieval lanes; subset of ("bm25", "wikilink").
                "wikilink" expands BM25 hits with 1-hop structural neighbors.
                A legacy "vec" entry is accepted but ignored (the dense lane
                was removed in ADR-042) so existing callers don't break.
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

    if "wikilink" in lanes:
        _wikilink_lane(conn, candidates)

    # Permanent-first authority tier (Centaur v0.2 / handoff fork 2): 永久卡是
    # 修修「怎麼想」的權威層，檢索排最前。We resolve candidate paths once, then
    # sort with a (is_permanent, score) key so any Permanent card that MATCHED
    # the query surfaces ahead of comparable Wiki hits — and critically, this
    # runs BEFORE the `[:top_k]` truncation below, so a relevant Permanent hit is
    # never lost in truncation (panel Codex §5: do the boost pre-top_k, not in a
    # wrapper post-sort). Only candidates that already matched are in the pool —
    # an irrelevant Permanent card is never conjured up.
    path_by_rowid = _resolve_candidate_paths(conn, list(candidates))

    def _is_permanent(rowid: int) -> bool:
        return _is_permanent_path(path_by_rowid.get(rowid, ""))

    # Reciprocal Rank Fusion: score = Σ 1/(k + rank_in_lane)
    scored: list[tuple[int, float, dict[str, int]]] = []
    for rowid, lane_ranks in candidates.items():
        score = sum(1.0 / (_RRF_K + r) for r in lane_ranks.values())
        scored.append((rowid, score, lane_ranks))
    # tier 0 = Permanent (sorts first), tier 1 = everything else; within a tier,
    # higher RRF score wins.
    scored.sort(key=lambda x: (0 if _is_permanent(x[0]) else 1, -x[1]))

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
