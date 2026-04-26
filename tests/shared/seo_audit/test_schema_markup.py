"""Schema markup checks tests — SC1-SC5 共 5 rule × ≥3 case。

特別覆蓋：
- 多個 ld+json script tag、@graph 包裝、array 都能展平
- @type 是 list（多型）也能 match
- Article 子類（NewsArticle / BlogPosting / MedicalWebPage）
- JSON syntax error 隔離出 SC4，SC1-3/5 status=skip
"""

from __future__ import annotations

import json

from bs4 import BeautifulSoup

from shared.seo_audit.schema_markup import check_schema_markup


def _soup_with_jsonld(*payloads) -> BeautifulSoup:
    """payloads 可以是 dict / list / str（str 直接塞，可造 syntax error）。"""
    blocks = []
    for p in payloads:
        if isinstance(p, str):
            blocks.append(f'<script type="application/ld+json">{p}</script>')
        else:
            blocks.append(f'<script type="application/ld+json">{json.dumps(p)}</script>')
    html = f"<html><head>{''.join(blocks)}</head><body></body></html>"
    return BeautifulSoup(html, "html.parser")


def _by(checks, rid):
    return next(c for c in checks if c.rule_id == rid)


# ── SC4 parse error ──


def test_sc4_pass_clean():
    soup = _soup_with_jsonld({"@type": "Article", "headline": "x"})
    c = _by(check_schema_markup(soup), "SC4")
    assert c.status == "pass"


def test_sc4_pass_no_blocks():
    soup = BeautifulSoup("<html><head></head><body></body></html>", "html.parser")
    c = _by(check_schema_markup(soup), "SC4")
    assert c.status == "pass"


def test_sc4_fail_syntax_error():
    soup = _soup_with_jsonld('{"@type": "Article",}')  # trailing comma
    c = _by(check_schema_markup(soup), "SC4")
    assert c.status == "fail"


def test_sc4_fail_partial_error():
    """3 個 block, 1 個壞 → fail with detail count。"""
    soup = _soup_with_jsonld(
        {"@type": "Article", "headline": "ok"},
        '{"@type": "Bad",}',  # broken
        {"@type": "WebSite", "name": "ok2"},
    )
    c = _by(check_schema_markup(soup), "SC4")
    assert c.status == "fail"
    assert c.details["error_count"] == 1


# ── SC1 Article ──


def test_sc1_pass_article():
    soup = _soup_with_jsonld({"@type": "Article", "headline": "x"})
    c = _by(check_schema_markup(soup), "SC1")
    assert c.status == "pass"


def test_sc1_pass_blogposting():
    soup = _soup_with_jsonld({"@type": "BlogPosting", "headline": "x"})
    c = _by(check_schema_markup(soup), "SC1")
    assert c.status == "pass"


def test_sc1_pass_via_graph():
    soup = _soup_with_jsonld(
        {
            "@context": "https://schema.org",
            "@graph": [
                {"@type": "WebSite", "name": "shosho"},
                {"@type": "MedicalWebPage", "headline": "x"},
            ],
        }
    )
    c = _by(check_schema_markup(soup), "SC1")
    assert c.status == "pass"
    assert "MedicalWebPage" in c.actual


def test_sc1_fail_no_article():
    soup = _soup_with_jsonld({"@type": "WebSite", "name": "x"})
    c = _by(check_schema_markup(soup), "SC1")
    assert c.status == "fail"


def test_sc1_skip_when_parse_error():
    soup = _soup_with_jsonld('{"@type":"Article",,}')
    c = _by(check_schema_markup(soup), "SC1")
    assert c.status == "skip"


def test_sc1_pass_when_type_is_list():
    """@type 可能是 ["Article", "MedicalWebPage"]。"""
    soup = _soup_with_jsonld({"@type": ["MedicalWebPage", "Article"], "headline": "x"})
    c = _by(check_schema_markup(soup), "SC1")
    assert c.status == "pass"


# ── SC2 BreadcrumbList ──


def test_sc2_pass():
    soup = _soup_with_jsonld({"@type": "BreadcrumbList", "itemListElement": []})
    c = _by(check_schema_markup(soup), "SC2")
    assert c.status == "pass"


def test_sc2_warn_missing():
    soup = _soup_with_jsonld({"@type": "Article", "headline": "x"})
    c = _by(check_schema_markup(soup), "SC2")
    assert c.status == "warn"


def test_sc2_skip_when_parse_error():
    soup = _soup_with_jsonld("not json at all")
    c = _by(check_schema_markup(soup), "SC2")
    assert c.status == "skip"


# ── SC3 Author ──


def test_sc3_pass():
    soup = _soup_with_jsonld(
        {
            "@type": "Article",
            "headline": "x",
            "author": {
                "@type": "Person",
                "name": "修修",
                "url": "https://shosho.tw/about",
            },
        }
    )
    c = _by(check_schema_markup(soup), "SC3")
    assert c.status == "pass"


def test_sc3_warn_no_author():
    soup = _soup_with_jsonld({"@type": "Article", "headline": "x"})
    c = _by(check_schema_markup(soup), "SC3")
    assert c.status == "warn"
    assert "缺" in c.actual


def test_sc3_warn_missing_url():
    soup = _soup_with_jsonld(
        {
            "@type": "Article",
            "headline": "x",
            "author": {"@type": "Person", "name": "修修"},
        }
    )
    c = _by(check_schema_markup(soup), "SC3")
    assert c.status == "warn"
    assert "url" in c.actual


def test_sc3_skip_when_no_article():
    soup = _soup_with_jsonld({"@type": "WebSite", "name": "x"})
    c = _by(check_schema_markup(soup), "SC3")
    assert c.status == "skip"


def test_sc3_pass_with_list_author():
    """author 可能是 list of Person。"""
    soup = _soup_with_jsonld(
        {
            "@type": "Article",
            "headline": "x",
            "author": [
                {"@type": "Person", "name": "A", "url": "https://x/a"},
                {"@type": "Person", "name": "B"},  # incomplete
            ],
        }
    )
    c = _by(check_schema_markup(soup), "SC3")
    assert c.status == "pass"


# ── SC5 FAQ / HowTo ──


def test_sc5_pass_faq():
    soup = _soup_with_jsonld({"@type": "FAQPage", "mainEntity": []})
    c = _by(check_schema_markup(soup), "SC5")
    assert c.status == "pass"


def test_sc5_pass_howto():
    soup = _soup_with_jsonld({"@type": "HowTo", "name": "x"})
    c = _by(check_schema_markup(soup), "SC5")
    assert c.status == "pass"


def test_sc5_skip_no_faq_or_howto():
    """無 FAQ/HowTo 不 warn，因 deterministic 不知道內容是否該帶。"""
    soup = _soup_with_jsonld({"@type": "Article", "headline": "x"})
    c = _by(check_schema_markup(soup), "SC5")
    assert c.status == "skip"


def test_sc5_skip_when_parse_error():
    soup = _soup_with_jsonld('{"x":}')
    c = _by(check_schema_markup(soup), "SC5")
    assert c.status == "skip"


def test_returns_5_checks():
    soup = BeautifulSoup("<html><body></body></html>", "html.parser")
    checks = check_schema_markup(soup)
    assert sorted(c.rule_id for c in checks) == ["SC1", "SC2", "SC3", "SC4", "SC5"]
