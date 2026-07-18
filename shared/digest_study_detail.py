"""Assemble the per-study detail view for the Bridge digest viewer.

The digest markdown gives us a study's curated framing (journal, score,
Verdict, Why) but no abstract. This module fills the gap: given a digest
date + PMID it locates the parsed study, fetches the PubMed abstract +
authors + pub-date (cache-first), and translates the abstract to Traditional
Chinese once. The result feeds ``digest_study_detail.html``.

Cost / latency discipline (mirrors ``shared.digest_ask``):
- Abstract fetch (NCBI) and translation (LLM) run **only on cache-miss**;
  the result persists in ``pubmed_abstract_cache`` keyed by PMID. Repeat
  views are pure DB reads.
- Translation is a single call; the English abstract is cached even if the
  translation call fails, so a later view retries just the translation.

Everything external is injectable (``fetch`` / ``llm`` / ``store``) so tests
never hit the network or an LLM.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Optional

from shared import pubmed_abstract_store
from shared.digest_indexer import DigestIndexer
from shared.digest_parser import DigestStudy
from shared.llm_context import _local
from shared.log import get_logger
from shared.pubmed_client import efetch_abstracts

logger = get_logger("nakama.shared.digest_study_detail")

DEFAULT_MODEL: str = "claude-sonnet-4-6"
MAX_OUTPUT_TOKENS: int = 4096

_TRANSLATE_SYSTEM = (
    "你是醫學與生命科學文獻的專業譯者。使用者會給你一篇 PubMed 論文的英文 abstract，"
    "請翻成準確、通順的繁體中文。規則：\n"
    "1. 保留結構化標籤並譯為中文（BACKGROUND→背景、METHODS→方法、RESULTS→結果、"
    "CONCLUSIONS→結論、OBJECTIVE→目的 等），每段換行。\n"
    "2. 專有名詞、藥名、基因/蛋白名、量表縮寫保留英文原文，必要時在括號補中文。\n"
    "3. 數字、單位、統計值（p 值、CI、HR 等）原樣保留。\n"
    "4. 只輸出翻譯本身，不要加任何前言、說明或標題。"
)


@dataclass(frozen=True)
class StudyDetail:
    """View model for one PubMed study's detail page."""

    study: DigestStudy
    pmid: str
    date: str

    # PubMed-fetched fields (abstract's siblings the digest doesn't carry)
    abstract: str = ""  # English original, "" when PubMed has none
    abstract_zh: str = ""  # zh-TW translation, "" when unavailable
    authors: str = ""
    pub_date: str = ""
    doi: str = ""  # → publisher's own page via https://doi.org/{doi}
    pmcid: str = ""  # → free full text on PMC when present

    has_abstract: bool = False
    fetch_error: Optional[str] = None  # NCBI fetch failed → digest info only
    translate_error: Optional[str] = None  # abstract shown, translation failed


def _translate(text: str, llm: Callable[..., str], model: str, *, pmid: str, date: str) -> str:
    """Translate an English abstract to zh-TW via the LLM facade.

    Sets ``_local.scope_json`` for per-call cost audit, mirroring
    ``shared.digest_ask`` (ADR-030 follow-up #700).
    """
    prompt = f"<abstract>\n{text}\n</abstract>\n\n請依規則翻成繁體中文。"
    scope_json = json.dumps(
        {"surface": "digest_study_translate", "pmid": pmid, "date": date, "model": model},
        ensure_ascii=False,
    )
    prior = getattr(_local, "scope_json", None)
    _local.scope_json = scope_json
    try:
        out = llm(prompt, system=_TRANSLATE_SYSTEM, model=model, max_tokens=MAX_OUTPUT_TOKENS)
    finally:
        _local.scope_json = prior
    return out.strip()


def _find_study(indexer: DigestIndexer, date_: str, pmid: str) -> Optional[DigestStudy]:
    for s in indexer.load_studies("pubmed", date_):
        if s.external_id == pmid:
            return s
    return None


def load_study_detail(
    indexer: DigestIndexer,
    date_: str,
    pmid: str,
    *,
    llm: Callable[..., str] | None = None,
    fetch: Callable[[list[str]], list[dict[str, Any]]] | None = None,
    store=pubmed_abstract_store,
    model: str = DEFAULT_MODEL,
) -> Optional[StudyDetail]:
    """Build the detail view for ``pubmed/{date_}/{pmid}``.

    Returns ``None`` when the PMID is not one of that digest's studies (the
    route turns this into a 404). Fetch / translation problems are surfaced
    as ``fetch_error`` / ``translate_error`` on the returned view model, never
    raised — the page still renders the digest-side framing.
    """
    study = _find_study(indexer, date_, pmid)
    if study is None:
        return None

    if fetch is None:
        fetch = efetch_abstracts

    cached = store.get(pmid)

    if cached is None:
        # Cache-miss: fetch from NCBI. A network/API failure is non-fatal —
        # we render the digest framing and let a later view retry (nothing
        # cached), rather than poisoning the cache with an error.
        try:
            rows = fetch([pmid])
        except Exception as exc:  # noqa: BLE001 — any NCBI/network failure, surface don't crash
            logger.warning("efetch abstract failed pmid=%s: %s", pmid, exc)
            return StudyDetail(
                study=study,
                pmid=pmid,
                date=date_,
                fetch_error=f"PubMed 抓取失敗（{exc.__class__.__name__}），稍後重試。",
            )
        if not rows:
            return StudyDetail(
                study=study,
                pmid=pmid,
                date=date_,
                fetch_error="PubMed 查無此文獻（PMID 可能已撤下）。",
            )
        art = rows[0]
        store.upsert_fetch(
            pmid,
            title=art.get("title", ""),
            journal=art.get("journal", ""),
            authors=art.get("authors", ""),
            pub_date=art.get("pub_date", ""),
            issn=art.get("issn", ""),
            doi=art.get("doi", ""),
            pmcid=art.get("pmcid", ""),
            abstract=art.get("abstract", ""),
        )
        cached = store.get(pmid) or {}

    abstract = (cached.get("abstract") or "").strip()
    authors = cached.get("authors") or ""
    pub_date = cached.get("pub_date") or ""
    doi = cached.get("doi") or ""
    pmcid = cached.get("pmcid") or ""

    if not abstract:
        # PubMed genuinely has no abstract (e.g. letters, editorials).
        return StudyDetail(
            study=study,
            pmid=pmid,
            date=date_,
            authors=authors,
            pub_date=pub_date,
            doi=doi,
            pmcid=pmcid,
            has_abstract=False,
        )

    abstract_zh = (cached.get("abstract_zh") or "").strip()
    translate_error: Optional[str] = None

    if not abstract_zh:
        if llm is None:
            from shared.llm import ask as facade_ask

            llm = facade_ask
        try:
            abstract_zh = _translate(abstract, llm, model, pmid=pmid, date=date_)
            store.set_translation(pmid, abstract_zh, model=model)
        except Exception as exc:  # noqa: BLE001 — translation failure ≠ page failure
            logger.warning("abstract translation failed pmid=%s: %s", pmid, exc)
            abstract_zh = ""
            translate_error = f"翻譯失敗（{exc.__class__.__name__}），已顯示英文原文。"

    return StudyDetail(
        study=study,
        pmid=pmid,
        date=date_,
        abstract=abstract,
        abstract_zh=abstract_zh,
        authors=authors,
        pub_date=pub_date,
        doi=doi,
        pmcid=pmcid,
        has_abstract=True,
        translate_error=translate_error,
    )
