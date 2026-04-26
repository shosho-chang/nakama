"""Headings checks tests — H1/H2/H3 共 3 rule × ≥3 case。"""

from __future__ import annotations

from bs4 import BeautifulSoup

from shared.seo_audit.headings import check_headings


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _by(checks, rule_id):
    return next(c for c in checks if c.rule_id == rule_id)


# ── H1 唯一 ──


def test_h1_pass_single():
    c = _by(check_headings(_soup("<html><body><h1>Title</h1></body></html>")), "H1")
    assert c.status == "pass"


def test_h1_fail_missing():
    c = _by(check_headings(_soup("<html><body><h2>x</h2></body></html>")), "H1")
    assert c.status == "fail"
    assert "0" in c.actual


def test_h1_fail_multiple():
    c = _by(check_headings(_soup("<html><body><h1>A</h1><h1>B</h1></body></html>")), "H1")
    assert c.status == "fail"
    assert c.details["h1_texts"] == ["A", "B"]


# ── H2: 跳級 ──


def test_h2_pass_no_skip():
    html = "<html><body><h1>1</h1><h2>2</h2><h3>3</h3><h2>2b</h2></body></html>"
    c = _by(check_headings(_soup(html)), "H2")
    assert c.status == "pass"


def test_h2_warn_h1_to_h3():
    html = "<html><body><h1>1</h1><h3>3</h3></body></html>"
    c = _by(check_headings(_soup(html)), "H2")
    assert c.status == "warn"
    assert ("H1", "H3") in c.details["skips"]


def test_h2_warn_h2_to_h4():
    html = "<html><body><h1>1</h1><h2>2</h2><h4>4</h4></body></html>"
    c = _by(check_headings(_soup(html)), "H2")
    assert c.status == "warn"


def test_h2_pass_back_to_h2_after_h3():
    """從 H3 回 H2 不算跳級（only forward skip 算）。"""
    html = "<html><body><h1>1</h1><h2>2</h2><h3>3</h3><h2>2b</h2></body></html>"
    c = _by(check_headings(_soup(html)), "H2")
    assert c.status == "pass"


# ── H3: 至少 1 個 H2 ──


def test_h3_pass_one_h2():
    html = "<html><body><h1>1</h1><h2>2</h2></body></html>"
    c = _by(check_headings(_soup(html)), "H3")
    assert c.status == "pass"


def test_h3_warn_no_h2():
    html = "<html><body><h1>1</h1><p>內容</p></body></html>"
    c = _by(check_headings(_soup(html)), "H3")
    assert c.status == "warn"


def test_h3_pass_many_h2():
    html = "<html><body><h1>1</h1>" + "<h2>x</h2>" * 5 + "</body></html>"
    c = _by(check_headings(_soup(html)), "H3")
    assert c.status == "pass"
    assert "5" in c.actual


def test_returns_3_checks():
    checks = check_headings(_soup("<html><body></body></html>"))
    assert [c.rule_id for c in checks] == ["H1", "H2", "H3"]
