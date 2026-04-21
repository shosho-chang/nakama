"""Unit tests for Robin PubMed digest pipeline — pure-function helpers only.

Full pipeline tests would need to mock feedparser + Anthropic + obsidian_writer;
skip that layer here and rely on the manual smoke test in the PR description.
"""

from __future__ import annotations

import pytest

from agents.robin.pubmed_digest import (
    _clean_abstract,
    _clean_journal,
    _parse_json,
    _strip_html,
)
from shared.journal_metrics import lookup


class TestStripHtml:
    def test_removes_tags(self):
        assert _strip_html("<p>hello <b>world</b></p>") == "hello world"

    def test_decodes_entities(self):
        assert _strip_html("&amp;&lt;&gt;") == "&<>"

    def test_empty(self):
        assert _strip_html("") == ""
        assert _strip_html(None) == ""

    def test_collapses_whitespace(self):
        assert _strip_html("<p>a   b\n\tc</p>") == "a b c"


class TestCleanAbstract:
    def test_strips_pubmed_citation_header(self):
        raw = (
            "Front Endocrinol (Lausanne). 2026 Apr 2;17:1780806. "
            "doi: 10.3389/fendo.2026.1780806. eCollection 2026. "
            "ABSTRACT BACKGROUND: Estrogens have been proposed..."
        )
        cleaned = _clean_abstract(raw)
        assert cleaned.startswith("BACKGROUND: Estrogens")

    def test_preserves_text_without_abstract_keyword(self):
        raw = "Just an abstract without the marker word"
        assert _clean_abstract(raw) == raw

    def test_handles_empty(self):
        assert _clean_abstract("") == ""


class TestCleanJournal:
    def test_strips_subtitle(self):
        assert (
            _clean_journal("Supportive care in cancer : official journal of the MASCC")
            == "Supportive care in cancer"
        )

    def test_preserves_simple_name(self):
        assert _clean_journal("Cell Death and Disease") == "Cell Death and Disease"

    def test_empty(self):
        assert _clean_journal("") == ""


class TestParseJson:
    def test_plain_json(self):
        assert _parse_json('{"a": 1, "b": 2}') == {"a": 1, "b": 2}

    def test_wrapped_in_prose(self):
        text = 'Here is the JSON:\n{"x": true}\nHope it helps.'
        assert _parse_json(text) == {"x": True}

    def test_wrapped_in_code_block(self):
        text = '```json\n{"n": 42}\n```'
        assert _parse_json(text) == {"n": 42}

    def test_raises_on_no_json(self):
        with pytest.raises(ValueError):
            _parse_json("No JSON here at all")


class TestJournalLookup:
    """These tests require data/scimago_journals.csv to be present."""

    def test_known_journal_q1(self):
        r = lookup(journal_name="Cell Death and Disease")
        if r is None:
            pytest.skip("Scimago CSV 未載入（CI 未 commit 或尚未執行 ETL）")
        assert r["quartile"] == "Q1"
        assert r["sjr"] > 0

    def test_ampersand_variant_matches(self):
        """'Cell Death & Disease' 應匹配到 'Cell Death and Disease'。"""
        r = lookup(journal_name="Cell Death & Disease")
        if r is None:
            pytest.skip("Scimago CSV 未載入")
        assert r["quartile"] == "Q1"

    def test_lancet_without_the(self):
        """'Lancet' 應匹配到 'The Lancet'（leading 'the' 被去掉）。"""
        r = lookup(journal_name="Lancet")
        if r is None:
            pytest.skip("Scimago CSV 未載入")
        assert r["title"] == "The Lancet"

    def test_unknown_returns_none(self):
        assert lookup(journal_name="Totally Fake Journal of Nonexistent Studies") is None

    def test_issn_lookup(self):
        """NEJM 的 ISSN。"""
        r = lookup(issn="00284793")
        if r is None:
            pytest.skip("Scimago CSV 未載入")
        assert "New England" in r["title"]

    def test_both_none_returns_none(self):
        assert lookup() is None


class TestEtlParsers:
    """ETL 純函式：parse_sjr, parse_issns（European decimal / comma separator）。"""

    def test_parse_sjr_european_decimal(self):
        from scripts.update_scimago import parse_sjr

        assert parse_sjr("104,065") == 104.065
        assert parse_sjr("0,5") == 0.5

    def test_parse_sjr_empty(self):
        from scripts.update_scimago import parse_sjr

        assert parse_sjr("") is None
        assert parse_sjr("  ") is None

    def test_parse_sjr_invalid(self):
        from scripts.update_scimago import parse_sjr

        assert parse_sjr("not a number") is None

    def test_parse_issns_multiple(self):
        from scripts.update_scimago import parse_issns

        assert parse_issns("15424863, 00079235") == ["15424863", "00079235"]

    def test_parse_issns_strips_hyphens(self):
        from scripts.update_scimago import parse_issns

        assert parse_issns("1542-4863") == ["15424863"]

    def test_parse_issns_empty(self):
        from scripts.update_scimago import parse_issns

        assert parse_issns("") == []
