"""Twitter/X 搜尋 — 透過 DuckDuckGo 搜尋 site:x.com 抓取熱門推文。"""

from __future__ import annotations

import re
from urllib.parse import unquote

import httpx

from shared.log import get_logger

logger = get_logger("nakama.zoro.twitter")

_DDG_URL = "https://html.duckduckgo.com/html/"


def search_recent_tweets(
    topic: str,
    max_results: int = 10,
    *,
    region: str | None = None,
) -> dict:
    """Search for popular tweets about a topic via DuckDuckGo site search.

    No API key needed — uses DuckDuckGo HTML search to find x.com results.

    Args:
        topic: Search query.
        max_results: Maximum tweets to return.
        region: DuckDuckGo region code passed as the ``kl`` query param. Use
            ``"tw-tzh"`` to bias results toward Taiwan / Traditional Chinese,
            ``"us-en"`` for US English, etc. Defaults to None (DDG default
            region — typically broad / all-locale).

            keyword_research's ``twitter_zh`` channel passes ``"tw-tzh"`` so
            results favor Taiwan tweets and avoid zh-CN KOL noise (GH #33
            Item 5 eval finding — unrestricted DDG search picked up Charles
            Zhang's zh-CN tweets that triggered Mandarin-mainland-focused
            YouTube title seeds).

    Returns:
        dict with keys: tweets (list of dicts with text, url, username).
        Returns empty dict on failure.
    """
    params = {"q": f"{topic} site:x.com"}
    if region:
        params["kl"] = region

    try:
        resp = httpx.get(
            _DDG_URL,
            params=params,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
            },
            timeout=10,
            follow_redirects=True,
        )
        resp.raise_for_status()
        tweets = _parse_ddg_results(resp.text, max_results)
        return {"tweets": tweets} if tweets else {}

    except Exception as e:
        logger.error(f"Twitter search error: {e}")
        return {}


def _parse_ddg_results(html: str, max_results: int) -> list[dict]:
    """Extract tweet info from DuckDuckGo search result HTML."""
    tweets = []
    seen_urls = set()

    tweet_url_re = re.compile(r"https?://(?:www\.)?(?:x\.com|twitter\.com)/([\w]+)/status/(\d+)")

    # DuckDuckGo wraps result links in uddg= redirects
    uddg_re = re.compile(r"uddg=(https?[^&\"]+)")

    # Find each result block
    blocks = re.split(r'class="result__body"', html)

    for block in blocks:
        # Extract the URL from uddg redirect
        uddg_match = uddg_re.search(block)
        if not uddg_match:
            continue

        decoded_url = unquote(uddg_match.group(1))
        tweet_match = tweet_url_re.search(decoded_url)
        if not tweet_match:
            continue

        username = tweet_match.group(1)
        tweet_id = tweet_match.group(2)

        # Skip non-user pages
        if username.lower() in (
            "i",
            "search",
            "explore",
            "home",
            "hashtag",
            "x",
            "twitter",
        ):
            continue

        url = f"https://x.com/{username}/status/{tweet_id}"
        if url in seen_urls:
            continue
        seen_urls.add(url)

        # Extract snippet text
        snippet = ""
        snippet_match = re.search(
            r'class="result__snippet"[^>]*>(.*?)</a>',
            block,
            re.DOTALL,
        )
        if snippet_match:
            snippet = re.sub(r"<[^>]+>", "", snippet_match.group(1)).strip()

        # Extract display name from title
        name = ""
        title_match = re.search(
            r'class="result__a"[^>]*>(.*?)</a>',
            block,
            re.DOTALL,
        )
        if title_match:
            name = re.sub(r"<[^>]+>", "", title_match.group(1)).strip()
            name = re.split(r"\s+on\s+X\b|\s+[-/–]\s+X\b|\(\s*@", name)[0].strip()

        tweets.append(
            {
                "text": snippet[:200] if snippet else "",
                "created_at": "",
                "likes": 0,
                "retweets": 0,
                "replies": 0,
                "impressions": 0,
                "author": name,
                "username": username,
                "url": url,
            }
        )

        if len(tweets) >= max_results:
            break

    return tweets
