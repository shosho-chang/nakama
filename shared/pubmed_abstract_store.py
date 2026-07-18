"""Cache for Bridge digest study-detail: PubMed abstract + zh-TW translation.

Keyed by stable PMID. First view of a study fetches the abstract from NCBI
and translates it once; every later view reads this cache (no network, no LLM).

State layer, not vault — Bridge stays read-only against the Obsidian vault
(Issue #231). Table DDL lives in ``shared.state._init_tables``; this module
owns the CRUD.

Two-phase write so a fetch survives a later translation failure:
- :func:`upsert_fetch` stores the PubMed metadata + English abstract
  (``abstract_zh`` stays NULL).
- :func:`set_translation` fills in the Chinese translation afterwards.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get(pmid: str) -> Optional[dict[str, Any]]:
    """Return the cached row for ``pmid`` as a dict, or ``None`` if absent."""
    from shared.state import _get_conn

    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM pubmed_abstract_cache WHERE pmid = ?",
        (pmid,),
    ).fetchone()
    return dict(row) if row is not None else None


def upsert_fetch(
    pmid: str,
    *,
    title: str = "",
    journal: str = "",
    authors: str = "",
    pub_date: str = "",
    issn: str = "",
    doi: str = "",
    pmcid: str = "",
    abstract: str = "",
) -> None:
    """Store fetched PubMed metadata + English abstract.

    Idempotent on ``pmid``. Refreshes ``fetched_at`` and — because the source
    text changed — clears any stale translation so it gets regenerated.
    """
    from shared.state import _get_conn

    conn = _get_conn()
    conn.execute(
        """
        INSERT INTO pubmed_abstract_cache
            (pmid, title, journal, authors, pub_date, issn, doi, pmcid, abstract,
             abstract_zh, translate_model, fetched_at, translated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, NULL)
        ON CONFLICT(pmid) DO UPDATE SET
            title           = excluded.title,
            journal         = excluded.journal,
            authors         = excluded.authors,
            pub_date        = excluded.pub_date,
            issn            = excluded.issn,
            doi             = excluded.doi,
            pmcid           = excluded.pmcid,
            abstract        = excluded.abstract,
            abstract_zh     = NULL,
            translate_model = NULL,
            fetched_at      = excluded.fetched_at,
            translated_at   = NULL
        """,
        (pmid, title, journal, authors, pub_date, issn, doi, pmcid, abstract, _now()),
    )
    conn.commit()


def set_translation(pmid: str, abstract_zh: str, *, model: str) -> None:
    """Fill in the zh-TW translation for an already-fetched ``pmid`` (no-op if
    the row does not exist yet)."""
    from shared.state import _get_conn

    conn = _get_conn()
    conn.execute(
        """
        UPDATE pubmed_abstract_cache
           SET abstract_zh = ?, translate_model = ?, translated_at = ?
         WHERE pmid = ?
        """,
        (abstract_zh, model, _now(), pmid),
    )
    conn.commit()
