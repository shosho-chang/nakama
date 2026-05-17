"""Tests for ``agents.brook.synthesize`` (issue #459, ADR-021 §3).

Strategy: mock ``shared.kb_hybrid_search.search`` and the LLM ``ask`` callable
— we're testing Brook's synthesize logic (multi-query fan-out, dedupe,
outline contract enforcement, store write), not the underlying retrieval or
LLM. Per ``feedback_test_api_isolation.md``, no test hits a real API.

Coverage:

- multi-query fan-out actually issues both zh-topic and en-keywords queries
- dedupe by ``(path, chunk_id)`` keeps the best ``rrf_score``
- evidence pool grouping: one item per source path, items sorted by best score
- frozen defaults exposed and equal to the ADR-021 §3 freeze values
- outline drafter rejects: bad JSON, missing key, wrong section count, unknown
  evidence ref, too few refs per section
- happy path writes a store with the expected shape and slug
- re-run preserves ``user_actions`` and ``outline_final``
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from agents.brook import synthesize as synthesize_pkg
from agents.brook.synthesize import (
    BROOK_SYNTHESIZE_ENGINE,
    BROOK_SYNTHESIZE_TOP_K,
    OutlineDraftError,
    SynthesizeResult,
    synthesize,
)
from agents.brook.synthesize._outline import draft_outline
from agents.brook.synthesize._search import gather_evidence
from shared import brook_synthesize_store as store_mod
from shared.kb_hybrid_search import SearchHit
from shared.schemas.brook_synthesize import EvidencePoolItem, UserAction

# ---------------------------------------------------------------------------
# Fixtures — fake hits + fake LLM
# ---------------------------------------------------------------------------


def _hit(
    chunk_id: int,
    path: str,
    score: float,
    *,
    heading: str = "",
    page_title: str = "",
    text: str = "body",
) -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id,
        path=path,
        heading=heading,
        page_title=page_title or path.rsplit("/", 1)[-1],
        chunk_text=text,
        rrf_score=score,
        lane_ranks={"bm25": 1, "vec": 1},
    )


@pytest.fixture
def isolated_store(monkeypatch):
    """Point the brook_synthesize_store at a temp dir per-test."""
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("NAKAMA_BROOK_SYNTHESIZE_DIR", tmp)
        # Reset per-slug locks so we don't leak state between tests
        store_mod._locks.clear()
        yield Path(tmp)


def _make_outline_response(slugs: list[str], n_sections: int = 5) -> str:
    """Build an outline JSON that cites two distinct slugs per section."""
    if len(slugs) < 2:
        raise AssertionError("test setup needs >=2 slugs")
    sections = []
    for i in range(n_sections):
        a = slugs[i % len(slugs)]
        b = slugs[(i + 1) % len(slugs)]
        sections.append(
            {
                "section": i + 1,
                "heading": f"第 {i + 1} 段",
                "evidence_refs": [a, b],
            }
        )
    return json.dumps({"sections": sections}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Frozen defaults (ADR-021 §3 freeze 2026-05-07)
# ---------------------------------------------------------------------------


def test_frozen_defaults_match_adr_021_section_3():
    assert BROOK_SYNTHESIZE_TOP_K == 15
    assert BROOK_SYNTHESIZE_ENGINE == "hybrid"
    assert synthesize_pkg.MULTI_QUERY is True
    assert synthesize_pkg.OUTLINE_MIN_SECTIONS == 5
    assert synthesize_pkg.OUTLINE_MAX_SECTIONS == 7
    assert synthesize_pkg.OUTLINE_MIN_REFS_PER_SECTION == 2


# ---------------------------------------------------------------------------
# _search.gather_evidence
# ---------------------------------------------------------------------------


def test_gather_evidence_runs_both_query_lanes():
    """Multi-query fan-out: zh topic + en keywords both reach search()."""
    queries_seen: list[str] = []

    def fake_search(q, top_k, lanes, db=None):
        queries_seen.append(q)
        return [_hit(1, "KB/Wiki/Sources/a", 0.5)]

    with patch("agents.brook.synthesize._search.kb_hybrid_search.search", side_effect=fake_search):
        gather_evidence("肌酸對認知功能", ["creatine", "cognition"])

    assert "肌酸對認知功能" in queries_seen
    assert any("creatine" in q for q in queries_seen)
    assert len(queries_seen) == 2


def test_gather_evidence_dedupes_same_chunk_keeps_best_score():
    """Same (path, chunk_id) hit by both lanes → kept once at best score."""

    def fake_search(q, top_k, lanes, db=None):
        if "creatine" in q:
            return [_hit(1, "KB/Wiki/Sources/paper-a", 0.9)]
        return [_hit(1, "KB/Wiki/Sources/paper-a", 0.5)]

    with patch("agents.brook.synthesize._search.kb_hybrid_search.search", side_effect=fake_search):
        pool = gather_evidence("肌酸", ["creatine"])

    assert len(pool) == 1
    assert len(pool[0].chunks) == 1
    assert pool[0].chunks[0]["chunk_id"] == 1
    assert pool[0].chunks[0]["rrf_score"] == pytest.approx(0.9)
    assert "zh-topic" in pool[0].hit_reason
    assert "en-keywords" in pool[0].hit_reason


def test_gather_evidence_groups_by_source_and_sorts():
    """Two sources, multiple chunks; sources sorted by best chunk score."""

    def fake_search(q, top_k, lanes, db=None):
        return [
            _hit(1, "KB/Wiki/Sources/paper-a", 0.4),
            _hit(2, "KB/Wiki/Sources/paper-a", 0.7),  # best in paper-a
            _hit(3, "KB/Wiki/Sources/paper-b", 0.9),  # best overall
        ]

    with patch("agents.brook.synthesize._search.kb_hybrid_search.search", side_effect=fake_search):
        pool = gather_evidence("topic", ["kw"])

    assert [item.slug for item in pool] == [
        "KB/Wiki/Sources/paper-b",
        "KB/Wiki/Sources/paper-a",
    ]
    paper_a = next(p for p in pool if p.slug == "KB/Wiki/Sources/paper-a")
    # chunks within an item are sorted by score desc
    assert [c["chunk_id"] for c in paper_a.chunks] == [2, 1]


def test_gather_evidence_passes_top_k_through():
    """Frozen TOP_K=15 actually reaches kb_hybrid_search."""
    captured: dict = {}

    def fake_search(q, top_k, lanes, db=None):
        captured["top_k"] = top_k
        captured["lanes"] = lanes
        return []

    with patch("agents.brook.synthesize._search.kb_hybrid_search.search", side_effect=fake_search):
        gather_evidence("topic", ["kw"], top_k=BROOK_SYNTHESIZE_TOP_K)

    assert captured["top_k"] == 15
    assert captured["lanes"] == ("bm25", "vec")


def test_gather_evidence_rejects_unknown_engine():
    with pytest.raises(ValueError, match="unsupported BROOK_SYNTHESIZE_ENGINE"):
        gather_evidence("topic", ["kw"], engine="haiku")


def test_gather_evidence_rejects_empty_input():
    with pytest.raises(ValueError, match="non-empty topic or keywords"):
        gather_evidence("", [])


# ---------------------------------------------------------------------------
# _outline.draft_outline
# ---------------------------------------------------------------------------


def _pool_of(*slugs: str) -> list[EvidencePoolItem]:
    return [
        EvidencePoolItem(
            slug=slug,
            chunks=[
                {
                    "chunk_id": i + 1,
                    "heading": f"H {slug}",
                    "page_title": slug,
                    "chunk_text": "...",
                    "rrf_score": 0.5,
                    "lane_ranks": {"bm25": 1},
                }
            ],
            hit_reason="matched zh-topic",
        )
        for i, slug in enumerate(slugs)
    ]


def test_draft_outline_happy_path():
    pool = _pool_of("a", "b", "c")
    response = _make_outline_response(["a", "b", "c"], n_sections=5)

    sections = draft_outline("topic", ["kw"], pool, ask_fn=lambda *_a, **_kw: response)
    assert len(sections) == 5
    assert all(len(s.evidence_refs) >= 2 for s in sections)
    assert [s.section for s in sections] == [1, 2, 3, 4, 5]


def test_draft_outline_strips_markdown_fence():
    pool = _pool_of("a", "b")
    inner = _make_outline_response(["a", "b"], n_sections=5)
    fenced = f"```json\n{inner}\n```"
    sections = draft_outline("t", [], pool, ask_fn=lambda *_a, **_kw: fenced)
    assert len(sections) == 5


def test_draft_outline_rejects_non_json():
    pool = _pool_of("a", "b")
    with pytest.raises(OutlineDraftError, match="non-JSON"):
        draft_outline("t", [], pool, ask_fn=lambda *_a, **_kw: "not json at all")


def test_draft_outline_rejects_missing_sections_key():
    pool = _pool_of("a", "b")
    with pytest.raises(OutlineDraftError, match="missing `sections`"):
        draft_outline("t", [], pool, ask_fn=lambda *_a, **_kw: "{}")


def test_draft_outline_rejects_wrong_section_count():
    pool = _pool_of("a", "b")
    response = _make_outline_response(["a", "b"], n_sections=3)  # below min=5
    with pytest.raises(OutlineDraftError, match="3 sections"):
        draft_outline("t", [], pool, ask_fn=lambda *_a, **_kw: response)


def test_draft_outline_rejects_unknown_slug():
    pool = _pool_of("a", "b")
    bad = json.dumps(
        {
            "sections": [
                {"section": i + 1, "heading": f"h{i}", "evidence_refs": ["a", "ghost"]}
                for i in range(5)
            ]
        }
    )
    with pytest.raises(OutlineDraftError, match="unknown evidence slugs"):
        draft_outline("t", [], pool, ask_fn=lambda *_a, **_kw: bad)


def test_draft_outline_rejects_too_few_refs():
    pool = _pool_of("a", "b", "c")
    bad = json.dumps(
        {
            "sections": [
                {"section": i + 1, "heading": f"h{i}", "evidence_refs": ["a"]} for i in range(5)
            ]
        }
    )
    with pytest.raises(OutlineDraftError, match="cites 1 refs"):
        draft_outline("t", [], pool, ask_fn=lambda *_a, **_kw: bad)


def test_draft_outline_rejects_section_ordering_drift():
    pool = _pool_of("a", "b")
    bad = json.dumps(
        {
            "sections": [
                {"section": 1, "heading": "h1", "evidence_refs": ["a", "b"]},
                {"section": 3, "heading": "h2", "evidence_refs": ["a", "b"]},
                {"section": 4, "heading": "h3", "evidence_refs": ["a", "b"]},
                {"section": 5, "heading": "h4", "evidence_refs": ["a", "b"]},
                {"section": 6, "heading": "h5", "evidence_refs": ["a", "b"]},
            ]
        }
    )
    with pytest.raises(OutlineDraftError, match="section ordering"):
        draft_outline("t", [], pool, ask_fn=lambda *_a, **_kw: bad)


def test_draft_outline_rejects_empty_pool():
    with pytest.raises(OutlineDraftError, match="empty evidence pool"):
        draft_outline("t", [], [], ask_fn=lambda *_a, **_kw: "")


# ---------------------------------------------------------------------------
# End-to-end synthesize() — search + outline + store
# ---------------------------------------------------------------------------


def test_synthesize_writes_store_with_expected_shape(isolated_store):
    slugs = [
        "KB/Wiki/Sources/paper-a",
        "KB/Wiki/Sources/paper-b",
        "KB/Wiki/Sources/paper-c",
    ]

    def fake_search(q, top_k, lanes, db=None):
        return [
            _hit(1, slugs[0], 0.9, heading="Intro"),
            _hit(2, slugs[1], 0.8, heading="Method"),
            _hit(3, slugs[2], 0.7, heading="Result"),
        ]

    response = _make_outline_response(slugs, n_sections=5)

    with patch("agents.brook.synthesize._search.kb_hybrid_search.search", side_effect=fake_search):
        result = synthesize(
            "creatine-cognitive",
            "肌酸對認知功能",
            ["creatine", "cognition"],
            ask_fn=lambda *_a, **_kw: response,
        )

    assert isinstance(result, SynthesizeResult)
    assert result.slug == "creatine-cognitive"
    assert len(result.evidence_pool) == 3
    assert len(result.outline_draft) == 5

    # Store should be readable + match
    persisted = store_mod.read("creatine-cognitive")
    assert persisted.project_slug == "creatine-cognitive"
    assert persisted.topic == "肌酸對認知功能"
    assert persisted.keywords == ["creatine", "cognition"]
    assert len(persisted.evidence_pool) == 3
    assert len(persisted.outline_draft) == 5
    assert all(item.slug in slugs for item in persisted.evidence_pool)
    # Each outline section's refs must resolve to a pool slug
    pool_slugs = {item.slug for item in persisted.evidence_pool}
    for section in persisted.outline_draft:
        assert set(section.evidence_refs).issubset(pool_slugs)


def test_synthesize_rerun_preserves_user_actions_and_final(isolated_store):
    slugs = ["KB/Wiki/Sources/a", "KB/Wiki/Sources/b"]

    def fake_search(q, top_k, lanes, db=None):
        return [_hit(1, slugs[0], 0.9), _hit(2, slugs[1], 0.8)]

    response = _make_outline_response(slugs, n_sections=5)

    with patch("agents.brook.synthesize._search.kb_hybrid_search.search", side_effect=fake_search):
        synthesize("proj-x", "topic", ["kw"], ask_fn=lambda *_a, **_kw: response)

    # Simulate user review: one action recorded, outline_final written.
    store_mod.append_user_action(
        "proj-x",
        UserAction(
            timestamp="2026-05-07T00:00:00Z",
            action="reject_from_section",
            evidence_slug=slugs[0],
            section=2,
        ),
    )
    after_review = store_mod.read("proj-x")
    final_sections = after_review.outline_draft[:3]
    store_mod.update_outline_final("proj-x", final_sections)

    # Re-run synthesize — overwrite pool/draft, but preserve user state.
    with patch("agents.brook.synthesize._search.kb_hybrid_search.search", side_effect=fake_search):
        synthesize("proj-x", "topic", ["kw", "extra"], ask_fn=lambda *_a, **_kw: response)

    after_rerun = store_mod.read("proj-x")
    assert after_rerun.keywords == ["kw", "extra"]  # overwritten
    assert len(after_rerun.user_actions) == 1  # preserved
    assert after_rerun.user_actions[0].evidence_slug == slugs[0]
    assert len(after_rerun.outline_final) == 3  # preserved


def test_synthesize_propagates_outline_draft_error(isolated_store):
    """Outline failure must NOT write the store (no half-baked artifacts)."""

    def fake_search(q, top_k, lanes, db=None):
        return [_hit(1, "KB/Wiki/Sources/a", 0.9), _hit(2, "KB/Wiki/Sources/b", 0.8)]

    with patch("agents.brook.synthesize._search.kb_hybrid_search.search", side_effect=fake_search):
        with pytest.raises(OutlineDraftError):
            synthesize(
                "proj-fail",
                "topic",
                ["kw"],
                ask_fn=lambda *_a, **_kw: "not json",
            )

    assert not store_mod.exists("proj-fail")


# ---------------------------------------------------------------------------
# ADR-027 §Decision 4 — trending_angles + unmatched warning
# ---------------------------------------------------------------------------


def _outline_with_trending_match(slugs: list[str], per_section_match: list[list[str]]) -> str:
    """Build an outline JSON where each section carries explicit trending_match.

    ``per_section_match[i]`` is the ``trending_match`` value for section i+1.
    Section count = len(per_section_match), must be within the 5–7 contract.
    Each section cites two pool slugs (rotating).
    """
    sections = []
    for i, match in enumerate(per_section_match):
        a = slugs[i % len(slugs)]
        b = slugs[(i + 1) % len(slugs)]
        sections.append(
            {
                "section": i + 1,
                "heading": f"第 {i + 1} 段",
                "evidence_refs": [a, b],
                "trending_match": list(match),
            }
        )
    return json.dumps({"sections": sections}, ensure_ascii=False)


def test_synthesize_unmatched_trending_angles(isolated_store):
    """5 angles in, 3 matched by outline → 2 land in unmatched warning."""
    slugs = [
        "KB/Wiki/Sources/paper-a",
        "KB/Wiki/Sources/paper-b",
        "KB/Wiki/Sources/paper-c",
    ]

    def fake_search(q, top_k, lanes, db=None):
        return [
            _hit(1, slugs[0], 0.9),
            _hit(2, slugs[1], 0.8),
            _hit(3, slugs[2], 0.7),
        ]

    # 5 sections, sections 1/2/3 each match one of a/b/c; sections 4/5 match nothing.
    response = _outline_with_trending_match(
        slugs,
        per_section_match=[["a"], ["b"], ["c"], [], []],
    )

    with patch("agents.brook.synthesize._search.kb_hybrid_search.search", side_effect=fake_search):
        result = synthesize(
            "trend-proj",
            "topic",
            ["kw"],
            ask_fn=lambda *_a, **_kw: response,
            trending_angles=["a", "b", "c", "d", "e"],
        )

    assert sorted(result.store.unmatched_trending_angles) == ["d", "e"]
    persisted = store_mod.read("trend-proj")
    assert sorted(persisted.unmatched_trending_angles) == ["d", "e"]
    # Per-section trending_match round-tripped through schema + store.
    assert persisted.outline_draft[0].trending_match == ["a"]
    assert persisted.outline_draft[3].trending_match == []


def test_synthesize_no_trending_angles_is_backwards_compatible(isolated_store):
    """Omitting trending_angles: empty unmatched, prompt identical to baseline."""
    slugs = ["KB/Wiki/Sources/a", "KB/Wiki/Sources/b"]

    def fake_search(q, top_k, lanes, db=None):
        return [_hit(1, slugs[0], 0.9), _hit(2, slugs[1], 0.8)]

    captured_prompts: list[str] = []

    def fake_ask(prompt, **_kw):
        captured_prompts.append(prompt)
        return _make_outline_response(slugs, n_sections=5)

    # Baseline call (no trending_angles).
    with patch("agents.brook.synthesize._search.kb_hybrid_search.search", side_effect=fake_search):
        result = synthesize("proj-baseline", "topic", ["kw"], ask_fn=fake_ask)

    assert result.store.unmatched_trending_angles == []
    # No rendered angles block: the header line (which only appears when
    # angles are supplied) must not be present. The bare phrase "trending"
    # may appear in the static prompt body (e.g. `trending_match` field
    # description), so we anchor on the exact rendered block header.
    assert "可選參考；不可為配 angle 編造" not in captured_prompts[0]

    # Same call with trending_angles=None must produce the same prompt.
    with patch("agents.brook.synthesize._search.kb_hybrid_search.search", side_effect=fake_search):
        synthesize(
            "proj-baseline-none",
            "topic",
            ["kw"],
            ask_fn=fake_ask,
            trending_angles=None,
        )

    assert captured_prompts[0] == captured_prompts[1]


def test_synthesize_unmatched_ignores_llm_invented_angles(isolated_store):
    """LLM emits trending_match values not in the input list — ignored in unmatched calc.

    Decision (documented in ``synthesize.__init__``): we do NOT bounce the
    outline when the LLM hallucinates an angle name (it's a soft warning
    field, not a hard contract like ``evidence_refs``). The
    ``unmatched_trending_angles`` calculation counts only input angles that
    no section matched, so an invented "nonexistent" claim cannot make a
    real input angle appear "matched".
    """
    slugs = ["KB/Wiki/Sources/a", "KB/Wiki/Sources/b"]

    def fake_search(q, top_k, lanes, db=None):
        return [_hit(1, slugs[0], 0.9), _hit(2, slugs[1], 0.8)]

    # Sections 1..5 all claim trending_match=["nonexistent"]; the user supplied
    # ["real-1", "real-2"] — neither was matched by any section.
    response = _outline_with_trending_match(
        slugs,
        per_section_match=[["nonexistent"]] * 5,
    )

    with patch("agents.brook.synthesize._search.kb_hybrid_search.search", side_effect=fake_search):
        result = synthesize(
            "proj-ghost",
            "topic",
            ["kw"],
            ask_fn=lambda *_a, **_kw: response,
            trending_angles=["real-1", "real-2"],
        )

    # Outline still persists with the LLM's invented trending_match — we
    # don't fail the run, just don't count it as a match.
    assert result.store.outline_draft[0].trending_match == ["nonexistent"]
    # All input angles unmatched.
    assert sorted(result.store.unmatched_trending_angles) == ["real-1", "real-2"]


def test_store_loads_legacy_json_without_unmatched_field(isolated_store):
    """Persisted stores written before ADR-027 still load — field is optional."""
    from shared.schemas.brook_synthesize import BrookSynthesizeStore

    legacy_path = isolated_store / "legacy-proj.json"
    legacy_path.write_text(
        json.dumps(
            {
                "project_slug": "legacy-proj",
                "topic": "old",
                "keywords": [],
                "evidence_pool": [],
                "outline_draft": [],
                "user_actions": [],
                "outline_final": [],
                "schema_version": 1,
                "updated_at": "",
            }
        ),
        encoding="utf-8",
    )

    loaded = BrookSynthesizeStore.model_validate_json(legacy_path.read_text(encoding="utf-8"))
    assert loaded.unmatched_trending_angles == []
