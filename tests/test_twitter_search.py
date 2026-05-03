"""agents/zoro/twitter_api.py — search_recent_tweets() unit tests.

真 DDG/X 不打（會慢且 flaky）；mock httpx.get。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from agents.zoro import twitter_api


def _empty_ddg_response() -> MagicMock:
    fake = MagicMock()
    fake.text = "<html></html>"  # _parse_ddg_results returns [] on empty
    fake.raise_for_status = MagicMock()
    return fake


# ──────────────────────────────────────────────────────────────────────────
# search_recent_tweets — region biasing for zh-TW vs zh-CN
# (GH #33 Item 5: twitter_zh region 分流)
# ──────────────────────────────────────────────────────────────────────────


def test_search_recent_tweets_without_region_omits_kl_param():
    """Backward compat: bare call sends only ``q`` to DDG, no ``kl``."""
    with patch("httpx.get", return_value=_empty_ddg_response()) as mock_get:
        twitter_api.search_recent_tweets("creatine")

    _args, kwargs = mock_get.call_args
    assert kwargs["params"] == {"q": "creatine site:x.com"}
    assert "kl" not in kwargs["params"]


def test_search_recent_tweets_with_region_passes_kl_param():
    """region='tw-tzh' → DDG receives ``kl=tw-tzh`` to bias to Taiwan zh-TW."""
    with patch("httpx.get", return_value=_empty_ddg_response()) as mock_get:
        twitter_api.search_recent_tweets("深度睡眠", region="tw-tzh")

    _args, kwargs = mock_get.call_args
    assert kwargs["params"]["q"] == "深度睡眠 site:x.com"
    assert kwargs["params"]["kl"] == "tw-tzh"


def test_search_recent_tweets_empty_region_omits_kl_param():
    """Falsy region (None, empty string) keeps legacy DDG default."""
    with patch("httpx.get", return_value=_empty_ddg_response()) as mock_get:
        twitter_api.search_recent_tweets("topic", region="")

    _args, kwargs = mock_get.call_args
    assert "kl" not in kwargs["params"]
