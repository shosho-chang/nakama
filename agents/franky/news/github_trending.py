"""GitHub trending Python fetcher — Franky news S1 experimental low-trust source.

Trust tier semantics（ADR-023 §2 S1）：
  - 此 source 屬 experimental low-trust → 候選帶 ``trust_tier: experimental``，
    下游 score 階段對其 cap 到 4（不能拿 5）。
  - 進 curate pool 前先跑 repo sanity check（age / stars / README / license /
    最近 commit），任一 fail 直接砍 — 不污染 LLM curate prompt。

實作策略：
  - 抓 https://github.com/trending/python（HTML，不是 API；GitHub 沒給 trending API）
  - 解出 owner/repo 列表
  - 用 GitHub REST API（``api.github.com/repos/{owner}/{repo}``）補齊 metadata：
    created_at / pushed_at / license / readme / topics
  - topic_filter（agent / llm / mcp / claude）：repo topics 或 description 命中即收
  - sanity_check 全綠才產 candidate

對齊 official_blogs.gather_candidates 的 candidate dict schema，
news_digest 的 merge 階段可直接拼進 candidates list。
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

import httpx
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

from shared.log import get_logger
from shared.state import is_seen


def _github_api_headers() -> dict[str, str]:
    """Auth header for api.github.com calls. Without GITHUB_TOKEN env var the
    unauth limit is 60 req/hr shared by host IP — exhausted within a single
    cron run. With token: 5000 req/hr per token."""
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers

logger = get_logger("nakama.franky.news.github_trending")

TRENDING_URL = "https://github.com/trending/python"
PUBLISHER = "GitHub Trending"
FEED_NAME = "github_trending_python"
SOURCE_KEY = "ai_news_blog"
TRUST_TIER = "experimental"
SCORE_CEILING = 4

_HTTP_TIMEOUT = 30
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
_SUMMARY_CAP = 1500
# trending list href 形如 /owner/repo（避開 /owner/repo/stargazers 之類）
_REPO_HREF_RE = re.compile(r"^/([^/\s]+)/([^/\s]+)$")


@dataclass(frozen=True)
class TrendingConfig:
    language: str = "python"
    topic_filter: tuple[str, ...] = ("agent", "llm", "mcp", "claude")
    min_age_days: int = 30
    min_stars: int = 100
    require_readme: bool = True
    require_license: bool = True
    recent_commit_days: int = 90


def load_trending_config(raw: dict | None) -> TrendingConfig:
    """Parse the ``github_trending:`` block from ai_news_sources.yaml."""
    if not raw:
        return TrendingConfig()
    sanity = raw.get("sanity") or {}
    return TrendingConfig(
        language=str(raw.get("language") or "python"),
        topic_filter=tuple(t.lower() for t in (raw.get("topic_filter") or [])),
        min_age_days=int(sanity.get("min_age_days", 30)),
        min_stars=int(sanity.get("min_stars", 100)),
        require_readme=bool(sanity.get("require_readme", True)),
        require_license=bool(sanity.get("require_license", True)),
        recent_commit_days=int(sanity.get("recent_commit_days", 90)),
    )


def gather_candidates(
    cfg: TrendingConfig | None = None,
    *,
    now: datetime | None = None,
    skip_seen: bool = True,
    html_override: str | None = None,
    repo_meta_fetcher: "callable | None" = None,
) -> list[dict]:
    """抓 GitHub trending Python → topic_filter → sanity check → candidate dicts.

    Args:
        cfg:                trending config（缺則用預設）
        now:                當下時間（test 注入）
        skip_seen:          True 用 shared.state.is_seen 過濾已見 item_id
        html_override:      測試注入 trending HTML
        repo_meta_fetcher:  測試注入 (owner, repo) → metadata dict 的函式；
                            production 走 _fetch_repo_metadata
    """
    cfg = cfg or TrendingConfig()
    now = now or datetime.now(timezone.utc)
    fetcher = repo_meta_fetcher or _fetch_repo_metadata

    html = html_override if html_override is not None else _fetch_trending_html(cfg.language)
    if not html:
        logger.info("[github_trending] no HTML fetched, skipping")
        return []

    repos = _parse_trending_repos(html)
    logger.info(f"[github_trending] parsed {len(repos)} trending repos")

    candidates: list[dict] = []
    for repo in repos:
        owner, name = repo["owner"], repo["name"]
        try:
            meta = fetcher(owner, name)
        except Exception as e:
            logger.warning(f"[github_trending] metadata fetch failed for {owner}/{name}: {e}")
            continue
        if meta is None:
            continue

        if not _topic_match(meta, cfg.topic_filter):
            logger.debug(f"[github_trending] {owner}/{name} skipped: topic filter miss")
            continue

        ok, reason = sanity_check(meta, cfg, now=now)
        if not ok:
            logger.info(f"[github_trending] {owner}/{name} dropped sanity: {reason}")
            continue

        item_id = f"github-trending-{owner}-{name}"
        if skip_seen and is_seen(SOURCE_KEY, item_id):
            continue

        url = meta.get("html_url") or f"https://github.com/{owner}/{name}"
        pushed_at = meta.get("pushed_at") or meta.get("updated_at") or ""
        published_ts, published_iso = _parse_iso(pushed_at)
        age_hours = 0.0
        if published_ts > 0:
            age_hours = round((now.timestamp() - published_ts) / 3600.0, 2)

        summary = (meta.get("description") or "").strip()
        topics = meta.get("topics") or []
        if topics:
            summary = (summary + " | topics: " + ", ".join(topics)).strip(" |")

        candidates.append(
            {
                "item_id": item_id,
                "title": f"{owner}/{name} — {meta.get('description') or 'trending'}",
                "publisher": PUBLISHER,
                "feed_name": FEED_NAME,
                "url": url,
                "summary": summary[:_SUMMARY_CAP],
                "published": published_iso,
                "published_ts": published_ts,
                "age_hours": age_hours,
                "trust_tier": TRUST_TIER,
                "score_ceiling": SCORE_CEILING,
            }
        )

    candidates.sort(key=lambda c: c["published_ts"], reverse=True)
    logger.info(
        f"[github_trending] kept {len(candidates)} after topic filter + sanity check + dedupe"
    )
    return candidates


# ---------------------------------------------------------------------------
# Sanity check — exposed so tests can pin each rule with a fixture
# ---------------------------------------------------------------------------


def sanity_check(
    meta: dict, cfg: TrendingConfig, *, now: datetime | None = None
) -> tuple[bool, str]:
    """Return (ok, reason). All five rules must pass; first fail wins.

    Rules（ADR-023 §2 S1）：
      1. age ≥ min_age_days（created_at）
      2. stars ≥ min_stars（stargazers_count）
      3. has README（require_readme=True 時）
      4. has license（require_license=True 時）
      5. 最近 commit 活動：pushed_at 在 recent_commit_days 內
    """
    now = now or datetime.now(timezone.utc)

    created_at = meta.get("created_at") or ""
    created_ts, _ = _parse_iso(created_at)
    if created_ts <= 0:
        return False, "missing created_at"
    age_days = (now.timestamp() - created_ts) / 86400.0
    if age_days < cfg.min_age_days:
        return False, f"too young ({age_days:.0f}d < {cfg.min_age_days}d)"

    stars = int(meta.get("stargazers_count") or 0)
    if stars < cfg.min_stars:
        return False, f"too few stars ({stars} < {cfg.min_stars})"

    if cfg.require_readme and not meta.get("has_readme", False):
        return False, "no README"

    if cfg.require_license:
        lic = meta.get("license")
        # GitHub API 給 dict（spdx_id / key）；空 license 給 None
        if not lic or (isinstance(lic, dict) and not (lic.get("spdx_id") or lic.get("key"))):
            return False, "no license"

    pushed_at = meta.get("pushed_at") or ""
    pushed_ts, _ = _parse_iso(pushed_at)
    if pushed_ts <= 0:
        return False, "missing pushed_at"
    inactive_days = (now.timestamp() - pushed_ts) / 86400.0
    if inactive_days > cfg.recent_commit_days:
        return False, f"stale ({inactive_days:.0f}d since last push)"

    return True, "ok"


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------


def _fetch_trending_html(language: str = "python") -> str | None:
    url = f"https://github.com/trending/{language}"
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
        logger.warning(f"GitHub trending fetch failed: {e}")
        return None


def _fetch_repo_metadata(owner: str, repo: str) -> dict | None:
    """Hit api.github.com/repos/{owner}/{repo} + check README existence.

    Production path. Tests should pass ``repo_meta_fetcher`` to skip network.
    """
    api_url = f"https://api.github.com/repos/{owner}/{repo}"
    try:
        resp = httpx.get(
            api_url,
            headers={"User-Agent": _USER_AGENT, **_github_api_headers()},
            timeout=_HTTP_TIMEOUT,
            follow_redirects=True,
        )
        resp.raise_for_status()
        meta = resp.json()
    except Exception as e:
        logger.warning(f"GitHub repo API failed for {owner}/{repo}: {e}")
        return None

    # README check — separate endpoint
    has_readme = False
    try:
        readme_resp = httpx.get(
            f"https://api.github.com/repos/{owner}/{repo}/readme",
            headers={"User-Agent": _USER_AGENT, **_github_api_headers()},
            timeout=_HTTP_TIMEOUT,
            follow_redirects=True,
        )
        has_readme = readme_resp.status_code == 200
    except Exception:
        has_readme = False

    meta["has_readme"] = has_readme
    return meta


# ---------------------------------------------------------------------------
# Pure parsers
# ---------------------------------------------------------------------------


def _parse_trending_repos(html: str) -> list[dict]:
    """Extract owner/name from /trending/python HTML.

    Each repo card has an <h2> with a <a href="/owner/repo">. Star count lives
    next to a SVG with class octicon-star — but for sanity we re-fetch via API,
    so the trending HTML only needs to give us (owner, repo).
    """
    soup = BeautifulSoup(html, "html.parser")
    seen: set[tuple[str, str]] = set()
    repos: list[dict] = []
    for h2 in soup.find_all("h2"):
        a = h2.find("a", href=True)
        if a is None:
            continue
        m = _REPO_HREF_RE.match(a["href"].strip())
        if not m:
            continue
        owner, name = m.group(1), m.group(2)
        if (owner, name) in seen:
            continue
        seen.add((owner, name))
        repos.append({"owner": owner, "name": name})
    return repos


def _topic_match(meta: dict, topic_filter: Iterable[str]) -> bool:
    """Return True if any filter token appears in repo topics or description."""
    filters = [t.lower() for t in topic_filter if t]
    if not filters:
        return True
    haystack = " ".join(
        [
            (meta.get("description") or ""),
            " ".join(meta.get("topics") or []),
            (meta.get("name") or ""),
        ]
    ).lower()
    return any(f in haystack for f in filters)


def _parse_iso(value: str) -> tuple[float, str]:
    if not value:
        return 0.0, ""
    try:
        dt = dateparser.parse(value)
    except (ValueError, OverflowError):
        return 0.0, ""
    if dt is None:
        return 0.0, ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp(), dt.isoformat()


def _hash_id(*parts: Any) -> str:
    h = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return h[:16]


__all__ = [
    "TrendingConfig",
    "TRUST_TIER",
    "SCORE_CEILING",
    "load_trending_config",
    "gather_candidates",
    "sanity_check",
]
