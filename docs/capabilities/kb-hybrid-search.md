# KB Hybrid Search

BM25 + dense-vector dual-lane retrieval over the Obsidian vault KB/Wiki corpus.
Supersedes the filesystem-walk + Claude Haiku ranker path for bulk retrieval use cases.

## Capability

| Property | Value |
|----------|-------|
| Module | `shared.kb_hybrid_search`, `shared.kb_indexer`, `shared.kb_embedder` |
| Invoked by | `agents.robin.kb_search.search_kb(..., engine="hybrid")` |
| Engine | BM25 (SQLite FTS5) + dense-vec (model2vec potion-base-8M) |
| Fusion | Reciprocal Rank Fusion k=60, 30 candidates per lane |
| Index DB | `kb_index.db` (separate from state.db; sqlite-vec extension required) |

## IO Contract

### Indexing

```python
from shared.kb_hybrid_search import get_kb_conn
from shared.kb_indexer import index_vault, IndexStats

conn = get_kb_conn()                    # opens data/kb_index.db
stats: IndexStats = index_vault(vault_path, conn)
# stats.files_indexed, stats.files_skipped, stats.chunks_added
```

### Search (direct)

```python
from shared.kb_hybrid_search import search, SearchHit

hits: list[SearchHit] = search(
    query,
    top_k=10,
    lanes=("bm25", "vec"),   # both lanes (default)
)
# SearchHit fields: chunk_id, path, heading, page_title, chunk_text, rrf_score, lane_ranks
```

### Search (via kb_search.search_kb)

```python
from agents.robin.kb_search import search_kb

results = search_kb(query, vault_path, engine="hybrid")
# Returns list[dict] with keys: type, title, path, preview, relevance_reason
# Same schema as engine="haiku" — existing callers zero-change
```

## Chunking Strategy

- Pages split on `## ` H2 headings
- Minimum chunk length: 30 characters
- Skipped sections: `References`, `See Also`, `Related`, `延伸閱讀`, `參考資料`
- FTS5 column weights: chunk_text=1.0, section=0.5, heading_context=0.3

## Incremental Indexing

`kb_index_meta` tracks `(path, mtime_ns, file_hash)` per page.
Second `index_vault()` call on an unchanged vault completes in <1 s (mtime_ns short-circuit).

## Measured Cost & Latency

| Metric | Value |
|--------|-------|
| Embedding model | model2vec potion-base-8M (≈25 MB download, cached) |
| Embedding latency | ~1 ms/chunk on CPU (no GPU required) |
| Index build (845 pages) | ~30–60 s on desktop (CPU only) |
| Search latency (BM25+vec, 845 pages) | <50 ms |
| LLM cost per search | $0 (no LLM call) |

## Extension Points

- **Different vault path**: `index_vault(your_vault_path, db)` — no config change needed
- **Single-lane mode**: `search(q, lanes=("bm25",))` or `search(q, lanes=("vec",))`
- **Top-k tuning**: `search(q, top_k=20)` for broader recall
- **Custom DB path**: `NAKAMA_KB_INDEX_DB_PATH=/path/to/custom.db` env override
- **Model swap**: change `_MODEL_NAME` in `shared/kb_embedder.py` (must be 256-dim model2vec)
