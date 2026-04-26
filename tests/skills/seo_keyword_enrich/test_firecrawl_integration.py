"""End-to-end test for firecrawl SERP integration in `enrich.py` (Slice F).

Covers four phase paths:
- `1.5 (gsc + firecrawl)`     — both OK
- `1.5 (gsc + serp-skipped)`  — runner returns None
- `1 (gsc-only)`              — `enable_serp=False`
- runner exception path       — falls back to skipped phase
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from shared.schemas.publishing import SEOContextV1


def _load_enrich_module():
    repo_root = Path(__file__).resolve().parents[3]
    enrich_path = repo_root / ".claude" / "skills" / "seo-keyword-enrich" / "scripts" / "enrich.py"
    spec = importlib.util.spec_from_file_location(
        "seo_keyword_enrich_enrich_under_test_f", enrich_path
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


enrich_mod = _load_enrich_module()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _kw_research_md() -> str:
    fm = (
        "type: keyword-research\n"
        "topic: morning coffee sleep\n"
        "topic_en: morning coffee sleep\n"
        "content_type: blog\n"
        "target_site: wp_shosho\n"
        "core_keywords:\n"
        "- keyword: 晨間咖啡 睡眠\n"
        "  opportunity: high\n"
    )
    return f"---\n{fm}---\n\n# body\n"


def _gsc_row(keyword: str, url: str, *, impressions: int = 100, position: float = 8.0) -> dict:
    return {
        "keys": [keyword, url],
        "clicks": 1,
        "impressions": impressions,
        "ctr": 0.01,
        "position": position,
    }


def _fake_client(rows: list[dict[str, Any]]) -> MagicMock:
    client = MagicMock()
    client.query.return_value = rows
    return client


def _fixed_now_taipei() -> datetime:
    from zoneinfo import ZoneInfo

    return datetime(2026, 4, 26, 3, 0, 0, tzinfo=ZoneInfo("Asia/Taipei"))


def _extract_phase(md: str) -> str:
    """Extract `phase:` value from frontmatter (between leading --- markers)."""
    import yaml

    parts = md.split("---", 2)
    fm = yaml.safe_load(parts[1])
    return fm["phase"]


def _extract_ctx(md: str) -> SEOContextV1:
    _, _, after_open = md.partition("```json\n")
    json_text, _, _ = after_open.partition("\n```")
    return SEOContextV1.model_validate_json(json_text)


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.setenv("GSC_PROPERTY_SHOSHO", "sc-domain:shosho.tw")
    input_path = tmp_path / "kw.md"
    input_path.write_text(_kw_research_md(), encoding="utf-8")
    output_dir = tmp_path / "out"
    rows = [_gsc_row("晨間咖啡 睡眠", "https://shosho.tw/p", impressions=400, position=8.5)]
    return {
        "input_path": input_path,
        "output_dir": output_dir,
        "client": _fake_client(rows),
    }


# ---------------------------------------------------------------------------
# Phase: gsc + firecrawl (happy path)
# ---------------------------------------------------------------------------


def test_enrich_with_serp_writes_summary_and_phase(setup: dict) -> None:
    summary = "共同框架：講原理。我方差異化：在地化案例 + 中文 RCT。"
    captured: dict = {}

    def _runner(kw: str) -> str:
        captured["kw"] = kw
        return summary

    out_path = enrich_mod.enrich(
        input_path=setup["input_path"],
        output_dir=setup["output_dir"],
        client=setup["client"],
        serp_runner=_runner,
        now_fn=_fixed_now_taipei,
    )

    md = out_path.read_text(encoding="utf-8")
    ctx = _extract_ctx(md)

    assert captured["kw"] == "晨間咖啡 睡眠"
    assert ctx.competitor_serp_summary == summary
    assert _extract_phase(md) == "1.5 (gsc + firecrawl)"
    # Human summary mentions chars + Haiku source
    assert "Competitor SERP 摘要" in md
    assert "Haiku" in md


def test_enrich_with_serp_human_summary_chars_count(setup: dict) -> None:
    summary = "差異化角度：A B C D E"
    out_path = enrich_mod.enrich(
        input_path=setup["input_path"],
        output_dir=setup["output_dir"],
        client=setup["client"],
        serp_runner=lambda _: summary,
        now_fn=_fixed_now_taipei,
    )
    md = out_path.read_text(encoding="utf-8")
    assert f"{len(summary)} 字" in md


# ---------------------------------------------------------------------------
# Phase: gsc + serp-skipped (runner returns None or raises)
# ---------------------------------------------------------------------------


def test_enrich_serp_runner_returns_none_marks_skipped(setup: dict) -> None:
    out_path = enrich_mod.enrich(
        input_path=setup["input_path"],
        output_dir=setup["output_dir"],
        client=setup["client"],
        serp_runner=lambda _: None,
        now_fn=_fixed_now_taipei,
    )
    md = out_path.read_text(encoding="utf-8")
    ctx = _extract_ctx(md)

    assert ctx.competitor_serp_summary is None
    assert _extract_phase(md) == "1.5 (gsc + serp-skipped)"
    assert "摘要失敗" in md and "已降級" in md


def test_enrich_default_serp_runner_used_when_unspecified(
    setup: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`serp_runner=None` → falls back to `_default_serp_runner` which the
    test patches at module level so we don't hit firecrawl."""
    called: dict = {}

    def _stub(kw: str) -> str | None:
        called["kw"] = kw
        return "stub summary"

    monkeypatch.setattr(enrich_mod, "_default_serp_runner", _stub)

    out_path = enrich_mod.enrich(
        input_path=setup["input_path"],
        output_dir=setup["output_dir"],
        client=setup["client"],
        now_fn=_fixed_now_taipei,
    )
    assert called["kw"] == "晨間咖啡 睡眠"
    md = out_path.read_text(encoding="utf-8")
    assert _extract_ctx(md).competitor_serp_summary == "stub summary"


# ---------------------------------------------------------------------------
# Phase: gsc-only (--no-serp / enable_serp=False)
# ---------------------------------------------------------------------------


def test_enrich_no_serp_skips_runner_completely(setup: dict) -> None:
    """`enable_serp=False` → runner not called, phase = 1 (gsc-only)."""
    runner = MagicMock()
    out_path = enrich_mod.enrich(
        input_path=setup["input_path"],
        output_dir=setup["output_dir"],
        client=setup["client"],
        enable_serp=False,
        serp_runner=runner,
        now_fn=_fixed_now_taipei,
    )
    runner.assert_not_called()
    md = out_path.read_text(encoding="utf-8")
    assert _extract_phase(md) == "1 (gsc-only)"
    assert _extract_ctx(md).competitor_serp_summary is None
    assert "以 `--no-serp` 跳過" in md


def test_cli_no_serp_flag_disables_runner(
    setup: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CLI --no-serp must propagate to enrich(enable_serp=False)."""
    runner = MagicMock(side_effect=AssertionError("must not be called"))
    monkeypatch.setattr(enrich_mod, "_default_serp_runner", runner)
    monkeypatch.setattr(enrich_mod.GSCClient, "from_env", classmethod(lambda cls: setup["client"]))

    rc = enrich_mod.main(
        [
            "--input",
            str(setup["input_path"]),
            "--output-dir",
            str(setup["output_dir"]),
            "--no-serp",
        ]
    )
    assert rc == 0
    out_files = list(setup["output_dir"].glob("enriched-*.md"))
    assert len(out_files) == 1
    md = out_files[0].read_text(encoding="utf-8")
    assert _extract_phase(md) == "1 (gsc-only)"


def test_cli_dry_run_reports_serp_status(
    setup: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """dry-run JSON includes serp_enabled flag."""
    monkeypatch.setattr(enrich_mod.GSCClient, "from_env", classmethod(lambda cls: setup["client"]))

    rc = enrich_mod.main(
        [
            "--input",
            str(setup["input_path"]),
            "--output-dir",
            str(setup["output_dir"]),
            "--dry-run",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["serp_enabled"] is True


def test_cli_dry_run_reflects_no_serp(
    setup: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(enrich_mod.GSCClient, "from_env", classmethod(lambda cls: setup["client"]))
    rc = enrich_mod.main(
        [
            "--input",
            str(setup["input_path"]),
            "--output-dir",
            str(setup["output_dir"]),
            "--dry-run",
            "--no-serp",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["serp_enabled"] is False


# ---------------------------------------------------------------------------
# Default runner — graceful fallback under upstream failures
# ---------------------------------------------------------------------------


def test_default_serp_runner_returns_none_on_firecrawl_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_default_serp_runner` 抓 firecrawl 例外 → return None（不 raise）。"""
    from shared.firecrawl_serp import FirecrawlSerpError

    def _boom(*_a, **_kw):
        raise FirecrawlSerpError("quota exceeded")

    monkeypatch.setattr("shared.firecrawl_serp.fetch_top_n_serp", _boom)
    assert enrich_mod._default_serp_runner("kw") is None


def test_default_serp_runner_returns_none_on_summarizer_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """firecrawl OK 但 summarizer raise → return None（不 raise，phase=skipped）。"""
    monkeypatch.setattr(
        "shared.firecrawl_serp.fetch_top_n_serp",
        lambda *a, **kw: [{"url": "u", "title": "t", "content_markdown": "c"}],
    )

    def _boom(*_a, **_kw):
        raise RuntimeError("anthropic down")

    monkeypatch.setattr("shared.seo_enrich.serp_summarizer.summarize_serp", _boom)
    assert enrich_mod._default_serp_runner("kw") is None


def test_default_serp_runner_returns_none_on_empty_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """firecrawl 回 0 筆（合法、不 raise）→ skip summarizer，return None。"""
    monkeypatch.setattr("shared.firecrawl_serp.fetch_top_n_serp", lambda *a, **kw: [])
    # Sentinel: ensure summarizer is NEVER called when pages are empty
    summarizer = MagicMock()
    monkeypatch.setattr("shared.seo_enrich.serp_summarizer.summarize_serp", summarizer)
    assert enrich_mod._default_serp_runner("kw") is None
    summarizer.assert_not_called()
