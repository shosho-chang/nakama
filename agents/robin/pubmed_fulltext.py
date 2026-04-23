"""PubMed 全文下載：合法 OA 來源（PMC + Unpaywall）。

流程：
1. 從 NCBI efetch XML 抓 DOI + PMCID
2. 若有 PMCID：嘗試從 PMC 直接下載 PDF
3. 否則若有 DOI：查 Unpaywall OA 版本
4. 都拿不到：needs_manual（有 DOI，可手動取得）或 not_found

所有下載存到 vault 的 KB/Attachments/pubmed/{pmid}.pdf。
不處理付費或非法（sci-hub 等）來源。
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
_PMC_PDF_URL_TMPL = "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC{pmcid}/pdf/"
_EUROPE_PMC_PDF_URL_TMPL = "https://europepmc.org/articles/PMC{pmcid}?pdf=render"
_UNPAYWALL_URL_TMPL = "https://api.unpaywall.org/v2/{doi}"


Status = Literal["oa_downloaded", "oa_html", "needs_manual", "not_found"]


class FullTextResult(TypedDict, total=False):
    status: Status
    source: Optional[str]
    pdf_relpath: Optional[str]
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
    """嘗試下載一篇 PubMed 論文全文。

    Args:
        pmid: PubMed ID
        attachments_abs_dir: PDF 存放的絕對路徑（會自動 mkdir）
        vault_relative_prefix: vault 相對路徑前綴，回傳時會組成完整 relpath
        email: 聯絡 email（NCBI / Unpaywall 都要求帶）
        ncbi_api_key: 可選，帶上 NCBI API key 可把 rate limit 從 3/s 提升到 10/s
        timeout: 每個 HTTP 請求的秒數上限
    """
    doi, pmcid = _lookup_ids(pmid, email=email, api_key=ncbi_api_key, timeout=timeout)

    if pmcid:
        pdf_relpath = _download_pmc_pdf(
            pmcid=pmcid,
            pmid=pmid,
            attachments_abs_dir=attachments_abs_dir,
            vault_relative_prefix=vault_relative_prefix,
            email=email,
            timeout=timeout,
        )
        if pdf_relpath:
            _logger.info(f"[fulltext] PMID {pmid} via PMC{pmcid}")
            return {
                "status": "oa_downloaded",
                "source": "pmc",
                "pdf_relpath": pdf_relpath,
                "doi": doi,
                "note": f"PMC{pmcid}",
            }
        # PMC NCBI /pdf/ endpoint 2024 後常回 HTML landing page；Europe PMC mirror
        # 仍直接回 application/pdf，不需過 publisher IdP cookie flow。
        pdf_relpath = _download_europe_pmc_pdf(
            pmcid=pmcid,
            pmid=pmid,
            attachments_abs_dir=attachments_abs_dir,
            vault_relative_prefix=vault_relative_prefix,
            email=email,
            timeout=timeout,
        )
        if pdf_relpath:
            _logger.info(f"[fulltext] PMID {pmid} via Europe PMC PMC{pmcid}")
            return {
                "status": "oa_downloaded",
                "source": "europe_pmc",
                "pdf_relpath": pdf_relpath,
                "doi": doi,
                "note": f"Europe PMC mirror of PMC{pmcid}",
            }
        _logger.debug(
            f"[fulltext] PMID {pmid} PMC{pmcid} 兩條 PMC 路徑都失敗，fallback 試 Unpaywall"
        )

    if doi:
        pdf_url = _query_unpaywall(doi, email=email, timeout=timeout)
        if pdf_url:
            pdf_relpath = _download_pdf(
                url=pdf_url,
                pmid=pmid,
                attachments_abs_dir=attachments_abs_dir,
                vault_relative_prefix=vault_relative_prefix,
                email=email,
                timeout=timeout,
            )
            if pdf_relpath:
                _logger.info(f"[fulltext] PMID {pmid} via Unpaywall")
                return {
                    "status": "oa_downloaded",
                    "source": "unpaywall",
                    "pdf_relpath": pdf_relpath,
                    "doi": doi,
                    "note": "via Unpaywall",
                }

    # 第 5 層 fallback：透過 NCBI elink prlinks 找 publisher 的 Free HTML 全文，
    # 用 shared.web_scraper 抓 → 圖片本地化 → 可選附帶 PDF。
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
            "pdf_relpath": publisher.get("pdf_relpath"),
            "html_relpath": publisher["html_relpath"],
            "publisher_url": publisher["publisher_url"],
            "doi": doi,
            "note": publisher["note"],
        }

    if doi:
        _logger.info(f"[fulltext] PMID {pmid} 非 OA，需手動取得")
        return {
            "status": "needs_manual",
            "source": None,
            "pdf_relpath": None,
            "doi": doi,
            "note": "非 OA 來源，請用 DOI 手動取得全文",
        }
    # 到這裡表示無 DOI；pmcid 可能是 None（真的沒識別碼）或有值但兩條 PMC 路徑都失敗
    if pmcid:
        note = (
            f"PMC{pmcid} 下載失敗（PMC NCBI + Europe PMC 皆不可用），可手動至 PubMed Central 取得"
        )
        _logger.info(f"[fulltext] PMID {pmid} PMC{pmcid} 下載失敗且無 DOI")
    else:
        note = "PubMed 無 DOI / PMCID，無法取得全文"
        _logger.info(f"[fulltext] PMID {pmid} 無 DOI 無 PMCID")
    return {
        "status": "not_found",
        "source": None,
        "pdf_relpath": None,
        "doi": None,
        "note": note,
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


def _download_pmc_pdf(
    *,
    pmcid: str,
    pmid: str,
    attachments_abs_dir: Path,
    vault_relative_prefix: str,
    email: str,
    timeout: float = 30.0,
) -> Optional[str]:
    """嘗試從 PMC 直接下載 PDF。成功回傳 vault-relative 路徑，否則 None。"""
    url = _PMC_PDF_URL_TMPL.format(pmcid=pmcid)
    return _download_pdf(
        url=url,
        pmid=pmid,
        attachments_abs_dir=attachments_abs_dir,
        vault_relative_prefix=vault_relative_prefix,
        email=email,
        timeout=timeout,
    )


def _download_europe_pmc_pdf(
    *,
    pmcid: str,
    pmid: str,
    attachments_abs_dir: Path,
    vault_relative_prefix: str,
    email: str,
    timeout: float = 30.0,
) -> Optional[str]:
    """嘗試從 Europe PMC 鏡像下載 PDF。成功回傳 vault-relative 路徑，否則 None。"""
    url = _EUROPE_PMC_PDF_URL_TMPL.format(pmcid=pmcid)
    return _download_pdf(
        url=url,
        pmid=pmid,
        attachments_abs_dir=attachments_abs_dir,
        vault_relative_prefix=vault_relative_prefix,
        email=email,
        timeout=timeout,
    )


def _query_unpaywall(
    doi: str,
    *,
    email: str,
    timeout: float = 30.0,
) -> Optional[str]:
    """查 Unpaywall API，回傳 best OA PDF URL（沒有就 None）。"""
    url = _UNPAYWALL_URL_TMPL.format(doi=doi)
    try:
        r = httpx.get(
            url,
            params={"email": email},
            headers={"User-Agent": _user_agent(email)},
            timeout=timeout,
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
    except httpx.HTTPError as e:
        _logger.warning(f"[fulltext] Unpaywall DOI {doi} 失敗：{e}")
        return None

    try:
        data = r.json()
    except ValueError:
        return None
    best = data.get("best_oa_location") or {}
    return best.get("url_for_pdf") or None


def _download_pdf(
    *,
    url: str,
    pmid: str,
    attachments_abs_dir: Path,
    vault_relative_prefix: str,
    email: str,
    timeout: float = 30.0,
) -> Optional[str]:
    """下載 PDF 存到 attachments_abs_dir/{pmid}.pdf，回傳 vault-relative 路徑。

    若 URL 回傳非 PDF（HTML landing page 等）則丟棄，回傳 None。
    已存在的 PDF 會直接回傳現有路徑（不重複下載）。
    """
    attachments_abs_dir.mkdir(parents=True, exist_ok=True)
    dest = attachments_abs_dir / f"{pmid}.pdf"
    relpath = f"{vault_relative_prefix}/{pmid}.pdf"

    if dest.exists() and dest.stat().st_size > 1024:
        return relpath

    try:
        with httpx.stream(
            "GET",
            url,
            headers={"User-Agent": _user_agent(email)},
            timeout=timeout,
            follow_redirects=True,
        ) as r:
            if r.status_code != 200:
                _logger.warning(f"[fulltext] PDF download {url} → HTTP {r.status_code}")
                return None
            ctype = r.headers.get("content-type", "").lower()
            if "pdf" not in ctype:
                _logger.warning(f"[fulltext] {url} 回傳 {ctype}，不是 PDF")
                return None
            with open(dest, "wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)
    except httpx.HTTPError as e:
        _logger.warning(f"[fulltext] PDF download {url} 失敗：{e}")
        if dest.exists():
            dest.unlink()
        return None

    if dest.stat().st_size < 1024:
        dest.unlink()
        return None

    return relpath
