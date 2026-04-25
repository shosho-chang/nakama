"""End-to-end tests for the `seo-keyword-enrich` pipeline (ADR-009 Slice B).

All tests mock the GSCClient — none hit the real API. `live_gsc` marker is
intentionally not used; the real E2E benchmark (T1) is a修修-driven manual
smoke test after merge.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from shared.schemas.publishing import SEOContextV1

# ---------------------------------------------------------------------------
# Dynamic import — the skill lives at `.claude/skills/seo-keyword-enrich/scripts/enrich.py`.
# The directory contains a hyphen so it is NOT a regular Python package;
# importlib is the clean way to load it without adding sys.path shims.
# ---------------------------------------------------------------------------


def _load_enrich_module():
    repo_root = Path(__file__).resolve().parents[3]
    enrich_path = repo_root / ".claude" / "skills" / "seo-keyword-enrich" / "scripts" / "enrich.py"
    spec = importlib.util.spec_from_file_location(
        "seo_keyword_enrich_enrich_under_test", enrich_path
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


enrich_mod = _load_enrich_module()


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _kw_research_md(
    *,
    topic: str = "morning coffee sleep",
    topic_en: str = "morning coffee sleep",
    target_site: str | None = "wp_shosho",
    core_keywords: list[dict[str, Any]] | None = None,
    include_type: bool = True,
) -> str:
    """Build a minimal keyword-research frontmatter markdown."""
    if core_keywords is None:
        core_keywords = [
            {"keyword": "晨間咖啡 睡眠", "opportunity": "high"},
            {"keyword": "咖啡因 代謝", "opportunity": "medium"},
        ]
    fm: dict[str, Any] = {
        "topic": topic,
        "topic_en": topic_en,
        "content_type": "blog",
        "core_keywords": core_keywords,
    }
    if include_type:
        fm = {"type": "keyword-research", **fm}
    if target_site is not None:
        fm["target_site"] = target_site
    import yaml as _yaml

    body = "# Example keyword research\n\nbody text\n"
    return "---\n" + _yaml.safe_dump(fm, sort_keys=False, allow_unicode=True) + "---\n\n" + body


def _gsc_row(
    keyword: str,
    url: str,
    *,
    clicks: int = 0,
    impressions: int = 0,
    ctr: float | None = None,
    position: float = 15.0,
) -> dict[str, Any]:
    ctr_val = ctr if ctr is not None else (clicks / impressions if impressions else 0.0)
    return {
        "keys": [keyword, url],
        "clicks": clicks,
        "impressions": impressions,
        "ctr": ctr_val,
        "position": position,
    }


@pytest.fixture
def input_md(tmp_path: Path) -> Path:
    p = tmp_path / "morning-coffee-sleep.md"
    p.write_text(_kw_research_md(), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# parse_keyword_research_input
# ---------------------------------------------------------------------------


def test_parse_input_happy(input_md: Path) -> None:
    parsed = enrich_mod.parse_keyword_research_input(input_md)
    assert parsed.topic == "morning coffee sleep"
    assert parsed.primary_keyword == "晨間咖啡 睡眠"
    assert parsed.core_keywords == ("晨間咖啡 睡眠", "咖啡因 代謝")
    assert parsed.target_site_hint == "wp_shosho"
    assert parsed.source_path == input_md


def test_parse_input_missing_frontmatter(tmp_path: Path) -> None:
    p = tmp_path / "no-fm.md"
    p.write_text("# just a body, no frontmatter\n", encoding="utf-8")
    with pytest.raises(enrich_mod.EnrichInputError, match="frontmatter"):
        enrich_mod.parse_keyword_research_input(p)


def test_parse_input_wrong_type(tmp_path: Path) -> None:
    p = tmp_path / "wrong-type.md"
    p.write_text(_kw_research_md(include_type=False), encoding="utf-8")
    with pytest.raises(enrich_mod.EnrichInputError, match="type='keyword-research'"):
        enrich_mod.parse_keyword_research_input(p)


def test_parse_input_missing_core_keywords(tmp_path: Path) -> None:
    p = tmp_path / "no-core.md"
    p.write_text(_kw_research_md(core_keywords=[]), encoding="utf-8")
    with pytest.raises(enrich_mod.EnrichInputError, match="core_keywords"):
        enrich_mod.parse_keyword_research_input(p)


def test_parse_input_empty_keyword_entries(tmp_path: Path) -> None:
    p = tmp_path / "empty-kws.md"
    p.write_text(
        _kw_research_md(core_keywords=[{"keyword": "   "}, {"keyword": ""}]),
        encoding="utf-8",
    )
    with pytest.raises(enrich_mod.EnrichInputError, match="non-empty"):
        enrich_mod.parse_keyword_research_input(p)


def test_parse_input_invalid_target_site(tmp_path: Path) -> None:
    p = tmp_path / "bad-target.md"
    p.write_text(_kw_research_md(target_site="wp_bogus"), encoding="utf-8")
    with pytest.raises(enrich_mod.EnrichInputError, match="target_site"):
        enrich_mod.parse_keyword_research_input(p)


def test_parse_input_missing_file(tmp_path: Path) -> None:
    with pytest.raises(enrich_mod.EnrichInputError, match="does not exist"):
        enrich_mod.parse_keyword_research_input(tmp_path / "nope.md")


# ---------------------------------------------------------------------------
# resolve_target_site
# ---------------------------------------------------------------------------


def test_resolve_target_site_from_frontmatter(tmp_path: Path) -> None:
    p = tmp_path / "fm-fleet.md"
    p.write_text(_kw_research_md(target_site="wp_fleet"), encoding="utf-8")
    parsed = enrich_mod.parse_keyword_research_input(p)
    assert enrich_mod.resolve_target_site(parsed) == "wp_fleet"


def test_resolve_target_site_default_when_absent(tmp_path: Path) -> None:
    p = tmp_path / "fm-notarget.md"
    p.write_text(_kw_research_md(target_site=None), encoding="utf-8")
    parsed = enrich_mod.parse_keyword_research_input(p)
    assert enrich_mod.resolve_target_site(parsed) == "wp_shosho"


def test_resolve_target_site_override_wins(tmp_path: Path) -> None:
    p = tmp_path / "fm-override.md"
    p.write_text(_kw_research_md(target_site="wp_shosho"), encoding="utf-8")
    parsed = enrich_mod.parse_keyword_research_input(p)
    assert enrich_mod.resolve_target_site(parsed, override="wp_fleet") == "wp_fleet"


def test_resolve_target_site_bad_override(tmp_path: Path) -> None:
    p = tmp_path / "fm-badovr.md"
    p.write_text(_kw_research_md(), encoding="utf-8")
    parsed = enrich_mod.parse_keyword_research_input(p)
    with pytest.raises(enrich_mod.TargetSiteResolutionError):
        enrich_mod.resolve_target_site(parsed, override="wp_nowhere")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# build_gsc_query_payload
# ---------------------------------------------------------------------------


def test_build_gsc_query_payload_shosho() -> None:
    env = {"GSC_PROPERTY_SHOSHO": "sc-domain:shosho.tw"}
    prop, start, end = enrich_mod.build_gsc_query_payload(
        "wp_shosho", end_date=enrich_mod.date(2026, 4, 20), env=env
    )
    assert prop == "sc-domain:shosho.tw"
    assert end == "2026-04-20"
    # 28-day window (inclusive) → start = end - 27
    assert start == "2026-03-24"


def test_build_gsc_query_payload_fleet() -> None:
    env = {"GSC_PROPERTY_FLEET": "sc-domain:fleet.shosho.tw"}
    prop, start, end = enrich_mod.build_gsc_query_payload(
        "wp_fleet", end_date=enrich_mod.date(2026, 4, 20), env=env
    )
    assert prop == "sc-domain:fleet.shosho.tw"
    assert end == "2026-04-20"
    assert start == "2026-03-24"


def test_build_gsc_query_payload_missing_env() -> None:
    with pytest.raises(enrich_mod.GSCPropertyNotConfiguredError, match="GSC_PROPERTY_SHOSHO"):
        enrich_mod.build_gsc_query_payload(
            "wp_shosho", end_date=enrich_mod.date(2026, 4, 20), env={}
        )


def test_build_gsc_query_payload_blank_env() -> None:
    with pytest.raises(enrich_mod.GSCPropertyNotConfiguredError, match="empty"):
        enrich_mod.build_gsc_query_payload(
            "wp_shosho",
            end_date=enrich_mod.date(2026, 4, 20),
            env={"GSC_PROPERTY_SHOSHO": "   "},
        )


# ---------------------------------------------------------------------------
# build_seo_context
# ---------------------------------------------------------------------------


def _fixed_now() -> datetime:
    return datetime(2026, 4, 26, 3, 0, 0, tzinfo=timezone.utc)


def test_build_seo_context_happy(tmp_path: Path) -> None:
    rows = [
        # Primary keyword multi-URL → aggregated
        _gsc_row(
            "晨間咖啡 睡眠",
            "https://shosho.tw/morning-coffee",
            clicks=5,
            impressions=400,
            position=8.0,
        ),
        _gsc_row(
            "晨間咖啡 睡眠",
            "https://shosho.tw/sleep-basics",
            clicks=2,
            impressions=200,
            position=12.0,
        ),
        # Related (impressions decides ranking)
        _gsc_row(
            "咖啡因 代謝",
            "https://shosho.tw/caffeine-metabolism",
            clicks=1,
            impressions=300,
            position=9.0,
        ),
        _gsc_row(
            "睡眠 效率",
            "https://shosho.tw/sleep-efficiency",
            clicks=0,
            impressions=150,
            position=25.0,
        ),
        # Striking distance (single URL in [10,21])
        _gsc_row(
            "褪黑激素 劑量",
            "https://shosho.tw/melatonin-dose",
            clicks=0,
            impressions=80,
            position=14.0,
        ),
        # Cannibalization candidate (2 URLs, both >10 impressions, balanced share)
        _gsc_row("午睡 長度", "https://shosho.tw/nap-1", clicks=0, impressions=250, position=18.0),
        _gsc_row("午睡 長度", "https://shosho.tw/nap-2", clicks=0, impressions=200, position=19.0),
    ]
    src = tmp_path / "kw.md"
    src.write_text(_kw_research_md(), encoding="utf-8")

    ctx = enrich_mod.build_seo_context(
        rows=rows,
        target_site="wp_shosho",
        primary_keyword="晨間咖啡 睡眠",
        source_path=src,
        now_fn=_fixed_now,
    )

    assert isinstance(ctx, SEOContextV1)
    assert ctx.target_site == "wp_shosho"
    assert ctx.primary_keyword is not None
    assert ctx.primary_keyword.keyword == "晨間咖啡 睡眠"
    assert ctx.primary_keyword.clicks == 7  # 5 + 2
    assert ctx.primary_keyword.impressions == 600  # 400 + 200
    # Related: excludes primary, ranked by impressions
    related_keywords = [r.keyword for r in ctx.related_keywords]
    assert "晨間咖啡 睡眠" not in related_keywords
    assert related_keywords[0] == "咖啡因 代謝"  # highest impressions among non-primary
    # Striking distance: rows with position in [10, 21]
    assert any(sd.keyword == "褪黑激素 劑量" for sd in ctx.striking_distance)
    # Cannibalization: 午睡 長度 has 2 URLs both over threshold
    assert any(w.keyword == "午睡 長度" for w in ctx.cannibalization_warnings)
    # Slice B stubs
    assert ctx.competitor_serp_summary is None
    # generated_at forced UTC-aware
    assert ctx.generated_at == _fixed_now()
    assert str(src) == ctx.source_keyword_research_path


def test_build_seo_context_empty_rows(tmp_path: Path) -> None:
    src = tmp_path / "kw.md"
    src.write_text(_kw_research_md(), encoding="utf-8")
    ctx = enrich_mod.build_seo_context(
        rows=[],
        target_site="wp_shosho",
        primary_keyword="晨間咖啡 睡眠",
        source_path=src,
        now_fn=_fixed_now,
    )
    assert ctx.primary_keyword is None
    assert ctx.related_keywords == []
    assert ctx.striking_distance == []
    assert ctx.cannibalization_warnings == []


def test_build_seo_context_related_cap(tmp_path: Path) -> None:
    """Related keywords capped at `_MAX_RELATED_KEYWORDS` (20)."""
    rows: list[dict[str, Any]] = []
    for i in range(30):
        rows.append(
            _gsc_row(f"關鍵字{i:02d}", f"https://shosho.tw/p{i}", impressions=100 - i, position=5.0)
        )
    src = tmp_path / "kw.md"
    src.write_text(_kw_research_md(), encoding="utf-8")
    ctx = enrich_mod.build_seo_context(
        rows=rows,
        target_site="wp_shosho",
        primary_keyword="unrelated",
        source_path=src,
        now_fn=_fixed_now,
    )
    assert len(ctx.related_keywords) == enrich_mod._MAX_RELATED_KEYWORDS


def test_build_seo_context_clamps_low_position(tmp_path: Path) -> None:
    """GSC can return avg_position < 1.0; must clamp into schema range."""
    rows = [_gsc_row("tiny", "https://shosho.tw/p", clicks=0, impressions=1, position=0.5)]
    src = tmp_path / "kw.md"
    src.write_text(_kw_research_md(), encoding="utf-8")
    ctx = enrich_mod.build_seo_context(
        rows=rows,
        target_site="wp_shosho",
        primary_keyword="tiny",
        source_path=src,
        now_fn=_fixed_now,
    )
    assert ctx.primary_keyword is not None
    assert ctx.primary_keyword.avg_position == 1.0  # clamped


# ---------------------------------------------------------------------------
# render_output_markdown
# ---------------------------------------------------------------------------


def _build_sample_ctx(tmp_path: Path) -> SEOContextV1:
    src = tmp_path / "kw.md"
    src.write_text(_kw_research_md(), encoding="utf-8")
    rows = [
        _gsc_row("晨間咖啡 睡眠", "https://shosho.tw/m1", clicks=5, impressions=400, position=8.5),
        _gsc_row("褪黑激素 劑量", "https://shosho.tw/mel", clicks=0, impressions=80, position=14.0),
    ]
    return enrich_mod.build_seo_context(
        rows=rows,
        target_site="wp_shosho",
        primary_keyword="晨間咖啡 睡眠",
        source_path=src,
        now_fn=_fixed_now,
    )


def _extract_json_block(md: str) -> str:
    _, _, after_open = md.partition("```json\n")
    json_text, _, _ = after_open.partition("\n```")
    return json_text


def test_render_output_frontmatter(tmp_path: Path) -> None:
    ctx = _build_sample_ctx(tmp_path)
    md = enrich_mod.render_output_markdown(ctx)
    assert md.startswith("---\n")
    assert "type: seo-context" in md
    assert "schema_version: 1" in md
    assert "target_site: wp_shosho" in md
    # yaml scalar form; exact quoting style is yaml-dumper's choice — just
    # verify round-trip via yaml.safe_load below.
    assert "phase:" in md and "1 (gsc-only)" in md
    assert "2026-04-26T03:00:00+00:00" in md
    assert "source_keyword_research_path:" in md
    # Round-trip via yaml.safe_load to prove values are the strings we expect.
    import yaml as _yaml

    fm_block = md.split("---\n", 2)[1]
    fm = _yaml.safe_load(fm_block)
    assert fm["type"] == "seo-context"
    assert fm["schema_version"] == 1
    assert fm["target_site"] == "wp_shosho"
    assert fm["phase"] == "1 (gsc-only)"


def test_render_output_json_roundtrip(tmp_path: Path) -> None:
    ctx = _build_sample_ctx(tmp_path)
    md = enrich_mod.render_output_markdown(ctx)
    json_text = _extract_json_block(md)
    # Must be valid JSON + round-trip through SEOContextV1
    parsed = json.loads(json_text)
    assert parsed["target_site"] == "wp_shosho"
    ctx2 = SEOContextV1.model_validate_json(json_text)
    assert ctx2 == ctx


def test_render_output_human_summary(tmp_path: Path) -> None:
    ctx = _build_sample_ctx(tmp_path)
    md = enrich_mod.render_output_markdown(ctx)
    assert "人類可讀摘要" in md
    assert "Primary keyword" in md
    assert "Striking distance" in md
    assert "Cannibalization" in md


def test_render_output_handles_empty_context(tmp_path: Path) -> None:
    src = tmp_path / "kw.md"
    src.write_text(_kw_research_md(), encoding="utf-8")
    ctx = enrich_mod.build_seo_context(
        rows=[],
        target_site="wp_fleet",
        primary_keyword="nope",
        source_path=src,
        now_fn=_fixed_now,
    )
    md = enrich_mod.render_output_markdown(ctx)
    ctx2 = SEOContextV1.model_validate_json(_extract_json_block(md))
    assert ctx2.target_site == "wp_fleet"
    assert ctx2.primary_keyword is None
    assert "GSC 28 天窗內無資料" in md


# ---------------------------------------------------------------------------
# End-to-end enrich()
# ---------------------------------------------------------------------------


def _fake_client(rows: list[dict[str, Any]]) -> MagicMock:
    client = MagicMock()
    client.query.return_value = rows
    return client


def test_enrich_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GSC_PROPERTY_SHOSHO", "sc-domain:shosho.tw")
    input_path = tmp_path / "morning-coffee-sleep.md"
    input_path.write_text(_kw_research_md(), encoding="utf-8")
    output_dir = tmp_path / "out"

    rows = [
        _gsc_row("晨間咖啡 睡眠", "https://shosho.tw/m1", clicks=5, impressions=400, position=8.5),
        _gsc_row("咖啡因 代謝", "https://shosho.tw/caff", clicks=1, impressions=200, position=9.0),
        _gsc_row("褪黑激素 劑量", "https://shosho.tw/mel", clicks=0, impressions=80, position=14.0),
    ]
    client = _fake_client(rows)

    def _now_taipei() -> datetime:
        from zoneinfo import ZoneInfo

        return datetime(2026, 4, 26, 3, 0, 0, tzinfo=ZoneInfo("Asia/Taipei"))

    out_path = enrich_mod.enrich(
        input_path=input_path,
        output_dir=output_dir,
        client=client,
        now_fn=_now_taipei,
    )

    # Client called with expected shape
    client.query.assert_called_once()
    kwargs = client.query.call_args.kwargs
    assert kwargs["site"] == "sc-domain:shosho.tw"
    assert kwargs["dimensions"] == ["query", "page"]
    assert kwargs["row_limit"] == 1000

    # File exists, round-trips
    assert out_path.exists()
    md = out_path.read_text(encoding="utf-8")
    ctx = SEOContextV1.model_validate_json(_extract_json_block(md))
    assert ctx.target_site == "wp_shosho"
    assert ctx.primary_keyword is not None
    assert ctx.primary_keyword.keyword == "晨間咖啡 睡眠"


def test_enrich_filename_uses_taipei_tz(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """2026-04-25 23:30 UTC → 2026-04-26 07:30 Taipei → filename YYYYMMDD must be Taipei."""
    monkeypatch.setenv("GSC_PROPERTY_SHOSHO", "sc-domain:shosho.tw")
    input_path = tmp_path / "kw.md"
    input_path.write_text(_kw_research_md(), encoding="utf-8")
    output_dir = tmp_path / "out"

    def _now_late_utc() -> datetime:
        return datetime(2026, 4, 25, 23, 30, 0, tzinfo=timezone.utc)

    out_path = enrich_mod.enrich(
        input_path=input_path,
        output_dir=output_dir,
        client=_fake_client([]),
        now_fn=_now_late_utc,
    )
    # Filename date part must be 20260426 (Taipei rollover), not 20260425 (UTC).
    assert out_path.name == "enriched-kw-20260426.md"


def test_enrich_no_client_falls_back_to_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`client=None` triggers `GSCClient.from_env()` — patched at the module level
    (not at shared.gsc_client) since enrich.py imports the class by name."""
    monkeypatch.setenv("GSC_PROPERTY_SHOSHO", "sc-domain:shosho.tw")
    input_path = tmp_path / "kw.md"
    input_path.write_text(_kw_research_md(), encoding="utf-8")
    output_dir = tmp_path / "out"

    fake = _fake_client([])
    monkeypatch.setattr(enrich_mod.GSCClient, "from_env", classmethod(lambda cls: fake))

    out_path = enrich_mod.enrich(
        input_path=input_path,
        output_dir=output_dir,
        client=None,
    )
    fake.query.assert_called_once()
    assert out_path.exists()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_dry_run_skips_gsc(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GSC_PROPERTY_SHOSHO", "sc-domain:shosho.tw")
    input_path = tmp_path / "kw.md"
    input_path.write_text(_kw_research_md(), encoding="utf-8")
    output_dir = tmp_path / "out"

    # If GSC were actually called, this sentinel would raise.
    def _no_call(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError("GSC must not be called in --dry-run")

    monkeypatch.setattr(enrich_mod.GSCClient, "from_env", classmethod(lambda cls: _no_call))

    rc = enrich_mod.main(
        [
            "--input",
            str(input_path),
            "--output-dir",
            str(output_dir),
            "--dry-run",
        ]
    )
    assert rc == 0

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["target_site"] == "wp_shosho"
    assert payload["gsc_property"] == "sc-domain:shosho.tw"
    assert payload["primary_keyword"] == "晨間咖啡 睡眠"
    # Output dir should NOT have been created in dry-run
    assert not output_dir.exists()


def test_cli_full_run_writes_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GSC_PROPERTY_SHOSHO", "sc-domain:shosho.tw")
    input_path = tmp_path / "kw.md"
    input_path.write_text(_kw_research_md(), encoding="utf-8")
    output_dir = tmp_path / "out"

    fake = _fake_client(
        [
            _gsc_row(
                "晨間咖啡 睡眠",
                "https://shosho.tw/m1",
                clicks=1,
                impressions=50,
                position=7.0,
            ),
        ]
    )
    monkeypatch.setattr(enrich_mod.GSCClient, "from_env", classmethod(lambda cls: fake))

    rc = enrich_mod.main(
        [
            "--input",
            str(input_path),
            "--output-dir",
            str(output_dir),
        ]
    )
    assert rc == 0
    assert output_dir.exists()
    written = list(output_dir.glob("enriched-kw-*.md"))
    assert len(written) == 1
