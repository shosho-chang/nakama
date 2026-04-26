"""Metadata + OpenGraph + Twitter Card check tests — 9 條 rule × ≥3 case。"""

from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from shared.seo_audit.metadata import check_metadata


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _by_rule(checks, rule_id):
    return next(c for c in checks if c.rule_id == rule_id)


# ── M1: title 50-60 ──


def test_m1_pass():
    title = "x" * 55
    checks = check_metadata(_soup(f"<html><head><title>{title}</title></head></html>"), "https://x")
    c = _by_rule(checks, "M1")
    assert c.status == "pass"


def test_m1_too_short():
    title = "x" * 30
    checks = check_metadata(_soup(f"<html><head><title>{title}</title></head></html>"), "https://x")
    c = _by_rule(checks, "M1")
    assert c.status == "warn"
    assert "30" in c.actual


def test_m1_too_long():
    title = "x" * 80
    checks = check_metadata(_soup(f"<html><head><title>{title}</title></head></html>"), "https://x")
    c = _by_rule(checks, "M1")
    assert c.status == "warn"


def test_m1_missing():
    checks = check_metadata(_soup("<html><head></head></html>"), "https://x")
    c = _by_rule(checks, "M1")
    assert c.status == "fail"


def test_m1_unicode_counts_chars_not_bytes():
    """CJK 字符計數同 ASCII（每字 1 個 char），55 個中文字應 pass。"""
    title = "睡" * 55
    checks = check_metadata(_soup(f"<html><head><title>{title}</title></head></html>"), "https://x")
    c = _by_rule(checks, "M1")
    assert len(title) == 55
    assert c.status == "pass"


def test_m1_nested_tags_in_title_not_misreported_as_missing():
    """`<title>` containing nested elements should be read via get_text(), not
    `.string` (which returns None for nested-tag content)."""
    title_inner = "Hi " + "x" * 50 + " World"
    html = f"<html><head><title>Hi <b>{'x' * 50}</b> World</title></head></html>"
    checks = check_metadata(_soup(html), "https://x")
    c = _by_rule(checks, "M1")
    # Length of "Hi " + 50 'x' + " World" = 3 + 50 + 6 = 59 → in pass range
    assert len(title_inner) == 59
    assert c.status == "pass", f"got status={c.status}, actual={c.actual!r}"
    assert "缺少 <title>" not in c.actual


# ── M2: meta description 150-160 ──


def test_m2_pass():
    desc = "y" * 155
    checks = check_metadata(
        _soup(f'<html><head><meta name="description" content="{desc}"></head></html>'),
        "https://x",
    )
    c = _by_rule(checks, "M2")
    assert c.status == "pass"


def test_m2_too_short():
    desc = "y" * 100
    checks = check_metadata(
        _soup(f'<html><head><meta name="description" content="{desc}"></head></html>'),
        "https://x",
    )
    c = _by_rule(checks, "M2")
    assert c.status == "warn"


def test_m2_too_long():
    desc = "y" * 200
    checks = check_metadata(
        _soup(f'<html><head><meta name="description" content="{desc}"></head></html>'),
        "https://x",
    )
    c = _by_rule(checks, "M2")
    assert c.status == "warn"


def test_m2_missing():
    checks = check_metadata(_soup("<html><head></head></html>"), "https://x")
    c = _by_rule(checks, "M2")
    assert c.status == "fail"


# ── M3: canonical ──


def test_m3_pass_self():
    html = '<html><head><link rel="canonical" href="https://shosho.tw/post-a"></head></html>'
    checks = check_metadata(_soup(html), "https://shosho.tw/post-a")
    c = _by_rule(checks, "M3")
    assert c.status == "pass"


def test_m3_pass_trailing_slash_normalized():
    html = '<html><head><link rel="canonical" href="https://shosho.tw/post-a/"></head></html>'
    checks = check_metadata(_soup(html), "https://shosho.tw/post-a")
    c = _by_rule(checks, "M3")
    assert c.status == "pass"


def test_m3_missing():
    checks = check_metadata(_soup("<html><head></head></html>"), "https://shosho.tw/post-a")
    c = _by_rule(checks, "M3")
    assert c.status == "fail"
    assert c.severity == "critical"


def test_m3_wrong_target():
    html = '<html><head><link rel="canonical" href="https://other.com/post"></head></html>'
    checks = check_metadata(_soup(html), "https://shosho.tw/post-a")
    c = _by_rule(checks, "M3")
    assert c.status == "fail"


def test_m3_relative_canonical_resolved_against_page_url():
    """Relative canonical hrefs must be resolved against page_url before
    comparison; previously a relative href like ``/post-a`` always failed."""
    html = '<html><head><link rel="canonical" href="/post-a"></head></html>'
    checks = check_metadata(_soup(html), "https://shosho.tw/post-a")
    c = _by_rule(checks, "M3")
    assert c.status == "pass", f"got actual={c.actual!r}"


def test_m3_uppercase_host_treated_as_self_match():
    """Hosts are case-insensitive (RFC 3986); ``Example.COM`` must match
    ``example.com``."""
    html = '<html><head><link rel="canonical" href="https://Example.COM/post"></head></html>'
    checks = check_metadata(_soup(html), "https://example.com/post")
    c = _by_rule(checks, "M3")
    assert c.status == "pass"


def test_m3_query_string_difference_is_not_self_match():
    """Stripping the query string used to make ``?utm=x`` look like a
    self-canonical even when it isn't. Now query strings are preserved."""
    html = '<html><head><link rel="canonical" href="https://shosho.tw/post-a?utm=campaign"></head></html>'
    checks = check_metadata(_soup(html), "https://shosho.tw/post-a")
    c = _by_rule(checks, "M3")
    assert c.status == "fail", f"got actual={c.actual!r}"


def test_m3_query_string_match_is_self_match():
    """If both URLs have the same query string they should match."""
    html = (
        '<html><head><link rel="canonical" href="https://shosho.tw/post-a?lang=en"></head></html>'
    )
    checks = check_metadata(_soup(html), "https://shosho.tw/post-a?lang=en")
    c = _by_rule(checks, "M3")
    assert c.status == "pass"


# ── M4: robots noindex ──


def test_m4_pass_no_robots():
    checks = check_metadata(_soup("<html><head></head></html>"), "https://x")
    c = _by_rule(checks, "M4")
    assert c.status == "pass"


def test_m4_pass_index_follow():
    html = '<html><head><meta name="robots" content="index, follow"></head></html>'
    c = _by_rule(check_metadata(_soup(html), "https://x"), "M4")
    assert c.status == "pass"


def test_m4_fail_noindex():
    html = '<html><head><meta name="robots" content="noindex, follow"></head></html>'
    c = _by_rule(check_metadata(_soup(html), "https://x"), "M4")
    assert c.status == "fail"
    assert c.severity == "critical"


# ── M5: viewport ──


def test_m5_pass():
    html = (
        '<html><head><meta name="viewport" '
        'content="width=device-width, initial-scale=1"></head></html>'
    )
    c = _by_rule(check_metadata(_soup(html), "https://x"), "M5")
    assert c.status == "pass"


def test_m5_warn_no_device_width():
    html = '<html><head><meta name="viewport" content="initial-scale=1"></head></html>'
    c = _by_rule(check_metadata(_soup(html), "https://x"), "M5")
    assert c.status == "warn"


def test_m5_missing():
    c = _by_rule(check_metadata(_soup("<html><head></head></html>"), "https://x"), "M5")
    assert c.status == "fail"


# ── O1: og:title + og:description ──


def test_o1_pass():
    html = """
    <html><head>
    <meta property="og:title" content="標題">
    <meta property="og:description" content="描述">
    </head></html>
    """
    c = _by_rule(check_metadata(_soup(html), "https://x"), "O1")
    assert c.status == "pass"


def test_o1_fail_missing_desc():
    html = '<html><head><meta property="og:title" content="t"></head></html>'
    c = _by_rule(check_metadata(_soup(html), "https://x"), "O1")
    assert c.status == "fail"
    assert "og:description" in c.actual


def test_o1_fail_missing_both():
    c = _by_rule(check_metadata(_soup("<html><head></head></html>"), "https://x"), "O1")
    assert c.status == "fail"


# ── O2: og:image meta ──


def test_o2_pass():
    html = '<html><head><meta property="og:image" content="https://x/img.jpg"></head></html>'
    c = _by_rule(check_metadata(_soup(html), "https://x"), "O2")
    assert c.status == "pass"


def test_o2_fail_missing():
    c = _by_rule(check_metadata(_soup("<html><head></head></html>"), "https://x"), "O2")
    assert c.status == "fail"


def test_o2_pass_with_other_og_present():
    html = """
    <html><head>
    <meta property="og:title" content="t">
    <meta property="og:image" content="https://x/img.jpg">
    </head></html>
    """
    c = _by_rule(check_metadata(_soup(html), "https://x"), "O2")
    assert c.status == "pass"


# ── O3: og:url == canonical ──


def test_o3_pass_match():
    html = """
    <html><head>
    <meta property="og:url" content="https://x.com/post">
    <link rel="canonical" href="https://x.com/post">
    </head></html>
    """
    c = _by_rule(check_metadata(_soup(html), "https://x.com/post"), "O3")
    assert c.status == "pass"


def test_o3_warn_missing_og_url():
    html = '<html><head><link rel="canonical" href="https://x.com/post"></head></html>'
    c = _by_rule(check_metadata(_soup(html), "https://x.com/post"), "O3")
    assert c.status == "warn"


def test_o3_skip_both_missing():
    c = _by_rule(check_metadata(_soup("<html><head></head></html>"), "https://x"), "O3")
    assert c.status == "skip"


def test_o3_warn_mismatch():
    html = """
    <html><head>
    <meta property="og:url" content="https://x.com/old">
    <link rel="canonical" href="https://x.com/new">
    </head></html>
    """
    c = _by_rule(check_metadata(_soup(html), "https://x.com/new"), "O3")
    assert c.status == "warn"
    assert "og:url=" in c.actual


# ── O4: twitter:card ──


@pytest.mark.parametrize("value", ["summary", "summary_large_image", "app", "player"])
def test_o4_pass(value):
    html = f'<html><head><meta name="twitter:card" content="{value}"></head></html>'
    c = _by_rule(check_metadata(_soup(html), "https://x"), "O4")
    assert c.status == "pass"


def test_o4_warn_missing():
    c = _by_rule(check_metadata(_soup("<html><head></head></html>"), "https://x"), "O4")
    assert c.status == "warn"


def test_o4_warn_invalid_value():
    html = '<html><head><meta name="twitter:card" content="bogus"></head></html>'
    c = _by_rule(check_metadata(_soup(html), "https://x"), "O4")
    assert c.status == "warn"


# ── 整體 ──


def test_check_metadata_returns_9_checks():
    checks = check_metadata(_soup("<html><head></head></html>"), "https://x")
    rule_ids = [c.rule_id for c in checks]
    assert rule_ids == ["M1", "M2", "M3", "M4", "M5", "O1", "O2", "O3", "O4"]
