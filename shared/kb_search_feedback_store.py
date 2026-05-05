"""Persist 👍/👎 ground truth signals from digest.md checkboxes.

Idempotent upsert on (book_id, item_cfi, hit_path). Used by
agents.robin.book_digest_writer; future Chopper retrieval QA dataset.
"""

from __future__ import annotations

from datetime import datetime, timezone


def upsert_feedback(
    *,
    book_id: str,
    item_cfi: str,
    query_text: str,
    hit_path: str,
    signal: str,
    source: str = "digest",
) -> None:
    """Insert or replace a single feedback record.

    signal must be "up" or "down".
    Idempotent: ON CONFLICT updates signal + query_text + marked_at.
    """
    from shared.state import _get_conn

    conn = _get_conn()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        """
        INSERT INTO kb_search_feedback
            (book_id, item_cfi, query_text, hit_path, signal, marked_at, source)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(book_id, item_cfi, hit_path) DO UPDATE SET
            signal     = excluded.signal,
            query_text = excluded.query_text,
            marked_at  = excluded.marked_at,
            source     = excluded.source
        """,
        (book_id, item_cfi, query_text, hit_path, signal, now, source),
    )
    conn.commit()
