"""PubMed 全文：publisher HTML fallback only。

歷史上有 PMC / Europe PMC / Unpaywall 三層 PDF 下載 + publisher HTML 第四層，
PDF 全部走 ``shared.pdf_parser.parse_pdf`` 解析。整套 PDF 流程於 2026-05-23
拔除（修修決策：日常工作流改用 news-coo 抓 publisher HTML，不再經 PubMed PDF
路徑）。本模組僅保留 publisher HTML 嘗試。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, Optional, TypedDict

import httpx

from agents.robin.pubmed_html import fetch_publisher_html
from shared.log import get_logger

_logger = get_logger("nakama.robin.fulltext")

_NCBI_EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"


Status = Literal["oa_html", "needs_manual", "not_found"]


class FullTextResult(TypedDict, total=False):
    status: Status
    source: Optional[str]
    html_relpath: Optional[str]
    publisher_url: Optional[str]
    doi: Optional[str]
    note: str


def fetch_fulltext(
    pmid: str,
    *,
    attachments_abs_dir: Path,
    vault_relative_prefix: str,
    email: str,
    ncbi_api_key: Optional[str] = None,
    timeout: float = 30.0,
) -> FullTextResult:
    """嘗試取得一篇 PubMed 論文 publisher HTML 全文。"""
    doi, _ = _lookup_ids(pmid, email=email, api_key=ncbi_api_key, timeout=timeout)

    publisher = fetch_publisher_html(
        pmid,
        doi=doi,
        attachments_abs_dir=attachments_abs_dir,
        vault_relative_prefix=vault_relative_prefix,
        email=email,
        ncbi_api_key=ncbi_api_key,
        timeout=timeout,
    )
    if publisher:
        return {
            "status": "oa_html",
            "source": publisher["source"],
            "html_relpath": publisher["html_relpath"],
            "publisher_url": publisher["publisher_url"],
            "doi": doi,
            "note": publisher["note"],
        }

    if doi:
        _logger.info(f"[fulltext] PMID {pmid} 非 OA HTML，需手動取得")
        return {
            "status": "needs_manual",
            "source": None,
            "doi": doi,
            "note": "publisher HTML 不可得，請用 DOI 手動取得全文",
        }
    _logger.info(f"[fulltext] PMID {pmid} 無 DOI、無 publisher HTML")
    return {
        "status": "not_found",
        "source": None,
        "doi": None,
        "note": "PubMed 無 DOI 且無 publisher HTML",
    }


def _user_agent(email: str) -> str:
    return f"Nakama-Robin/1.0 (+{email})"


def _lookup_ids(
    pmid: str,
    *,
    email: str,
    api_key: Optional[str] = None,
    timeout: float = 30.0,
) -> tuple[Optional[str], Optional[str]]:
    """從 NCBI efetch XML 抓指定 PMID 的 DOI 和 PMCID。

    回傳 (doi, pmcid_numeric)；任一可能為 None。PMCID 已去除 "PMC" 前綴。
    """
    params: dict[str, str] = {"db": "pubmed", "id": pmid, "rettype": "xml"}
    if api_key:
        params["api_key"] = api_key

    try:
        r = httpx.get(
            _NCBI_EFETCH,
            params=params,
            headers={"User-Agent": _user_agent(email)},
            timeout=timeout,
        )
        r.raise_for_status()
    except httpx.HTTPError as e:
        _logger.warning(f"[fulltext] efetch PMID {pmid} 失敗：{e}")
        return (None, None)

    xml = r.text
    doi = _extract_article_id(xml, "doi")
    pmcid = _extract_article_id(xml, "pmc")
    if pmcid and pmcid.upper().startswith("PMC"):
        pmcid = pmcid[3:]
    return (doi, pmcid)


_ARTICLE_ID_RE_TMPL = r'<ArticleId IdType="{idtype}">([^<]+)</ArticleId>'


def _extract_article_id(xml: str, idtype: str) -> Optional[str]:
    """從 PubMed XML 抓 ArticleId（idtype 例如 'doi' / 'pmc'）。"""
    pattern = _ARTICLE_ID_RE_TMPL.format(idtype=idtype)
    m = re.search(pattern, xml)
    return m.group(1).strip() if m else None
