# ruff: noqa: E501  — fixture HTML strings contain CJK lines longer than 100 chars.
"""End-to-end pipeline test for `seo-audit-post` (ADR-009 Slice D.2).

All side effects mocked: PageSpeed runner / GSC querier / KB searcher /
LLM reviewer / compliance scanner are injected as fakes. fetch_html still
runs against an httpx mock via monkeypatch (the module's network call).
Verifies markdown structure, frontmatter shape, all section headers
present per `references/output-contract.md`.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from shared.seo_audit.types import AuditCheck

# ---------------------------------------------------------------------------
# Dynamic import — `.claude/skills/seo-audit-post/scripts/audit.py` lives
# in a hyphenated dir; importlib is the clean way to load it.
# ---------------------------------------------------------------------------


def _load_audit_module():
    repo_root = Path(__file__).resolve().parents[3]
    audit_path = repo_root / ".claude" / "skills" / "seo-audit-post" / "scripts" / "audit.py"
    spec = importlib.util.spec_from_file_location("seo_audit_post_audit_under_test", audit_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


audit_mod = _load_audit_module()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_FIXTURE_HTML = (
    """<!doctype html>
<html lang="zh-Hant">
<head>
<title>Zone 2 訓練完整指南：有氧能量系統與燃脂效率</title>
<meta name="description" content="Zone 2 訓練是低強度長時間有氧訓練的關鍵，本文解析心率區間、生理機制以及進階教練常用的訓練菜單範例">
<link rel="canonical" href="https://shosho.tw/zone-2-training-guide">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta property="og:title" content="Zone 2 訓練完整指南">
<meta property="og:description" content="低強度長時間有氧訓練全解析">
<meta property="og:image" content="https://shosho.tw/img/zone2.jpg">
<meta property="og:url" content="https://shosho.tw/zone-2-training-guide">
<meta name="twitter:card" content="summary_large_image">
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Article",
 "headline":"Zone 2 訓練完整指南",
 "author":{"@type":"Person","name":"修修","url":"https://shosho.tw/about"},
 "datePublished":"2025-12-01"}
</script>
</head>
<body>
<h1>Zone 2 訓練完整指南</h1>
<h2>什麼是 Zone 2？</h2>
<p>Zone 2 訓練是低強度有氧訓練，主要靠有氧能量系統供能。本文章從生理學機制、心率區間定義、訓練量規劃，到常見錯誤一一拆解，協助你建立可執行的長期訓練計畫。</p>
<h2>能量系統三大類型</h2>
<p>有氧能量系統 / 磷酸肌酸系統 / 糖解系統 — 三者比例隨強度與時間動態變化。Zone 2 的精髓在於把有氧系統訓練到極限。</p>
<img src="/img/hr-chart.jpg" alt="心率區間圖" width="800" height="600">
<a href="/aerobic-system">有氧能量系統解析</a>
<a href="https://pubmed.ncbi.nlm.nih.gov/12345678/">PubMed 文獻</a>
"""
    + (
        "<p>本段為配重內容，重點解釋 Zone 2 訓練在不同運動族群（耐力選手 / 一般愛好者）的應用差異與操作細節。"
        * 60
    )
    + """</p>
</body></html>
"""
)


def _fake_audit_check(rule_id: str, status: str = "pass") -> AuditCheck:
    return AuditCheck(
        rule_id=rule_id,
        name=f"fake {rule_id}",
        category="semantic",
        severity="warning",
        status=status,  # type: ignore[arg-type]
        actual=f"actual {rule_id}",
        expected=f"expected {rule_id}",
        fix_suggestion=f"fix {rule_id}",
    )


def _fake_pagespeed_response() -> dict:
    return {
        "lighthouseResult": {
            "categories": {
                "performance": {"score": 0.78},
                "seo": {"score": 0.95},
                "best-practices": {"score": 0.91},
                "accessibility": {"score": 0.88},
            },
            "audits": {
                "largest-contentful-paint": {"numericValue": 4200},
                "cumulative-layout-shift": {"numericValue": 0.08},
            },
        },
        "loadingExperience": {
            "metrics": {
                "INTERACTION_TO_NEXT_PAINT_MS": {"percentile": 187},
            }
        },
    }


def _fake_gsc_rows() -> list[dict]:
    return [
        {
            "keys": ["zone 2 訓練"],
            "clicks": 168,
            "impressions": 2540,
            "ctr": 0.066,
            "position": 8.3,
        },
        {
            "keys": ["zone 2 心率"],
            "clicks": 22,
            "impressions": 890,
            "ctr": 0.025,
            "position": 14.7,
        },
        {
            "keys": ["zone 2 訓練 跑步"],
            "clicks": 5,
            "impressions": 320,
            "ctr": 0.016,
            "position": 18.2,
        },
    ]


def _fake_kb_results() -> list[dict]:
    return [
        {
            "type": "concept",
            "title": "有氧能量系統",
            "path": "KB/Wiki/Concepts/有氧能量系統",
            "preview": "...",
            "relevance_reason": "Zone 2 主要靠有氧系統",
        },
        {
            "type": "concept",
            "title": "磷酸肌酸系統",
            "path": "KB/Wiki/Concepts/磷酸肌酸系統",
            "preview": "...",
            "relevance_reason": "對照短時間高強度",
        },
    ]


def _fake_llm_reviewer(*args, **kwargs) -> list[AuditCheck]:
    # 12 條 — 11 pass + 1 fail (L4)
    rule_ids = [f"L{i}" for i in range(1, 13)]
    return [_fake_audit_check(rid, "pass" if rid != "L4" else "fail") for rid in rule_ids]


def _fake_compliance_scanner(text: str) -> dict:
    return {"medical_claim": False, "absolute_assertion": False, "matched_terms": []}


# `patch_fetch_html` lives in conftest.py — auto-injected by pytest fixture
# discovery. Cross-file usage shares the same `_FIXTURE_HTML` constant via
# direct module import (see test_audit_no_gsc / test_audit_no_kb).


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _now():
    return datetime(2026, 4, 26, 3, 0, 0, tzinfo=timezone.utc)


def test_pipeline_writes_markdown_with_full_frontmatter(tmp_path, patch_fetch_html):
    out_path = audit_mod.audit(
        url="https://shosho.tw/zone-2-training-guide",
        output_dir=tmp_path,
        focus_keyword="zone 2 訓練",
        gsc_property="sc-domain:shosho.tw",
        enable_kb=True,
        vault_path=tmp_path / "vault",
        pagespeed_strategy="mobile",
        llm_level="sonnet",
        pagespeed_runner=lambda u, s: _fake_pagespeed_response(),
        gsc_querier=lambda p, s, e: _fake_gsc_rows(),
        kb_searcher=lambda q, v, k: _fake_kb_results(),
        compliance_scanner=_fake_compliance_scanner,
        llm_reviewer=_fake_llm_reviewer,
        now_fn=_now,
    )

    assert out_path.exists()
    md = out_path.read_text(encoding="utf-8")
    assert md.startswith("---\n")

    # Frontmatter
    head, _body = md.split("---\n", 2)[1], md.split("---\n", 2)[2]
    fm = yaml.safe_load(head)
    assert fm["type"] == "seo-audit-report"
    assert fm["schema_version"] == 1
    assert fm["audit_target"] == "https://shosho.tw/zone-2-training-guide"
    assert fm["target_site"] == "wp_shosho"
    assert fm["focus_keyword"] == "zone 2 訓練"
    assert fm["pagespeed_strategy"] == "mobile"
    assert fm["llm_level"] == "sonnet"
    assert fm["gsc_section"] == "included"
    assert fm["kb_section"] == "included"
    assert {"total", "pass", "warn", "fail", "skip", "overall_grade"} <= set(fm["summary"])
    assert fm["summary"]["total"] >= 12  # at least the 12 LLM checks

    # All 7 section headers present + correct order
    expected = [
        "## 1. Summary",
        "## 2. Critical Fixes",
        "## 3. Warnings",
        "## 4. Info",
        "## 5. PageSpeed Insights Summary",
        "## 6. GSC Ranking",
        "## 7. Internal Link Suggestions",
    ]
    last_idx = -1
    for header in expected:
        idx = md.find(header)
        assert idx > last_idx, f"section out of order or missing: {header}"
        last_idx = idx


def test_pagespeed_summary_rendered_in_markdown(tmp_path, patch_fetch_html):
    out_path = audit_mod.audit(
        url="https://shosho.tw/zone-2-training-guide",
        output_dir=tmp_path,
        focus_keyword="zone 2 訓練",
        enable_kb=False,
        pagespeed_runner=lambda u, s: _fake_pagespeed_response(),
        compliance_scanner=_fake_compliance_scanner,
        llm_reviewer=_fake_llm_reviewer,
        now_fn=_now,
    )
    md = out_path.read_text(encoding="utf-8")
    assert "Performance**: 78 / 100" in md
    assert "SEO**: 95 / 100" in md
    assert "LCP: 4.2s" in md
    assert "INP: 187ms" in md
    assert "CLS: 0.08" in md


def test_gsc_section_includes_table_and_striking(tmp_path, patch_fetch_html):
    out_path = audit_mod.audit(
        url="https://shosho.tw/zone-2-training-guide",
        output_dir=tmp_path,
        focus_keyword="zone 2 訓練",
        gsc_property="sc-domain:shosho.tw",
        enable_kb=False,
        pagespeed_runner=lambda u, s: _fake_pagespeed_response(),
        gsc_querier=lambda p, s, e: _fake_gsc_rows(),
        compliance_scanner=_fake_compliance_scanner,
        llm_reviewer=_fake_llm_reviewer,
        now_fn=_now,
    )
    md = out_path.read_text(encoding="utf-8")
    # Top row of GSC table
    assert "| Query | Clicks | Impressions | CTR | Position |" in md
    assert "zone 2 訓練" in md
    # Striking-distance: 「zone 2 心率」pos 14.7, imp 890 + 「zone 2 訓練 跑步」pos 18.2 imp 320
    assert "Striking distance opportunities" in md
    assert "zone 2 心率" in md


def test_kb_section_lists_internal_link_suggestions(tmp_path, patch_fetch_html):
    out_path = audit_mod.audit(
        url="https://shosho.tw/zone-2-training-guide",
        output_dir=tmp_path,
        focus_keyword="zone 2 訓練",
        enable_kb=True,
        vault_path=tmp_path / "vault",
        pagespeed_runner=lambda u, s: _fake_pagespeed_response(),
        kb_searcher=lambda q, v, k: _fake_kb_results(),
        compliance_scanner=_fake_compliance_scanner,
        llm_reviewer=_fake_llm_reviewer,
        now_fn=_now,
    )
    md = out_path.read_text(encoding="utf-8")
    assert "[[KB/Wiki/Concepts/有氧能量系統]]" in md
    assert "Zone 2 主要靠有氧系統" in md


def test_filename_uses_taipei_date(tmp_path, patch_fetch_html):
    out_path = audit_mod.audit(
        url="https://shosho.tw/zone-2-training-guide",
        output_dir=tmp_path,
        focus_keyword="zone 2 訓練",
        enable_kb=False,
        pagespeed_runner=lambda u, s: _fake_pagespeed_response(),
        compliance_scanner=_fake_compliance_scanner,
        llm_reviewer=_fake_llm_reviewer,
        now_fn=_now,
    )
    # 2026-04-26 03:00 UTC → 2026-04-26 11:00 Taipei → 20260426
    assert out_path.name == "audit-zone-2-training-guide-20260426.md"


def test_llm_level_none_skips_llm_call(tmp_path, patch_fetch_html):
    called = {"count": 0}

    def boom_reviewer(*args, **kwargs):
        called["count"] += 1
        return []

    out_path = audit_mod.audit(
        url="https://shosho.tw/zone-2-training-guide",
        output_dir=tmp_path,
        focus_keyword="zone 2 訓練",
        enable_kb=False,
        llm_level="none",
        pagespeed_runner=lambda u, s: _fake_pagespeed_response(),
        compliance_scanner=_fake_compliance_scanner,
        llm_reviewer=boom_reviewer,
        now_fn=_now,
    )
    # We do NOT short-circuit at orchestrator level — llm_review.review() handles "none"
    # internally and returns 12 skipped checks. Caller still calls reviewer once.
    md = out_path.read_text(encoding="utf-8")
    assert "llm_level: none" in md


def test_llm_reviewer_exception_does_not_break_pipeline(tmp_path, patch_fetch_html):
    def raising_reviewer(*args, **kwargs):
        raise RuntimeError("LLM unavailable")

    out_path = audit_mod.audit(
        url="https://shosho.tw/zone-2-training-guide",
        output_dir=tmp_path,
        focus_keyword="zone 2 訓練",
        enable_kb=False,
        pagespeed_runner=lambda u, s: _fake_pagespeed_response(),
        compliance_scanner=_fake_compliance_scanner,
        llm_reviewer=raising_reviewer,
        now_fn=_now,
    )
    # Pipeline still produces a report
    assert out_path.exists()


def test_pagespeed_failure_falls_back_to_empty_summary(tmp_path, patch_fetch_html):
    def boom_pagespeed(url, strategy):
        raise RuntimeError("PageSpeed API down")

    out_path = audit_mod.audit(
        url="https://shosho.tw/zone-2-training-guide",
        output_dir=tmp_path,
        focus_keyword="zone 2 訓練",
        enable_kb=False,
        pagespeed_runner=boom_pagespeed,
        compliance_scanner=_fake_compliance_scanner,
        llm_reviewer=_fake_llm_reviewer,
        now_fn=_now,
    )
    md = out_path.read_text(encoding="utf-8")
    # PageSpeed scores all "—" but the section is still rendered
    assert "Performance**: —" in md or "Performance**: " in md
    assert "## 5. PageSpeed Insights Summary" in md


def test_grade_computation_critical_fail(tmp_path, patch_fetch_html):
    """3 critical fails → grade F."""

    def reviewer_with_3_critical_fails(*args, **kwargs):
        return [
            AuditCheck(
                rule_id=f"L{i}",
                name=f"fake L{i}",
                category="semantic",
                severity="critical",
                status="fail" if i <= 3 else "pass",
                actual="...",
                expected="...",
                fix_suggestion="...",
            )
            for i in range(1, 13)
        ]

    out_path = audit_mod.audit(
        url="https://shosho.tw/zone-2-training-guide",
        output_dir=tmp_path,
        focus_keyword="zone 2 訓練",
        enable_kb=False,
        pagespeed_runner=lambda u, s: _fake_pagespeed_response(),
        compliance_scanner=_fake_compliance_scanner,
        llm_reviewer=reviewer_with_3_critical_fails,
        now_fn=_now,
    )
    md = out_path.read_text(encoding="utf-8")
    fm = yaml.safe_load(md.split("---\n", 2)[1])
    assert fm["summary"]["overall_grade"] == "F"


def test_kb_section_skipped_when_kb_searcher_returns_empty(tmp_path, patch_fetch_html):
    """A1 follow-up: empty KB results 應 emit `skipped (no results)` not `included`."""
    out_path = audit_mod.audit(
        url="https://shosho.tw/zone-2-training-guide",
        output_dir=tmp_path,
        focus_keyword="zone 2 訓練",
        enable_kb=True,
        vault_path=tmp_path / "vault",
        pagespeed_runner=lambda u, s: _fake_pagespeed_response(),
        kb_searcher=lambda q, v, k: [],  # empty results
        compliance_scanner=_fake_compliance_scanner,
        llm_reviewer=_fake_llm_reviewer,
        now_fn=_now,
    )
    md = out_path.read_text(encoding="utf-8")
    fm = yaml.safe_load(md.split("---\n", 2)[1])
    assert fm["kb_section"] == "skipped (no results)"


def test_resolve_target_site_does_not_misclassify_wfleet_prefix():
    """A2 follow-up: `wfleet.shosho.tw` 不該被誤分類為 wp_fleet（lstrip foot-gun）。"""
    # `lstrip("www.")` 過去把 wfleet → fleet，現在 removeprefix 不動
    result = audit_mod._resolve_target_site("https://wfleet.shosho.tw/some-page")
    assert result is None  # not "wp_fleet"
    # Ensure standard fleet host still works
    assert audit_mod._resolve_target_site("https://fleet.shosho.tw/some-page") == "wp_fleet"
    assert audit_mod._resolve_target_site("https://www.shosho.tw/some-page") == "wp_shosho"


def test_audit_uses_injected_html_fetcher(tmp_path):
    """F5-C 2026-04-27: audit() 必須接 html_fetcher 注入，不可 hard-wire fetch_html。

    用於 caller IP 被 CF SBFM 擋的場景（VPS datacenter IP 打 shosho.tw 全 403），
    走 firecrawl 為 fetcher 替代 default httpx。
    """
    from bs4 import BeautifulSoup

    from shared.seo_audit.html_fetcher import FetchResult
    from shared.seo_audit.types import AuditCheck

    fake_calls = []

    def _fake_fetcher(url):
        fake_calls.append(url)
        soup = BeautifulSoup(_FIXTURE_HTML, "html.parser")
        check = AuditCheck(
            rule_id="FETCH",
            name="page fetched OK",
            category="fetch",
            severity="critical",
            status="pass",
            actual="fake fetcher 200",
            expected="HTTP 2xx/3xx",
            fix_suggestion="",
            details={"fetcher": "fake"},
        )
        return FetchResult(
            url=url,
            final_url=url,
            status_code=200,
            content_type="text/html",
            response_time_ms=10,
            html=_FIXTURE_HTML,
            soup=soup,
            fetch_check=check,
        )

    out_path = audit_mod.audit(
        url="https://shosho.tw/zone-2-training-guide",
        output_dir=tmp_path,
        focus_keyword="zone 2 訓練",
        enable_kb=False,
        pagespeed_runner=lambda u, s: _fake_pagespeed_response(),
        compliance_scanner=_fake_compliance_scanner,
        llm_reviewer=_fake_llm_reviewer,
        html_fetcher=_fake_fetcher,
        now_fn=_now,
    )

    assert fake_calls == ["https://shosho.tw/zone-2-training-guide"]
    assert out_path.exists()
