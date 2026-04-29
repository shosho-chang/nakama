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
