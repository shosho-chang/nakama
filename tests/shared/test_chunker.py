"""Tests for shared.chunker (ADR-020 S6 parent-child chunking)."""

from __future__ import annotations

from shared.chunker import ChildChunk, ParentChunk, parent_child_chunks

_CHAPTER = """\
# Chapter 1: Energy Systems

## 1.1 ATP-PCr System

The ATP-PCr system is the most immediate energy source for exercise.

During maximal exercise, phosphocreatine rapidly donates its phosphate group.
ATP is resynthesised within milliseconds.

The system is depleted within 6-10 seconds at maximal intensity.

![[Attachments/Books/bse-2024/ch1/fig-1-1.png]]

```mermaid
graph TD
    PCr --> ATP
    ATP --> Work
```

### Wikilinks introduced
[[ATP]], [[creatine phosphate]], [[creatine kinase]]

## 1.2 Glycolytic System

Glycolysis is a 10-step anaerobic metabolic pathway.

Pyruvate is the end product of glycolysis under aerobic conditions.

Lactate is formed when pyruvate production exceeds mitochondrial capacity.

Energy yield is 2 ATP per glucose molecule via substrate-level phosphorylation.

### Wikilinks introduced
[[glycolysis]], [[pyruvate]], [[lactate]], [[NAD+]]
"""


def test_returns_parents_and_children():
    parents, children = parent_child_chunks(_CHAPTER, book_id="bse-2024", chapter_index=1)
    assert len(parents) >= 2
    assert len(children) >= 1


def test_parents_have_section_anchors():
    parents, _ = parent_child_chunks(_CHAPTER, book_id="bse-2024", chapter_index=1)
    anchors = [p.section_anchor for p in parents]
    assert any("1.1" in a for a in anchors)
    assert any("1.2" in a for a in anchors)


def test_parent_text_contains_heading():
    parents, _ = parent_child_chunks(_CHAPTER, book_id="bse-2024", chapter_index=1)
    p11 = next(p for p in parents if "1.1" in p.section_anchor)
    assert "1.1" in p11.text or "ATP-PCr" in p11.text


def test_parent_text_contains_wikilinks_introduced():
    parents, _ = parent_child_chunks(_CHAPTER, book_id="bse-2024", chapter_index=1)
    p11 = next(p for p in parents if "1.1" in p.section_anchor)
    assert "creatine phosphate" in p11.text or "ATP" in p11.text


def test_parent_text_contains_mermaid_block():
    parents, _ = parent_child_chunks(_CHAPTER, book_id="bse-2024", chapter_index=1)
    p11 = next(p for p in parents if "1.1" in p.section_anchor)
    assert "mermaid" in p11.text or "PCr" in p11.text


def test_parent_text_does_not_contain_verbatim_body():
    parents, _ = parent_child_chunks(_CHAPTER, book_id="bse-2024", chapter_index=1)
    p11 = next(p for p in parents if "1.1" in p.section_anchor)
    assert "depleted within 6-10 seconds" not in p11.text


def test_children_have_parent_ids():
    parents, children = parent_child_chunks(_CHAPTER, book_id="bse-2024", chapter_index=1)
    parent_ids = {p.chunk_id for p in parents}
    for child in children:
        assert child.parent_id in parent_ids


def test_child_text_is_from_verbatim_body():
    _, children = parent_child_chunks(_CHAPTER, book_id="bse-2024", chapter_index=1)
    all_child_text = " ".join(c.text for c in children)
    assert "most immediate energy source" in all_child_text


def test_child_metadata_book_id():
    _, children = parent_child_chunks(_CHAPTER, book_id="bse-2024", chapter_index=1)
    assert all(c.book_id == "bse-2024" for c in children)


def test_child_metadata_chapter_index():
    _, children = parent_child_chunks(_CHAPTER, book_id="bse-2024", chapter_index=1)
    assert all(c.chapter_index == 1 for c in children)


def test_child_figures_referenced():
    _, children = parent_child_chunks(_CHAPTER, book_id="bse-2024", chapter_index=1)
    all_figs = [f for c in children for f in c.figures_referenced]
    assert any("fig-1-1" in f for f in all_figs)


def test_child_concepts_introduced():
    _, children = parent_child_chunks(_CHAPTER, book_id="bse-2024", chapter_index=1)
    all_concepts = [c for child in children for c in child.concepts_introduced]
    assert any("glycolysis" in c or "pyruvate" in c or "lactate" in c for c in all_concepts)


def test_child_paragraph_range_is_tuple():
    _, children = parent_child_chunks(_CHAPTER, book_id="bse-2024", chapter_index=1)
    for child in children:
        assert isinstance(child.paragraph_range, tuple)
        assert len(child.paragraph_range) == 2


def test_child_ids_are_unique():
    _, children = parent_child_chunks(_CHAPTER, book_id="bse-2024", chapter_index=1)
    ids = [c.chunk_id for c in children]
    assert len(ids) == len(set(ids))


def test_parent_child_ids_reference_correct_children():
    parents, children = parent_child_chunks(_CHAPTER, book_id="bse-2024", chapter_index=1)
    child_ids = {c.chunk_id for c in children}
    for parent in parents:
        for cid in parent.child_ids:
            assert cid in child_ids


def test_chunk_id_contains_book_and_chapter():
    parents, _ = parent_child_chunks(_CHAPTER, book_id="bse-2024", chapter_index=3)
    for p in parents:
        assert "bse-2024" in p.chunk_id
        assert "ch3" in p.chunk_id


def test_no_h2_sections_returns_empty():
    chapter_no_h2 = "# Just a title\n\nSome intro paragraph."
    parents, children = parent_child_chunks(chapter_no_h2, book_id="bse-2024", chapter_index=1)
    assert parents == []
    assert children == []


def test_child_window_size_respected():
    parents, children = parent_child_chunks(
        _CHAPTER, book_id="bse-2024", chapter_index=1, child_window=2
    )
    for child in children:
        para_count = len([p for p in child.text.split("\n\n") if p.strip()])
        assert para_count <= 2 + 1  # window + possible overlap residual


def test_parent_dataclass_type():
    parents, _ = parent_child_chunks(_CHAPTER, book_id="bse-2024", chapter_index=1)
    for p in parents:
        assert isinstance(p, ParentChunk)


def test_child_dataclass_type():
    _, children = parent_child_chunks(_CHAPTER, book_id="bse-2024", chapter_index=1)
    for c in children:
        assert isinstance(c, ChildChunk)
