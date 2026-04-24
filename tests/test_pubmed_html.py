"""Tests for agents/robin/pubmed_html.py — publisher HTML fallback.

No network access; httpx and scrape_url both mocked.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx

from agents.robin import pubmed_html
from agents.robin.pubmed_html import (
    _is_free,
    _query_elink_free_url,
    fetch_publisher_html,
)

# Sample elink JSON response (shape from NCBI E-utils 2026-04)
ELINK_JSON_FREE = {
    "header": {"type": "elink", "version": "0.3"},
    "linksets": [
        {
            "dbfrom": "pubmed",
            "idurllist": [
                {
                    "id": "42020128",
                    "objurls": [
                        {
                            "url": {
                                "value": "https://bmjopen.bmj.com/lookup/pmidlookup?pmid=42020128"
                            },
                            "iconurl": {"value": "https://example/icon.png"},
                            "subjecttypes": ["publishers/providers"],
                            "attributes": ["Free"],
                            "provider": {"name": "HighWire"},
                        }
                    ],
                }
            ],
        }
    ],
}

ELINK_JSON_NO_FREE = {
    "linksets": [
        {
            "dbfrom": "pubmed",
            "idurllist": [
                {
                    "id": "11111",
                    "objurls": [
                        {
                            "url": {"value": "https://sd.example.com/paywall"},
                            "attributes": ["Subscription"],
                        }
                    ],
                }
            ],
        }
    ],
}

ELINK_JSON_EMPTY = {"linksets": []}

PUBLISHER_MD_OK = (
    "# Lean Mass Preservation in Semaglutide Users\n\n"
    + "Abstract\n\n"
    + ("This is the body of the full-text paper. " * 80)  # ~3000+ chars
    + "\n\n![Figure 1](https://cdn.bmj.com/fig1.png)\n\n"
    + "Download the [full PDF](https://bmjopen.bmj.com/content/16/4/e116911.full.pdf)\n"
)

PUBLISHER_MD_PAYWALL = (
    "# Lean Mass Preservation in Semaglutide Users\n\n"
    + "Sign in to read the full article.\n"
    + ("This abstract is moderately long. " * 60)
)

PUBLISHER_MD_SHORT = "# Tiny\n\nJust an abstract, nothing more."


# ---------------------------------------------------------------------------
# _is_free
# ---------------------------------------------------------------------------


class TestIsFree:
    def test_attributes_list_with_free(self):
        assert _is_free({"attributes": ["Free"]}) is True

    def test_attributes_case_insensitive(self):
        assert _is_free({"attributes": ["free full text"]}) is True

    def test_singular_attribute_str(self):
        assert _is_free({"attribute": "Free"}) is True

    def test_no_free_marker(self):
        assert _is_free({"attributes": ["Subscription"]}) is False

    def test_missing_attributes(self):
        assert _is_free({}) is False


# ---------------------------------------------------------------------------
# _query_elink_free_url
# ---------------------------------------------------------------------------


def _mock_elink_response(body: dict[str, Any]) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.json = MagicMock(return_value=body)
    resp.raise_for_status = MagicMock()
    return resp


class TestQueryElinkFreeUrl:
    def test_returns_free_url(self):
        with patch.object(
            pubmed_html.httpx, "get", return_value=_mock_elink_response(ELINK_JSON_FREE)
        ):
            url = _query_elink_free_url("42020128", email="a@b.com")
        assert url == "https://bmjopen.bmj.com/lookup/pmidlookup?pmid=42020128"

    def test_none_when_no_free(self):
        with patch.object(
            pubmed_html.httpx, "get", return_value=_mock_elink_response(ELINK_JSON_NO_FREE)
        ):
            url = _query_elink_free_url("11111", email="a@b.com")
        assert url is None

    def test_none_when_empty(self):
        with patch.object(
            pubmed_html.httpx, "get", return_value=_mock_elink_response(ELINK_JSON_EMPTY)
        ):
            url = _query_elink_free_url("0", email="a@b.com")
        assert url is None

    def test_none_on_http_error(self):
        with patch.object(pubmed_html.httpx, "get", side_effect=httpx.ConnectError("x")):
            url = _query_elink_free_url("42020128", email="a@b.com")
        assert url is None

    def test_none_on_bad_json(self):
        resp = MagicMock(spec=httpx.Response)
        resp.json = MagicMock(side_effect=ValueError("not json"))
        resp.raise_for_status = MagicMock()
        with patch.object(pubmed_html.httpx, "get", return_value=resp):
            url = _query_elink_free_url("42020128", email="a@b.com")
        assert url is None


# ---------------------------------------------------------------------------
# fetch_publisher_html
# ---------------------------------------------------------------------------


class TestFetchPublisherHtml:
    def test_happy_path_writes_md_and_downloads_images(self, tmp_path: Path):
        with (
            patch.object(
                pubmed_html,
                "_query_elink_free_url",
                return_value="https://bmjopen.bmj.com/content/16/4/e116911",
            ),
            patch.object(pubmed_html, "scrape_url", return_value=PUBLISHER_MD_OK),
            patch.object(
                pubmed_html,
                "download_markdown_images",
                return_value=(
                    PUBLISHER_MD_OK.replace(
                        "https://cdn.bmj.com/fig1.png",
                        "KB/Attachments/pubmed/42020128/img-1.png",
                    ),
                    ["KB/Attachments/pubmed/42020128/img-1.png"],
                ),
            ),
            patch.object(
                pubmed_html, "_stream_pdf", return_value="KB/Attachments/pubmed/42020128.pdf"
            ),
        ):
            result = fetch_publisher_html(
                "42020128",
                doi="10.1136/bmjopen-2026-116911",
                attachments_abs_dir=tmp_path,
                vault_relative_prefix="KB/Attachments/pubmed",
                email="a@b.com",
            )

        assert result is not None
        assert result["source"] == "publisher"
        assert result["html_relpath"] == "KB/Attachments/pubmed/42020128.md"
        assert result["pdf_relpath"] == "KB/Attachments/pubmed/42020128.pdf"
        assert result["publisher_url"] == "https://bmjopen.bmj.com/content/16/4/e116911"
        assert result["image_count"] == 1
        assert "bmjopen.bmj.com" in result["note"]

        md_file = tmp_path / "42020128.md"
        assert md_file.exists()
        assert "KB/Attachments/pubmed/42020128/img-1.png" in md_file.read_text(encoding="utf-8")

    def test_returns_none_when_no_free_link(self, tmp_path: Path):
        with patch.object(pubmed_html, "_query_elink_free_url", return_value=None):
            result = fetch_publisher_html(
                "11111",
                doi="10.1/x",
                attachments_abs_dir=tmp_path,
                vault_relative_prefix="KB/Attachments/pubmed",
                email="a@b.com",
            )
        assert result is None

    def test_returns_none_on_scrape_failure(self, tmp_path: Path):
        with (
            patch.object(pubmed_html, "_query_elink_free_url", return_value="https://p/x"),
            patch.object(pubmed_html, "scrape_url", side_effect=RuntimeError("scraper died")),
        ):
            result = fetch_publisher_html(
                "42020128",
                doi="10.1/x",
                attachments_abs_dir=tmp_path,
                vault_relative_prefix="KB/Attachments/pubmed",
                email="a@b.com",
            )
        assert result is None

    def test_returns_none_when_too_short(self, tmp_path: Path):
        with (
            patch.object(pubmed_html, "_query_elink_free_url", return_value="https://p/x"),
            patch.object(pubmed_html, "scrape_url", return_value=PUBLISHER_MD_SHORT),
        ):
            result = fetch_publisher_html(
                "42020128",
                doi="10.1/x",
                attachments_abs_dir=tmp_path,
                vault_relative_prefix="KB/Attachments/pubmed",
                email="a@b.com",
            )
        assert result is None

    def test_returns_none_when_paywall_keyword(self, tmp_path: Path):
        with (
            patch.object(pubmed_html, "_query_elink_free_url", return_value="https://p/x"),
            patch.object(pubmed_html, "scrape_url", return_value=PUBLISHER_MD_PAYWALL),
        ):
            result = fetch_publisher_html(
                "42020128",
                doi="10.1/x",
                attachments_abs_dir=tmp_path,
                vault_relative_prefix="KB/Attachments/pubmed",
                email="a@b.com",
            )
        assert result is None

    def test_no_pdf_link_is_fine(self, tmp_path: Path):
        """Publisher 頁沒 PDF link：result 仍成功、pdf_relpath=None。"""
        md_no_pdf = "# Title\n\nLong body text. " * 200
        with (
            patch.object(pubmed_html, "_query_elink_free_url", return_value="https://p/x"),
            patch.object(pubmed_html, "scrape_url", return_value=md_no_pdf),
            patch.object(pubmed_html, "download_markdown_images", return_value=(md_no_pdf, [])),
        ):
            result = fetch_publisher_html(
                "42020128",
                doi="10.1/x",
                attachments_abs_dir=tmp_path,
                vault_relative_prefix="KB/Attachments/pubmed",
                email="a@b.com",
            )
        assert result is not None
        assert result["pdf_relpath"] is None
        assert result["image_count"] == 0
