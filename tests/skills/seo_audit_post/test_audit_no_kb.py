"""KB section: --no-kb / vault path missing / kb_search exception → graceful skip."""

from __future__ import annotations

from datetime import datetime, timezone

import yaml

from tests.skills.seo_audit_post.test_audit_pipeline import (
    _fake_compliance_scanner,
    _fake_llm_reviewer,
    _fake_pagespeed_response,
    audit_mod,
)


def _now():
    return datetime(2026, 4, 26, 3, 0, 0, tzinfo=timezone.utc)


def test_no_kb_flag_skips_kb_searcher(tmp_path, patch_fetch_html):
    kb_called = {"count": 0}

    def boom_kb(*args, **kwargs):
        kb_called["count"] += 1
        raise AssertionError("KB search should not be called when enable_kb=False")

    out_path = audit_mod.audit(
        url="https://shosho.tw/zone-2-training-guide",
        output_dir=tmp_path,
        focus_keyword="zone 2 訓練",
        enable_kb=False,
        pagespeed_runner=lambda u, s: _fake_pagespeed_response(),
        kb_searcher=boom_kb,
        compliance_scanner=_fake_compliance_scanner,
        llm_reviewer=_fake_llm_reviewer,
        now_fn=_now,
    )
    assert kb_called["count"] == 0
    md = out_path.read_text(encoding="utf-8")
    fm = yaml.safe_load(md.split("---\n", 2)[1])
    assert fm["kb_section"] == "skipped (--no-kb)"
    assert "## 7. Internal Link Suggestions" in md
    assert "跳過" in md


def test_vault_path_missing_skips_kb(tmp_path, patch_fetch_html):
    kb_called = {"count": 0}

    def boom_kb(*args, **kwargs):
        kb_called["count"] += 1
        return []

    out_path = audit_mod.audit(
        url="https://shosho.tw/zone-2-training-guide",
        output_dir=tmp_path,
        focus_keyword="zone 2 訓練",
        enable_kb=True,
        vault_path=None,
        pagespeed_runner=lambda u, s: _fake_pagespeed_response(),
        kb_searcher=boom_kb,
        compliance_scanner=_fake_compliance_scanner,
        llm_reviewer=_fake_llm_reviewer,
        now_fn=_now,
    )
    assert kb_called["count"] == 0
    md = out_path.read_text(encoding="utf-8")
    fm = yaml.safe_load(md.split("---\n", 2)[1])
    assert "missing" in fm["kb_section"]


def test_kb_search_exception_does_not_break_pipeline(tmp_path, patch_fetch_html):
    def raising_kb(*args, **kwargs):
        raise RuntimeError("vault unreachable")

    out_path = audit_mod.audit(
        url="https://shosho.tw/zone-2-training-guide",
        output_dir=tmp_path,
        focus_keyword="zone 2 訓練",
        enable_kb=True,
        vault_path=tmp_path / "vault",
        pagespeed_runner=lambda u, s: _fake_pagespeed_response(),
        kb_searcher=raising_kb,
        compliance_scanner=_fake_compliance_scanner,
        llm_reviewer=_fake_llm_reviewer,
        now_fn=_now,
    )
    assert out_path.exists()
    md = out_path.read_text(encoding="utf-8")
    fm = yaml.safe_load(md.split("---\n", 2)[1])
    assert fm["kb_section"].startswith("error")
    assert "錯誤跳過" in md


def test_focus_keyword_missing_skips_kb_search(tmp_path, patch_fetch_html):
    """No focus_keyword → KB search has no query → skip with reason."""
    kb_called = {"count": 0}

    def boom_kb(*args, **kwargs):
        kb_called["count"] += 1
        return []

    out_path = audit_mod.audit(
        url="https://shosho.tw/zone-2-training-guide",
        output_dir=tmp_path,
        focus_keyword=None,
        enable_kb=True,
        vault_path=tmp_path / "vault",
        pagespeed_runner=lambda u, s: _fake_pagespeed_response(),
        kb_searcher=boom_kb,
        compliance_scanner=_fake_compliance_scanner,
        llm_reviewer=_fake_llm_reviewer,
        now_fn=_now,
    )
    assert kb_called["count"] == 0
    md = out_path.read_text(encoding="utf-8")
    fm = yaml.safe_load(md.split("---\n", 2)[1])
    assert "missing" in fm["kb_section"]
