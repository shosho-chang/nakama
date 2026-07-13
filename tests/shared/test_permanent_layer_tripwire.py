"""Centaur N520 tripwires — KB/Permanent/ write-discipline negative tests.

紅線 1（v0.2 §7）= AI 絕不寫 KB/Permanent/ 正文與 status。These tests are the
mechanical enforcement: the promotion resolver chokepoint refuses any target that
lands in the permanent layer, and the bookkeeping writer refuses any non-whitelist
key or body write. No human-authoring path exists yet (N523) so every test here is
NEGATIVE by design (panel Codex §4 / Gemini §4): we prove automated paths CANNOT
touch the permanent layer, not that a human path can.
"""

from __future__ import annotations

import inspect

import pytest

from shared.permanent_layer import (
    ALLOWED_BOOKKEEPING_KEYS,
    PermanentWriteViolation,
    assert_not_permanent_target,
    is_permanent_path,
    update_permanent_bookkeeping,
)
from shared.schemas.promotion_manifest import ConceptReviewItem, SourcePageReviewItem

# ---------------------------------------------------------------------------
# is_permanent_path — segment-aware path detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,expected",
    [
        ("KB/Permanent/好系統讓你不需要意志力.md", True),
        ("KB/Permanent", True),
        ("KB/Permanent/nested/card.md", True),
        (r"E:\Shosho LifeOS\KB\Permanent\card.md", True),
        ("KB/PermanentDrafts/card.md", False),  # prefix collision must NOT match
        ("KB/Wiki/Concepts/overtraining.md", False),
        ("KB/Annotations/foo.md", False),
    ],
)
def test_is_permanent_path_segment_aware(path, expected):
    assert is_permanent_path(path) is expected


# ---------------------------------------------------------------------------
# assert_not_permanent_target — the negative tripwire
# ---------------------------------------------------------------------------


def test_assert_not_permanent_target_passes_none_and_wiki():
    assert_not_permanent_target(None)  # no target → safe
    assert_not_permanent_target("KB/Wiki/Concepts/overtraining.md")  # wiki → safe


def test_assert_not_permanent_target_raises_on_permanent():
    with pytest.raises(PermanentWriteViolation):
        assert_not_permanent_target("KB/Permanent/某張卡.md")


# ---------------------------------------------------------------------------
# resolve_target_path — chokepoint covers BOTH gate + commit consumers
# ---------------------------------------------------------------------------


def _source_item(target: str | None) -> SourcePageReviewItem:
    return SourcePageReviewItem(
        item_id="it-1",
        recommendation="defer",  # defer avoids the include-needs-evidence invariant
        action="create",
        reason="test",
        confidence=0.5,
        source_importance=0.5,
        reader_salience=0.5,
        target_kb_path=target,
    )


def test_resolver_blocks_malicious_permanent_target():
    """Even a manifest that sets target_kb_path into KB/Permanent/ is refused."""
    from shared.promotion_targets import resolve_target_path

    item = _source_item("KB/Permanent/惡意注入.md")
    with pytest.raises(PermanentWriteViolation):
        resolve_target_path(item)


def test_resolver_still_resolves_normal_targets():
    """Regression: guard must not break legitimate Wiki targets."""
    from shared.promotion_targets import resolve_target_path

    assert resolve_target_path(_source_item("KB/Wiki/Sources/foo.md")) == "KB/Wiki/Sources/foo.md"
    assert resolve_target_path(_source_item(None)) is None
    # Concept with no canonical match → None (unchanged behavior)
    concept = ConceptReviewItem(
        item_id="c-1",
        recommendation="defer",
        action="create_global_concept",
        reason="test",
        confidence=0.5,
        source_importance=0.5,
        reader_salience=0.5,
        concept_label="overtraining",
    )
    assert resolve_target_path(concept) is None


def test_both_consumers_route_through_guarded_resolver():
    """gate + commit both import resolve_target_path (single chokepoint).

    Guarding inside resolve_target_path therefore covers both write surfaces —
    asserted structurally so a future refactor that bypasses the chokepoint trips
    this test (panel Codex §1: resolver is called from gate:120 AND commit:379).
    """
    import shared.promotion_acceptance_gate as gate
    import shared.promotion_commit as commit

    assert "resolve_target_path" in inspect.getsource(gate)
    assert "resolve_target_path" in inspect.getsource(commit)


# ---------------------------------------------------------------------------
# update_permanent_bookkeeping — whitelist + body preservation
# ---------------------------------------------------------------------------

_CARD = """---
type: permanent
status: seedling
author: human
created: 2026-06-11
modified: 2026-06-11
source_refs: []
aliases: []
---

好系統讓你不需要意志力——把消耗意志力的環節先自動化。

支持:: [[把摩擦降到零]] — 因為摩擦是意志力的稅
"""


def _write_card(tmp_path):
    card = tmp_path / "KB" / "Permanent" / "好系統讓你不需要意志力.md"
    card.parent.mkdir(parents=True)
    card.write_text(_CARD, encoding="utf-8")
    return card


def test_bookkeeping_allows_whitelist_keys_and_preserves_body(tmp_path):
    card = _write_card(tmp_path)
    update_permanent_bookkeeping(
        card,
        {"source_refs": ["[[Literature/卡片盒筆記]] ^cfi-6-26-106"], "modified": "2026-06-12"},
    )
    text = card.read_text(encoding="utf-8")
    # body verbatim preserved (正文 + typed edge line untouched)
    assert "好系統讓你不需要意志力——把消耗意志力的環節先自動化。" in text
    assert "支持:: [[把摩擦降到零]] — 因為摩擦是意志力的稅" in text
    # bookkeeping applied
    assert "2026-06-12" in text
    assert "cfi-6-26-106" in text
    # status / author untouched
    assert "status: seedling" in text
    assert "author: human" in text


@pytest.mark.parametrize("bad_key", ["status", "type", "author", "body", "正文"])
def test_bookkeeping_rejects_non_whitelist_keys(tmp_path, bad_key):
    card = _write_card(tmp_path)
    with pytest.raises(PermanentWriteViolation):
        update_permanent_bookkeeping(card, {bad_key: "x"})


def test_bookkeeping_rejects_non_permanent_path(tmp_path):
    other = tmp_path / "KB" / "Wiki" / "Concepts" / "foo.md"
    other.parent.mkdir(parents=True)
    other.write_text("---\ntype: concept\n---\nbody", encoding="utf-8")
    with pytest.raises(PermanentWriteViolation):
        update_permanent_bookkeeping(other, {"modified": "2026-06-12"})


def test_whitelist_constant_is_exactly_three_keys():
    assert ALLOWED_BOOKKEEPING_KEYS == frozenset({"source_refs", "modified", "aliases"})
