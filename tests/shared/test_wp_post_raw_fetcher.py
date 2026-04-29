"""Tests for `shared/wp_post_raw_fetcher.py` — slice 5 / issue #234.

Coverage:

- ``fetch_raw_html`` happy path returns the raw HTML.
- WP REST returning a non-dict / missing content / missing raw → graceful
  ``error_message``.
- ``WordPressClient.from_env`` env-missing → graceful ``error_message``.
- HTTP layer error (`WPServerError`) → graceful ``error_message``.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from shared import wp_post_raw_fetcher
from shared.wordpress_client import WPServerError


@pytest.fixture
def wp_env(monkeypatch):
    monkeypatch.setenv("WP_SHOSHO_BASE_URL", "http://wp.test")
    monkeypatch.setenv("WP_SHOSHO_USERNAME", "u")
    monkeypatch.setenv("WP_SHOSHO_APP_PASSWORD", "p")


class TestFetchRawHtml:
    def test_happy_path_returns_raw_html(self, wp_env):
        fake_response = {
            "id": 42,
            "content": {
                "raw": "<!-- wp:paragraph --><p>hello</p>",
                "rendered": "<p>hello</p>",
            },
        }
        with patch(
            "shared.wp_post_raw_fetcher.WordPressClient._request",
            return_value=fake_response,
        ):
            result = wp_post_raw_fetcher.fetch_raw_html(
                target_site="wp_shosho",
                wp_post_id=42,
            )
        assert result.ok is True
        assert result.error_message is None
        assert result.raw_html == "<!-- wp:paragraph --><p>hello</p>"

    def test_missing_env_returns_error(self, monkeypatch):
        monkeypatch.delenv("WP_SHOSHO_BASE_URL", raising=False)
        monkeypatch.delenv("WP_SHOSHO_USERNAME", raising=False)
        monkeypatch.delenv("WP_SHOSHO_APP_PASSWORD", raising=False)
        result = wp_post_raw_fetcher.fetch_raw_html(
            target_site="wp_shosho",
            wp_post_id=42,
        )
        assert result.ok is False
        assert "env missing" in result.error_message

    def test_wp_server_error_returns_error(self, wp_env):
        with patch(
            "shared.wp_post_raw_fetcher.WordPressClient._request",
            side_effect=WPServerError("503"),
        ):
            result = wp_post_raw_fetcher.fetch_raw_html(
                target_site="wp_shosho",
                wp_post_id=42,
            )
        assert result.ok is False
        assert "WPServerError" in result.error_message
        assert "503" in result.error_message

    def test_non_dict_response_returns_error(self, wp_env):
        with patch(
            "shared.wp_post_raw_fetcher.WordPressClient._request",
            return_value=["not", "a", "dict"],
        ):
            result = wp_post_raw_fetcher.fetch_raw_html(
                target_site="wp_shosho",
                wp_post_id=42,
            )
        assert result.ok is False
        assert "non-dict" in result.error_message

    def test_missing_content_dict_returns_error(self, wp_env):
        with patch(
            "shared.wp_post_raw_fetcher.WordPressClient._request",
            return_value={"id": 42, "content": "<p>only rendered</p>"},
        ):
            result = wp_post_raw_fetcher.fetch_raw_html(
                target_site="wp_shosho",
                wp_post_id=42,
            )
        assert result.ok is False
        assert "no content dict" in result.error_message

    def test_missing_raw_key_returns_error(self, wp_env):
        with patch(
            "shared.wp_post_raw_fetcher.WordPressClient._request",
            return_value={"id": 42, "content": {"rendered": "<p>x</p>"}},
        ):
            result = wp_post_raw_fetcher.fetch_raw_html(
                target_site="wp_shosho",
                wp_post_id=42,
            )
        assert result.ok is False
        assert "content.raw missing" in result.error_message

    def test_empty_raw_html_is_valid(self, wp_env):
        """Empty body is a valid result, not an error."""
        with patch(
            "shared.wp_post_raw_fetcher.WordPressClient._request",
            return_value={"id": 42, "content": {"raw": "", "rendered": ""}},
        ):
            result = wp_post_raw_fetcher.fetch_raw_html(
                target_site="wp_shosho",
                wp_post_id=42,
            )
        assert result.ok is True
        assert result.raw_html == ""

    def test_default_sanitize_strips_notion_classes(self, wp_env):
        """Real-world Notion-Bricks output: nested spans with plugin classes
        + data-token-index. Default fetch should strip them so the review
        textarea is human-readable.
        """
        notion_input = (
            '<p><strong><span class="notion-enable-hover" data-token-index="0">'
            "注重健康飲食的朋友</span>"
            '<span class="discussion-level-1 discussion-id-17a4f11d notion-enable-hover" '
            'data-token-index="1">「抗發炎食物」</span>'
            "</strong></p>"
        )
        with patch(
            "shared.wp_post_raw_fetcher.WordPressClient._request",
            return_value={"id": 42, "content": {"raw": notion_input, "rendered": ""}},
        ):
            result = wp_post_raw_fetcher.fetch_raw_html(
                target_site="wp_shosho",
                wp_post_id=42,
            )
        assert result.ok is True
        # Plugin runtime junk gone:
        assert "notion-enable-hover" not in result.raw_html
        assert "discussion-level-1" not in result.raw_html
        assert "data-token-index" not in result.raw_html
        # Prose preserved:
        assert "注重健康飲食的朋友" in result.raw_html
        assert "「抗發炎食物」" in result.raw_html
        # Semantic tags survive (the review reviewer needs to see the
        # bold/paragraph structure):
        assert "<strong>" in result.raw_html
        assert "<p>" in result.raw_html

    def test_sanitize_false_returns_raw_unchanged(self, wp_env):
        notion_input = '<p><span class="notion-enable-hover" data-token-index="0">x</span></p>'
        with patch(
            "shared.wp_post_raw_fetcher.WordPressClient._request",
            return_value={"id": 42, "content": {"raw": notion_input, "rendered": ""}},
        ):
            result = wp_post_raw_fetcher.fetch_raw_html(
                target_site="wp_shosho",
                wp_post_id=42,
                sanitize=False,
            )
        assert result.raw_html == notion_input


class TestSanitizeReviewHtml:
    """Direct tests for the public `sanitize_review_html` helper.

    Bordered narrowly: strip plugin runtime classes/attrs and unwrap empty
    spans. Must NOT touch Gutenberg block comments, wp-block-* classes,
    href/src/alt, or any block-level structure that round-trips back via
    `update_post`.
    """

    def test_strips_notion_class_pattern(self):
        out = wp_post_raw_fetcher.sanitize_review_html('<span class="notion-enable-hover">x</span>')
        assert "notion-enable-hover" not in out
        # span had only that class, so unwraps to bare text:
        assert out == "x"

    def test_strips_discussion_class_pattern(self):
        out = wp_post_raw_fetcher.sanitize_review_html(
            '<span class="discussion-level-1 discussion-id-abc">x</span>'
        )
        assert "discussion-" not in out
        assert out == "x"

    def test_strips_brxe_and_brx_class_pattern(self):
        out = wp_post_raw_fetcher.sanitize_review_html(
            '<div class="brxe-block brx-foo wp-block-group">x</div>'
        )
        # plugin classes gone, Gutenberg wp-block-* preserved:
        assert "brxe-" not in out
        assert "brx-foo" not in out
        assert "wp-block-group" in out

    def test_strips_data_token_index(self):
        out = wp_post_raw_fetcher.sanitize_review_html(
            '<span data-token-index="0" data-token-uuid="u1">x</span>'
        )
        assert "data-token-index" not in out
        assert "data-token-uuid" not in out

    def test_preserves_wp_block_comments(self):
        """ADR-005b §5 / slice #235 round-trip: WP block markers must
        survive byte-for-byte. We don't strip HTML comments at all."""
        html = '<!-- wp:paragraph --><p class="wp-block-paragraph">x</p><!-- /wp:paragraph -->'
        out = wp_post_raw_fetcher.sanitize_review_html(html)
        assert "<!-- wp:paragraph -->" in out
        assert "<!-- /wp:paragraph -->" in out
        assert "wp-block-paragraph" in out

    def test_preserves_links_and_attrs(self):
        out = wp_post_raw_fetcher.sanitize_review_html(
            '<a href="https://shosho.tw/x" rel="noopener">link</a>'
        )
        assert 'href="https://shosho.tw/x"' in out
        assert 'rel="noopener"' in out

    def test_preserves_image_attrs(self):
        out = wp_post_raw_fetcher.sanitize_review_html(
            '<img src="/uploads/x.jpg" alt="a hero shot" width="800">'
        )
        assert 'src="/uploads/x.jpg"' in out
        assert 'alt="a hero shot"' in out
        assert 'width="800"' in out

    def test_idempotent(self):
        html = (
            '<p><span class="notion-enable-hover" data-token-index="0">x</span>'
            "<strong>y</strong></p>"
        )
        once = wp_post_raw_fetcher.sanitize_review_html(html)
        twice = wp_post_raw_fetcher.sanitize_review_html(once)
        assert once == twice

    def test_empty_input_returns_empty(self):
        assert wp_post_raw_fetcher.sanitize_review_html("") == ""
        assert wp_post_raw_fetcher.sanitize_review_html("   ") == "   "

    def test_unwraps_only_attributeless_spans(self):
        """A span that still carries a semantic class (not on noise list)
        should be kept; only fully-empty spans unwrap."""
        out = wp_post_raw_fetcher.sanitize_review_html(
            '<span class="custom-highlight">x</span><span>y</span>'
        )
        # custom-highlight is not in noise pattern, span kept:
        assert '<span class="custom-highlight">x</span>' in out
        # plain <span>y</span> unwraps:
        assert ">y</span>" not in out
        assert "y" in out

    def test_preserves_paragraph_structure(self):
        """Nested noise spans inside <p><strong>…</strong></p> — after
        stripping span wrappers, the paragraph + strong + text order
        must be preserved verbatim."""
        html = (
            "<p><strong>"
            '<span class="notion-enable-hover" data-token-index="0">A</span>'
            '<span class="discussion-level-1" data-token-index="1">B</span>'
            '<span class="notion-enable-hover" data-token-index="2">C</span>'
            "</strong></p>"
        )
        out = wp_post_raw_fetcher.sanitize_review_html(html)
        assert out == "<p><strong>ABC</strong></p>"
