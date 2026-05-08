"""Tests for scripts/bench_kb_search.py — ADR-021 §3 mini-bench harness (#457).

Mocks the kb_search entrypoint via the `search_fn` injection seam so the
suite doesn't need a populated `data/kb_index.db` or FlagEmbedding.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.bench_kb_search import (  # noqa: E402
    Topic,
    compute_recall_precision,
    load_topics,
    main,
    render_report,
    render_topic_table,
    run_bench,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_topics() -> list[Topic]:
    return [
        Topic(
            id="creatine_cognitive",
            query="肌酸對認知功能的影響",
            description="bilingual",
            ground_truth=["creatine", "cognitive-function"],
        ),
        Topic(
            id="uncurated_topic",
            query="something",
            description="no truth",
            ground_truth=[],
        ),
    ]


def _fake_search_factory(return_map):
    """Build a fake `search_kb` that returns hits keyed by (query, engine, k)."""

    def fake(query, vault_path, top_k=8, *, engine="hybrid", purpose="general"):
        return return_map.get((query, engine, top_k), [])

    return fake


# ---------------------------------------------------------------------------
# Recall / precision
# ---------------------------------------------------------------------------


def test_recall_precision_perfect():
    hits = [
        {"path": "KB/Wiki/Concepts/creatine.md", "title": "Creatine"},
        {"path": "KB/Wiki/Concepts/cognitive-function.md", "title": "Cognitive function"},
    ]
    truth = ["creatine", "cognitive-function"]
    recall, precision, matched = compute_recall_precision(hits, truth)
    assert recall == 1.0
    assert precision == 1.0
    assert matched == ["cognitive-function", "creatine"]


def test_recall_precision_partial():
    hits = [
        {"path": "KB/Wiki/Concepts/creatine.md", "title": "Creatine"},
        {"path": "KB/Wiki/Concepts/unrelated.md", "title": "Unrelated"},
    ]
    truth = ["creatine", "cognitive-function"]
    recall, precision, _ = compute_recall_precision(hits, truth)
    assert recall == 0.5  # 1/2 truth slugs matched
    assert precision == 0.5  # 1/2 hits matched


def test_recall_precision_empty_truth_returns_none():
    hits = [{"path": "x", "title": "y"}]
    recall, precision, matched = compute_recall_precision(hits, [])
    assert recall is None
    assert precision is None
    assert matched == []


def test_recall_precision_empty_hits_zero():
    recall, precision, _ = compute_recall_precision([], ["creatine"])
    assert recall == 0.0
    assert precision == 0.0


# ---------------------------------------------------------------------------
# run_bench cross-product
# ---------------------------------------------------------------------------


def test_run_bench_dispatches_full_cross_product(sample_topics):
    fake = _fake_search_factory(
        {
            ("肌酸對認知功能的影響", "hybrid", 8): [
                {"path": "KB/Wiki/Concepts/creatine.md", "title": "Creatine"},
            ],
        }
    )
    results = run_bench(
        topics=sample_topics,
        ks=(8, 15),
        engines=("hybrid", "haiku"),
        vault_path=Path("/tmp/vault"),
        search_fn=fake,
    )
    # 2 topics × 2 engines × 2 Ks = 8
    assert len(results) == 8
    # The (creatine, hybrid, 8) cell got our single hit; others get []
    creatine_hybrid_8 = next(
        r
        for r in results
        if r.topic_id == "creatine_cognitive" and r.engine == "hybrid" and r.k == 8
    )
    assert len(creatine_hybrid_8.hits) == 1
    assert creatine_hybrid_8.ok


def test_run_bench_records_errors_per_cell(sample_topics):
    def boom(*args, **kwargs):
        raise RuntimeError("FlagEmbedding not installed")

    results = run_bench(
        topics=sample_topics[:1],
        ks=(8,),
        engines=("hybrid",),
        vault_path=Path("/tmp/vault"),
        search_fn=boom,
    )
    assert len(results) == 1
    assert results[0].ok is False
    assert "FlagEmbedding" in (results[0].error or "")


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def test_render_topic_table_shape(sample_topics):
    fake = _fake_search_factory(
        {
            ("肌酸對認知功能的影響", "hybrid", 8): [
                {
                    "path": "KB/Wiki/Concepts/creatine.md",
                    "title": "Creatine",
                    "heading": "Cognitive effects",
                    "rrf_score": 0.123,
                },
            ],
        }
    )
    results = run_bench(
        topics=sample_topics[:1],
        ks=(8,),
        engines=("hybrid",),
        vault_path=Path("/tmp/vault"),
        search_fn=fake,
    )
    table = render_topic_table(sample_topics[0], results)
    assert "### Topic: creatine_cognitive" in table
    assert "| Engine | K | Returned" in table
    assert "| hybrid | 8 |" in table
    assert "creatine.md" in table
    # Recall = 1/2 (only creatine slug matched), precision = 1/1
    assert "0.50" in table
    assert "1.00" in table


def test_render_topic_table_uncurated_shows_dashes(sample_topics):
    fake = _fake_search_factory({})
    results = run_bench(
        topics=[sample_topics[1]],  # uncurated
        ks=(8,),
        engines=("hybrid",),
        vault_path=Path("/tmp/vault"),
        search_fn=fake,
    )
    table = render_topic_table(sample_topics[1], results)
    assert "(uncurated)" in table
    # No metric → em dash
    assert "| — | — |" in table


def test_render_report_aggregate_includes_means(sample_topics):
    fake = _fake_search_factory(
        {
            ("肌酸對認知功能的影響", "hybrid", 8): [
                {"path": "KB/Wiki/Concepts/creatine.md", "title": "Creatine"},
                {"path": "KB/Wiki/Concepts/cognitive-function.md", "title": "Cognitive"},
            ],
        }
    )
    results = run_bench(
        topics=sample_topics,
        ks=(8,),
        engines=("hybrid",),
        vault_path=Path("/tmp/vault"),
        search_fn=fake,
    )
    report = render_report(
        sample_topics,
        results,
        date="2026-05-07",
        corpus_note="BGE-M3 test",
    )
    assert "# Brook synthesize mini-bench — 2026-05-07" in report
    assert "BGE-M3 test" in report
    assert "## Aggregate" in report
    assert "## Per-topic results" in report
    # The uncurated topic should be skipped from aggregate but listed per-topic
    assert "(uncurated)" in report


def test_render_report_includes_error_summary(sample_topics):
    report = render_report(
        sample_topics,
        [],
        date="2026-05-07",
        corpus_note="x",
        error_summary="bench did not run in CI",
    )
    assert "bench did not run in CI" in report


# ---------------------------------------------------------------------------
# load_topics / CLI
# ---------------------------------------------------------------------------


def test_load_topics_real_fixture():
    topics = load_topics(_REPO_ROOT / "tests" / "fixtures" / "brook_bench_topics.yaml")
    assert len(topics) == 5
    ids = {t.id for t in topics}
    assert {
        "exercise_cardiovascular",
        "sleep_performance",
        "microbiome_inflammation",
        "pediatric_exercise",
        "longevity_social",
    } == ids
    # Each topic must have a non-empty query
    assert all(t.query.strip() for t in topics)


def test_main_writes_report_with_mocked_search(tmp_path, monkeypatch):
    # Wire main() to a fake search via monkeypatching the lazy import.
    import agents.robin.kb_search as kb_search_mod

    def fake_search(query, vault_path, top_k=8, *, engine="hybrid", purpose="general"):
        return [
            {
                "path": f"KB/Wiki/Concepts/{engine}-{top_k}.md",
                "title": f"{engine}-{top_k}",
                "heading": "x",
                "rrf_score": 0.5,
            }
        ]

    monkeypatch.setattr(kb_search_mod, "search_kb", fake_search)

    out = tmp_path / "bench.md"
    rc = main(
        [
            "--topic",
            "exercise_cardiovascular",
            "--k",
            "8",
            "--engine",
            "hybrid",
            "--output",
            str(out),
        ]
    )
    assert rc == 0
    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "exercise_cardiovascular" in content
    assert "hybrid-8" in content
