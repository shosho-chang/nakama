"""Images checks tests — I1-I5 共 5 rule × ≥3 case。HEAD calls 全 mock。"""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest
from bs4 import BeautifulSoup

from shared.seo_audit.images import check_images


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _by(checks, rid):
    return next(c for c in checks if c.rule_id == rid)


def _mock_head(status_code=200, content_type="image/jpeg"):
    r = MagicMock(spec=httpx.Response)
    r.status_code = status_code
    r.headers = {"content-type": content_type}
    return r


@pytest.fixture(autouse=True)
def _mock_httpx_head(monkeypatch):
    """預設所有 HEAD 200 image/jpeg；個別 test 自行 patch。"""
    monkeypatch.setattr("shared.seo_audit.images.httpx.head", lambda *a, **kw: _mock_head())


# ── I1: alt 非空 ──


def test_i1_pass():
    html = '<html><body><img src="a.jpg" alt="描述"></body></html>'
    c = _by(check_images(_soup(html), "https://x.com/"), "I1")
    assert c.status == "pass"


def test_i1_warn_missing_alt():
    html = '<html><body><img src="a.jpg"><img src="b.jpg" alt="ok"></body></html>'
    c = _by(check_images(_soup(html), "https://x.com/"), "I1")
    assert c.status == "warn"
    assert "1/2" in c.actual


def test_i1_warn_empty_alt():
    html = '<html><body><img src="a.jpg" alt=""></body></html>'
    c = _by(check_images(_soup(html), "https://x.com/"), "I1")
    assert c.status == "warn"


def test_i1_skip_no_imgs():
    c = _by(check_images(_soup("<html><body></body></html>"), "https://x.com/"), "I1")
    assert c.status == "skip"


# ── I2: alt 長度 ──


def test_i2_pass_short_alt():
    html = '<html><body><img alt="短描述" src="a.jpg"></body></html>'
    c = _by(check_images(_soup(html), "https://x.com/"), "I2")
    assert c.status == "pass"


def test_i2_warn_long_alt():
    long_alt = "x" * 130
    html = f'<html><body><img alt="{long_alt}" src="a.jpg"></body></html>'
    c = _by(check_images(_soup(html), "https://x.com/"), "I2")
    assert c.status == "warn"


def test_i2_pass_at_boundary():
    alt = "x" * 124
    html = f'<html><body><img alt="{alt}" src="a.jpg"></body></html>'
    c = _by(check_images(_soup(html), "https://x.com/"), "I2")
    assert c.status == "pass"


# ── I3: og:image accessible ──


def test_i3_pass(monkeypatch):
    monkeypatch.setattr(
        "shared.seo_audit.images.httpx.head",
        lambda *a, **kw: _mock_head(200, "image/jpeg"),
    )
    html = (
        '<html><head><meta property="og:image" content="https://x.com/img.jpg">'
        "</head><body></body></html>"
    )
    c = _by(check_images(_soup(html), "https://x.com/"), "I3")
    assert c.status == "pass"


def test_i3_skip_no_og_image():
    c = _by(check_images(_soup("<html><body></body></html>"), "https://x.com/"), "I3")
    assert c.status == "skip"


def test_i3_fail_404(monkeypatch):
    monkeypatch.setattr(
        "shared.seo_audit.images.httpx.head",
        lambda *a, **kw: _mock_head(404, ""),
    )
    html = (
        '<html><head><meta property="og:image" content="https://x.com/img.jpg">'
        "</head><body></body></html>"
    )
    c = _by(check_images(_soup(html), "https://x.com/"), "I3")
    assert c.status == "fail"


def test_i3_fail_wrong_content_type(monkeypatch):
    monkeypatch.setattr(
        "shared.seo_audit.images.httpx.head",
        lambda *a, **kw: _mock_head(200, "text/html"),
    )
    html = (
        '<html><head><meta property="og:image" content="https://x.com/img.jpg">'
        "</head><body></body></html>"
    )
    c = _by(check_images(_soup(html), "https://x.com/"), "I3")
    assert c.status == "fail"


def test_i3_fail_network_error(monkeypatch):
    def raise_err(*a, **kw):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr("shared.seo_audit.images.httpx.head", raise_err)
    html = (
        '<html><head><meta property="og:image" content="https://x.com/img.jpg">'
        "</head><body></body></html>"
    )
    c = _by(check_images(_soup(html), "https://x.com/"), "I3")
    assert c.status == "fail"


# ── I4: lazy loading ──


def test_i4_skip_few_imgs():
    html = '<html><body><img src="a.jpg" alt="x"></body></html>'
    c = _by(check_images(_soup(html), "https://x.com/"), "I4")
    assert c.status == "skip"


def test_i4_pass_all_lazy():
    """5 張 img；前 3 不要求 lazy、後 2 全 lazy → 100% pass。"""
    imgs = "".join(f'<img src="{i}.jpg" alt="x">' for i in range(3))
    imgs += "".join(f'<img src="{i}.jpg" alt="x" loading="lazy">' for i in range(3, 5))
    html = f"<html><body>{imgs}</body></html>"
    c = _by(check_images(_soup(html), "https://x.com/"), "I4")
    assert c.status == "pass"


def test_i4_warn_no_lazy():
    imgs = "".join(f'<img src="{i}.jpg" alt="x">' for i in range(8))
    html = f"<html><body>{imgs}</body></html>"
    c = _by(check_images(_soup(html), "https://x.com/"), "I4")
    assert c.status == "warn"


# ── I5: WebP/AVIF ──


def test_i5_pass_majority_webp(monkeypatch):
    """50% 以上 image/webp → pass。"""
    counter = {"i": 0}

    def head(*a, **kw):
        counter["i"] += 1
        ct = "image/webp" if counter["i"] % 2 == 1 else "image/jpeg"
        return _mock_head(200, ct)

    monkeypatch.setattr("shared.seo_audit.images.httpx.head", head)
    imgs = "".join(f'<img src="{i}.webp" alt="x">' for i in range(4))
    html = f"<html><body>{imgs}</body></html>"
    c = _by(check_images(_soup(html), "https://x.com/"), "I5")
    assert c.status == "pass"


def test_i5_warn_no_webp(monkeypatch):
    monkeypatch.setattr(
        "shared.seo_audit.images.httpx.head",
        lambda *a, **kw: _mock_head(200, "image/jpeg"),
    )
    imgs = "".join(f'<img src="{i}.jpg" alt="x">' for i in range(4))
    html = f"<html><body>{imgs}</body></html>"
    c = _by(check_images(_soup(html), "https://x.com/"), "I5")
    assert c.status == "warn"


def test_i5_skip_no_imgs_with_src():
    html = "<html><body><img alt='x'></body></html>"
    c = _by(check_images(_soup(html), "https://x.com/"), "I5")
    assert c.status == "skip"


def test_i5_relative_url_resolved(monkeypatch):
    """img src 是相對 URL 時 HEAD call 應收到 absolute URL。"""
    captured = {}

    def head(url, **kw):
        captured["url"] = url
        return _mock_head(200, "image/webp")

    monkeypatch.setattr("shared.seo_audit.images.httpx.head", head)
    html = '<html><body><img src="img/a.webp" alt="x"></body></html>'
    check_images(_soup(html), "https://x.com/page")
    assert captured["url"] == "https://x.com/img/a.webp"


def test_returns_5_checks():
    checks = check_images(_soup("<html><body></body></html>"), "https://x.com/")
    assert [c.rule_id for c in checks] == ["I1", "I2", "I3", "I4", "I5"]
