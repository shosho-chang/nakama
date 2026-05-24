"""Tests for shared.markdown_wikilinks — WikilinkResolver."""

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
