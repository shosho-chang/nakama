"""官方 AI blog RSS 抓取 — Franky daily news digest 的 source layer（Slice A）。

每個 feed 抽 entries → 24h 內 → scout_seen 去重 → 回傳 candidate dicts，
schema 對齊 prompts/franky/news_curate.md 期望的 candidate 形狀。

設計沿用 agents/robin/pubmed_digest.py 的 `_fetch_feed` / `_parse_entry` pattern；
差別是 PubMed RSS 有制式格式（pubmed:PMID guid + ABSTRACT 標籤），
這裡是各家 blog feed 各種 schema，要更寬容。
"""

from __future__ import annotations

import calendar
import hashlib
import html
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import feedparser
import yaml

from shared.log import get_logger
from shared.state import is_seen

logger = get_logger("nakama.franky.news.official_blogs")

SOURCE_KEY = "ai_news_blog"


@dataclass(frozen=True)
class FeedConfig:
    name: str
    url: str
    publisher: str


def load_feeds(config_path: Path) -> list[FeedConfig]:
    """從 yaml 讀 feed 設定。空檔 / 缺檔回空 list（log warning）。"""
    if not config_path.exists():
        logger.warning(f"AI news feeds config not found: {config_path}")
        return []
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    feeds: list[FeedConfig] = []
    for raw in cfg.get("feeds", []) or []:
        name = (raw.get("name") or "").strip()
        url = (raw.get("url") or "").strip()
        publisher = (raw.get("publisher") or name or "").strip()
        if not name or not url:
            logger.warning(f"Skipping malformed feed entry: {raw!r}")
            continue
        feeds.append(FeedConfig(name=name, url=url, publisher=publisher))
    return feeds


def gather_candidates(
    feeds: Iterable[FeedConfig],
    *,
    now: datetime | None = None,
    max_age_hours: float = 24.0,
    skip_seen: bool = True,
) -> list[dict]:
    """從所有 feeds 抓 entries，過濾 24h 內 + 去重，回傳 candidate dicts。

    Args:
        feeds:          FeedConfig list
        now:            當下時間（test override 用），預設 datetime.now(UTC)
        max_age_hours:  超過此小時數的 entry 略過
        skip_seen:      True 時用 shared.state.is_seen 過濾已見過的 item_id

    Returns:
        candidate dict list，按 published_ts 由新到舊排序

    Schema per candidate:
        item_id        str    唯一識別（entry.id / guid 優先；缺則 sha256(url)[:16]）
        title          str
        publisher      str    config 內的 publisher 名（顯示用）
        feed_name      str    config 內的 feed name（內部 trace 用）
        url            str    entry.link
        summary        str    HTML strip + entity decode，最多 1500 字
        published      str    ISO timestamp（無 parse 結果用 entry.published 原字串）
        published_ts   float  Unix epoch；無法 parse 時為 0
        age_hours      float  距 now 的小時數（無 published_ts 時為 0）
    """
    now = now or datetime.now(timezone.utc)
    cutoff_ts = (now - timedelta(hours=max_age_hours)).timestamp()
    candidates: list[dict] = []

    for feed in feeds:
        entries = _fetch_feed(feed)
        logger.info(f"[{feed.name}] fetched {len(entries)} entries")
        kept_count = 0
        for entry in entries:
            cand = _entry_to_candidate(entry, feed)
            if cand is None:
                continue
            # Age filter (only when we have a parsable timestamp)
            if cand["published_ts"] > 0 and cand["published_ts"] < cutoff_ts:
                continue
            if skip_seen and is_seen(SOURCE_KEY, cand["item_id"]):
                continue
            if cand["published_ts"] > 0:
                cand["age_hours"] = round((now.timestamp() - cand["published_ts"]) / 3600.0, 2)
            candidates.append(cand)
            kept_count += 1
        logger.info(f"[{feed.name}] kept {kept_count} after age + dedupe filters")

    candidates.sort(key=lambda c: c["published_ts"], reverse=True)
    return candidates


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _fetch_feed(feed: FeedConfig) -> list[Any]:
    """Wrap feedparser.parse with error handling. Empty list on hard failure."""
    try:
        parsed = feedparser.parse(feed.url)
    except Exception as e:
        logger.warning(f"Feed fetch raised: {feed.url} — {e}")
        return []
    if parsed.bozo and not parsed.entries:
        logger.warning(f"Feed parse failed: {feed.url} — {parsed.bozo_exception}")
        return []
    if parsed.bozo:
        logger.debug(f"Feed bozo (warning only, entries present): {feed.url}")
    return list(parsed.entries)


_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    """HTML tag 剝除 + entity decode + whitespace 收斂。"""
    if not s:
        return ""
    s = _TAG_RE.sub(" ", s)
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _entry_to_candidate(entry: Any, feed: FeedConfig) -> dict | None:
    """單一 feedparser entry → candidate dict；缺 title/link 回 None。"""
    title = (getattr(entry, "title", "") or "").strip()
    link = (getattr(entry, "link", "") or "").strip()
    if not title or not link:
        return None

    # item_id: prefer entry.id (RSS GUID), fallback to sha256(link)
    raw_id = getattr(entry, "id", "") or getattr(entry, "guid", "")
    if not raw_id:
        raw_id = hashlib.sha256(link.encode("utf-8")).hexdigest()[:16]

    # summary 可能在 entry.summary 或 entry.content[0].value
    summary_html = ""
    content = getattr(entry, "content", None)
    if content and isinstance(content, list) and len(content) > 0:
        first = content[0]
        if isinstance(first, dict):
            summary_html = first.get("value", "")
    if not summary_html:
        summary_html = getattr(entry, "summary", "") or ""
    summary = _strip_html(summary_html)

    # published — feedparser 會 parse 成 published_parsed (struct_time, UTC)
    published_raw = getattr(entry, "published", "") or getattr(entry, "updated", "")
    published_ts = 0.0
    published_iso = published_raw
    parsed_struct = getattr(entry, "published_parsed", None) or getattr(
        entry, "updated_parsed", None
    )
    if parsed_struct:
        try:
            # calendar.timegm: struct_time → epoch as if UTC (mktime 會誤用 local TZ)
            published_ts = float(calendar.timegm(parsed_struct))
            published_iso = datetime.fromtimestamp(published_ts, tz=timezone.utc).isoformat()
        except (TypeError, ValueError, OverflowError):
            pass

    return {
        "item_id": str(raw_id),
        "title": title,
        "publisher": feed.publisher,
        "feed_name": feed.name,
        "url": link,
        "summary": summary[:1500],
        "published": published_iso,
        "published_ts": published_ts,
        "age_hours": 0.0,
    }
