"""Tests for shared.markdown_wikilinks — WikilinkResolver."""

import unicodedata
from urllib.parse import quote

from shared.markdown_wikilinks import WikilinkResolver


class TestWikilinkResolver:
    def test_digest_pubmed(self):
        r = WikilinkResolver()
        assert r.resolve("pubmed-42174253") is None  # default: no resolver registered

    def test_register_prefix_route(self):
        r = WikilinkResolver()
        r.register_prefix("pubmed-", "/bridge/source/")
        assert r.resolve("pubmed-42174253") == "/bridge/source/pubmed-42174253"

    def test_register_callable(self):
        r = WikilinkResolver()
        r.register(lambda t: f"/x/{t}" if t.startswith("a") else None)
        assert r.resolve("apple") == "/x/apple"
        assert r.resolve("banana") is None

    def test_callable_takes_priority_in_registration_order(self):
        r = WikilinkResolver()
        r.register(lambda t: "/first" if t == "x" else None)
        r.register(lambda t: "/second" if t == "x" else None)
        assert r.resolve("x") == "/first"

    def test_falls_through_to_next_resolver(self):
        r = WikilinkResolver()
        r.register(lambda t: None)  # always defers
        r.register(lambda t: "/got")
        assert r.resolve("anything") == "/got"

    def test_unknown_target_returns_none(self):
        r = WikilinkResolver()
        r.register_prefix("pubmed-", "/bridge/source/")
        assert r.resolve("not-pubmed") is None

    def test_strips_attachment_path_to_basename(self):
        # KB/Attachments/42.pdf — should be passed verbatim to resolvers
        r = WikilinkResolver()
        captured = []
        r.register(lambda t: captured.append(t) or None)
        r.resolve("KB/Attachments/42.pdf")
        assert captured == ["KB/Attachments/42.pdf"]

    def test_callable_form_for_use_with_render_markdown(self):
        r = WikilinkResolver()
        r.register_prefix("pubmed-", "/p/")
        # Should be directly usable as render_markdown's wikilink_resolver kwarg
        assert callable(r)
        assert r("pubmed-1") == "/p/pubmed-1"
        assert r("ghost") is None


class TestCJKRobustness:
    def test_cjk_register_prefix_percent_encodes_target(self):
        """register_prefix must percent-encode non-ASCII chars in the returned URL."""
        r = WikilinkResolver()
        r.register_prefix("wiki-", "/w/")
        url = r.resolve("wiki-健康")
        expected = "/w/wiki-" + quote("健康", safe="")
        assert url == expected

    def test_render_markdown_cjk_wikilink_href_percent_encoded(self):
        """End-to-end: CJK wikilink renders with percent-encoded href."""
        from shared.markdown import render_markdown

        r = WikilinkResolver()
        r.register_prefix("wiki-", "/w/")
        html = render_markdown("[[wiki-健康]]", wikilink_resolver=r)
        encoded = quote("健康", safe="")
        assert f"/w/wiki-{encoded}" in html

    def test_nfd_nfc_resolves_consistently(self):
        """NFD and NFC forms of the same target must both resolve."""
        nfc = "가나다"  # Korean Hangul, NFC composed syllables
        nfd = unicodedata.normalize("NFD", nfc)
        assert nfc != nfd, "test requires distinct NFD/NFC forms"

        r = WikilinkResolver()
        r.register(lambda t: "/wiki/korean" if t == nfc else None)

        assert r.resolve(nfc) == "/wiki/korean"
        assert r.resolve(nfd) == "/wiki/korean"  # must resolve despite NFD form

    def test_prefix_matching_is_case_sensitive(self):
        """register_prefix uses case-sensitive prefix matching (documented behavior)."""
        r = WikilinkResolver()
        r.register_prefix("wiki-", "/w/")
        assert r.resolve("wiki-test") == "/w/wiki-test"
        assert r.resolve("Wiki-test") is None  # case-sensitive: upper-case prefix fails
        assert r.resolve("WIKI-test") is None
