"""Tests for shared.pubmed_client — NCBI Entrez E-utilities thin wrapper.

Mocks ``httpx.get`` 全程，**不打真 NCBI API**。
依 feedback_pytest_monkeypatch_where_used — patch 到 pubmed_client 模組讀
``httpx`` 的 namespace。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from shared import pubmed_client
from shared.pubmed_client import PubMedClientError, esearch, esummary, lookup


def _fake_response(payload: dict, status_code: int = 200):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = payload
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}",
            request=MagicMock(),
            response=resp,
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


# ---------------------------------------------------------------------------
# esearch
# ---------------------------------------------------------------------------


def test_esearch_returns_pmids(monkeypatch):
    captured = {}

    def fake_get(url, params, timeout):
        captured["url"] = url
        captured["params"] = params
        return _fake_response(
            {"esearchresult": {"idlist": ["38945123", "38821456", "38712789"], "count": "3"}}
        )

    monkeypatch.setattr(pubmed_client.httpx, "get", fake_get)
    result = esearch("intermittent fasting", max_results=3)

    assert result == ["38945123", "38821456", "38712789"]
    assert captured["url"].endswith("/esearch.fcgi")
    assert captured["params"]["db"] == "pubmed"
    assert captured["params"]["term"] == "intermittent fasting"
    assert captured["params"]["retmax"] == "3"
    assert captured["params"]["retmode"] == "json"


def test_esearch_appends_since_year_filter(monkeypatch):
    captured = {}

    def fake_get(url, params, timeout):
        captured["params"] = params
        return _fake_response({"esearchresult": {"idlist": [], "count": "0"}})

    monkeypatch.setattr(pubmed_client.httpx, "get", fake_get)
    esearch("zone 2 training", since_year=2024)
    assert "AND 2024:3000[Date - Publication]" in captured["params"]["term"]


def test_esearch_uses_api_key_when_env_set(monkeypatch):
    captured = {}

    def fake_get(url, params, timeout):
        captured["params"] = params
        return _fake_response({"esearchresult": {"idlist": ["1"], "count": "1"}})

    monkeypatch.setenv("PUBMED_API_KEY", "secret-key-abc")
    monkeypatch.setattr(pubmed_client.httpx, "get", fake_get)
    esearch("x")
    assert captured["params"]["api_key"] == "secret-key-abc"


def test_esearch_omits_api_key_when_env_unset(monkeypatch):
    captured = {}

    def fake_get(url, params, timeout):
        captured["params"] = params
        return _fake_response({"esearchresult": {"idlist": [], "count": "0"}})

    monkeypatch.delenv("PUBMED_API_KEY", raising=False)
    monkeypatch.setattr(pubmed_client.httpx, "get", fake_get)
    esearch("x")
    assert "api_key" not in captured["params"]


def test_esearch_caps_max_results_at_200(monkeypatch):
    captured = {}

    def fake_get(url, params, timeout):
        captured["params"] = params
        return _fake_response({"esearchresult": {"idlist": [], "count": "0"}})

    monkeypatch.setattr(pubmed_client.httpx, "get", fake_get)
    esearch("x", max_results=10000)
    assert captured["params"]["retmax"] == "200"


def test_esearch_empty_query_raises():
    with pytest.raises(PubMedClientError, match="不能為空"):
        esearch("   ")


def test_esearch_http_error_raises_pubmed_client_error(monkeypatch):
    def fake_get(url, params, timeout):
        return _fake_response({}, status_code=503)

    monkeypatch.setattr(pubmed_client.httpx, "get", fake_get)
    with pytest.raises(PubMedClientError, match="HTTP"):
        esearch("x")


def test_esearch_non_json_raises(monkeypatch):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.json.side_effect = ValueError("not json")

    monkeypatch.setattr(pubmed_client.httpx, "get", lambda url, params, timeout: resp)
    with pytest.raises(PubMedClientError, match="JSON"):
        esearch("x")


def test_esearch_unexpected_structure_raises(monkeypatch):
    monkeypatch.setattr(
        pubmed_client.httpx,
        "get",
        lambda url, params, timeout: _fake_response({"unexpected": "shape"}),
    )
    with pytest.raises(PubMedClientError, match="結構非預期"):
        esearch("x")


# ---------------------------------------------------------------------------
# esummary
# ---------------------------------------------------------------------------


_SUMMARY_PAYLOAD = {
    "result": {
        "uids": ["38945123", "38821456"],
        "38945123": {
            "uid": "38945123",
            "title": "Time-restricted eating and metabolic health: an RCT.",
            "authors": [
                {"name": "Smith J", "authtype": "Author"},
                {"name": "Doe A", "authtype": "Author"},
                {"name": "Lee K", "authtype": "Author"},
                {"name": "Chen W", "authtype": "Author"},
            ],
            "fulljournalname": "JAMA Internal Medicine",
            "source": "JAMA Intern Med",
            "pubdate": "2024 Aug 12",
            "epubdate": "",
            "articleids": [
                {"idtype": "pubmed", "value": "38945123"},
                {"idtype": "doi", "value": "10.1001/jamainternmed.2024.1234"},
                {"idtype": "pmc", "value": "PMC12345678"},
            ],
            "pubtype": ["Journal Article", "Randomized Controlled Trial"],
        },
        "38821456": {
            "uid": "38821456",
            "title": "Single-author study on fasting.",
            "authors": [{"name": "Solo P", "authtype": "Author"}],
            "fulljournalname": "",
            "source": "Nutrients",
            "pubdate": "2024",
            "articleids": [{"idtype": "pubmed", "value": "38821456"}],
            "pubtype": ["Journal Article"],
        },
    }
}


def test_esummary_normalizes_full_record(monkeypatch):
    monkeypatch.setattr(
        pubmed_client.httpx,
        "get",
        lambda url, params, timeout: _fake_response(_SUMMARY_PAYLOAD),
    )
    out = esummary(["38945123", "38821456"])
    assert len(out) == 2

    first = out[0]
    assert first["pmid"] == "38945123"
    assert first["title"] == "Time-restricted eating and metabolic health: an RCT"
    assert first["authors"] == ["Smith J", "Doe A", "Lee K", "Chen W"]
    assert first["first_author"] == "Smith J"
    assert first["journal"] == "JAMA Internal Medicine"
    assert first["year"] == "2024"
    assert first["doi"] == "10.1001/jamainternmed.2024.1234"
    assert first["pmcid"] == "PMC12345678"
    assert first["pubtypes"] == ["Journal Article", "Randomized Controlled Trial"]


def test_esummary_falls_back_to_source_when_fulljournalname_missing(monkeypatch):
    monkeypatch.setattr(
        pubmed_client.httpx,
        "get",
        lambda url, params, timeout: _fake_response(_SUMMARY_PAYLOAD),
    )
    out = esummary(["38821456"])
    second = next(r for r in out if r["pmid"] == "38821456")
    assert second["journal"] == "Nutrients"
    assert second["doi"] == ""
    assert second["pmcid"] == ""


def test_esummary_empty_pmids_returns_empty_no_call(monkeypatch):
    called = False

    def fake_get(*a, **kw):
        nonlocal called
        called = True
        return _fake_response({})

    monkeypatch.setattr(pubmed_client.httpx, "get", fake_get)
    assert esummary([]) == []
    assert called is False


def test_esummary_skips_missing_pmids_in_response(monkeypatch, caplog):
    payload = {"result": {"uids": ["111"], "111": _SUMMARY_PAYLOAD["result"]["38945123"]}}
    monkeypatch.setattr(
        pubmed_client.httpx, "get", lambda url, params, timeout: _fake_response(payload)
    )
    out = esummary(["111", "999"])  # 999 not in payload
    assert len(out) == 1
    assert out[0]["pmid"] == "111"


def test_esummary_http_error_raises(monkeypatch):
    monkeypatch.setattr(
        pubmed_client.httpx,
        "get",
        lambda url, params, timeout: _fake_response({}, status_code=500),
    )
    with pytest.raises(PubMedClientError):
        esummary(["38945123"])


# ---------------------------------------------------------------------------
# lookup (esearch + esummary composed)
# ---------------------------------------------------------------------------


def test_lookup_chains_esearch_and_esummary(monkeypatch):
    calls = []

    def fake_get(url, params, timeout):
        calls.append((url, dict(params)))
        if url.endswith("esearch.fcgi"):
            return _fake_response({"esearchresult": {"idlist": ["38945123"], "count": "1"}})
        if url.endswith("esummary.fcgi"):
            return _fake_response(_SUMMARY_PAYLOAD)
        raise AssertionError(f"unexpected URL {url}")

    monkeypatch.setattr(pubmed_client.httpx, "get", fake_get)
    out = lookup("intermittent fasting", max_results=1)

    assert len(out) == 1
    assert out[0]["pmid"] == "38945123"
    assert calls[0][0].endswith("esearch.fcgi")
    assert calls[1][0].endswith("esummary.fcgi")


def test_lookup_short_circuits_when_esearch_empty(monkeypatch):
    def fake_get(url, params, timeout):
        if url.endswith("esearch.fcgi"):
            return _fake_response({"esearchresult": {"idlist": [], "count": "0"}})
        raise AssertionError("esummary must NOT be called when esearch returns empty")

    monkeypatch.setattr(pubmed_client.httpx, "get", fake_get)
    assert lookup("nonsense xxxyyy") == []
