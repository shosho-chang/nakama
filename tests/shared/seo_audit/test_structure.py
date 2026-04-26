"""Structure checks tests — S1-S3 共 3 rule × ≥3 case。

特別覆蓋：
- CJK 字元計數正確（「磷酸肌酸系統」=6 字）
- script / nav / footer 被 strip
- internal vs external link 分類（相對 path / 子網域 / mailto / tel / # anchor）
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from shared.seo_audit.structure import check_structure, count_words


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _by(checks, rid):
    return next(c for c in checks if c.rule_id == rid)


# ── count_words 單元 ──


def test_count_words_cjk_per_char():
    assert count_words("磷酸肌酸系統") == 6


def test_count_words_latin_token():
    assert count_words("hello world! foo-bar") == 3


def test_count_words_mixed():
    assert count_words("睡眠 (sleep) 不足") == 5


def test_count_words_punctuation_only_zero():
    assert count_words("、。，！？") == 0


def test_count_words_japanese():
    """ひらがな + カタカナ 各算一字。"""
    assert count_words("おはよう") == 4
    assert count_words("コーヒー") == 4


# ── S1 word count ──


def test_s1_pass_long_chinese():
    body = "睡眠不足會影響什麼？" * 200  # 200 * 11 = 2200 chars (CJK + 標點)
    html = f"<html><body><h1>x</h1><p>{body}</p></body></html>"
    c = _by(check_structure(_soup(html), "https://x.com/"), "S1")
    assert c.status == "pass"


def test_s1_warn_short():
    html = "<html><body><p>太短了。</p></body></html>"
    c = _by(check_structure(_soup(html), "https://x.com/"), "S1")
    assert c.status == "warn"


def test_s1_strips_script_and_nav():
    """script / nav / footer 不該被計入。"""
    junk = "<script>" + "alert('x')" * 500 + "</script>"
    junk += "<nav>" + "首頁 關於 部落格 " * 200 + "</nav>"
    body = "短"  # 1 字
    html = f"<html><body><h1>x</h1>{junk}<p>{body}</p></body></html>"
    c = _by(check_structure(_soup(html), "https://x.com/"), "S1")
    assert c.status == "warn"
    assert "1" in c.actual or "2" in c.actual


def test_s1_pass_at_boundary():
    body = "字" * 1500
    html = f"<html><body><p>{body}</p></body></html>"
    c = _by(check_structure(_soup(html), "https://x.com/"), "S1")
    assert c.status == "pass"


# ── S2 internal links ──


def test_s2_pass():
    html = """
    <html><body>
      <a href="https://x.com/post-1">一</a>
      <a href="/post-2">二</a>
      <a href="/post-3">三</a>
    </body></html>
    """
    c = _by(check_structure(_soup(html), "https://x.com/"), "S2")
    assert c.status == "pass"
    assert "3" in c.actual


def test_s2_warn_zero():
    html = '<html><body><a href="https://other.com">x</a></body></html>'
    c = _by(check_structure(_soup(html), "https://x.com/"), "S2")
    assert c.status == "warn"


def test_s2_subdomain_is_external():
    """fleet.shosho.tw 對 shosho.tw 算 external。"""
    html = """
    <html><body>
      <a href="https://fleet.shosho.tw/x">community</a>
      <a href="/post-only">internal</a>
    </body></html>
    """
    c = _by(check_structure(_soup(html), "https://shosho.tw/post"), "S2")
    assert c.status == "warn"  # only 1 internal


def test_s2_skips_mailto_tel_anchor_javascript():
    html = """
    <html><body>
      <a href="mailto:a@b.com">m</a>
      <a href="tel:+886">t</a>
      <a href="#section">a</a>
      <a href="javascript:void(0)">j</a>
      <a href="/internal-1">i1</a>
    </body></html>
    """
    c = _by(check_structure(_soup(html), "https://x.com/"), "S2")
    # 只有 1 internal → warn
    assert c.status == "warn"
    assert "1" in c.actual


# ── S3 external links ──


def test_s3_pass():
    html = '<html><body><a href="https://pubmed.ncbi.nlm.nih.gov/x">paper</a></body></html>'
    c = _by(check_structure(_soup(html), "https://x.com/"), "S3")
    assert c.status == "pass"


def test_s3_warn_zero():
    html = '<html><body><a href="/internal">x</a></body></html>'
    c = _by(check_structure(_soup(html), "https://x.com/"), "S3")
    assert c.status == "warn"


def test_s3_subdomain_counts_external():
    html = '<html><body><a href="https://fleet.shosho.tw/x">y</a></body></html>'
    c = _by(check_structure(_soup(html), "https://shosho.tw/post"), "S3")
    assert c.status == "pass"


def test_returns_3_checks():
    checks = check_structure(_soup("<html><body></body></html>"), "https://x.com/")
    assert [c.rule_id for c in checks] == ["S1", "S2", "S3"]
