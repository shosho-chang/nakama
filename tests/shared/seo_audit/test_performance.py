"""Performance checks tests — P1-P3 共 3 rule × ≥3 case。Mock PageSpeed response。"""

from __future__ import annotations

import pytest

from shared.seo_audit.performance import check_performance


def _by(checks, rid):
    return next(c for c in checks if c.rule_id == rid)


def _make_response(
    *,
    lcp_ms: float | None = None,
    cls_value: float | None = None,
    inp_ms: float | None = None,
) -> dict:
    audits = {}
    if lcp_ms is not None:
        audits["largest-contentful-paint"] = {"numericValue": lcp_ms}
    if cls_value is not None:
        audits["cumulative-layout-shift"] = {"numericValue": cls_value}

    loading = {}
    if inp_ms is not None:
        loading = {"metrics": {"INTERACTION_TO_NEXT_PAINT_MS": {"percentile": inp_ms}}}

    return {"lighthouseResult": {"audits": audits}, "loadingExperience": loading}


# ── P1 LCP ──


@pytest.mark.parametrize(
    "lcp_ms,expected",
    [(1500, "pass"), (2400, "pass"), (2500, "fail"), (4200, "fail")],
)
def test_p1_threshold(lcp_ms, expected):
    """boundary: 2500ms = 2.5s → fail；2499 → pass。"""
    out = check_performance(_make_response(lcp_ms=lcp_ms))
    c = _by(out, "P1")
    assert c.status == expected


def test_p1_skip_when_missing():
    c = _by(check_performance({}), "P1")
    assert c.status == "skip"


def test_p1_skip_when_not_numeric():
    resp = {"lighthouseResult": {"audits": {"largest-contentful-paint": {}}}}
    c = _by(check_performance(resp), "P1")
    assert c.status == "skip"


def test_p1_severity_critical():
    out = check_performance(_make_response(lcp_ms=5000))
    c = _by(out, "P1")
    assert c.severity == "critical"


# ── P2 INP（CrUX field） ──


def test_p2_pass():
    out = check_performance(_make_response(inp_ms=150))
    c = _by(out, "P2")
    assert c.status == "pass"


def test_p2_fail():
    out = check_performance(_make_response(inp_ms=300))
    c = _by(out, "P2")
    assert c.status == "fail"


def test_p2_skip_no_crux():
    """CrUX 缺資料 → skip 不 fail。"""
    out = check_performance(_make_response(lcp_ms=2000))  # no inp_ms
    c = _by(out, "P2")
    assert c.status == "skip"


def test_p2_threshold_boundary():
    out = check_performance(_make_response(inp_ms=200))
    c = _by(out, "P2")
    assert c.status == "fail"  # 200 不算 < 200


# ── P3 CLS ──


@pytest.mark.parametrize(
    "cls_v,expected", [(0.05, "pass"), (0.099, "pass"), (0.1, "fail"), (0.5, "fail")]
)
def test_p3_threshold(cls_v, expected):
    out = check_performance(_make_response(cls_value=cls_v))
    c = _by(out, "P3")
    assert c.status == expected


def test_p3_skip_missing():
    c = _by(check_performance({}), "P3")
    assert c.status == "skip"


def test_p3_actual_format():
    out = check_performance(_make_response(cls_value=0.234567))
    c = _by(out, "P3")
    assert c.actual == "0.235"  # 3 dp


# ── 整體 ──


def test_returns_3_checks():
    out = check_performance({})
    assert [c.rule_id for c in out] == ["P1", "P2", "P3"]


def test_full_pass_response():
    """完整健康 response → 三條 pass。"""
    resp = _make_response(lcp_ms=1800, cls_value=0.05, inp_ms=120)
    out = check_performance(resp)
    assert [c.status for c in out] == ["pass", "pass", "pass"]


def test_handles_non_dict_audits_gracefully():
    """audits 是非 dict（API 異常 shape）→ 不 raise，全 skip。"""
    resp = {"lighthouseResult": {"audits": None}}
    out = check_performance(resp)
    # 不 raise；全部 skip 因 audits 解不出 numeric
    assert all(c.status == "skip" for c in out)
