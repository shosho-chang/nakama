"""SEO audit pipeline for `seo-audit-post` skill — Slice D.2 (ADR-009 Phase 1.5).

Orchestrates the deterministic modules from `shared/seo_audit/` (D.1) + PageSpeed
Insights + LLM semantic check (D.2 §附錄 C) + optional GSC ranking section +
optional Robin KB internal-link suggestion → markdown report (§附錄 B).

Design — pure functions + injectable clients
--------------------------------------------
The orchestrator is a thin wrapper; each side-effect dependency (PageSpeed,
GSC, Robin KB, LLM) is injected so unit tests can stub them out:

- `audit(...)` orchestrator — accepts `pagespeed_runner` / `gsc_query` /
  `kb_search` / `llm_review` callables; defaults wire to real modules.
- All rendering / counting helpers are pure.

§附錄 B contract guarantees:
- Frontmatter `type: seo-audit-report`
- 5 mandatory sections (1 Summary / 2 Critical / 3 Warnings / 4 Info /
  5 PageSpeed) + 2 optional (6 GSC / 7 Internal links).

CLI: `python audit.py --url <url> --output-dir <dir> [--focus-keyword K]
[--gsc-property sc-domain:shosho.tw] [--no-kb] [--strategy desktop]
[--llm-level haiku|none]`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

# Make repo root importable so `python <this-file>.py` works from any cwd.
_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import yaml  # noqa: E402

from shared.log import get_logger  # noqa: E402
from shared.seo_audit import (  # noqa: E402
    AuditCheck,
    AuditResult,
    FetchResult,
    LLMLevel,
    check_headings,
    check_images,
    check_metadata,
    check_performance,
    check_schema_markup,
    check_structure,
    fetch_html,
    fetch_html_via_firecrawl,
    llm_review,  # noqa: E402
)

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]

logger = get_logger("nakama.seo_audit_post.pipeline")
_TAIPEI = ZoneInfo("Asia/Taipei")

_GSC_END_LAG_DAYS = 3
_GSC_WINDOW_DAYS = 28
_KB_TOP_K = 5

# ---------------------------------------------------------------------------
# Type aliases for injected dependencies
# ---------------------------------------------------------------------------

Strategy = str  # "mobile" | "desktop"
PageSpeedRunner = Callable[[str, Strategy], dict[str, Any]]
GSCQuerier = Callable[[str, str, str], list[dict[str, Any]]]  # (property, start, end)
KBSearcher = Callable[[str, Path, int], list[dict[str, Any]]]  # (query, vault, top_k)
ComplianceScanner = Callable[[str], dict[str, Any]]
LLMReviewer = Callable[..., list[AuditCheck]]
HtmlFetcher = Callable[[str], FetchResult]


# ---------------------------------------------------------------------------
# Result aggregator (extends AuditResult with section metadata)
# ---------------------------------------------------------------------------


@dataclass
class AuditOutcome:
    """Wraps `AuditResult` + side-channel data needed to render the markdown."""

    result: AuditResult
    pagespeed_strategy: str
    pagespeed_summary: dict[str, Any]  # extracted scores + Core Web Vitals
    llm_level: LLMLevel
    target_site: str | None
    focus_keyword: str | None
    gsc_section: str  # "included" | "skipped (non-self-hosted)" | "error" | "skipped (--no-gsc)"
    gsc_rows: list[dict[str, Any]] = field(default_factory=list)
    kb_section: str = "skipped (--no-kb)"
    kb_results: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Default-injection wrappers (real-world side effects — mocked in tests)
# ---------------------------------------------------------------------------


def _default_pagespeed_runner(url: str, strategy: str) -> dict[str, Any]:
    from shared.pagespeed_client import PageSpeedClient

    return PageSpeedClient.from_env().run(url, strategy=strategy)  # type: ignore[arg-type]


def _default_gsc_querier(gsc_property: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
    from shared.gsc_client import GSCClient

    client = GSCClient.from_env()
    return client.query(
        site=gsc_property,
        start_date=start_date,
        end_date=end_date,
        dimensions=["query"],
        row_limit=100,
    )


def _default_kb_searcher(query: str, vault_path: Path, top_k: int) -> list[dict[str, Any]]:
    from agents.robin.kb_search import search_kb

    return search_kb(query, vault_path, top_k=top_k, purpose="seo_audit")


def _default_compliance_scanner(text: str) -> dict[str, Any]:
    from shared.compliance import scan_text

    gate = scan_text(text)
    return {
        "medical_claim": gate.medical_claim,
        "absolute_assertion": gate.absolute_assertion,
        "matched_terms": list(gate.matched_terms),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slugify(text: str) -> str:
    """簡單 slugify — 給 filename / report frontmatter 用。"""
    s = re.sub(r"https?://", "", text)
    s = re.sub(r"[^\w一-鿿\-]+", "-", s, flags=re.UNICODE)
    s = re.sub(r"-+", "-", s).strip("-").lower()
    return s or "audit"


def _output_filename(url: str, now_fn: Callable[[], datetime] | None = None) -> str:
    now = now_fn() if now_fn is not None else datetime.now(tz=_TAIPEI)
    date_str = now.astimezone(_TAIPEI).strftime("%Y%m%d")
    parsed = urlsplit(url)
    slug_source = parsed.path.strip("/") or parsed.netloc
    return f"audit-{_slugify(slug_source)}-{date_str}.md"


def _resolve_target_site(url: str) -> str | None:
    """URL host → target_site app-name (`wp_shosho` / `wp_fleet`)；未知 host → None。"""
    try:
        from shared.schemas.site_mapping import host_to_target_site

        host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
        return host_to_target_site(host)
    except Exception:
        return None


def _gsc_window(now_fn: Callable[[], datetime] | None) -> tuple[str, str]:
    now = now_fn() if now_fn is not None else datetime.now(tz=_TAIPEI)
    end = (now.astimezone(_TAIPEI) - timedelta(days=_GSC_END_LAG_DAYS)).date()
    start = end - timedelta(days=_GSC_WINDOW_DAYS - 1)
    return start.isoformat(), end.isoformat()


def _extract_pagespeed_summary(pagespeed_result: dict[str, Any]) -> dict[str, Any]:
    """從 PageSpeed raw response 抽 category scores + CrUX field metrics → 摘要 dict。"""
    summary: dict[str, Any] = {
        "performance_score": None,
        "seo_score": None,
        "best_practices_score": None,
        "accessibility_score": None,
        "lcp_seconds": None,
        "inp_ms": None,
        "cls": None,
    }
    if not isinstance(pagespeed_result, dict):
        return summary
    lh = pagespeed_result.get("lighthouseResult") or {}
    categories = lh.get("categories") or {}
    for key, score_key in (
        ("performance", "performance_score"),
        ("seo", "seo_score"),
        ("best-practices", "best_practices_score"),
        ("accessibility", "accessibility_score"),
    ):
        cat = categories.get(key) or {}
        score = cat.get("score")
        if isinstance(score, (int, float)):
            summary[score_key] = round(score * 100)
    audits = lh.get("audits") or {}
    lcp_raw = (audits.get("largest-contentful-paint") or {}).get("numericValue")
    if isinstance(lcp_raw, (int, float)):
        summary["lcp_seconds"] = round(lcp_raw / 1000.0, 2)
    cls_raw = (audits.get("cumulative-layout-shift") or {}).get("numericValue")
    if isinstance(cls_raw, (int, float)):
        summary["cls"] = round(cls_raw, 3)
    loading_exp = pagespeed_result.get("loadingExperience") or {}
    metrics = loading_exp.get("metrics") or {}
    inp = (metrics.get("INTERACTION_TO_NEXT_PAINT_MS") or {}).get("percentile")
    if isinstance(inp, (int, float)):
        summary["inp_ms"] = int(inp)
    return summary


def _grade(checks: list[AuditCheck]) -> str:
    """A / B+ / B / C+ / C / D / F by fail count + critical-fail count."""
    fail = sum(1 for c in checks if c.status == "fail")
    crit_fail = sum(1 for c in checks if c.status == "fail" and c.severity == "critical")
    if crit_fail >= 3:
        return "F"
    if crit_fail >= 1 and fail >= 5:
        return "D"
    if crit_fail >= 1:
        return "C"
    if fail >= 5:
        return "C+"
    if fail >= 3:
        return "B"
    if fail >= 1:
        return "B+"
    return "A"


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


_CATEGORY_ORDER: tuple[tuple[str, str], ...] = (
    ("fetch", "Fetch"),
    ("metadata", "Metadata"),
    ("opengraph", "OpenGraph"),
    ("headings", "Headings"),
    ("images", "Images"),
    ("structure", "Structure"),
    ("schema", "Schema"),
    ("performance", "Performance"),
    ("semantic", "Semantic"),
)


def _render_frontmatter(outcome: AuditOutcome, now: datetime) -> str:
    checks = outcome.result.checks
    fm = {
        "type": "seo-audit-report",
        "schema_version": 1,
        "audit_target": outcome.result.url,
        "target_site": outcome.target_site,
        "focus_keyword": outcome.focus_keyword,
        "fetched_at": now.astimezone(timezone.utc).isoformat(),
        "phase": "1.5 (deterministic + llm)",
        "generated_by": "seo-audit-post (Slice D.2)",
        "pagespeed_strategy": outcome.pagespeed_strategy,
        "llm_level": outcome.llm_level,
        "gsc_section": outcome.gsc_section,
        "kb_section": outcome.kb_section,
        "summary": {
            "total": len(checks),
            "pass": sum(1 for c in checks if c.status == "pass"),
            "warn": sum(1 for c in checks if c.status == "warn"),
            "fail": sum(1 for c in checks if c.status == "fail"),
            "skip": sum(1 for c in checks if c.status == "skip"),
            "overall_grade": _grade(checks),
        },
    }
    return yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).rstrip() + "\n"


def _render_summary_section(checks: list[AuditCheck]) -> list[str]:
    lines = ["## 1. Summary", ""]
    lines.append("| 類別 | Pass | Warn | Fail | Skip |")
    lines.append("|---|---|---|---|---|")
    totals = [0, 0, 0, 0]
    for cat_key, cat_label in _CATEGORY_ORDER:
        cat_checks = [c for c in checks if c.category == cat_key]
        if not cat_checks:
            continue
        p = sum(1 for c in cat_checks if c.status == "pass")
        w = sum(1 for c in cat_checks if c.status == "warn")
        f = sum(1 for c in cat_checks if c.status == "fail")
        s = sum(1 for c in cat_checks if c.status == "skip")
        totals[0] += p
        totals[1] += w
        totals[2] += f
        totals[3] += s
        lines.append(f"| {cat_label} | {p} | {w} | {f} | {s} |")
    lines.append(
        f"| **Total** | **{totals[0]}** | **{totals[1]}** | **{totals[2]}** | **{totals[3]}** |"
    )
    lines.append("")
    lines.append(f"**Overall grade: {_grade(checks)}**")
    lines.append("")
    crit = [c for c in checks if c.status == "fail" and c.severity == "critical"]
    if crit:
        lines.append("最重要修法（按 severity）：")
        for i, c in enumerate(crit[:5], 1):
            lines.append(f"{i}. [{c.rule_id}] {c.name} — {c.actual}")
        lines.append("")
    return lines


def _render_check_block(c: AuditCheck) -> list[str]:
    lines = [f"### [{c.rule_id}] {c.name}", ""]
    lines.append(f"- **Actual**: {c.actual}")
    lines.append(f"- **Expected**: {c.expected}")
    if c.fix_suggestion:
        lines.append(f"- **Fix**: {c.fix_suggestion}")
    lines.append("")
    return lines


def _render_critical_section(checks: list[AuditCheck]) -> list[str]:
    crit = [c for c in checks if c.status == "fail" and c.severity == "critical"]
    lines = ["## 2. Critical Fixes（必修）", ""]
    if not crit:
        lines.append("（無）")
        lines.append("")
        return lines
    for c in crit:
        lines.extend(_render_check_block(c))
    return lines


def _render_warnings_section(checks: list[AuditCheck]) -> list[str]:
    items = [
        c
        for c in checks
        if (c.status == "warn") or (c.status == "fail" and c.severity != "critical")
    ]
    lines = ["## 3. Warnings（建議修）", ""]
    if not items:
        lines.append("（無）")
        lines.append("")
        return lines
    for c in items:
        lines.extend(_render_check_block(c))
    return lines


def _render_info_section(checks: list[AuditCheck]) -> list[str]:
    info = [c for c in checks if c.severity == "info" and c.status != "skip"]
    lines = ["## 4. Info（觀察）", ""]
    if not info:
        lines.append("（無）")
        lines.append("")
        return lines
    for c in info:
        lines.append(f"- [{c.rule_id}] {c.name} — {c.actual}")
    lines.append("")
    return lines


def _render_pagespeed_section(summary: dict[str, Any], strategy: str) -> list[str]:
    lines = ["## 5. PageSpeed Insights Summary", ""]

    def fmt_score(v):
        return f"{v} / 100" if v is not None else "—"

    lines.append(f"- **Performance**: {fmt_score(summary.get('performance_score'))} ({strategy})")
    lines.append(f"- **SEO**: {fmt_score(summary.get('seo_score'))}")
    lines.append(f"- **Best Practices**: {fmt_score(summary.get('best_practices_score'))}")
    lines.append(f"- **Accessibility**: {fmt_score(summary.get('accessibility_score'))}")
    lines.append("")
    lines.append("Core Web Vitals:")
    lcp = summary.get("lcp_seconds")
    inp = summary.get("inp_ms")
    cls = summary.get("cls")
    lines.append(f"- LCP: {lcp}s" if lcp is not None else "- LCP: —")
    lines.append(f"- INP: {inp}ms" if inp is not None else "- INP: — (CrUX 無 field data)")
    lines.append(f"- CLS: {cls}" if cls is not None else "- CLS: —")
    lines.append("")
    return lines


def _render_gsc_section(outcome: AuditOutcome) -> list[str]:
    lines = ["## 6. GSC Ranking（last 28 days）", ""]
    if outcome.gsc_section != "included":
        if outcome.gsc_section == "skipped (non-self-hosted)":
            lines.append("不適用（URL 非自有網站）")
        elif outcome.gsc_section.startswith("error"):
            lines.append(f"錯誤跳過：{outcome.gsc_section}")
        else:
            lines.append(f"跳過：{outcome.gsc_section}")
        lines.append("")
        return lines
    if not outcome.gsc_rows:
        lines.append("此頁面 28 天內 GSC 無資料（可能是新頁 / 無 impression）")
        lines.append("")
        return lines
    lines.append("| Query | Clicks | Impressions | CTR | Position |")
    lines.append("|---|---|---|---|---|")
    rows = sorted(
        outcome.gsc_rows,
        key=lambda r: int(r.get("impressions", 0) or 0),
        reverse=True,
    )[:15]
    for r in rows:
        keys = r.get("keys") or [""]
        kw = keys[0] if keys else ""
        clicks = int(r.get("clicks", 0) or 0)
        imp = int(r.get("impressions", 0) or 0)
        ctr = float(r.get("ctr", 0.0) or 0.0)
        pos = float(r.get("position", 0.0) or 0.0)
        lines.append(f"| {kw} | {clicks} | {imp} | {ctr * 100:.1f}% | {pos:.1f} |")
    lines.append("")
    striking = [
        r
        for r in outcome.gsc_rows
        if 11.0 <= float(r.get("position", 0.0) or 0.0) <= 20.0
        and int(r.get("impressions", 0) or 0) >= 50
    ]
    if striking:
        lines.append("**Striking distance opportunities**（pos 11-20，impressions ≥ 50）：")
        for r in sorted(striking, key=lambda x: -int(x.get("impressions", 0) or 0))[:5]:
            keys = r.get("keys") or [""]
            kw = keys[0] if keys else ""
            pos = float(r.get("position", 0.0) or 0.0)
            imp = int(r.get("impressions", 0) or 0)
            lines.append(f"- 「{kw}」 — pos {pos:.1f}, {imp} imp/28d → 內文補充 + internal link")
        lines.append("")
    return lines


def _render_kb_section(outcome: AuditOutcome) -> list[str]:
    lines = ["## 7. Internal Link Suggestions（via Robin KB）", ""]
    if outcome.kb_section != "included":
        if outcome.kb_section.startswith("error"):
            lines.append(f"錯誤跳過：{outcome.kb_section}")
        else:
            lines.append(f"跳過：{outcome.kb_section}")
        lines.append("")
        return lines
    if not outcome.kb_results:
        lines.append("KB 內無相關頁面（可佐證內文觀點的素材尚未建檔）")
        lines.append("")
        return lines
    lines.append("從 KB 找到的相關 page（按相關性排序）：")
    lines.append("")
    for i, r in enumerate(outcome.kb_results, 1):
        path = r.get("path", "")
        reason = r.get("relevance_reason", "")
        lines.append(f"{i}. [[{path}]] — {reason}")
    lines.append("")
    return lines


def render_markdown(outcome: AuditOutcome, now: datetime, title: str | None = None) -> str:
    checks = outcome.result.checks
    fm = _render_frontmatter(outcome, now)
    body: list[str] = []
    body.append(f"# SEO Audit — {title or outcome.result.url}")
    body.append("")
    body.extend(_render_summary_section(checks))
    body.extend(_render_critical_section(checks))
    body.extend(_render_warnings_section(checks))
    body.extend(_render_info_section(checks))
    body.extend(_render_pagespeed_section(outcome.pagespeed_summary, outcome.pagespeed_strategy))
    body.extend(_render_gsc_section(outcome))
    body.extend(_render_kb_section(outcome))
    return "---\n" + fm + "---\n\n" + "\n".join(body).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def audit(
    url: str,
    output_dir: Path,
    *,
    focus_keyword: str | None = None,
    gsc_property: str | None = None,
    enable_kb: bool = True,
    vault_path: Path | None = None,
    pagespeed_strategy: str = "mobile",
    llm_level: LLMLevel = "sonnet",
    pagespeed_runner: PageSpeedRunner | None = None,
    gsc_querier: GSCQuerier | None = None,
    kb_searcher: KBSearcher | None = None,
    compliance_scanner: ComplianceScanner | None = None,
    llm_reviewer: LLMReviewer | None = None,
    html_fetcher: HtmlFetcher | None = None,
    now_fn: Callable[[], datetime] | None = None,
) -> Path:
    """Run full audit pipeline and write markdown report. Returns the path.

    All side-effecting collaborators are injectable. With everything `None`,
    the function wires real PageSpeed / GSC / Robin KB / compliance_scan / LLM
    review modules. Tests pass fakes.

    `html_fetcher=None` 走 default `fetch_html`（httpx），CLI `--via-firecrawl`
    會 inject `fetch_html_via_firecrawl` — 用於 caller IP 被 CF SBFM 擋的場景
    （VPS datacenter IP 打 shosho.tw 會 403）。
    """
    pagespeed_runner = pagespeed_runner or _default_pagespeed_runner
    compliance_scanner = compliance_scanner or _default_compliance_scanner
    html_fetcher = html_fetcher or fetch_html

    now = now_fn() if now_fn is not None else datetime.now(tz=timezone.utc)
    fetched_at_iso = now.astimezone(timezone.utc).isoformat()

    # 1. Fetch HTML
    fetch = html_fetcher(url)
    result = AuditResult(url=url, fetched_at=fetched_at_iso)
    result.checks.append(fetch.fetch_check)

    if fetch.soup is None:
        # 早收 — fetch 失敗就只回 fetch fail check + 空殼 markdown
        outcome = AuditOutcome(
            result=result,
            pagespeed_strategy=pagespeed_strategy,
            pagespeed_summary={},
            llm_level=llm_level,
            target_site=_resolve_target_site(url),
            focus_keyword=focus_keyword,
            gsc_section="skipped (fetch failed)",
            kb_section="skipped (fetch failed)",
        )
        return _write_report(outcome, now, output_dir, url, fetch_title=None)

    # 2. PageSpeed (序貫 — 對齊 §D.2.6 邊界，不做 asyncio)
    try:
        pagespeed_result = pagespeed_runner(url, pagespeed_strategy)
    except Exception as e:
        logger.warning("pagespeed_failed url=%s err=%s", url, e)
        pagespeed_result = {}

    pagespeed_summary = _extract_pagespeed_summary(pagespeed_result)

    # 3. Deterministic checks (D.1)
    result.checks.extend(check_metadata(fetch.soup, url, focus_keyword=focus_keyword))
    result.checks.extend(check_headings(fetch.soup, focus_keyword=focus_keyword))
    result.checks.extend(check_images(fetch.soup, base_url=url))
    result.checks.extend(check_structure(fetch.soup, base_url=url, focus_keyword=focus_keyword))
    result.checks.extend(check_schema_markup(fetch.soup))
    result.checks.extend(check_performance(pagespeed_result))

    # 4. KB context (for LLM L10 + report §7)
    target_site = _resolve_target_site(url)
    kb_section = "skipped (--no-kb)"
    kb_results: list[dict[str, Any]] = []
    if enable_kb and focus_keyword and vault_path is not None:
        try:
            kb_searcher_fn = kb_searcher or _default_kb_searcher
            kb_results = kb_searcher_fn(focus_keyword, vault_path, _KB_TOP_K) or []
            kb_section = "included" if kb_results else "skipped (no results)"
        except Exception as e:
            logger.warning("kb_search_failed err=%s", e)
            kb_results = []
            kb_section = f"error ({type(e).__name__})"
    elif enable_kb and (not focus_keyword or vault_path is None):
        kb_section = "skipped (focus_keyword or vault_path missing)"

    # 5. Compliance pre-scan → feed LLM L9 context
    try:
        text_for_compliance = fetch.soup.get_text(separator="\n", strip=True)
        compliance_findings = compliance_scanner(text_for_compliance)
    except Exception as e:
        logger.warning("compliance_scan_failed err=%s", e)
        compliance_findings = None

    # 6. LLM semantic check (12 條 batch)
    reviewer = llm_reviewer or llm_review.review
    try:
        llm_checks = reviewer(
            fetch.soup,
            fetch.html,
            focus_keyword,
            url=url,
            kb_context=kb_results,
            compliance_findings=compliance_findings,
            model=llm_level,
        )
    except Exception as e:
        logger.warning("llm_review_call_uncaught err=%s", e)
        llm_checks = []
    result.checks.extend(llm_checks)

    # 7. GSC query (only for self-hosted URLs)
    gsc_section, gsc_rows = _maybe_gsc_query(url, gsc_property, target_site, gsc_querier, now_fn)

    title = _extract_title(fetch.soup)
    outcome = AuditOutcome(
        result=result,
        pagespeed_strategy=pagespeed_strategy,
        pagespeed_summary=pagespeed_summary,
        llm_level=llm_level,
        target_site=target_site,
        focus_keyword=focus_keyword,
        gsc_section=gsc_section,
        gsc_rows=gsc_rows,
        kb_section=kb_section,
        kb_results=kb_results,
    )
    return _write_report(outcome, now, output_dir, url, fetch_title=title)


def _maybe_gsc_query(
    url: str,
    gsc_property: str | None,
    target_site: str | None,
    gsc_querier: GSCQuerier | None,
    now_fn: Callable[[], datetime] | None,
) -> tuple[str, list[dict[str, Any]]]:
    if target_site is None and gsc_property is None:
        return "skipped (non-self-hosted)", []
    try:
        if gsc_property is None:
            import os

            env_key = "GSC_PROPERTY_SHOSHO" if target_site == "wp_shosho" else "GSC_PROPERTY_FLEET"
            gsc_property = os.environ.get(env_key, "")
            if not gsc_property:
                return f"skipped ({env_key} not set)", []
        querier = gsc_querier or _default_gsc_querier
        start, end = _gsc_window(now_fn)
        rows = querier(gsc_property, start, end)
    except Exception as e:
        logger.warning("gsc_query_failed err=%s", e)
        return f"error ({type(e).__name__})", []
    # GSC 回傳全站 rows；filter 到此 page
    page_path = urlsplit(url).path or "/"
    matched: list[dict[str, Any]] = []
    for r in rows:
        keys = r.get("keys") or []
        # query-only dimensions → keys=[query]
        # query+page dimensions → keys=[query, page]
        if len(keys) == 1:
            matched.append(r)
        elif len(keys) >= 2:
            page_url = keys[1]
            if page_url == url or urlsplit(page_url).path == page_path:
                matched.append({"keys": [keys[0]], **{k: v for k, v in r.items() if k != "keys"}})
    return "included", matched


def _extract_title(soup) -> str | None:
    if soup is None:
        return None
    t = soup.find("title")
    if t is None:
        return None
    text = t.get_text().strip()
    return text or None


def _write_report(
    outcome: AuditOutcome,
    now: datetime,
    output_dir: Path,
    url: str,
    fetch_title: str | None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    md = render_markdown(outcome, now, title=fetch_title)
    out_path = output_dir / _output_filename(url, now_fn=lambda: now)
    out_path.write_text(md, encoding="utf-8")
    logger.info("seo_audit_wrote path=%s", out_path)
    return out_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="seo-audit-post pipeline (ADR-009 Phase 1.5 Slice D.2)."
    )
    parser.add_argument("--url", required=True, help="Target URL (with scheme)")
    parser.add_argument(
        "--output-dir", type=Path, required=True, help="Directory for the markdown report"
    )
    parser.add_argument("--focus-keyword", default=None, help="Focus keyword (for L1/L2/L3/L4)")
    parser.add_argument(
        "--gsc-property",
        default=None,
        help="GSC property (e.g. sc-domain:shosho.tw); if URL host maps via "
        "site_mapping and env GSC_PROPERTY_SHOSHO/FLEET is set, omit this",
    )
    parser.add_argument(
        "--no-kb", action="store_true", help="Disable Robin KB internal-link section"
    )
    parser.add_argument(
        "--vault-path",
        type=Path,
        default=None,
        help="Obsidian vault root (auto from VAULT_PATH env if unset)",
    )
    parser.add_argument(
        "--strategy", choices=["mobile", "desktop"], default="mobile", help="PageSpeed strategy"
    )
    parser.add_argument(
        "--llm-level",
        choices=["sonnet", "haiku", "none"],
        default="sonnet",
        help="LLM semantic level; --llm-level=none skips L1-L12 (純 deterministic)",
    )
    parser.add_argument(
        "--via-firecrawl",
        action="store_true",
        help="Fetch target HTML via firecrawl scrape (formats=rawHtml). "
        "用於 caller IP 被 CF SBFM 擋的場景（如 VPS datacenter IP 打 shosho.tw 全 403）。"
        "每次 audit +1 firecrawl credit。",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    args = _parse_args(argv)

    vault_path = args.vault_path
    if vault_path is None and not args.no_kb:
        import os

        vault_env = os.environ.get("VAULT_PATH")
        if vault_env:
            vault_path = Path(vault_env)

    html_fetcher = fetch_html_via_firecrawl if args.via_firecrawl else None

    out_path = audit(
        url=args.url,
        output_dir=args.output_dir,
        focus_keyword=args.focus_keyword,
        gsc_property=args.gsc_property,
        enable_kb=not args.no_kb,
        vault_path=vault_path,
        pagespeed_strategy=args.strategy,
        llm_level=args.llm_level,
        html_fetcher=html_fetcher,
    )
    print(json.dumps({"output_path": str(out_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
