"""URL not in HOST_TO_TARGET_SITE → §6 GSC says "不適用" without raising."""

from __future__ import annotations

from datetime import datetime, timezone

import yaml

# `patch_fetch_html` lives in conftest.py — pytest auto-discovers it.
from tests.skills.seo_audit_post.test_audit_pipeline import (
    _fake_compliance_scanner,
    _fake_llm_reviewer,
    _fake_pagespeed_response,
    audit_mod,
)


def _now():
    return datetime(2026, 4, 26, 3, 0, 0, tzinfo=timezone.utc)


def test_non_self_hosted_url_skips_gsc_section_gracefully(tmp_path, patch_fetch_html, monkeypatch):
    """example.com is not in HOST_TO_TARGET_SITE → no GSC query attempted."""
    gsc_called = {"count": 0}

    def boom_gsc(*args, **kwargs):
        gsc_called["count"] += 1
        raise AssertionError("GSC should not be called for non-self-hosted URLs")

    # Override the fetch_html monkeypatch URL — it currently always returns
    # the fixture for any URL, so we just call audit() with a different URL.
    out_path = audit_mod.audit(
        url="https://example.com/some-blog-post",
        output_dir=tmp_path,
        focus_keyword="test",
        enable_kb=False,
        pagespeed_runner=lambda u, s: _fake_pagespeed_response(),
        gsc_querier=boom_gsc,
        compliance_scanner=_fake_compliance_scanner,
        llm_reviewer=_fake_llm_reviewer,
        now_fn=_now,
    )
    assert gsc_called["count"] == 0

    md = out_path.read_text(encoding="utf-8")
    fm = yaml.safe_load(md.split("---\n", 2)[1])
    assert fm["target_site"] is None
    assert fm["gsc_section"] == "skipped (non-self-hosted)"
    assert "## 6. GSC Ranking" in md
    assert "不適用" in md


def test_self_hosted_but_gsc_property_env_missing(tmp_path, patch_fetch_html, monkeypatch):
    """Self-hosted URL but env GSC_PROPERTY_SHOSHO empty → graceful skip."""
    monkeypatch.delenv("GSC_PROPERTY_SHOSHO", raising=False)
    monkeypatch.delenv("GSC_PROPERTY_FLEET", raising=False)
    gsc_called = {"count": 0}

    def boom_gsc(*args, **kwargs):
        gsc_called["count"] += 1
        return []

    out_path = audit_mod.audit(
        url="https://shosho.tw/zone-2-training-guide",
        output_dir=tmp_path,
        focus_keyword="zone 2 訓練",
        gsc_property=None,  # rely on env (which is now empty)
        enable_kb=False,
        pagespeed_runner=lambda u, s: _fake_pagespeed_response(),
        gsc_querier=boom_gsc,
        compliance_scanner=_fake_compliance_scanner,
        llm_reviewer=_fake_llm_reviewer,
        now_fn=_now,
    )
    assert gsc_called["count"] == 0
    md = out_path.read_text(encoding="utf-8")
    fm = yaml.safe_load(md.split("---\n", 2)[1])
    assert fm["target_site"] == "wp_shosho"
    assert fm["gsc_section"].startswith("skipped (GSC_PROPERTY_SHOSHO not set)")


def test_gsc_query_exception_caught_and_marked(tmp_path, patch_fetch_html):
    """GSC API error → gsc_section starts with 'error', pipeline still produces report."""

    def raising_gsc(*args, **kwargs):
        raise RuntimeError("GSC quota exceeded")

    out_path = audit_mod.audit(
        url="https://shosho.tw/zone-2-training-guide",
        output_dir=tmp_path,
        focus_keyword="zone 2 訓練",
        gsc_property="sc-domain:shosho.tw",
        enable_kb=False,
        pagespeed_runner=lambda u, s: _fake_pagespeed_response(),
        gsc_querier=raising_gsc,
        compliance_scanner=_fake_compliance_scanner,
        llm_reviewer=_fake_llm_reviewer,
        now_fn=_now,
    )
    assert out_path.exists()
    md = out_path.read_text(encoding="utf-8")
    fm = yaml.safe_load(md.split("---\n", 2)[1])
    assert fm["gsc_section"].startswith("error")
    assert "錯誤跳過" in md
