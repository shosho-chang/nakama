"""Tests for shared.arxiv_client — arXiv + Semantic Scholar wrapper.

Pattern aligns with ``tests/shared/test_pubmed_client.py``：monkeypatch
``httpx.get`` 全程，**不打真 API**。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from shared import arxiv_client
from shared.arxiv_client import (
    ArxivClientError,
    get_citations,
    get_paper,
    search,
)

_ARXIV_XML_2_ENTRIES = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2402.03300v2</id>
    <updated>2024-03-01T00:00:00Z</updated>
    <published>2024-02-05T00:00:00Z</published>
    <title>DeepSeekMath: Pushing the Limits of Mathematical Reasoning</title>
    <summary>We introduce DeepSeekMath, a 7B model...</summary>
    <author><name>Zhihong Shao</name></author>
    <author><name>Peiyi Wang</name></author>
    <author><name>Qihao Zhu</name></author>
    <link href="http://arxiv.org/abs/2402.03300v2" rel="alternate" type="text/html"/>
    <link title="pdf" href="http://arxiv.org/pdf/2402.03300v2" rel="related"/>
    <arxiv:primary_category xmlns:arxiv="http://arxiv.org/schemas/atom" term="cs.CL" scheme="http://arxiv.org/schemas/atom"/>
    <category term="cs.CL" scheme="http://arxiv.org/schemas/atom"/>
    <category term="cs.AI" scheme="http://arxiv.org/schemas/atom"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2401.12345v1</id>
    <updated>2024-01-23T00:00:00Z</updated>
    <published>2024-01-23T00:00:00Z</published>
    <title>A Second Test Paper</title>
    <summary>Abstract text here.</summary>
    <author><name>Alice Solo</name></author>
    <link href="http://arxiv.org/abs/2401.12345v1" rel="alternate" type="text/html"/>
    <arxiv:primary_category xmlns:arxiv="http://arxiv.org/schemas/atom" term="cs.AI" scheme="http://arxiv.org/schemas/atom"/>
    <category term="cs.AI" scheme="http://arxiv.org/schemas/atom"/>
  </entry>
</feed>"""

_ARXIV_XML_EMPTY = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"></feed>"""


def _fake_xml_response(body: str, status_code: int = 200):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.content = body.encode("utf-8")
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}", request=MagicMock(), response=resp
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


def _fake_json_response(payload: dict, status_code: int = 200):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = payload
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}", request=MagicMock(), response=resp
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


# ---------------------------------------------------------------------------
# search()
# ---------------------------------------------------------------------------


def test_search_parses_entries(monkeypatch):
    monkeypatch.setattr(
        arxiv_client.httpx,
        "get",
        lambda url, params, timeout: _fake_xml_response(_ARXIV_XML_2_ENTRIES),
    )
    results = search("deepseekmath", max_results=5)

    assert len(results) == 2
    first = results[0]
    assert first["arxiv_id"] == "2402.03300v2"
    assert first["title"].startswith("DeepSeekMath")
    assert first["authors"][:2] == ["Zhihong Shao", "Peiyi Wang"]
    assert first["first_author"] == "Zhihong Shao"
    assert first["published"] == "2024-02-05"
    assert first["primary_category"] == "cs.CL"
    assert "cs.CL" in first["categories"]
    assert "cs.AI" in first["categories"]
    assert first["pdf_url"] == "http://arxiv.org/pdf/2402.03300v2"
    assert first["abs_url"] == "http://arxiv.org/abs/2402.03300v2"


def test_search_caps_max_results(monkeypatch):
    captured = {}

    def fake_get(url, params, timeout):
        captured["params"] = params
        return _fake_xml_response(_ARXIV_XML_EMPTY)

    monkeypatch.setattr(arxiv_client.httpx, "get", fake_get)
    search("x", max_results=999)
    assert captured["params"]["max_results"] == "30"


def test_search_empty_query_raises():
    with pytest.raises(ArxivClientError, match="不能為空"):
        search("   ")


def test_search_passes_sort_by(monkeypatch):
    captured = {}

    def fake_get(url, params, timeout):
        captured["params"] = params
        return _fake_xml_response(_ARXIV_XML_EMPTY)

    monkeypatch.setattr(arxiv_client.httpx, "get", fake_get)
    search("x", sort_by="submittedDate")
    assert captured["params"]["sortBy"] == "submittedDate"


def test_search_http_error_wraps(monkeypatch):
    def fake_get(url, params, timeout):
        raise httpx.ConnectError("network down")

    monkeypatch.setattr(arxiv_client.httpx, "get", fake_get)
    with pytest.raises(ArxivClientError, match="HTTP 失敗"):
        search("x")


def test_search_bad_xml_wraps(monkeypatch):
    monkeypatch.setattr(
        arxiv_client.httpx,
        "get",
        lambda url, params, timeout: _fake_xml_response("not xml at all"),
    )
    with pytest.raises(ArxivClientError, match="XML 解析失敗"):
        search("x")


def test_search_empty_feed_returns_empty_list(monkeypatch):
    monkeypatch.setattr(
        arxiv_client.httpx,
        "get",
        lambda url, params, timeout: _fake_xml_response(_ARXIV_XML_EMPTY),
    )
    assert search("nonexistent") == []


# ---------------------------------------------------------------------------
# get_paper()
# ---------------------------------------------------------------------------


def test_get_paper_returns_entry(monkeypatch):
    monkeypatch.setattr(
        arxiv_client.httpx,
        "get",
        lambda url, params, timeout: _fake_xml_response(_ARXIV_XML_2_ENTRIES),
    )
    paper = get_paper("2402.03300")
    assert paper is not None
    assert paper["arxiv_id"] == "2402.03300v2"


def test_get_paper_no_entries_returns_none(monkeypatch):
    monkeypatch.setattr(
        arxiv_client.httpx,
        "get",
        lambda url, params, timeout: _fake_xml_response(_ARXIV_XML_EMPTY),
    )
    assert get_paper("9999.99999") is None


def test_get_paper_empty_id_raises():
    with pytest.raises(ArxivClientError, match="不能為空"):
        get_paper("   ")


# ---------------------------------------------------------------------------
# get_citations()
# ---------------------------------------------------------------------------


def test_get_citations_happy_path(monkeypatch):
    call_log = []

    def fake_get(url, params=None, timeout=None):
        call_log.append(url)
        if url.endswith("/citations"):
            return _fake_json_response(
                {
                    "data": [
                        {
                            "citingPaper": {
                                "title": "Citing paper one",
                                "authors": [{"name": "B Author"}],
                                "year": 2025,
                                "citationCount": 3,
                                "externalIds": {"ArXiv": "2502.00001"},
                            }
                        }
                    ]
                }
            )
        if url.endswith("/references"):
            return _fake_json_response(
                {
                    "data": [
                        {
                            "citedPaper": {
                                "title": "An earlier reference",
                                "authors": [{"name": "C Author"}],
                                "year": 2020,
                                "citationCount": 200,
                            }
                        }
                    ]
                }
            )
        # paper detail
        return _fake_json_response(
            {
                "title": "DeepSeekMath",
                "authors": [{"name": "Zhihong Shao"}],
                "year": 2024,
                "citationCount": 1500,
                "referenceCount": 60,
                "influentialCitationCount": 120,
                "isOpenAccess": True,
                "abstract": "We introduce...",
            }
        )

    monkeypatch.setattr(arxiv_client.httpx, "get", fake_get)
    result = get_citations("2402.03300v2", limit=5)

    assert result["paper"]["title"] == "DeepSeekMath"
    assert result["paper"]["citation_count"] == 1500
    assert result["paper"]["influential_citation_count"] == 120
    assert result["paper"]["is_open_access"] is True
    assert len(result["citing"]) == 1
    assert result["citing"][0]["arxiv_id"] == "2502.00001"
    assert len(result["references"]) == 1
    assert result["references"][0]["title"] == "An earlier reference"
    # version suffix v2 should be stripped before going to S2
    assert any("arXiv:2402.03300/citations" in u for u in call_log)


def test_get_citations_404_returns_empty_paper(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        return _fake_json_response({}, status_code=404)

    monkeypatch.setattr(arxiv_client.httpx, "get", fake_get)
    result = get_citations("9999.99999")
    assert result == {"paper": None, "citing": [], "references": []}


def test_get_citations_caps_limit(monkeypatch):
    captured_params = []

    def fake_get(url, params=None, timeout=None):
        if url.endswith("/citations") or url.endswith("/references"):
            captured_params.append(params)
            return _fake_json_response({"data": []})
        return _fake_json_response({"title": "x"})

    monkeypatch.setattr(arxiv_client.httpx, "get", fake_get)
    get_citations("2402.03300", limit=999)
    # cap to 20
    for p in captured_params:
        assert p["limit"] == "20"


def test_get_citations_http_error_wraps(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(arxiv_client.httpx, "get", fake_get)
    with pytest.raises(ArxivClientError, match="Semantic Scholar HTTP 失敗"):
        get_citations("2402.03300")


def test_get_citations_empty_id_raises():
    with pytest.raises(ArxivClientError, match="不能為空"):
        get_citations("")


@pytest.mark.parametrize(
    "input_id,expected_s2_id",
    [
        ("2402.03300", "arXiv:2402.03300"),
        ("2402.03300v1", "arXiv:2402.03300"),
        ("2402.03300v12", "arXiv:2402.03300"),
        # Regression: old-style ID containing 'v' inside category must not be truncated.
        # ``'cs.cv/0701001'.split('v')[0]`` → ``'cs.c'`` (bug). Fix: strip only trailing vN.
        ("cs.cv/0701001", "arXiv:cs.cv/0701001"),
        ("cs.cv/0701001v3", "arXiv:cs.cv/0701001"),
        ("hep-th/9901001", "arXiv:hep-th/9901001"),
    ],
)
def test_get_citations_strips_only_trailing_version(monkeypatch, input_id, expected_s2_id):
    captured = []

    def fake_get(url, params=None, timeout=None):
        captured.append(url)
        if url.endswith("/citations") or url.endswith("/references"):
            return _fake_json_response({"data": []})
        return _fake_json_response({"title": "t"})

    monkeypatch.setattr(arxiv_client.httpx, "get", fake_get)
    get_citations(input_id)

    # First call is the paper detail at /paper/{s2_id}
    assert captured[0].endswith(f"/paper/{expected_s2_id}"), (
        f"Wrong S2 id for input {input_id!r}: got URL {captured[0]}"
    )
