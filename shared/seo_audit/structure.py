"""Content structure deterministic checks（S1-S3）。

Slice D.1 §附錄 A 3 條 rule：
    S1 word count ≥ 1500（CJK-aware）
    S2 internal links ≥ 2
    S3 external links ≥ 1（非同 domain，視為權威源）

CJK 字元計數：「磷酸肌酸系統」算 6 字 not 1（不依賴空格）。實作策略：
    - regex 抓 CJK Unified Ideographs（U+4E00-U+9FFF）+ Hiragana/Katakana 範圍
      逐字計數
    - 同時保留拉丁字母 word-tokenization（`\\w+` 抓連續 ASCII / 拉丁文字）
    - 兩者相加 = 總字數

Internal vs external：以 `base_url` 的 host (netloc) 比對；同 host 為
internal。子網域算 external（避免 fleet.shosho.tw → shosho.tw 被當 internal
過度寬鬆）。

`focus_keyword` 同其他模組：保留簽名一致性，本層不用（keyword in body 屬 LLM
semantic check）。
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from shared.seo_audit.types import AuditCheck

_WORD_COUNT_MIN = 1500
_INTERNAL_LINKS_MIN = 2
_EXTERNAL_LINKS_MIN = 1

# CJK Unified Ideographs + Hiragana + Katakana + Hangul Syllables
# （Hangul 不屬 Health & Wellness 主場，但不增加成本順手包）
_CJK_RE = re.compile(r"[一-鿿぀-ゟ゠-ヿ가-힯]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9'\-]*")


def check_structure(
    soup: BeautifulSoup,
    base_url: str,
    focus_keyword: str | None = None,  # noqa: ARG001 — reserved for D.2 keyword sub-checks
) -> list[AuditCheck]:
    """跑 S1-S3 共 3 條 structure check。"""
    return [
        _check_word_count(soup),
        _check_internal_links(soup, base_url),
        _check_external_links(soup, base_url),
    ]


def count_words(text: str) -> int:
    """CJK-aware word count：CJK 逐字 + 拉丁逐 token。"""
    cjk_count = len(_CJK_RE.findall(text))
    latin_count = len(_LATIN_WORD_RE.findall(text))
    return cjk_count + latin_count


def _extract_body_text(soup: BeautifulSoup) -> str:
    """抓 body 內可見文字；扣除 script / style / nav / footer / header / aside。"""
    body = soup.body or soup
    for tag in body.find_all(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
        tag.decompose()
    # space separator 讓 latin word boundary 不黏在一起
    return body.get_text(separator=" ", strip=True)


def _check_word_count(soup: BeautifulSoup) -> AuditCheck:
    text = _extract_body_text(soup)
    n = count_words(text)
    if n >= _WORD_COUNT_MIN:
        return AuditCheck(
            rule_id="S1",
            name=f"word count ≥ {_WORD_COUNT_MIN}（CJK-aware）",
            category="structure",
            severity="warning",
            status="pass",
            actual=f"{n} 字",
            expected=f"≥ {_WORD_COUNT_MIN}",
            fix_suggestion="",
        )
    return AuditCheck(
        rule_id="S1",
        name=f"word count ≥ {_WORD_COUNT_MIN}（CJK-aware）",
        category="structure",
        severity="warning",
        status="warn",
        actual=f"{n} 字",
        expected=f"≥ {_WORD_COUNT_MIN}",
        fix_suggestion="Health 類 1500-2500 字為 sweet spot；補深度（機制 / 案例 / 引用）",
    )


def _normalize_host(u: str) -> str:
    return urlsplit(u).netloc.lower()


def _classify_links(soup: BeautifulSoup, base_url: str) -> tuple[list[str], list[str]]:
    """回傳 (internal_hrefs, external_hrefs)。
    - 跳過 mailto: / tel: / javascript: / # anchor
    - 子網域算 external
    - 相對 path 算 internal
    """
    base_host = _normalize_host(base_url)
    internal: list[str] = []
    external: list[str] = []

    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        lower = href.lower()
        if lower.startswith(("mailto:", "tel:", "javascript:")):
            continue
        if lower.startswith("#"):
            continue

        host = _normalize_host(href)
        if not host:
            # 相對路徑 → internal
            internal.append(href)
        elif host == base_host:
            internal.append(href)
        else:
            external.append(href)

    return internal, external


def _check_internal_links(soup: BeautifulSoup, base_url: str) -> AuditCheck:
    internal, _ = _classify_links(soup, base_url)
    if len(internal) >= _INTERNAL_LINKS_MIN:
        return AuditCheck(
            rule_id="S2",
            name=f"internal links ≥ {_INTERNAL_LINKS_MIN}",
            category="structure",
            severity="warning",
            status="pass",
            actual=f"{len(internal)} internal links",
            expected=f"≥ {_INTERNAL_LINKS_MIN}",
            fix_suggestion="",
        )
    return AuditCheck(
        rule_id="S2",
        name=f"internal links ≥ {_INTERNAL_LINKS_MIN}",
        category="structure",
        severity="warning",
        status="warn",
        actual=f"{len(internal)} internal links",
        expected=f"≥ {_INTERNAL_LINKS_MIN}",
        fix_suggestion="加 internal link 到既有 KB / pillar 文章（reuse Robin KB suggest）",
    )


def _check_external_links(soup: BeautifulSoup, base_url: str) -> AuditCheck:
    _, external = _classify_links(soup, base_url)
    if len(external) >= _EXTERNAL_LINKS_MIN:
        return AuditCheck(
            rule_id="S3",
            name=f"external links ≥ {_EXTERNAL_LINKS_MIN}（權威源）",
            category="structure",
            severity="info",
            status="pass",
            actual=f"{len(external)} external links",
            expected=f"≥ {_EXTERNAL_LINKS_MIN}",
            fix_suggestion="",
        )
    return AuditCheck(
        rule_id="S3",
        name=f"external links ≥ {_EXTERNAL_LINKS_MIN}（權威源）",
        category="structure",
        severity="info",
        status="warn",
        actual=f"{len(external)} external links",
        expected=f"≥ {_EXTERNAL_LINKS_MIN}",
        fix_suggestion="引用論文 / 權威機構 / 政府網站作為佐證",
    )
