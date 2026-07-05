"""storyboard_validator + validate-storyboard CLI tests（ADR-051 panel v2 §11）.

Covers:
- 乾淨 storyboard → 0 violations（明確驗證「已檢查，無問題」路徑）
- hard limits：cutaway 密度、連續同 component、連續 none、KOL 單源總長
- 詞彙 allow lists：layout / render_target / component / asset kind
- 出處護欄：stock 缺 source_url、kol 缺三欄
- schema 違規（asset ⟺ render_target）進 violations 而非 raise
- guardrails.yaml 數字對齊迴歸（2.5/min、kol 20s）
- CLI：errors → exit 1；乾淨 → exit 0 + provenance stage 蓋章
"""

from __future__ import annotations

from pathlib import Path

import yaml

from agents.brook.script_video import pipeline
from agents.brook.script_video.storyboard_validator import (
    Violation,
    format_report,
    load_guardrails,
    validate_storyboard,
)

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _beat(
    beat_id: int,
    *,
    decision: str = "cutaway",
    layout: str = "full_broll",
    target: str = "hyperframes",
    component: str = "bigstat",
    asset: dict | None = None,
    start: float = 0.0,
    duration: float = 4.0,
) -> dict:
    b: dict = {
        "beat_id": beat_id,
        "start_quote": f"開頭{beat_id}",
        "end_quote": f"結尾{beat_id}",
        "timing": {"start": start, "duration": duration},
        "broll_decision": decision,
        "layout": layout if decision == "cutaway" else "full_aroll",
        "broll": None,
        "status": {},
        "user_notes": [],
    }
    if decision == "cutaway":
        b["broll"] = {
            "render_target": target,
            "component": component,
            "params": {},
            "transitions": {},
        }
        if asset is not None:
            b["broll"]["asset"] = asset
    return b


def _kol_asset(source: str = "https://youtube.com/watch?v=x", **overrides) -> dict:
    spec = {
        "kind": "kol",
        "source_url": source,
        "source_span": "00:01:00-00:01:10",
        "attribution": "Andrew Huberman — Huberman Lab",
    }
    spec.update(overrides)
    return spec


_GUARDRAILS = load_guardrails()


# ---------------------------------------------------------------------------
# guardrails.yaml 數字對齊迴歸（panel v2 §8）
# ---------------------------------------------------------------------------


def test_guardrails_numbers_aligned_with_prompt_budget() -> None:
    """4/min 與 planner prompt「~15-25/10min」互斥 — 以 prompt 預算上限 2.5 為準."""
    limits = _GUARDRAILS["hard_limits"]
    assert limits["max_cutaways_per_minute"] == 2.5
    assert limits["kol_max_total_sec_per_source"] == 20


# ---------------------------------------------------------------------------
# validate_storyboard — 乾淨路徑
# ---------------------------------------------------------------------------


def test_clean_storyboard_no_violations() -> None:
    beats = [
        _beat(1, component="bigstat", start=0, duration=4),
        _beat(2, decision="none", start=30, duration=4),
        _beat(3, component="quote_card", start=60, duration=4),
    ]
    violations = validate_storyboard(beats, _GUARDRAILS, duration_sec=600.0)
    assert violations == []
    assert "0 errors, 0 warnings" in format_report(violations)


# ---------------------------------------------------------------------------
# hard limits
# ---------------------------------------------------------------------------


def test_cutaway_rate_over_budget_is_error() -> None:
    # 6 cutaways / 60s = 6/min > 2.5/min（連續 component 交錯避免混入其他違規）
    comps = ["bigstat", "quote_card"] * 3
    beats = [_beat(i, component=comps[i - 1], start=i * 8.0, duration=4) for i in range(1, 7)]
    violations = validate_storyboard(beats, _GUARDRAILS, duration_sec=60.0)
    rules = [v.rule for v in violations if v.severity == "error"]
    assert rules == ["cutaway_rate"]


def test_cutaway_rate_uses_beat_timing_fallback(caplog) -> None:
    """duration_sec=None 時退回 timing 最大 end（120s、2 cutaways → 1/min OK）."""
    beats = [
        _beat(1, component="bigstat", start=0, duration=4),
        _beat(2, component="quote_card", start=116, duration=4),
    ]
    violations = validate_storyboard(beats, _GUARDRAILS, duration_sec=None)
    assert violations == []


def test_cutaway_rate_unknown_duration_warns_not_silent() -> None:
    beats = [_beat(1)]
    beats[0]["timing"] = None
    violations = validate_storyboard(beats, _GUARDRAILS, duration_sec=None)
    assert [v for v in violations if v.rule == "cutaway_rate" and v.severity == "warning"]


def test_consecutive_same_component_is_error() -> None:
    beats = [
        _beat(1, component="bigstat", start=0),
        _beat(2, component="bigstat", start=30),
    ]
    violations = validate_storyboard(beats, _GUARDRAILS, duration_sec=600.0)
    assert [v for v in violations if v.rule == "consecutive_component" and v.beat_id == 2]


def test_consecutive_component_separated_by_none_is_ok() -> None:
    beats = [
        _beat(1, component="bigstat", start=0),
        _beat(2, decision="none", start=30),
        _beat(3, component="bigstat", start=60),
    ]
    violations = validate_storyboard(beats, _GUARDRAILS, duration_sec=600.0)
    assert [v for v in violations if v.rule == "consecutive_component"] == []


def test_consecutive_same_asset_kind_is_error() -> None:
    """asset 類的視覺重複比對用 kind（兩個相鄰 stock = 重複）."""
    stock = {"kind": "stock", "source_url": "https://elements.envato.com/a"}
    beats = [
        _beat(1, target="asset", component="stock", asset=dict(stock), start=0),
        _beat(2, target="asset", component="stock", asset=dict(stock), start=30),
    ]
    violations = validate_storyboard(beats, _GUARDRAILS, duration_sec=600.0)
    assert [v for v in violations if v.rule == "consecutive_component"]


def test_long_none_streak_is_warning_not_error() -> None:
    beats = [_beat(i, decision="none", start=i * 10.0) for i in range(1, 11)]
    violations = validate_storyboard(beats, _GUARDRAILS, duration_sec=600.0)
    streaks = [v for v in violations if v.rule == "consecutive_none"]
    assert len(streaks) == 1
    assert streaks[0].severity == "warning"


def test_kol_source_total_over_cap_is_error() -> None:
    beats = [
        _beat(1, target="asset", component="kol", asset=_kol_asset(), start=0, duration=12),
        _beat(
            2,
            decision="none",
            start=30,
        ),
        _beat(3, target="asset", component="kol", asset=_kol_asset(), start=60, duration=12),
    ]
    violations = validate_storyboard(beats, _GUARDRAILS, duration_sec=600.0)
    caps = [v for v in violations if v.rule == "kol_source_cap" and v.severity == "error"]
    assert caps and "24.0s" in caps[0].message


def test_kol_different_sources_under_cap_each_is_ok() -> None:
    beats = [
        _beat(
            1,
            target="asset",
            component="kol",
            asset=_kol_asset("https://youtube.com/watch?v=a"),
            start=0,
            duration=15,
        ),
        _beat(2, decision="none", start=30),
        _beat(
            3,
            target="asset",
            component="kol",
            asset=_kol_asset("https://youtube.com/watch?v=b"),
            start=60,
            duration=15,
        ),
    ]
    violations = validate_storyboard(beats, _GUARDRAILS, duration_sec=600.0)
    assert [v for v in violations if v.rule == "kol_source_cap"] == []


# ---------------------------------------------------------------------------
# 詞彙 allow lists
# ---------------------------------------------------------------------------


def test_unknown_layout_component_render_target_are_errors() -> None:
    beats = [
        _beat(1, layout="side_overlay_left", start=0),  # Phase 1.5 詞彙，未開放
        _beat(2, component="data-chart", start=30),  # Phase 2 component
    ]
    beats[1]["broll"]["render_target"] = "hyperframes"
    violations = validate_storyboard(beats, _GUARDRAILS, duration_sec=600.0)
    rules = {v.rule for v in violations if v.severity == "error"}
    assert {"layout", "component"} <= rules


# ---------------------------------------------------------------------------
# 出處護欄（D6）
# ---------------------------------------------------------------------------


def test_stock_without_source_url_is_error() -> None:
    beats = [_beat(1, target="asset", component="stock", asset={"kind": "stock"})]
    violations = validate_storyboard(beats, _GUARDRAILS, duration_sec=600.0)
    assert [v for v in violations if v.rule == "asset_provenance"]


def test_kol_missing_attribution_fields_is_error() -> None:
    beats = [
        _beat(
            1,
            target="asset",
            component="kol",
            asset=_kol_asset(source_span=None, attribution=None),
        )
    ]
    violations = validate_storyboard(beats, _GUARDRAILS, duration_sec=600.0)
    prov = [v for v in violations if v.rule == "asset_provenance"]
    assert prov and "source_span" in prov[0].message and "attribution" in prov[0].message


# ---------------------------------------------------------------------------
# schema 違規收進 violations（不 raise）
# ---------------------------------------------------------------------------


def test_schema_violation_reported_not_raised() -> None:
    beats = [_beat(1)]
    beats[0]["broll"]["asset"] = {"kind": "stock"}  # asset 只准 render_target='asset'
    violations = validate_storyboard(beats, _GUARDRAILS, duration_sec=600.0)
    assert [v for v in violations if v.rule == "schema" and v.beat_id == 1]


# ---------------------------------------------------------------------------
# format_report
# ---------------------------------------------------------------------------


def test_format_report_lists_every_violation_with_severity() -> None:
    violations = [
        Violation(rule="cutaway_rate", severity="error", beat_id=None, message="太密"),
        Violation(rule="consecutive_none", severity="warning", beat_id=7, message="太靜"),
    ]
    report = format_report(violations)
    assert "[ERROR" in report and "[WARNING" in report
    assert "beat 7" in report
    assert "1 errors, 1 warnings" in report


# ---------------------------------------------------------------------------
# CLI（exit code + provenance）
# ---------------------------------------------------------------------------


def _make_episode(tmp_path: Path, monkeypatch, beats: list[dict]) -> Path:
    data_root = tmp_path / "data" / "script_video"
    ep_dir = data_root / "ep-test"
    ep_dir.mkdir(parents=True)
    (ep_dir / "episode.yaml").write_text('id: ep-test\ntitle: "t"\n', encoding="utf-8")
    (ep_dir / "storyboard.yaml").write_text(
        yaml.dump(beats, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    monkeypatch.setattr(pipeline, "_DATA_ROOT", data_root)
    return ep_dir


def test_cli_clean_storyboard_exit_0_and_stamps_stage(tmp_path, monkeypatch, capsys) -> None:
    # 無 raw_recording.mp4 → 集長退回 timing 最大 end（none beat 撐出 120s 跨度）
    beats = [_beat(1, start=0, duration=4), _beat(2, decision="none", start=110, duration=10)]
    ep_dir = _make_episode(tmp_path, monkeypatch, beats)
    rc = pipeline.main(["--episode", "ep-test", "validate-storyboard"])
    assert rc == 0
    assert "0 errors" in capsys.readouterr().out
    meta = yaml.safe_load((ep_dir / "episode.yaml").read_text(encoding="utf-8"))
    assert "validate-storyboard" in meta["stages"]


def test_cli_errors_exit_1_and_no_stage_stamp(tmp_path, monkeypatch, capsys) -> None:
    beats = [
        _beat(1, component="bigstat", start=0),
        _beat(2, component="bigstat", start=10),  # 連續同 component
    ]
    ep_dir = _make_episode(tmp_path, monkeypatch, beats)
    rc = pipeline.main(["--episode", "ep-test", "validate-storyboard"])
    assert rc == 1
    assert "consecutive_component" in capsys.readouterr().out
    meta = yaml.safe_load((ep_dir / "episode.yaml").read_text(encoding="utf-8"))
    assert "validate-storyboard" not in meta.get("stages", {})


def test_cli_missing_storyboard_exit_1(tmp_path, monkeypatch) -> None:
    ep_dir = _make_episode(tmp_path, monkeypatch, [])
    (ep_dir / "storyboard.yaml").unlink()
    rc = pipeline.main(["--episode", "ep-test", "validate-storyboard"])
    assert rc == 1
