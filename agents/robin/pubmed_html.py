"""PubMed 全文 — publisher HTML fallback（第 5 層）。

當 PMC + Europe PMC + Unpaywall PDF 都拿不到時，透過 NCBI `elink cmd=prlinks`
查 publisher 網站的 Free 標記 URL，用 `shared.web_scraper.scrape_url()`
抓 HTML 轉 markdown，圖片本地化，並順手下載 publisher 頁面裡的 PDF link。

保守策略：
- 只跟 elink `Attribute` 含 "Free" 的連結
- 長度門檻 `_MIN_HTML_LENGTH` 字元
- paywall 關鍵字黑名單早退

輸出：
- `{attachments_abs_dir}/{pmid}.md` — raw markdown（圖片 URL 已 rewrite）
- `{attachments_abs_dir}/{pmid}/img-N.{ext}` — publisher 圖片
- `{attachments_abs_dir}/{pmid}.pdf` — 若 publisher 頁有 PDF link（optional）
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx

from shared.image_fetcher import download_markdown_images
from shared.log import get_logger
from shared.web_scraper import scrape_url

_logger = get_logger("nakama.robin.html")

_NCBI_ELINK = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi"

_MIN_HTML_LENGTH = 1500  # 少於此字元數視為沒抓到真正的全文

_PAYWALL_KEYWORDS = (
    "subscribe to continue",
    "purchase access",
    "sign in to read",
    "create an account to continue",
    "become a member",
    "subscription required",
    "please sign in",
    "institutional login",
)

# Match markdown PDF links: [label](https://...pdf[?query])
_PDF_LINK_RE = re.compile(r"\[([^\]]*)\]\((https?://[^\s)]+?\.pdf\b[^\s)]*)\)", re.IGNORECASE)


class PublisherResult(dict):
    """Lightweight dict wrapper for type hints only."""


def fetch_publisher_html(
    pmid: str,
    *,
    doi: Optional[str],
    attachments_abs_dir: Path,
    vault_relative_prefix: str,
    email: str,
    ncbi_api_key: Optional[str] = None,
    timeout: float = 30.0,
) -> Optional[dict]:
    """第 5 層 fallback：透過 publisher 網頁抓 HTML 全文。

    Args:
        pmid: PubMed ID
        doi: 已知的 DOI（上游傳入，此函式不重覆查 efetch）
        attachments_abs_dir: `KB/Attachments/pubmed/` 絕對路徑
        vault_relative_prefix: 對應的 vault 相對路徑前綴
        email: 聯絡 email（elink + scrape user-agent 用）
        ncbi_api_key: 可選 NCBI API key
        timeout: HTTP timeout（秒）

    Returns:
        成功時回 dict，含：
            source="publisher", html_relpath, pdf_relpath, publisher_url,
            image_count, note
        失敗（無 Free URL、scrape 失敗、長度不足、paywall）則回 None，
        讓上游繼續下一層 fallback。
    """
    publisher_url = _query_elink_free_url(
        pmid,
        email=email,
        api_key=ncbi_api_key,
        timeout=timeout,
    )
    if not publisher_url:
        _logger.debug(f"[html] PMID {pmid} elink 無 Free publisher link")
        return None

    try:
        raw_md = scrape_url(publisher_url)
    except Exception as e:
        _logger.warning(f"[html] PMID {pmid} scrape {publisher_url} 失敗：{e}")
        return None

    if not raw_md or len(raw_md) < _MIN_HTML_LENGTH:
        _logger.warning(
            f"[html] PMID {pmid} scrape 結果過短（{len(raw_md) if raw_md else 0} "
            f"< {_MIN_HTML_LENGTH}），疑似 abstract-only 或 paywall"
        )
        return None

    lower = raw_md.lower()
    for kw in _PAYWALL_KEYWORDS:
        if kw in lower:
            _logger.warning(f"[html] PMID {pmid} 命中 paywall 關鍵字 '{kw}'，放棄")
            return None

    # 圖片本地化 → rewrite raw_md
    per_pmid_abs_dir = attachments_abs_dir / pmid
    per_pmid_rel_prefix = f"{vault_relative_prefix.rstrip('/')}/{pmid}"
    rewritten_md, saved_images = download_markdown_images(
        raw_md,
        dest_dir=per_pmid_abs_dir,
        vault_relative_prefix=per_pmid_rel_prefix,
        base_url=publisher_url,
    )

    # 掃 PDF link（用 rewrite 前的 raw_md 比較穩，避免 scrape_url 已過濾 <a>）
    pdf_relpath = _try_download_publisher_pdf(
        raw_md,
        base_url=publisher_url,
        pmid=pmid,
        attachments_abs_dir=attachments_abs_dir,
        vault_relative_prefix=vault_relative_prefix,
        email=email,
        timeout=timeout,
    )

    # 寫 raw markdown（rewrite 後）到 attachments
    attachments_abs_dir.mkdir(parents=True, exist_ok=True)
    md_dest = attachments_abs_dir / f"{pmid}.md"
    md_dest.write_text(rewritten_md, encoding="utf-8")
    html_relpath = f"{vault_relative_prefix.rstrip('/')}/{pmid}.md"

    publisher_domain = urlparse(publisher_url).netloc or "publisher"
    note = f"Publisher HTML from {publisher_domain}"
    if pdf_relpath:
        note += "（含 PDF）"
    if saved_images:
        note += f"；{len(saved_images)} 張圖"

    _logger.info(
        f"[html] PMID {pmid} via publisher {publisher_domain} "
        f"(md {len(rewritten_md)} chars, {len(saved_images)} imgs, "
        f"pdf={'y' if pdf_relpath else 'n'})"
    )

    return {
        "source": "publisher",
        "html_relpath": html_relpath,
        "pdf_relpath": pdf_relpath,
        "publisher_url": publisher_url,
        "image_count": len(saved_images),
        "note": note,
    }


def _query_elink_free_url(
    pmid: str,
    *,
    email: str,
    api_key: Optional[str] = None,
    timeout: float = 30.0,
) -> Optional[str]:
    """查 NCBI elink prlinks，回第一個 Free 標記的 publisher URL。

    無結果、沒 Free 標記、或 JSON 格式不合預期都回 None。
    """
    params: dict[str, str] = {
        "dbfrom": "pubmed",
        "cmd": "prlinks",
        "id": pmid,
        "retmode": "json",
    }
    if api_key:
        params["api_key"] = api_key

    try:
        r = httpx.get(
            _NCBI_ELINK,
            params=params,
            headers={"User-Agent": f"Nakama-Robin/1.0 (+{email})"},
            timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        _logger.warning(f"[html] elink PMID {pmid} 失敗：{e}")
        return None

    for linkset in data.get("linksets", []) or []:
        for idurl in linkset.get("idurllist", []) or []:
            for obj in idurl.get("objurls", []) or []:
                if not _is_free(obj):
                    continue
                url = _extract_url(obj)
                if url and url.startswith(("http://", "https://")):
                    return url
    return None


def _is_free(objurl: dict) -> bool:
    """判斷 elink objurl 是否帶 Free 標記。"""
    attrs = objurl.get("attributes") or objurl.get("attribute") or []
    if isinstance(attrs, str):
        attrs = [attrs]
    for a in attrs:
        if isinstance(a, str) and "free" in a.lower():
            return True
    return False


def _extract_url(objurl: dict) -> Optional[str]:
    """從 elink objurl 結構裡挖 URL，容忍 dict 或 str 兩種格式。"""
    url_field = objurl.get("url")
    if isinstance(url_field, dict):
        return url_field.get("value")
    if isinstance(url_field, str):
        return url_field
    return None


def _try_download_publisher_pdf(
    md_text: str,
    *,
    base_url: str,
    pmid: str,
    attachments_abs_dir: Path,
    vault_relative_prefix: str,
    email: str,
    timeout: float,
) -> Optional[str]:
    """掃 md_text 找 PDF link，嘗試下載第一個 content-type=pdf 的連結。"""
    for match in _PDF_LINK_RE.finditer(md_text):
        pdf_url = match.group(2)
        if not pdf_url.startswith(("http://", "https://")):
            continue
        relpath = _stream_pdf(
            pdf_url,
            pmid=pmid,
            attachments_abs_dir=attachments_abs_dir,
            vault_relative_prefix=vault_relative_prefix,
            email=email,
            timeout=timeout,
        )
        if relpath:
            _logger.info(f"[html] PMID {pmid} publisher PDF 下載成功：{pdf_url}")
            return relpath
    return None


def _stream_pdf(
    url: str,
    *,
    pmid: str,
    attachments_abs_dir: Path,
    vault_relative_prefix: str,
    email: str,
    timeout: float,
) -> Optional[str]:
    """下載單個 PDF 到 {pmid}.pdf，驗 content-type=application/pdf。"""
    attachments_abs_dir.mkdir(parents=True, exist_ok=True)
    dest = attachments_abs_dir / f"{pmid}.pdf"
    relpath = f"{vault_relative_prefix.rstrip('/')}/{pmid}.pdf"

    if dest.exists() and dest.stat().st_size > 1024:
        return relpath

    try:
        with httpx.stream(
            "GET",
            url,
            headers={"User-Agent": f"Nakama-Robin/1.0 (+{email})"},
            timeout=timeout,
            follow_redirects=True,
        ) as r:
            if r.status_code != 200:
                _logger.debug(f"[html] publisher PDF {url} → HTTP {r.status_code}")
                return None
            ctype = r.headers.get("content-type", "").lower()
            if "pdf" not in ctype:
                _logger.debug(f"[html] publisher PDF {url} 回傳 {ctype}，不是 PDF")
                return None
            with open(dest, "wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)
    except httpx.HTTPError as e:
        _logger.debug(f"[html] publisher PDF {url} 失敗：{e}")
        if dest.exists():
            dest.unlink()
        return None

    if dest.stat().st_size < 1024:
        dest.unlink()
        return None
    return relpath
