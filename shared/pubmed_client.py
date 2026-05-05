"""NCBI Entrez E-utilities thin wrapper for medical literature quick lookup.

Used by Nami `pubmed_lookup` tool to bypass the slow / expensive Deep Research
path when the user just wants to verify a claim or surface top recent papers.

Coverage: esearch（PMIDs by query）+ esummary（title/authors/journal/year/doi/
pmcid，無 abstract）。Abstract 不抓 — PubMed 頁面與 Robin pubmed-to-reader
pipeline 已能補位。

Rate limit (per NCBI policy):
- 無 API key: 3 req/s
- 有 ``PUBMED_API_KEY`` env: 10 req/s

本模組不做主動 rate limit；單次 tool 呼叫最多 2 個 request（esearch + esummary），
連續多次呼叫由 caller 控制節奏。對齊 ``agents/robin/pubmed_digest.py`` 既有 sleep
策略（caller-side）。
"""

from __future__ import annotations

import os
from typing import Any
from xml.etree import ElementTree as ET

import httpx

from shared.log import get_logger

logger = get_logger("nakama.shared.pubmed_client")

_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_DEFAULT_TIMEOUT = 15.0
_TOOL_NAME = "nakama"
_DEFAULT_EMAIL = "bonvoyage.co.ltd@gmail.com"


class PubMedClientError(RuntimeError):
    """NCBI Entrez API 呼叫失敗（網路 / HTTP / 解析）。"""


def _api_key() -> str | None:
    return os.environ.get("PUBMED_API_KEY") or None


def _common_params() -> dict[str, str]:
    params = {
        "tool": _TOOL_NAME,
        "email": os.environ.get("PUBMED_EMAIL") or _DEFAULT_EMAIL,
    }
    key = _api_key()
    if key:
        params["api_key"] = key
    return params


def esearch(
    query: str,
    *,
    max_results: int = 10,
    since_year: int | None = None,
    sort: str = "relevance",
    timeout: float = _DEFAULT_TIMEOUT,
) -> list[str]:
    """Find PMIDs by query.

    Args:
        query: PubMed search expression（英文，可含 MeSH / boolean operators）
        max_results: 上限筆數（NCBI hard cap 10000，但 quick lookup 場景 ≤ 50）
        since_year: 限 ``YYYY:YYYY[Date - Publication]`` 格式的年份下限
        sort: NCBI sort 順序，預設 ``"relevance"``。Robin digest 走
            ``"pub_date"`` 拿最新（PubMed 接受的值列表見
            https://www.ncbi.nlm.nih.gov/books/NBK25499/#chapter4.ESearch）
        timeout: HTTP timeout（秒）

    Returns:
        PMIDs（可能為空 list）

    Raises:
        PubMedClientError: 網路 / 4xx-5xx / JSON 結構非預期
    """
    if not query.strip():
        raise PubMedClientError("query 不能為空")

    term = query.strip()
    if since_year is not None:
        term = f"{term} AND {since_year}:3000[Date - Publication]"

    params = {
        **_common_params(),
        "db": "pubmed",
        "term": term,
        "retmode": "json",
        "retmax": str(max(1, min(max_results, 200))),
        "sort": sort,
    }

    try:
        resp = httpx.get(f"{_BASE}/esearch.fcgi", params=params, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as e:
        raise PubMedClientError(f"esearch HTTP 失敗：{e}") from e
    except ValueError as e:
        raise PubMedClientError(f"esearch JSON 解析失敗：{e}") from e

    try:
        idlist = data["esearchresult"]["idlist"]
    except (KeyError, TypeError) as e:
        raise PubMedClientError(f"esearch 回應結構非預期：{data!r}") from e

    if not isinstance(idlist, list):
        raise PubMedClientError(f"esearch idlist 不是 list：{idlist!r}")
    return [str(p) for p in idlist]


def esummary(
    pmids: list[str],
    *,
    timeout: float = _DEFAULT_TIMEOUT,
) -> list[dict[str, Any]]:
    """Fetch summary metadata for a batch of PMIDs.

    Returns one dict per input PMID（順序對齊輸入），缺項留空字串 / 空 list。
    Output keys: pmid, title, authors（list[str]）, journal, year, doi, pmcid,
    first_author, pubdate, pubtypes（list[str]）

    Empty input → empty output（不打 API）。
    """
    if not pmids:
        return []

    params = {
        **_common_params(),
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "json",
    }

    try:
        resp = httpx.get(f"{_BASE}/esummary.fcgi", params=params, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as e:
        raise PubMedClientError(f"esummary HTTP 失敗：{e}") from e
    except ValueError as e:
        raise PubMedClientError(f"esummary JSON 解析失敗：{e}") from e

    try:
        result = data["result"]
    except (KeyError, TypeError) as e:
        raise PubMedClientError(f"esummary 回應缺 result：{data!r}") from e

    out: list[dict[str, Any]] = []
    for pmid in pmids:
        item = result.get(pmid)
        if not isinstance(item, dict):
            logger.warning("esummary missing PMID %s in batch response", pmid)
            continue
        out.append(_normalize_summary(pmid, item))
    return out


def _normalize_summary(pmid: str, item: dict[str, Any]) -> dict[str, Any]:
    """Pull the fields we care about out of a NCBI esummary item."""
    authors_raw = item.get("authors") or []
    authors = [a.get("name", "") for a in authors_raw if isinstance(a, dict) and a.get("name")]

    article_ids = item.get("articleids") or []
    doi = ""
    pmcid = ""
    for entry in article_ids:
        if not isinstance(entry, dict):
            continue
        idtype = entry.get("idtype")
        value = entry.get("value", "")
        if idtype == "doi" and value:
            doi = value
        elif idtype == "pmc" and value:
            pmcid = value

    pubdate = item.get("pubdate", "") or item.get("epubdate", "")
    year = ""
    if pubdate:
        # 通常是 "2024 Jan 5" 或 "2024" 或 "2024 Jan"
        year = pubdate.split(" ", 1)[0]

    return {
        "pmid": pmid,
        "title": item.get("title", "").rstrip("."),
        "authors": authors,
        "first_author": authors[0] if authors else "",
        "journal": item.get("fulljournalname") or item.get("source", ""),
        "year": year,
        "pubdate": pubdate,
        "doi": doi,
        "pmcid": pmcid,
        "pubtypes": list(item.get("pubtype") or []),
    }


def lookup(
    query: str,
    *,
    max_results: int = 5,
    since_year: int | None = None,
) -> list[dict[str, Any]]:
    """Convenience: esearch + esummary in one call. Empty result → empty list."""
    pmids = esearch(query, max_results=max_results, since_year=since_year)
    if not pmids:
        return []
    return esummary(pmids)


def efetch_abstracts(
    pmids: list[str],
    *,
    timeout: float = 30.0,
) -> list[dict[str, Any]]:
    """Batch efetch PubMed XML 解出 abstract 與基本欄位。

    esummary 不含 abstract（NCBI API 設計）；Robin curate 需要 abstract，所以
    eutils-type feed 走這條 efetch path 補 abstract。輸出 dict 與
    ``agents/robin/pubmed_digest.py:_parse_entry`` 對齊：

    keys: pmid, title, journal, abstract, pub_date, authors, url, issn

    Empty input → empty output（不打 API）。XML 解析失敗的單篇記 warning 跳過，
    不整批 raise（避免一兩篇損壞 entry 帶倒整個 feed）。
    """
    if not pmids:
        return []

    params = {
        **_common_params(),
        "db": "pubmed",
        "id": ",".join(pmids),
        "rettype": "abstract",
        "retmode": "xml",
    }

    try:
        resp = httpx.get(f"{_BASE}/efetch.fcgi", params=params, timeout=timeout)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise PubMedClientError(f"efetch HTTP 失敗：{e}") from e

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as e:
        raise PubMedClientError(f"efetch XML 解析失敗：{e}") from e

    out: list[dict[str, Any]] = []
    for article in root.findall(".//PubmedArticle"):
        try:
            parsed = _parse_pubmed_article(article)
        except Exception as e:  # noqa: BLE001 — XML schema 變動 / 缺欄位太多種，整體 catch
            pmid_el = article.find(".//PMID")
            pmid_str = pmid_el.text if pmid_el is not None else "?"
            logger.warning("efetch article parse failed pmid=%s err=%s", pmid_str, e)
            continue
        if parsed:
            out.append(parsed)
    return out


def _parse_pubmed_article(article: ET.Element) -> dict[str, Any] | None:
    """從一個 <PubmedArticle> element 抽出 candidate dict。"""
    pmid_el = article.find(".//PMID")
    if pmid_el is None or not pmid_el.text:
        return None
    pmid = pmid_el.text.strip()

    title_el = article.find(".//ArticleTitle")
    title = _text_with_inline(title_el).strip() if title_el is not None else ""
    if not title:
        return None

    journal_el = article.find(".//Journal/Title")
    journal = (journal_el.text or "").strip() if journal_el is not None else ""
    # Fallback: ISOAbbreviation（短名）
    if not journal:
        iso_el = article.find(".//Journal/ISOAbbreviation")
        if iso_el is not None and iso_el.text:
            journal = iso_el.text.strip()

    issn_el = article.find(".//Journal/ISSN")
    issn = (issn_el.text or "").strip() if issn_el is not None else ""

    # Abstract — 多段 <AbstractText Label="BACKGROUND"> 等需要拼接，保留 label 前綴
    abstract_parts: list[str] = []
    for abs_text in article.findall(".//Abstract/AbstractText"):
        text = _text_with_inline(abs_text).strip()
        if not text:
            continue
        label = abs_text.get("Label")
        if label:
            abstract_parts.append(f"{label}: {text}")
        else:
            abstract_parts.append(text)
    abstract = "\n".join(abstract_parts)

    # Pub date — 取 <PubDate>，組成 "YYYY MMM DD" 風格（與 RSS path 對齊）
    pub_date = ""
    pubdate_el = article.find(".//Journal/JournalIssue/PubDate")
    if pubdate_el is not None:
        year = (pubdate_el.findtext("Year") or "").strip()
        month = (pubdate_el.findtext("Month") or "").strip()
        day = (pubdate_el.findtext("Day") or "").strip()
        medline = (pubdate_el.findtext("MedlineDate") or "").strip()
        if year:
            pub_date = " ".join(filter(None, [year, month, day]))
        elif medline:
            pub_date = medline

    # Authors — "First Last, First Last, ..." 與 RSS path 對齊
    author_names: list[str] = []
    for author in article.findall(".//AuthorList/Author"):
        last = (author.findtext("LastName") or "").strip()
        fore = (author.findtext("ForeName") or "").strip()
        collective = (author.findtext("CollectiveName") or "").strip()
        if last and fore:
            author_names.append(f"{fore} {last}")
        elif last:
            author_names.append(last)
        elif collective:
            author_names.append(collective)
    authors = ", ".join(author_names)

    return {
        "pmid": pmid,
        "title": title,
        "journal": journal,
        "abstract": abstract,
        "pub_date": pub_date,
        "authors": authors,
        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        "issn": issn,
    }


def _text_with_inline(el: ET.Element) -> str:
    """串接 element text + 所有 inline child（保留 <i> / <sub> 等的內文，去 tag）。"""
    parts: list[str] = []
    if el.text:
        parts.append(el.text)
    for child in el:
        parts.append(_text_with_inline(child))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)
