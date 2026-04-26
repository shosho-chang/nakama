"""Schema.org JSON-LD deterministic checks（SC1-SC5）。

Slice D.1 §附錄 A 5 條 rule：
    SC1 Article schema 存在（含子類 NewsArticle / BlogPosting / MedicalWebPage）
    SC2 BreadcrumbList schema 存在
    SC3 Author schema（E-E-A-T 強化）— Article.author 是 Person + name + url
    SC4 Schema JSON-LD parse 無 error
    SC5 FAQPage / HowTo schema 偵測（Health rich result 高觸發）

策略：
    - 收集所有 `<script type="application/ld+json">` script tag
    - SC4 先跑 — parse 失敗 critical fail 直接擋 SC1-SC3/SC5（其他 rule 仍會跑，
      但讀不到 schema → 對應 status="fail" 或 "skip"）
    - 多個 script tag、@graph 包裝、單一 object、array 都要支援
    - Schema.org 子類用 `@type` 字串列表 fuzzy match（含子串即算）
"""

from __future__ import annotations

import json
from typing import Any

from bs4 import BeautifulSoup

from shared.seo_audit.types import AuditCheck

_ARTICLE_TYPES = (
    "Article",
    "NewsArticle",
    "BlogPosting",
    "MedicalWebPage",
    "MedicalArticle",
    "ScholarlyArticle",
    "TechArticle",
    "Report",
)


def check_schema_markup(soup: BeautifulSoup) -> list[AuditCheck]:
    """跑 SC1-SC5 共 5 條 schema check。"""
    parsed_blocks, parse_errors = _parse_jsonld_blocks(soup)
    flat_objects = _flatten_objects(parsed_blocks)

    sc4 = _check_parse_errors(parse_errors, len(parsed_blocks))
    sc1 = _check_article(flat_objects, has_parse_error=bool(parse_errors))
    sc2 = _check_breadcrumb(flat_objects, has_parse_error=bool(parse_errors))
    sc3 = _check_author(flat_objects, has_parse_error=bool(parse_errors))
    sc5 = _check_faq_or_howto(flat_objects, has_parse_error=bool(parse_errors))
    return [sc1, sc2, sc3, sc4, sc5]


def _parse_jsonld_blocks(
    soup: BeautifulSoup,
) -> tuple[list[Any], list[tuple[int, str]]]:
    """回傳 (parsed_objects, errors)。

    parsed_objects 是每個 script 的 raw parse 結果（可能是 dict / list / @graph）；
    errors = [(script_index, error_msg), ...]
    """
    parsed: list[Any] = []
    errors: list[tuple[int, str]] = []
    for idx, tag in enumerate(soup.find_all("script", attrs={"type": "application/ld+json"})):
        text = tag.string or tag.get_text() or ""
        text = text.strip()
        if not text:
            continue
        try:
            parsed.append(json.loads(text))
        except (json.JSONDecodeError, ValueError) as e:
            errors.append((idx, str(e)))
    return parsed, errors


def _flatten_objects(parsed_blocks: list[Any]) -> list[dict]:
    """展平 @graph / array 為 list[dict]。非 dict / 無 @type 的 entry skip。"""
    out: list[dict] = []
    for block in parsed_blocks:
        for obj in _walk(block):
            if isinstance(obj, dict) and "@type" in obj:
                out.append(obj)
    return out


def _walk(node: Any):
    """yield 所有 dict / list 中的 dict 物件（深度遍歷 @graph + array）。"""
    if isinstance(node, dict):
        yield node
        # @graph 是 schema.org 慣例，包多個 entity
        if isinstance(node.get("@graph"), list):
            for child in node["@graph"]:
                yield from _walk(child)
    elif isinstance(node, list):
        for child in node:
            yield from _walk(child)


def _types_of(obj: dict) -> list[str]:
    """`@type` 可能是 str 或 list；統一回 list[str]。"""
    t = obj.get("@type")
    if isinstance(t, str):
        return [t]
    if isinstance(t, list):
        return [s for s in t if isinstance(s, str)]
    return []


def _has_type(objects: list[dict], wanted: tuple[str, ...]) -> dict | None:
    for obj in objects:
        for t in _types_of(obj):
            if t in wanted:
                return obj
    return None


# ── SC4: parse error ──


def _check_parse_errors(errors: list[tuple[int, str]], total_blocks: int) -> AuditCheck:
    if not errors:
        return AuditCheck(
            rule_id="SC4",
            name="Schema JSON-LD parse 無 error",
            category="schema",
            severity="critical",
            status="pass",
            actual=f"{total_blocks} 個 ld+json block 全部 parse OK",
            expected="全部 parse OK",
            fix_suggestion="",
        )
    # 取第一個 error 給 fix 提示
    first_err = errors[0][1]
    return AuditCheck(
        rule_id="SC4",
        name="Schema JSON-LD parse 無 error",
        category="schema",
        severity="critical",
        status="fail",
        actual=f"{len(errors)}/{total_blocks} 個 block parse 失敗",
        expected="全部 parse OK",
        fix_suggestion="修正 JSON 語法（comma / quote / encoding）",
        details={"first_error": first_err, "error_count": len(errors)},
    )


# ── SC1: Article ──


def _check_article(objects: list[dict], *, has_parse_error: bool) -> AuditCheck:
    article = _has_type(objects, _ARTICLE_TYPES)
    if article:
        return AuditCheck(
            rule_id="SC1",
            name="Article schema 存在",
            category="schema",
            severity="warning",
            status="pass",
            actual=f"@type={','.join(_types_of(article))}",
            expected="Article 或子類存在",
            fix_suggestion="",
        )
    if has_parse_error:
        return AuditCheck(
            rule_id="SC1",
            name="Article schema 存在",
            category="schema",
            severity="warning",
            status="skip",
            actual="JSON-LD parse 失敗（先看 SC4）",
            expected="Article 或子類存在",
            fix_suggestion="先修 SC4 parse error 才能評估",
        )
    return AuditCheck(
        rule_id="SC1",
        name="Article schema 存在",
        category="schema",
        severity="warning",
        status="fail",
        actual="無 Article / NewsArticle / BlogPosting / MedicalWebPage 等",
        expected="Article 或子類存在",
        fix_suggestion=(
            "加 Article schema（headline / author / datePublished / image / articleBody）"
        ),
    )


# ── SC2: BreadcrumbList ──


def _check_breadcrumb(objects: list[dict], *, has_parse_error: bool) -> AuditCheck:
    bc = _has_type(objects, ("BreadcrumbList",))
    if bc:
        return AuditCheck(
            rule_id="SC2",
            name="BreadcrumbList schema 存在",
            category="schema",
            severity="info",
            status="pass",
            actual="BreadcrumbList 存在",
            expected="存在",
            fix_suggestion="",
        )
    if has_parse_error:
        return AuditCheck(
            rule_id="SC2",
            name="BreadcrumbList schema 存在",
            category="schema",
            severity="info",
            status="skip",
            actual="JSON-LD parse 失敗（先看 SC4）",
            expected="存在",
            fix_suggestion="先修 SC4 parse error",
        )
    return AuditCheck(
        rule_id="SC2",
        name="BreadcrumbList schema 存在",
        category="schema",
        severity="info",
        status="warn",
        actual="無 BreadcrumbList",
        expected="存在",
        fix_suggestion="加麵包屑 schema（Home > Category > Article）增加 SERP rich result",
    )


# ── SC3: Author（E-E-A-T） ──


def _check_author(objects: list[dict], *, has_parse_error: bool) -> AuditCheck:
    article = _has_type(objects, _ARTICLE_TYPES)
    if article is None:
        if has_parse_error:
            return AuditCheck(
                rule_id="SC3",
                name="Author schema（E-E-A-T 強化）",
                category="schema",
                severity="info",
                status="skip",
                actual="JSON-LD parse 失敗",
                expected="Article.author 是 Person + name + url",
                fix_suggestion="先修 SC4 parse error",
            )
        return AuditCheck(
            rule_id="SC3",
            name="Author schema（E-E-A-T 強化）",
            category="schema",
            severity="info",
            status="skip",
            actual="無 Article schema（先看 SC1）",
            expected="Article.author 是 Person + name + url",
            fix_suggestion="先補 Article schema",
        )

    author = article.get("author")
    # author 可能是 dict 或 list
    candidates: list[dict] = []
    if isinstance(author, dict):
        candidates = [author]
    elif isinstance(author, list):
        candidates = [a for a in author if isinstance(a, dict)]

    if not candidates:
        return AuditCheck(
            rule_id="SC3",
            name="Author schema（E-E-A-T 強化）",
            category="schema",
            severity="info",
            status="warn",
            actual="Article.author 缺",
            expected="Article.author 是 Person + name + url",
            fix_suggestion="補 author 物件（@type=Person, name, url 連到 about 頁）",
        )

    # 至少一位 author 滿足 Person + name + url
    good = next(
        (a for a in candidates if "Person" in _types_of(a) and a.get("name") and a.get("url")),
        None,
    )
    if good:
        return AuditCheck(
            rule_id="SC3",
            name="Author schema（E-E-A-T 強化）",
            category="schema",
            severity="info",
            status="pass",
            actual=f"author={good.get('name')} url={good.get('url')}",
            expected="Person + name + url",
            fix_suggestion="",
        )

    # 有 author 但缺欄位
    sample = candidates[0]
    missing = []
    if "Person" not in _types_of(sample):
        missing.append("@type=Person")
    if not sample.get("name"):
        missing.append("name")
    if not sample.get("url"):
        missing.append("url")
    return AuditCheck(
        rule_id="SC3",
        name="Author schema（E-E-A-T 強化）",
        category="schema",
        severity="info",
        status="warn",
        actual=f"author 缺欄位：{', '.join(missing)}",
        expected="Person + name + url",
        fix_suggestion="補齊 author 欄位連到作者個人頁",
    )


# ── SC5: FAQPage / HowTo ──


def _check_faq_or_howto(objects: list[dict], *, has_parse_error: bool) -> AuditCheck:
    found = _has_type(objects, ("FAQPage", "HowTo"))
    if found:
        return AuditCheck(
            rule_id="SC5",
            name="FAQPage / HowTo schema 偵測",
            category="schema",
            severity="info",
            status="pass",
            actual=f"@type={','.join(_types_of(found))}",
            expected="任一存在 / N/A",
            fix_suggestion="",
        )
    if has_parse_error:
        return AuditCheck(
            rule_id="SC5",
            name="FAQPage / HowTo schema 偵測",
            category="schema",
            severity="info",
            status="skip",
            actual="JSON-LD parse 失敗",
            expected="任一存在 / N/A",
            fix_suggestion="先修 SC4 parse error",
        )
    # Deterministic 層不能判斷內容語意上是否該帶 FAQ/HowTo（那是 LLM semantic）；
    # 沒 schema 一律 skip + 提示，而非 warn — 避免誤標非 FAQ 頁。
    return AuditCheck(
        rule_id="SC5",
        name="FAQPage / HowTo schema 偵測",
        category="schema",
        severity="info",
        status="skip",
        actual="無 FAQPage / HowTo（內容是否含 FAQ/step 留 LLM 評估）",
        expected="任一存在 / N/A",
        fix_suggestion=(
            "若內容含常見問答 / step-by-step 教學 → 加 FAQPage / HowTo schema "
            "觸發 rich result（SEOPress block 預設帶）"
        ),
    )
