"""Tests for ``agents.brook.synthesize._reject_discount`` (issue #460, ADR-021 §4).

Two layers of coverage:

1. Pure unit tests on ``apply_reject_discount`` — fixture pool + fixture
   ``user_actions`` → assert score discount + ordering. These are the AC
   evidence: rejected slugs sink, but a high-base slug with one reject can
   still beat a low-base no-reject slug (serendipitous rediscovery).

2. Integration test on ``synthesize()`` — mocks ``kb_hybrid_search`` and the
   LLM, seeds a prior store with ``user_actions``, asserts the outline
   prompt sees a re-ranked pool.

Per ``feedback_test_api_isolation.md`` no test hits a real API or real KB.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from agents.brook.synthesize import (
    REJECT_DISCOUNT_FACTOR,
    apply_reject_discount,
    synthesize,
)
from shared import brook_synthesize_store as store_mod
from shared.kb_hybrid_search import SearchHit
from shared.schemas.brook_synthesize import (
    BrookSynthesizeStore,
    EvidencePoolItem,
    UserAction,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _chunk(chunk_id: int, score: float, *, heading: str = "h") -> dict:
    return {
        "chunk_id": chunk_id,
        "heading": heading,
        "page_title": "p",
        "chunk_text": "...",
        "rrf_score": score,
        "lane_ranks": {"bm25": 1, "vec": 1},
    }


def _item(slug: str, *chunks: dict, hit_reason: str = "") -> EvidencePoolItem:
    return EvidencePoolItem(slug=slug, chunks=list(chunks), hit_reason=hit_reason)


def _hit(chunk_id: int, path: str, score: float, *, heading: str = "") -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id,
        path=path,
        heading=heading,
        page_title=path.rsplit("/", 1)[-1],
        chunk_text="body",
        rrf_score=score,
        lane_ranks={"bm25": 1, "vec": 1},
    )


def _reject(slug: str, *, when: str = "2026-05-07T00:00:00Z") -> UserAction:
    return UserAction(timestamp=when, action="reject_evidence_entirely", evidence_slug=slug)


def _section_reject(slug: str, section: int) -> UserAction:
    return UserAction(
        timestamp="2026-05-07T00:00:00Z",
        action="reject_from_section",
        evidence_slug=slug,
        section=section,
    )


def _make_outline_response(slugs: list[str], n_sections: int = 5) -> str:
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


@pytest.fixture
def isolated_store(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("NAKAMA_BROOK_SYNTHESIZE_DIR", tmp)
        store_mod._locks.clear()
        yield Path(tmp)


# ---------------------------------------------------------------------------
# apply_reject_discount — unit tests
# ---------------------------------------------------------------------------


def test_no_user_actions_is_noop():
    """Empty user_actions → return pool with identical ordering and scores."""
    pool = [
        _item("KB/Wiki/Sources/a", _chunk(1, 0.9)),
        _item("KB/Wiki/Sources/b", _chunk(2, 0.5)),
    ]
    out = apply_reject_discount(pool, [])
    assert [it.slug for it in out] == ["KB/Wiki/Sources/a", "KB/Wiki/Sources/b"]
    assert out[0].chunks[0]["rrf_score"] == pytest.approx(0.9)
    assert out[1].chunks[0]["rrf_score"] == pytest.approx(0.5)


def test_section_reject_does_not_discount_globally():
    """``reject_from_section`` is per-section grain — must NOT down-rank pool-wide.

    AC bullet #3: "不對 reject_from_section 全局降權".
    """
    pool = [
        _item("KB/Wiki/Sources/a", _chunk(1, 0.9)),
        _item("KB/Wiki/Sources/b", _chunk(2, 0.5)),
    ]
    actions = [_section_reject("KB/Wiki/Sources/a", section=2)]
    out = apply_reject_discount(pool, actions)
    # Order unchanged, score unchanged
    assert [it.slug for it in out] == ["KB/Wiki/Sources/a", "KB/Wiki/Sources/b"]
    assert out[0].chunks[0]["rrf_score"] == pytest.approx(0.9)


def test_single_reject_halves_scores_and_resorts():
    """One reject on the top slug → all its chunks scaled by factor; pool re-sorts."""
    pool = [
        _item(
            "KB/Wiki/Sources/a",
            _chunk(1, 0.9),
            _chunk(2, 0.7),
        ),
        _item("KB/Wiki/Sources/b", _chunk(3, 0.5)),
    ]
    out = apply_reject_discount(pool, [_reject("KB/Wiki/Sources/a")], discount_factor=0.5)
    by_slug = {it.slug: it for it in out}
    # paper-a chunks halved
    assert by_slug["KB/Wiki/Sources/a"].chunks[0]["rrf_score"] == pytest.approx(0.45)
    assert by_slug["KB/Wiki/Sources/a"].chunks[1]["rrf_score"] == pytest.approx(0.35)
    # paper-b untouched
    assert by_slug["KB/Wiki/Sources/b"].chunks[0]["rrf_score"] == pytest.approx(0.5)
    # paper-b now ranks above paper-a (best 0.5 > best 0.45)
    assert [it.slug for it in out] == [
        "KB/Wiki/Sources/b",
        "KB/Wiki/Sources/a",
    ]
    # downrank annotation present
    assert "downranked" in by_slug["KB/Wiki/Sources/a"].hit_reason


def test_multiple_rejects_compound_multiplicatively():
    """Two rejects on the same slug → score scaled by factor**2."""
    pool = [_item("KB/Wiki/Sources/a", _chunk(1, 0.8))]
    actions = [_reject("KB/Wiki/Sources/a"), _reject("KB/Wiki/Sources/a")]
    out = apply_reject_discount(pool, actions, discount_factor=0.5)
    # 0.8 * 0.5 * 0.5 = 0.2
    assert out[0].chunks[0]["rrf_score"] == pytest.approx(0.2)
    # plural form in note
    assert "2 prior rejects" in out[0].hit_reason


def test_serendipitous_rediscovery_high_base_beats_low_base_no_reject():
    """High-base slug with 1 reject can still out-rank a low-base no-reject slug.

    This is the core AC bullet #4: "若 RRF base score 高仍可入 top-K".
    Without this, the discount would be effectively a hide.
    """
    pool = [
        _item("KB/Wiki/Sources/strong", _chunk(1, 0.9)),  # rejected once
        _item("KB/Wiki/Sources/weak", _chunk(2, 0.3)),  # never rejected
    ]
    out = apply_reject_discount(pool, [_reject("KB/Wiki/Sources/strong")], discount_factor=0.5)
    # 0.9 * 0.5 = 0.45 > 0.3 — strong stays on top despite the reject
    assert [it.slug for it in out] == [
        "KB/Wiki/Sources/strong",
        "KB/Wiki/Sources/weak",
    ]
    assert out[0].chunks[0]["rrf_score"] == pytest.approx(0.45)


def test_rejected_slug_is_not_removed_from_pool():
    """Even a heavily-rejected slug stays in the pool — never hard-hidden.

    Three rejects with factor 0.5 → multiplier 0.125. The slug survives.
    """
    pool = [
        _item("KB/Wiki/Sources/a", _chunk(1, 0.4)),
        _item("KB/Wiki/Sources/b", _chunk(2, 0.3)),
    ]
    actions = [_reject("KB/Wiki/Sources/a")] * 3
    out = apply_reject_discount(pool, actions, discount_factor=0.5)
    slugs = [it.slug for it in out]
    assert "KB/Wiki/Sources/a" in slugs  # not hidden
    # 0.4 * 0.125 = 0.05, well below 0.3
    a = next(it for it in out if it.slug == "KB/Wiki/Sources/a")
    assert a.chunks[0]["rrf_score"] == pytest.approx(0.05)
    assert slugs[0] == "KB/Wiki/Sources/b"


def test_accepts_dict_user_actions():
    """user_actions list may contain raw dicts (e.g. JSON-loaded) — not just UserAction."""
    pool = [_item("KB/Wiki/Sources/a", _chunk(1, 0.8))]
    actions = [
        {
            "timestamp": "2026-05-07T00:00:00Z",
            "action": "reject_evidence_entirely",
            "evidence_slug": "KB/Wiki/Sources/a",
            "section": None,
        }
    ]
    out = apply_reject_discount(pool, actions, discount_factor=0.5)
    assert out[0].chunks[0]["rrf_score"] == pytest.approx(0.4)


def test_reject_unknown_slug_is_ignored():
    """Reject pointing at a slug not in the pool → no-op for that action."""
    pool = [_item("KB/Wiki/Sources/a", _chunk(1, 0.8))]
    out = apply_reject_discount(pool, [_reject("KB/Wiki/Sources/ghost")], discount_factor=0.5)
    assert out[0].chunks[0]["rrf_score"] == pytest.approx(0.8)


def test_empty_pool_is_noop():
    out = apply_reject_discount([], [_reject("anything")])
    assert out == []


def test_invalid_discount_factor_raises():
    pool = [_item("KB/Wiki/Sources/a", _chunk(1, 0.5))]
    with pytest.raises(ValueError, match=r"discount_factor"):
        apply_reject_discount(pool, [_reject("KB/Wiki/Sources/a")], discount_factor=1.5)
    with pytest.raises(ValueError, match=r"discount_factor"):
        apply_reject_discount(pool, [_reject("KB/Wiki/Sources/a")], discount_factor=-0.1)


def test_default_factor_is_the_constant():
    """Sanity — default discount factor is the published constant."""
    pool = [_item("KB/Wiki/Sources/a", _chunk(1, 1.0))]
    out = apply_reject_discount(pool, [_reject("KB/Wiki/Sources/a")])
    assert out[0].chunks[0]["rrf_score"] == pytest.approx(REJECT_DISCOUNT_FACTOR)


# ---------------------------------------------------------------------------
# Integration — synthesize() reads prior store and discounts before outline
# ---------------------------------------------------------------------------


def test_synthesize_first_run_has_no_discount(isolated_store):
    """No prior store → ordering is exactly what gather_evidence produced."""
    slugs = ["KB/Wiki/Sources/a", "KB/Wiki/Sources/b"]

    def fake_search(q, top_k, lanes, db=None):
        return [_hit(1, slugs[0], 0.9), _hit(2, slugs[1], 0.5)]

    response = _make_outline_response(slugs, n_sections=5)

    with patch(
        "agents.brook.synthesize._search.kb_hybrid_search.search",
        side_effect=fake_search,
    ):
        result = synthesize("first-run", "topic", ["kw"], ask_fn=lambda *_a, **_kw: response)

    # No prior actions → "a" stays on top because its RRF is higher
    assert [it.slug for it in result.evidence_pool] == [slugs[0], slugs[1]]
    assert result.evidence_pool[0].chunks[0]["rrf_score"] == pytest.approx(0.9)


def test_synthesize_rerun_applies_reject_discount(isolated_store):
    """Pre-seed a store with a reject_evidence_entirely → re-run sinks that slug."""
    slugs = ["KB/Wiki/Sources/strong", "KB/Wiki/Sources/weak"]

    # Seed a prior store with one reject_evidence_entirely on the strong slug.
    # Use create() so the re-run path in persist() sees the prior actions.
    seed = BrookSynthesizeStore(
        project_slug="proj-rerun",
        topic="topic",
        keywords=["kw"],
        evidence_pool=[],
        outline_draft=[],
        user_actions=[
            _reject(slugs[0]),
            _reject(slugs[0]),  # twice → multiplier 0.25
        ],
        outline_final=[],
    )
    store_mod.create(seed)

    def fake_search(q, top_k, lanes, db=None):
        # base scores: strong=0.9, weak=0.4 — without discount, strong wins.
        # After 2x reject (factor 0.5): strong=0.225, weak=0.4 → weak wins.
        return [_hit(1, slugs[0], 0.9), _hit(2, slugs[1], 0.4)]

    response = _make_outline_response(slugs, n_sections=5)

    with patch(
        "agents.brook.synthesize._search.kb_hybrid_search.search",
        side_effect=fake_search,
    ):
        result = synthesize("proj-rerun", "topic", ["kw"], ask_fn=lambda *_a, **_kw: response)

    # weak should now lead — strong is downranked but still present
    pool_slugs = [it.slug for it in result.evidence_pool]
    assert pool_slugs == [slugs[1], slugs[0]]
    strong_item = next(it for it in result.evidence_pool if it.slug == slugs[0])
    # 0.9 * 0.5 * 0.5 = 0.225
    assert strong_item.chunks[0]["rrf_score"] == pytest.approx(0.225)
    assert "downranked" in strong_item.hit_reason

    # User actions preserved through the re-run (sanity — orthogonal to discount)
    persisted = store_mod.read("proj-rerun")
    assert len(persisted.user_actions) == 2


def test_synthesize_rerun_section_reject_does_not_sink_pool(isolated_store):
    """A prior reject_from_section must NOT change pool ordering on re-run."""
    slugs = ["KB/Wiki/Sources/a", "KB/Wiki/Sources/b"]

    seed = BrookSynthesizeStore(
        project_slug="proj-section",
        topic="topic",
        keywords=["kw"],
        evidence_pool=[],
        outline_draft=[],
        user_actions=[_section_reject(slugs[0], section=2)],
        outline_final=[],
    )
    store_mod.create(seed)

    def fake_search(q, top_k, lanes, db=None):
        return [_hit(1, slugs[0], 0.9), _hit(2, slugs[1], 0.5)]

    response = _make_outline_response(slugs, n_sections=5)
    with patch(
        "agents.brook.synthesize._search.kb_hybrid_search.search",
        side_effect=fake_search,
    ):
        result = synthesize("proj-section", "topic", ["kw"], ask_fn=lambda *_a, **_kw: response)

    # Untouched ordering, untouched scores
    assert [it.slug for it in result.evidence_pool] == [slugs[0], slugs[1]]
    assert result.evidence_pool[0].chunks[0]["rrf_score"] == pytest.approx(0.9)
