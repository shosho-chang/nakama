"""Shared fixtures + helpers for seo_audit_post pipeline tests."""

from __future__ import annotations

import pytest

from tests.skills.seo_audit_post.test_audit_pipeline import (
    audit_mod,
)

__all__ = ["audit_mod"]


@pytest.fixture
def patch_fetch_html(monkeypatch):
    """Mock httpx.get so html_fetcher.fetch_html does not hit the network."""
    import shared.seo_audit.html_fetcher as fetcher_mod
    from tests.skills.seo_audit_post.test_audit_pipeline import _FIXTURE_HTML

    class _FakeResp:
        def __init__(self, body: str, status_code: int = 200):
            self.text = body
            self.status_code = status_code
            self.url = "https://shosho.tw/zone-2-training-guide"
            self.headers = {"content-type": "text/html; charset=utf-8"}

        def raise_for_status(self):
            return None

    def _fake_get(url, **kwargs):
        return _FakeResp(_FIXTURE_HTML)

    monkeypatch.setattr(fetcher_mod.httpx, "get", _fake_get)
