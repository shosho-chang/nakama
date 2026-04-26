"""Anthropic /news HTML scraper — Slice B fallback for the missing RSS feed.

Anthropic 沒官方 RSS：`/news/rss.xml` / `/news/feed` / `/research/rss.xml` 全 404
（Slice A 2026-04-26 smoke 已驗）。本 module 抓 https://www.anthropic.com/news
HTML、解 article anchor list、產 candidate dict（schema 對齊 official_blogs，
讓 news_digest.py 可直接 merge）。

頁面是 NextJS SSR，HTML 已含完整 article list（無需 JS render）。卡片有三種版型
（CSS class 全 hashed，靠 tag/structure 解析）：

    1. Hero / FeaturedGrid 主卡：<a><h2>Title</h2><time>...</time><p>Body</p></a>
    2. FeaturedGrid sideLink：  <a><div><time>...</time></div><h4>Title</h4><p>Body</p></a>
    3. PublicationList list：    <a><div><time>...</time><span>Category</span></div>
                                    <span class="...title">Title</span></a>

Title 解析策略：
  - 先找任何 <h1>–<h6>（覆蓋版型 1, 2）
  - fallback 找最長 substantive `<span>`（覆蓋版型 3，避開短 category 標籤）

日期沒 `datetime=` 屬性，只有 visible text，需 dateutil 解析。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import httpx
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

from shared.log import get_logger
from shared.state import is_seen

logger = get_logger("nakama.franky.news.anthropic_html")

NEWS_URL = "https://www.anthropic.com/news"
PUBLISHER = "Anthropic"
FEED_NAME = "anthropic_news_html"
SOURCE_KEY = "ai_news_blog"

_HTTP_TIMEOUT = 30
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
_SLUG_RE = re.compile(r"^/news/([^/?#]+)$")
_SUMMARY_CAP = 1500
_TITLE_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")
# PublicationList rows put title in a <span>; categories are short labels.
# Anything > this many chars is treated as a candidate title.
_MIN_TITLE_FALLBACK_LEN = 20


def gather_candidates(
    *,
    now: datetime | None = None,
    max_age_hours: float = 24.0,
    skip_seen: bool = True,
    html_override: str | None = None,
) -> list[dict]:
    """Scrape Anthropic /news → candidate dicts within `max_age_hours`.

    candidate dict schema 對齊 official_blogs.gather_candidates。

    Args:
        now:           測試覆寫的當下時間（UTC）；預設 datetime.now(UTC)
        max_age_hours: 超過此小時數的 entry 略過
        skip_seen:     True 時用 shared.state.is_seen 過濾已見過的 item_id
        html_override: 測試注入 fixture HTML；None 時走 _fetch_html()

    Returns:
        candidate dict list，按 published_ts 由新到舊排序
    """
    now = now or datetime.now(timezone.utc)
    cutoff_ts = (now - timedelta(hours=max_age_hours)).timestamp()

    html = html_override if html_override is not None else _fetch_html()
    if not html:
        logger.info("[anthropic_html] no HTML fetched, skipping")
        return []

    parsed = _parse_articles(html)
    logger.info(f"[anthropic_html] parsed {len(parsed)} articles")

    candidates: list[dict] = []
    seen_slugs: set[str] = set()
    for entry in parsed:
        slug = entry["slug"]
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)

        item_id = f"anthropic-news-{slug}"
        url = f"https://www.anthropic.com/news/{slug}"

        if entry["published_ts"] > 0 and entry["published_ts"] < cutoff_ts:
            continue
        if skip_seen and is_seen(SOURCE_KEY, item_id):
            continue

        age_hours = 0.0
        if entry["published_ts"] > 0:
            age_hours = round((now.timestamp() - entry["published_ts"]) / 3600.0, 2)

        candidates.append(
            {
                "item_id": item_id,
                "title": entry["title"],
                "publisher": PUBLISHER,
                "feed_name": FEED_NAME,
                "url": url,
                "summary": entry["summary"][:_SUMMARY_CAP],
                "published": entry["published_iso"],
                "published_ts": entry["published_ts"],
                "age_hours": age_hours,
            }
        )

    candidates.sort(key=lambda c: c["published_ts"], reverse=True)
    logger.info(f"[anthropic_html] kept {len(candidates)} after age + dedupe filters")
    return candidates


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _fetch_html(url: str = NEWS_URL) -> str | None:
    """GET Anthropic /news with Chrome UA. Returns None on failure (logged)."""
    try:
        resp = httpx.get(
            url,
            headers={"User-Agent": _USER_AGENT},
            timeout=_HTTP_TIMEOUT,
            follow_redirects=True,
        )
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        logger.warning(f"Anthropic /news fetch failed: {e}")
        return None


def _parse_articles(html: str) -> list[dict]:
    """Parse Anthropic /news HTML → list of article dicts.

    Each dict: {slug, title, summary, published_ts, published_iso}.
    Articles missing title or with unparsable date still emit (cutoff handles them).
    """
    soup = BeautifulSoup(html, "html.parser")
    articles: list[dict] = []

    for a in soup.find_all("a", href=True):
        m = _SLUG_RE.match(a["href"])
        if not m:
            continue
        slug = m.group(1)

        title = _extract_title(a)
        if not title:
            continue

        published_ts = 0.0
        published_iso = ""
        time_tag = a.find("time")
        if time_tag is not None:
            date_text = time_tag.get_text(strip=True)
            if date_text:
                try:
                    dt = dateparser.parse(date_text)
                    if dt is not None:
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        published_ts = dt.timestamp()
                        published_iso = dt.isoformat()
                except (ValueError, OverflowError):
                    pass

        p = a.find("p")
        summary = p.get_text(strip=True) if p is not None else ""

        articles.append(
            {
                "slug": slug,
                "title": title,
                "summary": summary,
                "published_ts": published_ts,
                "published_iso": published_iso,
            }
        )

    return articles


def _extract_title(a) -> str:
    """Find article title across the three card layouts (h2/h4/span)."""
    heading = a.find(_TITLE_TAGS)
    if heading is not None:
        text = heading.get_text(strip=True)
        if text:
            return text
    # PublicationList: title is the longest substantive span.
    candidates = [s.get_text(strip=True) for s in a.find_all("span")]
    candidates = [c for c in candidates if len(c) >= _MIN_TITLE_FALLBACK_LEN]
    if candidates:
        return max(candidates, key=len)
    return ""
