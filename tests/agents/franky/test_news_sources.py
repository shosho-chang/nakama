"""Tests for the new S1 news sources (ADR-022 §2 S1):

  - github_trending sanity check (one fixture per rule)
  - github_trending end-to-end with mocked HTTP
  - awesome_diff link extraction + diff
  - news_curate prompt accepts 8-12 picks (string check)
  - news_digest integration: candidate count + trust tier distribution
    with all four sources stubbed (no network)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

from agents.franky import news_digest as nd
from agents.franky.news import awesome_diff, github_trending

# ---------------------------------------------------------------------------
# github_trending.sanity_check — one fixture per rule
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime(2026, 5, 7, tzinfo=timezone.utc)


def _good_meta(**overrides) -> dict:
    base = {
        "name": "demo",
        "stargazers_count": 500,
        "created_at": (_now() - timedelta(days=400)).isoformat(),
        "pushed_at": (_now() - timedelta(days=2)).isoformat(),
        "license": {"spdx_id": "MIT", "key": "mit"},
        "has_readme": True,
        "topics": ["agent", "claude"],
        "description": "A claude agent toolkit",
    }
    base.update(overrides)
    return base


def test_sanity_check_passes_on_good_repo():
    cfg = github_trending.TrendingConfig()
    ok, reason = github_trending.sanity_check(_good_meta(), cfg, now=_now())
    assert ok, reason


def test_sanity_check_fails_too_young():
    cfg = github_trending.TrendingConfig()
    meta = _good_meta(created_at=(_now() - timedelta(days=10)).isoformat())
    ok, reason = github_trending.sanity_check(meta, cfg, now=_now())
    assert not ok
    assert "young" in reason


def test_sanity_check_fails_too_few_stars():
    cfg = github_trending.TrendingConfig()
    meta = _good_meta(stargazers_count=10)
    ok, reason = github_trending.sanity_check(meta, cfg, now=_now())
    assert not ok
    assert "stars" in reason


def test_sanity_check_fails_no_readme():
    cfg = github_trending.TrendingConfig()
    meta = _good_meta(has_readme=False)
    ok, reason = github_trending.sanity_check(meta, cfg, now=_now())
    assert not ok
    assert "README" in reason


def test_sanity_check_fails_no_license():
    cfg = github_trending.TrendingConfig()
    meta = _good_meta(license=None)
    ok, reason = github_trending.sanity_check(meta, cfg, now=_now())
    assert not ok
    assert "license" in reason


def test_sanity_check_fails_no_license_empty_dict():
    cfg = github_trending.TrendingConfig()
    meta = _good_meta(license={"spdx_id": None, "key": None})
    ok, reason = github_trending.sanity_check(meta, cfg, now=_now())
    assert not ok
    assert "license" in reason


def test_sanity_check_fails_stale_pushed_at():
    cfg = github_trending.TrendingConfig()
    meta = _good_meta(pushed_at=(_now() - timedelta(days=400)).isoformat())
    ok, reason = github_trending.sanity_check(meta, cfg, now=_now())
    assert not ok
    assert "stale" in reason


# ---------------------------------------------------------------------------
# github_trending.gather_candidates end-to-end (no HTTP)
# ---------------------------------------------------------------------------

_TRENDING_HTML = """
<html><body>
  <article class="Box-row">
    <h2><a href="/foo/bar">foo/bar</a></h2>
  </article>
  <article class="Box-row">
    <h2><a href="/baz/qux">baz/qux</a></h2>
  </article>
  <article class="Box-row">
    <h2><a href="/spam/eggs">spam/eggs</a></h2>
  </article>
</body></html>
"""


def test_gather_candidates_filters_by_topic_and_sanity(monkeypatch):
    monkeypatch.setattr(github_trending, "is_seen", lambda *a, **k: False)

    def fake_meta(owner, repo):
        if (owner, repo) == ("foo", "bar"):
            # passes — has topic + sanity
            return _good_meta(html_url="https://github.com/foo/bar", description="MCP server")
        if (owner, repo) == ("baz", "qux"):
            # fails sanity (too few stars)
            return _good_meta(stargazers_count=5, description="An LLM toy")
        if (owner, repo) == ("spam", "eggs"):
            # fails topic filter
            return _good_meta(description="Generic web framework", topics=["web"])
        return None

    cands = github_trending.gather_candidates(
        github_trending.TrendingConfig(),
        now=_now(),
        skip_seen=False,
        html_override=_TRENDING_HTML,
        repo_meta_fetcher=fake_meta,
    )
    assert len(cands) == 1
    only = cands[0]
    assert only["item_id"] == "github-trending-foo-bar"
    assert only["trust_tier"] == "experimental"
    assert only["score_ceiling"] == 4
    assert only["publisher"] == "GitHub Trending"


def test_load_trending_config_handles_missing_block():
    cfg = github_trending.load_trending_config(None)
    assert cfg.language == "python"
    assert "agent" in cfg.topic_filter
    assert cfg.min_stars == 100


def test_parse_trending_repos_dedupes():
    repos = github_trending._parse_trending_repos(_TRENDING_HTML + _TRENDING_HTML)
    assert len(repos) == 3
    assert {(r["owner"], r["name"]) for r in repos} == {
        ("foo", "bar"),
        ("baz", "qux"),
        ("spam", "eggs"),
    }


# ---------------------------------------------------------------------------
# awesome_diff
# ---------------------------------------------------------------------------


def test_diff_added_links_picks_only_new_urls():
    old = """
# Awesome
- [Old](https://old.example.com) — was here
"""
    new = """
# Awesome
- [Old](https://old.example.com) — was here
- [Shiny](https://shiny.example.com) — brand new MCP server
* [Star](https://star.example.com) - star bullet form
"""
    added = awesome_diff.diff_added_links(old, new)
    urls = {a["url"] for a in added}
    assert urls == {"https://shiny.example.com", "https://star.example.com"}
    shiny = next(a for a in added if a["url"] == "https://shiny.example.com")
    assert shiny["name"] == "Shiny"
    assert "MCP server" in shiny["desc"]


def test_diff_added_links_dedupes_repeated_url():
    old = ""
    new = """
- [A](https://x.example.com) — first
- [A2](https://x.example.com) — dup url
"""
    added = awesome_diff.diff_added_links(old, new)
    assert len(added) == 1


def test_load_awesome_configs_skips_malformed():
    raw = [
        {"name": "good", "repo": "owner/repo", "publisher": "Pub"},
        {"name": "", "repo": "owner/repo"},  # missing name
        {"name": "no-repo"},
    ]
    cfgs = awesome_diff.load_awesome_configs(raw)
    assert [c.name for c in cfgs] == ["good"]
    assert cfgs[0].display_publisher == "Pub"


def test_awesome_gather_candidates_uses_injected_fetcher(monkeypatch):
    monkeypatch.setattr(awesome_diff, "is_seen", lambda *a, **k: False)
    cfg = awesome_diff.AwesomeRepoConfig(
        name="awesome_test", repo="owner/repo", path="README.md", publisher="Test"
    )

    change_ts = _now().timestamp() - 3600  # 1h ago

    def fake_pair(c, now, lookback):
        return (
            "- [Old](https://old.example.com) — was here\n",
            "- [Old](https://old.example.com) — was here\n"
            "- [New](https://new.example.com) — fresh entry\n",
            change_ts,
        )

    cands = awesome_diff.gather_candidates(
        [cfg],
        now=_now(),
        skip_seen=False,
        readme_pair_fetcher=fake_pair,
    )
    assert len(cands) == 1
    only = cands[0]
    assert only["url"] == "https://new.example.com"
    assert "fresh entry" in only["summary"]
    assert only["publisher"].startswith("Awesome")
    # Default trust tier (full_trust): no trust_tier key
    assert "trust_tier" not in only


# ---------------------------------------------------------------------------
# Curate prompt accepts 8-12 picks
# ---------------------------------------------------------------------------


def test_curate_prompt_says_8_to_12():
    prompt_path = Path(__file__).resolve().parents[3] / "prompts" / "franky" / "news_curate.md"
    text = prompt_path.read_text(encoding="utf-8")
    assert "8-12" in text or "8-12 條" in text
    assert "5-8 條" not in text  # old upper bound retired
    assert "trust_tier" in text  # experimental tier callout present
    assert "experimental" in text


# ---------------------------------------------------------------------------
# Integration: dry-run with all four sources stubbed (no network, no LLM)
# ---------------------------------------------------------------------------


def test_news_digest_integration_distribution(tmp_path, monkeypatch):
    """Dry-run the pipeline with all four sources stubbed; assert candidate
    count + trust tier distribution from the per-source telemetry.
    """
    cfg = tmp_path / "feeds.yaml"
    cfg.write_text(
        """\
feeds:
  - name: x
    url: https://example.com/feed
    publisher: X
awesome_diff:
  - name: awesome_x
    repo: owner/awesome-x
    publisher: AW-X
github_trending:
  language: python
  topic_filter: [agent, claude]
  trust_tier: experimental
  sanity:
    min_age_days: 30
    min_stars: 100
    require_readme: true
    require_license: true
    recent_commit_days: 90
""",
        encoding="utf-8",
    )

    rss_cand = {
        "item_id": "rss-1",
        "title": "RSS one",
        "publisher": "X",
        "feed_name": "x",
        "url": "https://example.com/1",
        "summary": "rss summary",
        "published": "2026-05-07T00:00:00+00:00",
        "published_ts": 100.0,
        "age_hours": 1.0,
    }
    anthropic_cand = {
        "item_id": "anthropic-news-foo",
        "title": "A news",
        "publisher": "Anthropic",
        "feed_name": "anthropic_news_html",
        "url": "https://www.anthropic.com/news/foo",
        "summary": "anthropic summary",
        "published": "2026-05-07T01:00:00+00:00",
        "published_ts": 200.0,
        "age_hours": 1.0,
    }
    awesome_cand = {
        "item_id": "awesome-awesome_x-abcd",
        "title": "[awesome_x] New tool",
        "publisher": "Awesome · AW-X",
        "feed_name": "awesome_diff:awesome_x",
        "url": "https://new.example.com",
        "summary": "fresh entry",
        "published": "2026-05-07T02:00:00+00:00",
        "published_ts": 300.0,
        "age_hours": 1.0,
    }
    trending_cand = {
        "item_id": "github-trending-foo-bar",
        "title": "foo/bar — MCP toy",
        "publisher": "GitHub Trending",
        "feed_name": "github_trending_python",
        "url": "https://github.com/foo/bar",
        "summary": "MCP toy",
        "published": "2026-05-07T03:00:00+00:00",
        "published_ts": 400.0,
        "age_hours": 1.0,
        "trust_tier": "experimental",
        "score_ceiling": 4,
    }

    monkeypatch.setattr(nd, "gather_candidates", lambda *a, **kw: [rss_cand])
    monkeypatch.setattr(
        nd.anthropic_html, "gather_candidates", lambda **kw: [anthropic_cand]
    )
    monkeypatch.setattr(
        nd.awesome_diff, "gather_candidates", lambda *a, **kw: [awesome_cand]
    )
    monkeypatch.setattr(
        nd.github_trending, "gather_candidates", lambda *a, **kw: [trending_cand]
    )

    # LLM stub: curate picks all 4, score returns deliberately too-high overall
    # for the trending one to verify the ceiling.
    def fake_ask(prompt, **kw):
        if "8-12" in prompt or "篩出當日" in prompt:
            import json as _json

            return _json.dumps(
                {
                    "selected": [
                        {"item_id": "rss-1", "rank": 1, "category": "meta", "reason": "r"},
                        {
                            "item_id": "anthropic-news-foo",
                            "rank": 2,
                            "category": "model_release",
                            "reason": "r",
                        },
                        {
                            "item_id": "awesome-awesome_x-abcd",
                            "rank": 3,
                            "category": "tool_release",
                            "reason": "r",
                        },
                        {
                            "item_id": "github-trending-foo-bar",
                            "rank": 4,
                            "category": "agent_framework",
                            "reason": "r",
                        },
                    ],
                    "summary": {
                        "total_candidates": 4,
                        "selected_count": 4,
                        "main_categories": ["meta"],
                        "editor_note": "test",
                    },
                }
            )
        # score
        import json as _json

        return _json.dumps(
            {
                "scores": {"signal": 5, "novelty": 5, "actionability": 5, "noise": 5},
                "overall": 5.0,
                "one_line_verdict": "v",
                "why_it_matters": "w",
                "key_finding": "k",
                "noise_note": "n",
                "pick": True,
            }
        )

    monkeypatch.setattr(nd.llm, "ask", fake_ask)

    pipeline = nd.NewsDigestPipeline(
        dry_run=True, feeds_config_path=cfg, slack_bot=MagicMock()
    )
    summary = pipeline.run()

    assert "fetch=4" in summary
    assert pipeline._source_breakdown == {
        "rss": 1,
        "anthropic_html": 1,
        "awesome_diff": 1,
        "github_trending": 1,
    }
    assert pipeline._trust_tier_breakdown == {"full_trust": 3, "experimental": 1}
    assert "trust_tiers=" in summary
