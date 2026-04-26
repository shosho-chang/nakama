"""Metadata + OpenGraph + Twitter Card deterministic checks（M1-M5 + O1-O4）。

Slice D.1 §附錄 A 9 條 rule：
    M1 title 長度 50-60
    M2 meta description 150-160
    M3 canonical 存在且指向自己
    M4 robots 不誤設 noindex
    M5 viewport 含 width=device-width
    O1 og:title + og:description
    O2 og:image 存在 + URL 解析 OK（HEAD-poll 留 D.2，這層只查 meta 是否存在）
    O3 og:url == canonical
    O4 twitter:card

Note: O2 的「HEAD request 200 + image content-type」走 I3，這裡只驗 og:image
meta 存在；避免 metadata.py 跨層做 network call。
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from shared.seo_audit.types import AuditCheck

_TITLE_MIN = 50
_TITLE_MAX = 60
_DESC_MIN = 150
_DESC_MAX = 160


def check_metadata(
    soup: BeautifulSoup,
    url: str,
    focus_keyword: str | None = None,  # noqa: ARG001 — reserved for D.2 keyword sub-checks
) -> list[AuditCheck]:
    """跑 M1-M5 + O1-O4 共 9 條 metadata check。

    `focus_keyword` 目前只保留簽名一致性；M1/M2 不做 keyword 覆蓋（那屬 LLM
    semantic check L1/L2，§附錄 C）。
    """
    checks: list[AuditCheck] = []
    checks.append(_check_title(soup))
    checks.append(_check_meta_description(soup))
    checks.append(_check_canonical(soup, url))
    checks.append(_check_robots(soup))
    checks.append(_check_viewport(soup))
    checks.append(_check_og_title_desc(soup))
    checks.append(_check_og_image(soup))
    checks.append(_check_og_url_vs_canonical(soup))
    checks.append(_check_twitter_card(soup))
    return checks


def _check_title(soup: BeautifulSoup) -> AuditCheck:
    title_tag = soup.find("title")
    if title_tag is None or not (title_tag.string or "").strip():
        return AuditCheck(
            rule_id="M1",
            name="title 長度 50-60 字符",
            category="metadata",
            severity="warning",
            status="fail",
            actual="缺少 <title>",
            expected=f"{_TITLE_MIN} ≤ len ≤ {_TITLE_MAX}",
            fix_suggestion="補 <title> 標籤；含主要 focus keyword 並控制在 50-60 字符內",
        )
    text = title_tag.string.strip()
    length = len(text)
    if _TITLE_MIN <= length <= _TITLE_MAX:
        status = "pass"
    elif length < _TITLE_MIN:
        status = "warn"
    else:
        status = "warn"
    fix = "加長尾關鍵字補到 50 字以上" if length < _TITLE_MIN else "截短到 60 內，重要詞前置"
    return AuditCheck(
        rule_id="M1",
        name="title 長度 50-60 字符",
        category="metadata",
        severity="warning",
        status=status,
        actual=f"len={length}: {text!r}",
        expected=f"{_TITLE_MIN} ≤ len ≤ {_TITLE_MAX}",
        fix_suggestion=fix if status != "pass" else "",
    )


def _check_meta_description(soup: BeautifulSoup) -> AuditCheck:
    tag = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    content = (tag.get("content") if tag else "") or ""
    content = content.strip()
    if not content:
        return AuditCheck(
            rule_id="M2",
            name="meta description 長度 150-160 字符",
            category="metadata",
            severity="warning",
            status="fail",
            actual="缺少 meta description",
            expected=f"{_DESC_MIN} ≤ len ≤ {_DESC_MAX}",
            fix_suggestion='補 <meta name="description" content="..."> 約 150-160 字',
        )
    length = len(content)
    if _DESC_MIN <= length <= _DESC_MAX:
        status = "pass"
        fix = ""
    elif length < _DESC_MIN:
        status = "warn"
        fix = "加長到 150 字以上，自然帶入 focus keyword"
    else:
        status = "warn"
        fix = "截短到 160 字內避免 SERP 截斷"
    return AuditCheck(
        rule_id="M2",
        name="meta description 長度 150-160 字符",
        category="metadata",
        severity="warning",
        status=status,
        actual=f"len={length}",
        expected=f"{_DESC_MIN} ≤ len ≤ {_DESC_MAX}",
        fix_suggestion=fix,
    )


def _normalize_url(u: str) -> str:
    """drop fragment + trailing slash；做最小 normalization 給 self-referential
    canonical 比對用。"""
    parts = urlsplit(u)
    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return f"{parts.scheme}://{parts.netloc}{path}"


def _check_canonical(soup: BeautifulSoup, page_url: str) -> AuditCheck:
    tag = soup.find("link", rel="canonical")
    href = (tag.get("href") if tag else "") or ""
    if not href:
        return AuditCheck(
            rule_id="M3",
            name="canonical link 存在且指向自己",
            category="metadata",
            severity="critical",
            status="fail",
            actual="缺少 <link rel=canonical>",
            expected="存在且指向自己",
            fix_suggestion='加 <link rel="canonical" href="..."/> 指到 page 自身',
        )
    norm_canonical = _normalize_url(href)
    norm_page = _normalize_url(page_url)
    if norm_canonical == norm_page:
        return AuditCheck(
            rule_id="M3",
            name="canonical link 存在且指向自己",
            category="metadata",
            severity="critical",
            status="pass",
            actual=href,
            expected=f"== {page_url}",
            fix_suggestion="",
        )
    return AuditCheck(
        rule_id="M3",
        name="canonical link 存在且指向自己",
        category="metadata",
        severity="critical",
        status="fail",
        actual=href,
        expected=f"== {page_url}",
        fix_suggestion="canonical 指錯 URL（除非刻意 cross-domain canonical）；修正 href",
    )


def _check_robots(soup: BeautifulSoup) -> AuditCheck:
    tag = soup.find("meta", attrs={"name": re.compile(r"^robots$", re.I)})
    content = (tag.get("content") if tag else "") or ""
    if "noindex" in content.lower():
        return AuditCheck(
            rule_id="M4",
            name="meta robots 不誤設 noindex",
            category="metadata",
            severity="critical",
            status="fail",
            actual=f"robots={content!r}",
            expected="不含 noindex",
            fix_suggestion="移除 noindex 指令（除非刻意 unindex 此頁）",
        )
    return AuditCheck(
        rule_id="M4",
        name="meta robots 不誤設 noindex",
        category="metadata",
        severity="critical",
        status="pass",
        actual=f"robots={content!r}" if content else "未設 robots（預設 index/follow）",
        expected="不含 noindex",
        fix_suggestion="",
    )


def _check_viewport(soup: BeautifulSoup) -> AuditCheck:
    tag = soup.find("meta", attrs={"name": re.compile(r"^viewport$", re.I)})
    content = (tag.get("content") if tag else "") or ""
    if not content:
        return AuditCheck(
            rule_id="M5",
            name="viewport meta 存在（mobile-first）",
            category="metadata",
            severity="warning",
            status="fail",
            actual="缺少 viewport meta",
            expected="存在 + 含 width=device-width",
            fix_suggestion=(
                '加 <meta name="viewport" content="width=device-width, initial-scale=1">'
            ),
        )
    if "width=device-width" in content.lower():
        return AuditCheck(
            rule_id="M5",
            name="viewport meta 存在（mobile-first）",
            category="metadata",
            severity="warning",
            status="pass",
            actual=content,
            expected="含 width=device-width",
            fix_suggestion="",
        )
    return AuditCheck(
        rule_id="M5",
        name="viewport meta 存在（mobile-first）",
        category="metadata",
        severity="warning",
        status="warn",
        actual=content,
        expected="含 width=device-width",
        fix_suggestion="補 width=device-width 確保 mobile 適配",
    )


def _og_props(soup: BeautifulSoup) -> dict[str, str]:
    """蒐集 og:* meta 為 dict。"""
    out: dict[str, str] = {}
    for tag in soup.find_all("meta", property=re.compile(r"^og:", re.I)):
        prop = tag.get("property", "").lower()
        out[prop] = (tag.get("content") or "").strip()
    return out


def _check_og_title_desc(soup: BeautifulSoup) -> AuditCheck:
    og = _og_props(soup)
    has_title = bool(og.get("og:title"))
    has_desc = bool(og.get("og:description"))
    if has_title and has_desc:
        return AuditCheck(
            rule_id="O1",
            name="og:title + og:description",
            category="opengraph",
            severity="warning",
            status="pass",
            actual="og:title + og:description 都有",
            expected="兩者都存在",
            fix_suggestion="",
        )
    missing = []
    if not has_title:
        missing.append("og:title")
    if not has_desc:
        missing.append("og:description")
    return AuditCheck(
        rule_id="O1",
        name="og:title + og:description",
        category="opengraph",
        severity="warning",
        status="fail",
        actual=f"缺：{', '.join(missing)}",
        expected="兩者都存在",
        fix_suggestion="補 og:* meta tags（Yoast / SEOPress 等套件預設帶）",
    )


def _check_og_image(soup: BeautifulSoup) -> AuditCheck:
    og = _og_props(soup)
    image = og.get("og:image", "")
    if image:
        return AuditCheck(
            rule_id="O2",
            name="og:image meta 存在",
            category="opengraph",
            severity="warning",
            status="pass",
            actual=image,
            expected="og:image meta 含 URL",
            fix_suggestion="",
        )
    return AuditCheck(
        rule_id="O2",
        name="og:image meta 存在",
        category="opengraph",
        severity="warning",
        status="fail",
        actual="缺少 og:image",
        expected="og:image meta 含 URL",
        fix_suggestion="指定 og:image 為 1200x630 主視覺（FB/LinkedIn 分享預覽）",
    )


def _check_og_url_vs_canonical(soup: BeautifulSoup) -> AuditCheck:
    og = _og_props(soup)
    og_url = og.get("og:url", "")
    canonical_tag = soup.find("link", rel="canonical")
    canonical = (canonical_tag.get("href") if canonical_tag else "") or ""

    if not og_url and not canonical:
        return AuditCheck(
            rule_id="O3",
            name="og:url 等於 canonical",
            category="opengraph",
            severity="info",
            status="skip",
            actual="兩者皆缺",
            expected="兩者一致",
            fix_suggestion="先補 canonical（M3）+ og:url 對齊",
        )
    if not og_url:
        return AuditCheck(
            rule_id="O3",
            name="og:url 等於 canonical",
            category="opengraph",
            severity="info",
            status="warn",
            actual="缺 og:url",
            expected=f"og:url == {canonical}",
            fix_suggestion="補 og:url 對齊 canonical",
        )
    if not canonical:
        return AuditCheck(
            rule_id="O3",
            name="og:url 等於 canonical",
            category="opengraph",
            severity="info",
            status="warn",
            actual=f"og:url={og_url}",
            expected="canonical 存在且 == og:url",
            fix_suggestion="先補 canonical（M3）",
        )
    if _normalize_url(og_url) == _normalize_url(canonical):
        return AuditCheck(
            rule_id="O3",
            name="og:url 等於 canonical",
            category="opengraph",
            severity="info",
            status="pass",
            actual=f"og:url == canonical == {og_url}",
            expected="一致",
            fix_suggestion="",
        )
    return AuditCheck(
        rule_id="O3",
        name="og:url 等於 canonical",
        category="opengraph",
        severity="info",
        status="warn",
        actual=f"og:url={og_url} ≠ canonical={canonical}",
        expected="兩者一致",
        fix_suggestion="統一 og:url 與 canonical（避免社群分享指錯版本）",
    )


def _check_twitter_card(soup: BeautifulSoup) -> AuditCheck:
    tag = soup.find("meta", attrs={"name": re.compile(r"^twitter:card$", re.I)})
    content = (tag.get("content") if tag else "") or ""
    if content in ("summary", "summary_large_image", "app", "player"):
        return AuditCheck(
            rule_id="O4",
            name="twitter:card 存在",
            category="opengraph",
            severity="info",
            status="pass",
            actual=content,
            expected="summary / summary_large_image",
            fix_suggestion="",
        )
    if not content:
        return AuditCheck(
            rule_id="O4",
            name="twitter:card 存在",
            category="opengraph",
            severity="info",
            status="warn",
            actual="缺 twitter:card",
            expected="summary / summary_large_image",
            fix_suggestion='加 <meta name="twitter:card" content="summary_large_image">',
        )
    return AuditCheck(
        rule_id="O4",
        name="twitter:card 存在",
        category="opengraph",
        severity="info",
        status="warn",
        actual=f"twitter:card={content!r}（非標準值）",
        expected="summary / summary_large_image",
        fix_suggestion="改為 summary_large_image",
    )
