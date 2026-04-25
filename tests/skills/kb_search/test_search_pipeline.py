"""End-to-end tests for the `kb-search` skill.

The skill module lives at `.claude/skills/kb-search/scripts/search.py`.
The path contains a hyphen so it is not a regular Python package; we load
it via `importlib.util.spec_from_file_location` (same pattern as
`tests/skills/seo_keyword_enrich/test_enrich_pipeline.py`).

All tests inject a fake `post` callable and a frozen `now_fn` — none hit
a real `thousand_sunny` instance. The Robin server-side ranker is covered
by `tests/agents/robin/test_kb_search.py` (PR #119).
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest


def _load_search_module():
    repo_root = Path(__file__).resolve().parents[3]
    search_path = repo_root / ".claude" / "skills" / "kb-search" / "scripts" / "search.py"
    spec = importlib.util.spec_from_file_location("kb_search_search_under_test", search_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


search_mod = _load_search_module()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


FROZEN_NOW = datetime(2026, 4, 26, 3, 0, 0, tzinfo=timezone.utc)


def _frozen_now() -> datetime:
    return FROZEN_NOW


def _example_results() -> list[dict[str, Any]]:
    return [
        {
            "type": "concept",
            "title": "Zone 2 訓練協議",
            "path": "KB/Wiki/Concepts/zone-2-protocol",
            "preview": "Zone 2 是 60-70% 最大心率的有氧區間，主要訓練粒線體效率",
            "relevance_reason": "主題即 zone 2 訓練心率區間與適應機制",
        },
        {
            "type": "source",
            "title": "Peter Attia zone 2 podcast",
            "path": "KB/Wiki/Sources/peter-attia-zone2-podcast",
            "preview": "Peter Attia 與 Iñigo San Millán 對談 zone 2 的健康意義",
            "relevance_reason": "重要 source 對 zone 2 訓練量化建議",
        },
    ]


def _make_fake_post(captured: dict[str, Any], response: dict[str, Any]):
    """Build a fake post fn that records args and returns ``response``."""

    def fake(url: str, data: dict[str, str], headers: dict[str, str]) -> dict[str, Any]:
        captured["url"] = url
        captured["data"] = data
        captured["headers"] = headers
        return response

    return fake


# ---------------------------------------------------------------------------
# build_request_payload
# ---------------------------------------------------------------------------


def test_build_request_payload_form_encoded() -> None:
    payload = search_mod.build_request_payload("zone 2 訓練")
    assert payload == {"query": "zone 2 訓練"}


# ---------------------------------------------------------------------------
# parse_response
# ---------------------------------------------------------------------------


def test_parse_response_happy_path() -> None:
    hits = search_mod.parse_response({"results": _example_results()})
    assert len(hits) == 2
    assert hits[0].title == "Zone 2 訓練協議"
    assert hits[0].type == "concept"
    assert hits[0].path == "KB/Wiki/Concepts/zone-2-protocol"
    assert hits[0].relevance_reason.startswith("主題即")


def test_parse_response_missing_results_key_returns_empty() -> None:
    assert search_mod.parse_response({}) == []
    assert search_mod.parse_response({"other_key": []}) == []


def test_parse_response_results_not_list_returns_empty() -> None:
    assert search_mod.parse_response({"results": "not a list"}) == []
    assert search_mod.parse_response({"results": None}) == []


def test_parse_response_skips_non_dict_items() -> None:
    hits = search_mod.parse_response(
        {
            "results": [
                {
                    "type": "concept",
                    "title": "valid",
                    "path": "p",
                    "preview": "x",
                    "relevance_reason": "",
                },
                "not a dict",  # skipped
                42,  # skipped
                None,  # skipped
            ]
        }
    )
    assert len(hits) == 1
    assert hits[0].title == "valid"


def test_parse_response_missing_keys_default_to_empty_string() -> None:
    hits = search_mod.parse_response({"results": [{"title": "only-title"}]})
    assert len(hits) == 1
    assert hits[0].title == "only-title"
    assert hits[0].type == ""
    assert hits[0].path == ""
    assert hits[0].preview == ""
    assert hits[0].relevance_reason == ""


# ---------------------------------------------------------------------------
# render_markdown
# ---------------------------------------------------------------------------


def test_render_markdown_frontmatter_has_stable_contract() -> None:
    hits = search_mod.parse_response({"results": _example_results()})
    md = search_mod.render_markdown(
        query="zone 2 訓練",
        hits=hits,
        generated_at=FROZEN_NOW,
        api_base="http://127.0.0.1:8000",
    )
    assert md.startswith("---\n")
    assert "type: kb-search-result\n" in md
    assert "schema_version: 1\n" in md
    assert "generated_at: 2026-04-26T03:00:00+00:00\n" in md
    assert "api_base: http://127.0.0.1:8000\n" in md
    assert 'query: "zone 2 訓練"\n' in md
    assert "total_hits: 2\n" in md


def test_render_markdown_total_hits_matches_body() -> None:
    hits = search_mod.parse_response({"results": _example_results()})
    md = search_mod.render_markdown(
        query="q",
        hits=hits,
        generated_at=FROZEN_NOW,
        api_base="http://127.0.0.1:8000",
    )
    assert "total_hits: 2\n" in md
    # Each hit creates a numbered list line; counting "1. " / "2. " is enough.
    assert "1. **Zone 2 訓練協議**" in md
    assert "2. **Peter Attia zone 2 podcast**" in md


def test_render_markdown_includes_relevance_and_preview() -> None:
    hits = search_mod.parse_response({"results": _example_results()})
    md = search_mod.render_markdown(
        query="q",
        hits=hits,
        generated_at=FROZEN_NOW,
        api_base="http://127.0.0.1:8000",
    )
    assert "Relevance: 主題即 zone 2 訓練心率區間與適應機制" in md
    assert "Preview: Zone 2 是 60-70% 最大心率的有氧區間" in md


def test_render_markdown_wiki_candidates_use_wikilinks() -> None:
    hits = search_mod.parse_response({"results": _example_results()})
    md = search_mod.render_markdown(
        query="q",
        hits=hits,
        generated_at=FROZEN_NOW,
        api_base="http://127.0.0.1:8000",
    )
    assert "[[KB/Wiki/Concepts/zone-2-protocol]]" in md
    assert "[[KB/Wiki/Sources/peter-attia-zone2-podcast]]" in md


def test_render_markdown_no_hits_shows_empty_marker() -> None:
    md = search_mod.render_markdown(
        query="nonexistent",
        hits=[],
        generated_at=FROZEN_NOW,
        api_base="http://127.0.0.1:8000",
    )
    assert "total_hits: 0\n" in md
    assert "_No hits._" in md
    # No "Top hits" / "Wiki page candidates" sections when there are none.
    assert "## Top hits" not in md
    assert "## Wiki page candidates" not in md


def test_render_markdown_naive_datetime_treated_as_utc() -> None:
    naive = datetime(2026, 4, 26, 3, 0, 0)  # no tzinfo
    md = search_mod.render_markdown(
        query="q",
        hits=[],
        generated_at=naive,
        api_base="http://127.0.0.1:8000",
    )
    assert "generated_at: 2026-04-26T03:00:00+00:00\n" in md


# ---------------------------------------------------------------------------
# run_search — orchestrator
# ---------------------------------------------------------------------------


def test_run_search_happy_path_injects_post_and_now() -> None:
    captured: dict[str, Any] = {}
    fake_post = _make_fake_post(captured, {"results": _example_results()})

    hits, md = search_mod.run_search(
        query="zone 2 訓練",
        api_base="http://127.0.0.1:8000",
        api_key="secret",
        limit=8,
        post=fake_post,
        now_fn=_frozen_now,
    )

    assert captured["url"] == "http://127.0.0.1:8000/kb/research"
    assert captured["data"] == {"query": "zone 2 訓練"}
    assert captured["headers"] == {"X-Robin-Key": "secret"}
    assert len(hits) == 2
    assert "generated_at: 2026-04-26T03:00:00+00:00" in md
    assert "total_hits: 2" in md


def test_run_search_no_api_key_omits_header() -> None:
    captured: dict[str, Any] = {}
    fake_post = _make_fake_post(captured, {"results": []})

    search_mod.run_search(
        query="q",
        api_key=None,
        post=fake_post,
        now_fn=_frozen_now,
    )

    assert "X-Robin-Key" not in captured["headers"]


def test_run_search_strips_trailing_slash_from_api_base() -> None:
    captured: dict[str, Any] = {}
    fake_post = _make_fake_post(captured, {"results": []})

    search_mod.run_search(
        query="q",
        api_base="http://127.0.0.1:8000/",
        post=fake_post,
        now_fn=_frozen_now,
    )

    # No double-slash before the path.
    assert captured["url"] == "http://127.0.0.1:8000/kb/research"


def test_run_search_caps_at_limit_client_side() -> None:
    many = [
        {
            "type": "concept",
            "title": f"page-{i}",
            "path": f"KB/Wiki/Concepts/page-{i}",
            "preview": "",
            "relevance_reason": "",
        }
        for i in range(8)
    ]
    fake_post = _make_fake_post({}, {"results": many})

    hits, md = search_mod.run_search(
        query="q",
        limit=3,
        post=fake_post,
        now_fn=_frozen_now,
    )

    assert len(hits) == 3
    assert "total_hits: 3" in md
    # Only first three rendered.
    assert "page-0" in md
    assert "page-2" in md
    assert "page-3" not in md


def test_run_search_empty_query_raises() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        search_mod.run_search(query="", post=_make_fake_post({}, {}), now_fn=_frozen_now)
    with pytest.raises(ValueError, match="non-empty"):
        search_mod.run_search(query="   ", post=_make_fake_post({}, {}), now_fn=_frozen_now)


def test_run_search_invalid_limit_raises() -> None:
    with pytest.raises(ValueError, match="limit must be"):
        search_mod.run_search(query="q", limit=0, post=_make_fake_post({}, {}), now_fn=_frozen_now)
    with pytest.raises(ValueError, match="limit must be"):
        search_mod.run_search(query="q", limit=-3, post=_make_fake_post({}, {}), now_fn=_frozen_now)


def test_run_search_falls_back_to_default_now_when_omitted() -> None:
    """`now_fn=None` must forward to `datetime.now(tz=utc)`, not crash."""
    fake_post = _make_fake_post({}, {"results": []})
    # Should not raise; just verify the orchestrator runs without injection.
    hits, md = search_mod.run_search(query="q", post=fake_post)
    assert hits == []
    assert "generated_at:" in md


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------


def test_cli_writes_to_out_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}
    fake_post = _make_fake_post(captured, {"results": _example_results()})
    # Patch the default poster so `main` (which doesn't accept injection)
    # still avoids the network.
    monkeypatch.setattr(search_mod, "_default_post", fake_post)

    out_path = tmp_path / "subdir" / "result.md"
    rc = search_mod.main(
        [
            "--query",
            "zone 2 訓練",
            "--out",
            str(out_path),
            "--api-base",
            "http://127.0.0.1:8000",
            "--api-key",
            "k",
        ]
    )
    assert rc == 0
    assert out_path.exists()
    body = out_path.read_text(encoding="utf-8")
    assert "type: kb-search-result" in body
    assert "Zone 2 訓練協議" in body


def test_cli_returns_non_zero_on_invalid_query(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Even though `argparse` allows an empty string, our orchestrator rejects it.
    monkeypatch.setattr(search_mod, "_default_post", _make_fake_post({}, {"results": []}))
    rc = search_mod.main(["--query", "   ", "--out", "-"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "non-empty" in err


def test_cli_stdout_default(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_post = _make_fake_post({}, {"results": _example_results()})
    monkeypatch.setattr(search_mod, "_default_post", fake_post)

    rc = search_mod.main(["--query", "zone 2", "--out", "-", "--api-base", "http://x"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "type: kb-search-result" in out
    assert "Zone 2 訓練協議" in out


# ---------------------------------------------------------------------------
# Default poster — error wrapping
# ---------------------------------------------------------------------------


def test_default_post_wraps_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx as _httpx

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise _httpx.ConnectError("connection refused")

    monkeypatch.setattr(search_mod.httpx, "post", boom)
    with pytest.raises(search_mod.KbSearchError, match="HTTP error"):
        search_mod._default_post("http://x/kb/research", {"query": "q"}, {})


def test_default_post_wraps_non_200(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResp:
        status_code = 403
        text = "Unauthorized"

    monkeypatch.setattr(search_mod.httpx, "post", lambda *a, **k: FakeResp())
    with pytest.raises(search_mod.KbSearchError, match="403"):
        search_mod._default_post("http://x/kb/research", {"query": "q"}, {})


def test_default_post_wraps_non_json(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResp:
        status_code = 200
        text = "<html>not json</html>"

        def json(self) -> Any:
            raise ValueError("not json")

    monkeypatch.setattr(search_mod.httpx, "post", lambda *a, **k: FakeResp())
    with pytest.raises(search_mod.KbSearchError, match="non-JSON"):
        search_mod._default_post("http://x/kb/research", {"query": "q"}, {})
