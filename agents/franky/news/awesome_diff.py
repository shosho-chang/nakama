"""Awesome-list README diff fetcher — Franky news S1 full-trust source.

修修盯著兩個 awesome-list 看 ecosystem signal：
  - awesome-mcp-servers — MCP server 新增 → 反映 MCP 生態擴張
  - awesome-claude-code — Claude Code 周邊工具 → 反映 workflow 升級

實作策略：
  - 透過 GitHub Contents API 抓 README.md 當前內容 + 一個 lookback window
    內的 commit 點 README 內容，比較兩版差異（新增的 markdown link 行）
  - 每條新增的 ``- [Name](url) — desc`` 變成一條候選
  - 純文字 diff（不需 git binary），呼叫 GitHub REST API：
      GET /repos/{repo}/contents/{path}?ref={sha}     回 README at sha
      GET /repos/{repo}/commits?path={path}&per_page=N 回 README 改過的 commit list

候選 schema 對齊 official_blogs.gather_candidates，可直接 merge 進 news_digest。
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import httpx

from shared.log import get_logger
from shared.state import is_seen


def _github_api_headers() -> dict[str, str]:
    """Auth header for api.github.com calls. Without GITHUB_TOKEN env var the
    unauth limit is 60 req/hr shared by host IP — combined with github_trending
    that exhausts in a single run. With token: 5000 req/hr per token."""
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


logger = get_logger("nakama.franky.news.awesome_diff")

PUBLISHER_PREFIX = "Awesome"
FEED_NAME = "awesome_diff"
SOURCE_KEY = "ai_news_blog"

_HTTP_TIMEOUT = 30
_USER_AGENT = "Mozilla/5.0 (compatible; Nakama-Franky/0.1; +https://github.com/shosho-chang/nakama)"
_API = "https://api.github.com"
_SUMMARY_CAP = 1500
# Match "- [Name](url) — desc" or "- [Name](url) - desc" (allow blank desc)
_LINK_LINE_RE = re.compile(r"^\s*[-*]\s*\[([^\]]+)\]\(([^)]+)\)\s*[—\-:]?\s*(.*)$")


@dataclass(frozen=True)
class AwesomeRepoConfig:
    name: str
    repo: str  # "owner/repo"
    path: str = "README.md"
    publisher: str = ""

    @property
    def display_publisher(self) -> str:
        return self.publisher or self.repo


def load_awesome_configs(raw: list | None) -> list[AwesomeRepoConfig]:
    """Parse the ``awesome_diff:`` block from ai_news_sources.yaml."""
    if not raw:
        return []
    out: list[AwesomeRepoConfig] = []
    for entry in raw:
        name = (entry.get("name") or "").strip()
        repo = (entry.get("repo") or "").strip()
        path = (entry.get("path") or "README.md").strip()
        publisher = (entry.get("publisher") or "").strip()
        if not name or not repo:
            logger.warning(f"Skipping malformed awesome_diff entry: {entry!r}")
            continue
        out.append(AwesomeRepoConfig(name=name, repo=repo, path=path, publisher=publisher))
    return out


def gather_candidates(
    configs: Iterable[AwesomeRepoConfig],
    *,
    now: datetime | None = None,
    lookback_days: float = 7.0,
    skip_seen: bool = True,
    readme_pair_fetcher: "callable | None" = None,
) -> list[dict]:
    """For each repo, diff README between current and the most recent commit
    older than ``lookback_days`` ago. New ``- [Name](url) — desc`` lines become
    candidates.

    Args:
        configs:              awesome repo configs
        now:                  test-injectable now (UTC)
        lookback_days:        compare against the README state this far back
        skip_seen:            shared.state.is_seen filter
        readme_pair_fetcher:  test injectable (cfg, now, lookback) → (old_text, new_text, ts)
    """
    now = now or datetime.now(timezone.utc)
    fetcher = readme_pair_fetcher or _fetch_readme_pair

    candidates: list[dict] = []
    for cfg in configs:
        try:
            pair = fetcher(cfg, now, lookback_days)
        except Exception as e:
            logger.warning(f"[awesome_diff] {cfg.name} fetch failed: {e}")
            continue
        if pair is None:
            logger.info(f"[awesome_diff] {cfg.name} no diff window available")
            continue
        old_text, new_text, change_ts = pair

        new_links = diff_added_links(old_text, new_text)
        if not new_links:
            logger.info(f"[awesome_diff] {cfg.name} no new links in window")
            continue

        for link in new_links:
            item_id = f"awesome-{cfg.name}-{_hash_id(link['url'])}"
            if skip_seen and is_seen(SOURCE_KEY, item_id):
                continue
            published_iso = (
                datetime.fromtimestamp(change_ts, tz=timezone.utc).isoformat()
                if change_ts > 0
                else ""
            )
            age_hours = round((now.timestamp() - change_ts) / 3600.0, 2) if change_ts > 0 else 0.0
            summary = link["desc"] or f"New entry in {cfg.name}"
            candidates.append(
                {
                    "item_id": item_id,
                    "title": f"[{cfg.name}] {link['name']}",
                    "publisher": f"{PUBLISHER_PREFIX} · {cfg.display_publisher}",
                    "feed_name": f"{FEED_NAME}:{cfg.name}",
                    "url": link["url"],
                    "summary": summary[:_SUMMARY_CAP],
                    "published": published_iso,
                    "published_ts": float(change_ts),
                    "age_hours": age_hours,
                }
            )

    candidates.sort(key=lambda c: c["published_ts"], reverse=True)
    logger.info(f"[awesome_diff] kept {len(candidates)} candidate(s) across all awesome lists")
    return candidates


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def diff_added_links(old_text: str, new_text: str) -> list[dict]:
    """Return list of {name, url, desc} for markdown link bullets present in
    ``new_text`` but not in ``old_text``. Uniqueness keyed on URL.
    """
    old_urls = {
        m.group(2) for line in (old_text or "").splitlines() if (m := _LINK_LINE_RE.match(line))
    }
    out: list[dict] = []
    seen_urls: set[str] = set()
    for line in (new_text or "").splitlines():
        m = _LINK_LINE_RE.match(line)
        if not m:
            continue
        name, url, desc = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
        if not url or url in old_urls or url in seen_urls:
            continue
        seen_urls.add(url)
        out.append({"name": name, "url": url, "desc": desc})
    return out


def _fetch_readme_pair(
    cfg: AwesomeRepoConfig, now: datetime, lookback_days: float
) -> tuple[str, str, float] | None:
    """Production path: hit GitHub API for current README + most recent commit
    on this path that is older than the lookback boundary.
    """
    cutoff = now - timedelta(days=lookback_days)
    headers = {"User-Agent": _USER_AGENT, **_github_api_headers()}

    # 1. List commits touching this path
    try:
        resp = httpx.get(
            f"{_API}/repos/{cfg.repo}/commits",
            params={"path": cfg.path, "per_page": "30"},
            headers=headers,
            timeout=_HTTP_TIMEOUT,
            follow_redirects=True,
        )
        resp.raise_for_status()
        commits = resp.json() or []
    except Exception as e:
        logger.warning(f"[awesome_diff] commits API failed for {cfg.repo}: {e}")
        return None

    # Find the newest commit older than cutoff (== "old" anchor)
    old_sha: str | None = None
    newest_ts = 0.0
    for c in commits:
        date_str = (c.get("commit") or {}).get("author", {}).get("date") or ""
        try:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except ValueError:
            continue
        ts = dt.timestamp()
        newest_ts = max(newest_ts, ts)
        if dt < cutoff:
            old_sha = c.get("sha")
            break
    if old_sha is None:
        # No "older than cutoff" commit available — skip
        return None

    new_text = _fetch_readme(cfg.repo, cfg.path, ref=None, headers=headers) or ""
    old_text = _fetch_readme(cfg.repo, cfg.path, ref=old_sha, headers=headers) or ""
    return old_text, new_text, newest_ts


def _fetch_readme(repo: str, path: str, *, ref: str | None, headers: dict) -> str | None:
    params = {"ref": ref} if ref else {}
    try:
        resp = httpx.get(
            f"{_API}/repos/{repo}/contents/{path}",
            params=params,
            headers=headers,
            timeout=_HTTP_TIMEOUT,
            follow_redirects=True,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f"[awesome_diff] contents API failed for {repo}@{ref}: {e}")
        return None
    content = data.get("content") or ""
    encoding = data.get("encoding") or "base64"
    if encoding == "base64":
        try:
            return base64.b64decode(content).decode("utf-8", errors="replace")
        except Exception:
            return None
    return content


def _hash_id(*parts: Any) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]


__all__ = [
    "AwesomeRepoConfig",
    "load_awesome_configs",
    "gather_candidates",
    "diff_added_links",
]
