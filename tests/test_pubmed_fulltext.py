"""Unit tests for Robin PubMed full-text fetcher.

所有 HTTP 都 mock，不打真的 API。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx

from agents.robin import pubmed_fulltext
from agents.robin.pubmed_fulltext import (
    _extract_article_id,
    _lookup_ids,
    _query_unpaywall,
    fetch_fulltext,
)

PUBMED_XML_WITH_BOTH = """<?xml version="1.0" ?>
<PubmedArticleSet>
  <PubmedArticle>
    <PubmedData>
      <ArticleIdList>
        <ArticleId IdType="pubmed">12345</ArticleId>
        <ArticleId IdType="doi">10.1234/example</ArticleId>
        <ArticleId IdType="pmc">PMC98765</ArticleId>
      </ArticleIdList>
    </PubmedData>
  </PubmedArticle>
</PubmedArticleSet>"""

PUBMED_XML_ONLY_DOI = """<?xml version="1.0" ?>
<ArticleIdList>
  <ArticleId IdType="pubmed">12345</ArticleId>
  <ArticleId IdType="doi">10.1234/only-doi</ArticleId>
</ArticleIdList>"""

PUBMED_XML_NOTHING = """<?xml version="1.0" ?>
<ArticleIdList>
  <ArticleId IdType="pubmed">12345</ArticleId>
</ArticleIdList>"""


class TestExtractArticleId:
    def test_finds_doi(self):
        assert _extract_article_id(PUBMED_XML_WITH_BOTH, "doi") == "10.1234/example"

    def test_finds_pmc(self):
        assert _extract_article_id(PUBMED_XML_WITH_BOTH, "pmc") == "PMC98765"

    def test_returns_none_when_missing(self):
        assert _extract_article_id(PUBMED_XML_ONLY_DOI, "pmc") is None


class TestLookupIds:
    def test_returns_doi_and_pmcid_stripped(self):
        resp = MagicMock(spec=httpx.Response)
        resp.text = PUBMED_XML_WITH_BOTH
        resp.raise_for_status = MagicMock()
        with patch.object(pubmed_fulltext.httpx, "get", return_value=resp):
            doi, pmcid = _lookup_ids("12345", email="a@b.com")
        assert doi == "10.1234/example"
        assert pmcid == "98765"  # PMC prefix stripped

    def test_only_doi(self):
        resp = MagicMock(spec=httpx.Response)
        resp.text = PUBMED_XML_ONLY_DOI
        resp.raise_for_status = MagicMock()
        with patch.object(pubmed_fulltext.httpx, "get", return_value=resp):
            doi, pmcid = _lookup_ids("12345", email="a@b.com")
        assert doi == "10.1234/only-doi"
        assert pmcid is None

    def test_http_error_returns_both_none(self):
        with patch.object(
            pubmed_fulltext.httpx,
            "get",
            side_effect=httpx.ConnectError("no net"),
        ):
            doi, pmcid = _lookup_ids("12345", email="a@b.com")
        assert doi is None
        assert pmcid is None


class TestQueryUnpaywall:
    def test_returns_pdf_url_when_oa(self):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(
            return_value={
                "best_oa_location": {
                    "url_for_pdf": "https://example.com/paper.pdf",
                }
            }
        )
        with patch.object(pubmed_fulltext.httpx, "get", return_value=resp):
            url = _query_unpaywall("10.1234/x", email="a@b.com")
        assert url == "https://example.com/paper.pdf"

    def test_returns_none_when_404(self):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 404
        with patch.object(pubmed_fulltext.httpx, "get", return_value=resp):
            url = _query_unpaywall("10.1234/unknown", email="a@b.com")
        assert url is None

    def test_returns_none_when_no_best_location(self):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"best_oa_location": None})
        with patch.object(pubmed_fulltext.httpx, "get", return_value=resp):
            url = _query_unpaywall("10.1234/x", email="a@b.com")
        assert url is None


class TestFetchFulltext:
    """高階 orchestrator 測試 — 驗證不同情境下回傳結構正確。"""

    def test_pmc_success(self, tmp_path: Path):
        """有 PMCID 且 PMC 下載成功 → oa_downloaded via pmc。"""
        # 寫一個看起來像 PDF 的檔案，模擬下載
        pdf_bytes = b"%PDF-1.4\n" + b"x" * 2000

        with (
            patch.object(
                pubmed_fulltext,
                "_lookup_ids",
                return_value=("10.1/abc", "98765"),
            ),
            patch.object(
                pubmed_fulltext,
                "_download_pdf",
                side_effect=_fake_download_pdf(pdf_bytes, success=True),
            ),
        ):
            result = fetch_fulltext(
                "12345",
                attachments_abs_dir=tmp_path,
                vault_relative_prefix="KB/Attachments/pubmed",
                email="a@b.com",
            )

        assert result["status"] == "oa_downloaded"
        assert result["source"] == "pmc"
        assert result["pdf_relpath"] == "KB/Attachments/pubmed/12345.pdf"
        assert result["doi"] == "10.1/abc"

    def test_pmc_ncbi_fails_europe_pmc_succeeds(self, tmp_path: Path):
        """PMC NCBI 回 HTML landing page 失敗，Europe PMC 鏡像接手。"""
        call_count = {"pmc_ncbi": 0, "europe_pmc": 0, "other": 0}

        def _fake_download(**kwargs):
            url = kwargs["url"]
            if "europepmc.org" in url:
                call_count["europe_pmc"] += 1
                return f"{kwargs['vault_relative_prefix']}/{kwargs['pmid']}.pdf"
            if "ncbi.nlm.nih.gov" in url:
                call_count["pmc_ncbi"] += 1
                return None
            call_count["other"] += 1
            return None

        with (
            patch.object(
                pubmed_fulltext,
                "_lookup_ids",
                return_value=("10.1/abc", "98765"),
            ),
            patch.object(
                pubmed_fulltext,
                "_download_pdf",
                side_effect=_fake_download,
            ),
        ):
            result = fetch_fulltext(
                "12345",
                attachments_abs_dir=tmp_path,
                vault_relative_prefix="KB/Attachments/pubmed",
                email="a@b.com",
            )

        assert result["status"] == "oa_downloaded"
        assert result["source"] == "europe_pmc"
        assert call_count["pmc_ncbi"] == 1
        assert call_count["europe_pmc"] == 1
        assert call_count["other"] == 0  # 不該打到 Unpaywall

    def test_both_pmc_fail_unpaywall_succeeds(self, tmp_path: Path):
        """PMC NCBI + Europe PMC 兩條都掛，Unpaywall 接手。"""
        call_count = {"pmc_ncbi": 0, "europe_pmc": 0, "unpaywall": 0}

        def _fake_download(**kwargs):
            url = kwargs["url"]
            if "europepmc.org" in url:
                call_count["europe_pmc"] += 1
                return None
            if "ncbi.nlm.nih.gov" in url:
                call_count["pmc_ncbi"] += 1
                return None
            call_count["unpaywall"] += 1
            return f"{kwargs['vault_relative_prefix']}/{kwargs['pmid']}.pdf"

        with (
            patch.object(
                pubmed_fulltext,
                "_lookup_ids",
                return_value=("10.1/abc", "98765"),
            ),
            patch.object(
                pubmed_fulltext,
                "_download_pdf",
                side_effect=_fake_download,
            ),
            patch.object(
                pubmed_fulltext,
                "_query_unpaywall",
                return_value="https://example.com/oa.pdf",
            ),
        ):
            result = fetch_fulltext(
                "12345",
                attachments_abs_dir=tmp_path,
                vault_relative_prefix="KB/Attachments/pubmed",
                email="a@b.com",
            )

        assert result["status"] == "oa_downloaded"
        assert result["source"] == "unpaywall"
        assert call_count["pmc_ncbi"] == 1
        assert call_count["europe_pmc"] == 1
        assert call_count["unpaywall"] == 1

    def test_needs_manual_when_doi_exists_no_oa(self, tmp_path: Path):
        """有 DOI 但沒 PMC、Unpaywall 也沒 OA → needs_manual。"""
        with (
            patch.object(
                pubmed_fulltext,
                "_lookup_ids",
                return_value=("10.1/abc", None),
            ),
            patch.object(
                pubmed_fulltext,
                "_query_unpaywall",
                return_value=None,
            ),
        ):
            result = fetch_fulltext(
                "12345",
                attachments_abs_dir=tmp_path,
                vault_relative_prefix="KB/Attachments/pubmed",
                email="a@b.com",
            )

        assert result["status"] == "needs_manual"
        assert result["source"] is None
        assert result["pdf_relpath"] is None
        assert result["doi"] == "10.1/abc"

    def test_not_found_when_no_ids(self, tmp_path: Path):
        """連 DOI 都沒有 → not_found。"""
        with patch.object(
            pubmed_fulltext,
            "_lookup_ids",
            return_value=(None, None),
        ):
            result = fetch_fulltext(
                "12345",
                attachments_abs_dir=tmp_path,
                vault_relative_prefix="KB/Attachments/pubmed",
                email="a@b.com",
            )

        assert result["status"] == "not_found"
        assert result["doi"] is None
        assert "無 DOI / PMCID" in result["note"]

    def test_not_found_note_mentions_pmc_when_both_pmc_fail_no_doi(self, tmp_path: Path):
        """有 PMCID 但兩條 PMC 都失敗、無 DOI → not_found 且 note 要誠實交代，
        不能印「無 DOI / PMCID」這種謊。"""
        with (
            patch.object(
                pubmed_fulltext,
                "_lookup_ids",
                return_value=(None, "98765"),
            ),
            patch.object(
                pubmed_fulltext,
                "_download_pdf",
                return_value=None,
            ),
        ):
            result = fetch_fulltext(
                "12345",
                attachments_abs_dir=tmp_path,
                vault_relative_prefix="KB/Attachments/pubmed",
                email="a@b.com",
            )

        assert result["status"] == "not_found"
        assert result["doi"] is None
        # note 必須點出「PMC 下載失敗」，不能騙說「無 DOI / PMCID」
        assert "PMC98765" in result["note"]
        assert "下載失敗" in result["note"]


def _fake_download_pdf(pdf_bytes: bytes, success: bool):
    """Helper：產生一個模擬 _download_pdf 行為的 side_effect。"""

    def _fake(**kwargs):
        if not success:
            return None
        dest = kwargs["attachments_abs_dir"] / f"{kwargs['pmid']}.pdf"
        kwargs["attachments_abs_dir"].mkdir(parents=True, exist_ok=True)
        dest.write_bytes(pdf_bytes)
        return f"{kwargs['vault_relative_prefix']}/{kwargs['pmid']}.pdf"

    return _fake
