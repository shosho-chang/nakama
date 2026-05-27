"""arXiv + Semantic Scholar thin wrapper for academic literature lookup.

Used by Nami ``arxiv_lookup`` / ``arxiv_citations`` tools as a faster /
domain-specific alternative to web_search when船長要找學術論文。

Two data sources:

- **arXiv API** (https://export.arxiv.org/api/query) — Atom XML, no auth,
  ~1 req / 3s. Coverage: title / authors / abstract / categories / pdf URL.
- **Semantic Scholar Graph API** (https://api.semanticscholar.org/graph/v1)
  — JSON, no auth needed for basic use (1 req/s). Coverage: citation graph,
  influential citation count, references, recommendations.

設計對齊 ``shared/pubmed_client.py``：單一檔、stdlib + httpx、模組級 logger、
專屬 error class。Empty input → empty output（不打 API）。
"""

from __future__ import annotations

import re
from typing import Any
from xml.etree import ElementTree as ET

import httpx

from shared.log import get_logger

logger = get_logger("nakama.shared.arxiv_client")

_ARXIV_BASE = "https://export.arxiv.org/api/query"
_S2_BASE = "https://api.semanticscholar.org/graph/v1"
_DEFAULT_TIMEOUT = 20.0
_NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


class ArxivClientError(RuntimeError):
    """arXiv / Semantic Scholar API 呼叫失敗（網路 / HTTP / 解析）。"""


def search(
    query: str,
    *,
    max_results: int = 5,
    sort_by: str = "relevance",
    timeout: float = _DEFAULT_TIMEOUT,
) -> list[dict[str, Any]]:
    """Search arXiv by free-text query, return parsed entries.

    Args:
        query: free-text query (英文，可用 ``ti:``, ``au:``, ``cat:`` prefix
            或 boolean operators — 見 arXiv API docs)
        max_results: ≤ 30 for quick-lookup use case
        sort_by: ``"relevance"`` / ``"submittedDate"`` / ``"lastUpdatedDate"``

    Returns:
        list of dicts; each contains
        ``arxiv_id, title, authors, published, summary, categories, pdf_url,
        primary_category``。Empty list 表示無結果（**不 raise**）。

    Raises:
        ArxivClientError: HTTP / XML 解析失敗
    """
    if not query.strip():
        raise ArxivClientError("query 不能為空")

    capped = max(1, min(max_results, 30))
    params = {
        "search_query": query.strip(),
        "max_results": str(capped),
        "sortBy": sort_by,
        "sortOrder": "descending",
    }

    try:
        resp = httpx.get(_ARXIV_BASE, params=params, timeout=timeout)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise ArxivClientError(f"arXiv HTTP 失敗：{e}") from e

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as e:
        raise ArxivClientError(f"arXiv XML 解析失敗：{e}") from e

    out: list[dict[str, Any]] = []
    for entry in root.findall("a:entry", _NS):
        try:
            parsed = _parse_entry(entry)
        except Exception as e:  # noqa: BLE001 — XML schema 變動容忍
            logger.warning("arxiv entry parse failed err=%s", e)
            continue
        if parsed:
            out.append(parsed)
    return out


def get_paper(arxiv_id: str, *, timeout: float = _DEFAULT_TIMEOUT) -> dict[str, Any] | None:
    """Fetch a single paper by arXiv ID (e.g. ``2402.03300`` or ``2402.03300v1``).

    Returns None if not found / withdrawn-only entry.
    """
    aid = arxiv_id.strip()
    if not aid:
        raise ArxivClientError("arxiv_id 不能為空")

    params = {"id_list": aid}
    try:
        resp = httpx.get(_ARXIV_BASE, params=params, timeout=timeout)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise ArxivClientError(f"arXiv HTTP 失敗：{e}") from e

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as e:
        raise ArxivClientError(f"arXiv XML 解析失敗：{e}") from e

    entries = root.findall("a:entry", _NS)
    if not entries:
        return None
    try:
        return _parse_entry(entries[0])
    except Exception as e:  # noqa: BLE001
        logger.warning("arxiv get_paper parse failed id=%s err=%s", aid, e)
        return None


def get_citations(
    arxiv_id: str,
    *,
    limit: int = 10,
    timeout: float = _DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Semantic Scholar citation graph for a given arXiv paper.

    Returns dict with keys:
        ``paper`` (target paper metadata + counts),
        ``citing`` (list of recent papers citing this one),
        ``references`` (list of papers this one cites).

    Raises ``ArxivClientError`` on HTTP failure。Empty data section is OK
    (not all papers are indexed by S2).
    """
    aid = arxiv_id.strip()
    if not aid:
        raise ArxivClientError("arxiv_id 不能為空")

    capped = max(1, min(limit, 20))
    # S2 不接受 vN 後綴。只剝尾端版本號，避免吃掉舊式 ID 中段的 'v'
    # （例：``cs.cv/0701001`` 不可被 split('v')[0] 截成 ``cs.c``）。
    s2_id = f"arXiv:{re.sub(r'v\d+$', '', aid)}"
    paper_url = f"{_S2_BASE}/paper/{s2_id}"
    paper_fields = (
        "title,authors,year,citationCount,referenceCount,"
        "influentialCitationCount,isOpenAccess,abstract"
    )
    list_fields = "title,authors,year,citationCount,externalIds"

    try:
        paper_resp = httpx.get(
            paper_url, params={"fields": paper_fields}, timeout=timeout
        )
        if paper_resp.status_code == 404:
            return {"paper": None, "citing": [], "references": []}
        paper_resp.raise_for_status()
        paper = paper_resp.json()

        citing_resp = httpx.get(
            f"{paper_url}/citations",
            params={"fields": list_fields, "limit": str(capped)},
            timeout=timeout,
        )
        citing_resp.raise_for_status()
        citing_raw = citing_resp.json().get("data", [])

        refs_resp = httpx.get(
            f"{paper_url}/references",
            params={"fields": list_fields, "limit": str(capped)},
            timeout=timeout,
        )
        refs_resp.raise_for_status()
        refs_raw = refs_resp.json().get("data", [])
    except httpx.HTTPError as e:
        raise ArxivClientError(f"Semantic Scholar HTTP 失敗：{e}") from e
    except ValueError as e:
        raise ArxivClientError(f"Semantic Scholar JSON 解析失敗：{e}") from e

    return {
        "paper": _normalize_s2_paper(paper),
        "citing": [_normalize_s2_paper(item.get("citingPaper", {})) for item in citing_raw],
        "references": [_normalize_s2_paper(item.get("citedPaper", {})) for item in refs_raw],
    }


# ── parsers ──────────────────────────────────────────────────────────


def _parse_entry(entry: ET.Element) -> dict[str, Any] | None:
    title_el = entry.find("a:title", _NS)
    title = (title_el.text or "").strip().replace("\n", " ") if title_el is not None else ""
    if not title:
        return None

    id_el = entry.find("a:id", _NS)
    full_id = (id_el.text or "").strip() if id_el is not None else ""
    arxiv_id = full_id.rsplit("/abs/", 1)[-1] if full_id else ""

    summary_el = entry.find("a:summary", _NS)
    summary = (summary_el.text or "").strip() if summary_el is not None else ""

    pub_el = entry.find("a:published", _NS)
    published = (pub_el.text or "")[:10] if pub_el is not None else ""

    authors = [
        (a.find("a:name", _NS).text or "").strip()
        for a in entry.findall("a:author", _NS)
        if a.find("a:name", _NS) is not None
    ]

    categories = [c.get("term", "") for c in entry.findall("a:category", _NS) if c.get("term")]
    primary_el = entry.find("arxiv:primary_category", _NS)
    primary = primary_el.get("term", "") if primary_el is not None else ""

    pdf_url = ""
    abs_url = ""
    for link in entry.findall("a:link", _NS):
        if link.get("title") == "pdf":
            pdf_url = link.get("href", "")
        if link.get("rel") == "alternate":
            abs_url = link.get("href", "")
    if not abs_url and arxiv_id:
        abs_url = f"https://arxiv.org/abs/{arxiv_id}"
    if not pdf_url and arxiv_id:
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"

    return {
        "arxiv_id": arxiv_id,
        "title": title,
        "authors": authors,
        "first_author": authors[0] if authors else "",
        "published": published,
        "summary": summary,
        "categories": categories,
        "primary_category": primary,
        "abs_url": abs_url,
        "pdf_url": pdf_url,
    }


def _normalize_s2_paper(item: dict[str, Any]) -> dict[str, Any]:
    """Flatten a Semantic Scholar paper dict to a small consistent shape."""
    if not item:
        return {}
    authors = [a.get("name", "") for a in (item.get("authors") or []) if a.get("name")]
    external = item.get("externalIds") or {}
    return {
        "title": (item.get("title") or "").strip(),
        "authors": authors,
        "first_author": authors[0] if authors else "",
        "year": item.get("year"),
        "citation_count": item.get("citationCount"),
        "reference_count": item.get("referenceCount"),
        "influential_citation_count": item.get("influentialCitationCount"),
        "is_open_access": item.get("isOpenAccess"),
        "abstract": item.get("abstract") or "",
        "arxiv_id": external.get("ArXiv"),
        "doi": external.get("DOI"),
    }
