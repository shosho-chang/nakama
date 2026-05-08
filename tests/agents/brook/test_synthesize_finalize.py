"""Tests for ``agents.brook.synthesize.regenerate_outline_final`` (issue #462).

Strategy: mock the LLM ``ask_fn`` and inject pre-seeded
:class:`BrookSynthesizeStore` records. We are testing the finalize logic —
re-using the cached pool, applying the reject discount, honouring per-section
rejects — *not* the LLM or KB retrieval.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from agents.brook.synthesize import regenerate_outline_final
from agents.brook.synthesize._outline import OutlineDraftError
from shared import brook_synthesize_store as store_mod
from shared.brook_synthesize_store import StoreNotFoundError
from shared.schemas.brook_synthesize import (
    BrookSynthesizeStore,
    EvidencePoolItem,
    OutlineSection,
    UserAction,
)


@pytest.fixture
def isolated_store(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("NAKAMA_BROOK_SYNTHESIZE_DIR", tmp)
        store_mod._locks.clear()
        yield Path(tmp)


def _outline_json(slugs: list[str], n_sections: int = 5) -> str:
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


def _seed(
    slug: str,
    *,
    user_actions: list[UserAction] | None = None,
) -> BrookSynthesizeStore:
    s = BrookSynthesizeStore(
        project_slug=slug,
        topic="topic",
        keywords=["a", "b"],
        evidence_pool=[
            EvidencePoolItem(
                slug="alpha",
                chunks=[{"chunk_id": 1, "rrf_score": 0.9, "heading": "h"}],
                hit_reason="rrf",
            ),
            EvidencePoolItem(
                slug="beta",
                chunks=[{"chunk_id": 2, "rrf_score": 0.85, "heading": "h"}],
                hit_reason="rrf",
            ),
            EvidencePoolItem(
                slug="gamma",
                chunks=[{"chunk_id": 3, "rrf_score": 0.7, "heading": "h"}],
                hit_reason="rrf",
            ),
        ],
        outline_draft=[
            OutlineSection(section=1, heading="draft", evidence_refs=["alpha", "beta"]),
        ],
        user_actions=list(user_actions or []),
    )
    return store_mod.create(s)


# ── Happy path ───────────────────────────────────────────────────────────────


def test_regenerate_writes_outline_final(isolated_store):
    _seed("p1")
    fake = _outline_json(["alpha", "beta", "gamma"], n_sections=5)
    result = regenerate_outline_final("p1", ask_fn=lambda *a, **kw: fake)
    assert len(result.outline_final) == 5
    # outline_draft preserved
    assert len(result.outline_draft) == 1
    # persisted on disk
    fresh = store_mod.read("p1")
    assert len(fresh.outline_final) == 5


def test_regenerate_does_not_recompute_evidence_pool(isolated_store):
    """ADR-021 §3 Step 4: 廣搜結果 cached，不重撈. We assert this by
    verifying that no kb_hybrid_search call happens — the cached pool's
    items survive into the outline refs untouched."""
    _seed("p2")
    fake = _outline_json(["alpha", "beta"], n_sections=5)

    # If finalize accidentally re-ran gather_evidence, it would hit
    # kb_hybrid_search; patching it to raise is the simplest assertion.
    import shared.kb_hybrid_search as khs

    def boom(*a, **kw):
        raise AssertionError("finalize must not re-run KB search")

    orig = khs.search
    khs.search = boom
    try:
        result = regenerate_outline_final("p2", ask_fn=lambda *a, **kw: fake)
    finally:
        khs.search = orig
    assert len(result.outline_final) == 5


# ── Reject discount ──────────────────────────────────────────────────────────


def test_regenerate_applies_global_reject_discount(isolated_store):
    """A `reject_evidence_entirely` action discounts the rejected slug.

    We seed alpha as the top slug then reject it twice; gamma's score
    (0.7) should now beat alpha's discounted score (0.9 * 0.5^2 = 0.225)
    so the LLM sees gamma ahead in the prompt block. We assert the LLM
    received a prompt where alpha is no longer the top entry by capturing
    the prompt argument.
    """
    _seed(
        "p3",
        user_actions=[
            UserAction(
                timestamp="t1",
                action="reject_evidence_entirely",
                evidence_slug="alpha",
            ),
            UserAction(
                timestamp="t2",
                action="reject_evidence_entirely",
                evidence_slug="alpha",
            ),
        ],
    )
    captured: dict = {}

    def fake_ask(prompt, **kw):
        captured["prompt"] = prompt
        return _outline_json(["beta", "gamma", "alpha"], n_sections=5)

    regenerate_outline_final("p3", ask_fn=fake_ask)
    prompt = captured["prompt"]
    # In the evidence block, gamma should appear before alpha (alpha sunk).
    alpha_pos = prompt.find("- alpha:")
    gamma_pos = prompt.find("- gamma:")
    assert gamma_pos < alpha_pos, "alpha should be discounted below gamma after 2 rejects"


def test_regenerate_honours_per_section_reject(isolated_store):
    """`reject_from_section` removes the slug from that one section only."""
    _seed(
        "p4",
        user_actions=[
            UserAction(
                timestamp="t1",
                action="reject_from_section",
                section=2,
                evidence_slug="alpha",
            ),
        ],
    )
    fake = _outline_json(["alpha", "beta", "gamma"], n_sections=5)
    result = regenerate_outline_final("p4", ask_fn=lambda *a, **kw: fake)
    sec2 = next(s for s in result.outline_final if s.section == 2)
    assert "alpha" not in sec2.evidence_refs
    # other sections unaffected
    other_sections_with_alpha = [
        s for s in result.outline_final if s.section != 2 and "alpha" in s.evidence_refs
    ]
    assert other_sections_with_alpha, "alpha should remain in other sections"


# ── Errors ───────────────────────────────────────────────────────────────────


def test_regenerate_404_when_no_store(isolated_store):
    with pytest.raises(StoreNotFoundError):
        regenerate_outline_final("nope", ask_fn=lambda *a, **kw: "{}")


def test_regenerate_empty_pool_raises(isolated_store):
    s = BrookSynthesizeStore(
        project_slug="empty",
        topic="t",
        keywords=[],
        evidence_pool=[],
    )
    store_mod.create(s)
    with pytest.raises(ValueError, match="empty"):
        regenerate_outline_final("empty", ask_fn=lambda *a, **kw: "{}")


def test_regenerate_propagates_outline_contract_violations(isolated_store):
    _seed("p5")
    bad = json.dumps({"sections": []})
    with pytest.raises(OutlineDraftError):
        regenerate_outline_final("p5", ask_fn=lambda *a, **kw: bad)
