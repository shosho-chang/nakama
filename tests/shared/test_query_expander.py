"""Tests for shared.query_expander (ADR-020 S7 — bilingual query expansion + RRF merge)."""

from __future__ import annotations

from pathlib import Path

from shared.query_expander import expand_query, extract_wikilinks, rrf_merge
from shared.reranker import RankedResult

# ---------------------------------------------------------------------------
# extract_wikilinks
# ---------------------------------------------------------------------------


def test_extract_wikilinks_single():
    links = extract_wikilinks("[[腸道菌群]] 的功能是什麼？")
    assert links == ["腸道菌群"]


def test_extract_wikilinks_multiple():
    links = extract_wikilinks("[[ATP]] 和 [[磷酸肌酸]] 的關係")
    assert links == ["ATP", "磷酸肌酸"]


def test_extract_wikilinks_none():
    links = extract_wikilinks("什麼是 ATP？")
    assert links == []


def test_extract_wikilinks_with_alias():
    links = extract_wikilinks("[[腸道菌群|gut flora]] 的分布")
    assert links == ["腸道菌群"]


def test_extract_wikilinks_empty_string():
    links = extract_wikilinks("")
    assert links == []


# ---------------------------------------------------------------------------
# expand_query — no wikilinks
# ---------------------------------------------------------------------------


def test_expand_no_wikilinks_returns_original():
    result = expand_query(
        "什麼是 ATP？", vault_path=Path("/vault"), _read_concept_fn=lambda p: None
    )
    assert result == ["什麼是 ATP？"]


def test_expand_empty_query_returns_original():
    result = expand_query("", vault_path=Path("/vault"), _read_concept_fn=lambda p: None)
    assert result == [""]


# ---------------------------------------------------------------------------
# expand_query — with wikilinks
# ---------------------------------------------------------------------------


def _fake_concept_page(terms: list[str]) -> str:
    terms_yaml = "\n".join(f"  - {t}" for t in terms)
    return f"---\nschema_version: 3\nen_source_terms:\n{terms_yaml}\n---\n\n# 腸道菌群\n"


def test_expand_with_wikilink_adds_en_terms():
    fake = _fake_concept_page(["gut microbiota", "intestinal flora"])
    result = expand_query(
        "[[腸道菌群]] 的功能",
        vault_path=Path("/vault"),
        _read_concept_fn=lambda p: fake,
    )
    assert "[[腸道菌群]] 的功能" in result
    assert "gut microbiota" in result
    assert "intestinal flora" in result


def test_expand_preserves_original_as_first():
    fake = _fake_concept_page(["gut microbiota"])
    result = expand_query(
        "[[腸道菌群]] 研究",
        vault_path=Path("/vault"),
        _read_concept_fn=lambda p: fake,
    )
    assert result[0] == "[[腸道菌群]] 研究"


def test_expand_concept_not_found_returns_original_only():
    result = expand_query(
        "[[unknown_concept]]",
        vault_path=Path("/vault"),
        _read_concept_fn=lambda p: None,
    )
    assert result == ["[[unknown_concept]]"]


def test_expand_concept_no_en_source_terms_returns_original_only():
    no_terms = "---\nschema_version: 2\ntitle: ATP\n---\n"
    result = expand_query(
        "[[ATP]] 的合成",
        vault_path=Path("/vault"),
        _read_concept_fn=lambda p: no_terms,
    )
    assert result == ["[[ATP]] 的合成"]


def test_expand_concept_empty_en_source_terms_returns_original_only():
    empty = "---\nschema_version: 3\nen_source_terms: []\n---\n"
    result = expand_query(
        "[[概念]]",
        vault_path=Path("/vault"),
        _read_concept_fn=lambda p: empty,
    )
    assert result == ["[[概念]]"]


def test_expand_reads_correct_path():
    seen_paths: list[Path] = []

    def capture(p: Path) -> str | None:
        seen_paths.append(p)
        return None

    expand_query(
        "[[腸道菌群]]",
        vault_path=Path("/my/vault"),
        _read_concept_fn=capture,
    )
    assert len(seen_paths) == 1
    assert seen_paths[0] == Path("/my/vault/KB/Wiki/腸道菌群.md")


def test_expand_multiple_wikilinks():
    def fake_read(p: Path) -> str | None:
        if "腸道菌群" in str(p):
            return _fake_concept_page(["gut microbiota"])
        if "ATP" in str(p):
            return _fake_concept_page(["adenosine triphosphate"])
        return None

    result = expand_query(
        "[[腸道菌群]] 與 [[ATP]] 代謝",
        vault_path=Path("/vault"),
        _read_concept_fn=fake_read,
    )
    assert result[0] == "[[腸道菌群]] 與 [[ATP]] 代謝"
    assert "gut microbiota" in result
    assert "adenosine triphosphate" in result


# ---------------------------------------------------------------------------
# rrf_merge
# ---------------------------------------------------------------------------


def _r(chunk_id: str, score: float = 0.5) -> RankedResult:
    return RankedResult(chunk_id=chunk_id, text=f"text {chunk_id}", score=score)


def test_rrf_merge_single_list_preserves_order():
    lst = [_r("c1", 0.9), _r("c2", 0.7), _r("c3", 0.5)]
    merged = rrf_merge([lst], k=60, top_n=3)
    assert [r.chunk_id for r in merged] == ["c1", "c2", "c3"]


def test_rrf_merge_boosts_items_in_multiple_lists():
    list1 = [_r("c1"), _r("c2"), _r("c3")]
    list2 = [_r("c2"), _r("c4"), _r("c5")]
    merged = rrf_merge([list1, list2], k=60, top_n=5)
    assert merged[0].chunk_id == "c2"


def test_rrf_merge_respects_top_n():
    lst = [_r(f"c{i}") for i in range(10)]
    merged = rrf_merge([lst], k=60, top_n=5)
    assert len(merged) == 5


def test_rrf_merge_fewer_results_than_top_n():
    lst = [_r("c1"), _r("c2")]
    merged = rrf_merge([lst], k=60, top_n=10)
    assert len(merged) == 2


def test_rrf_merge_empty_input():
    merged = rrf_merge([], k=60, top_n=5)
    assert merged == []


def test_rrf_merge_empty_lists_inside():
    merged = rrf_merge([[], []], k=60, top_n=5)
    assert merged == []


def test_rrf_merge_score_is_rrf():
    lst = [_r("c1")]
    merged = rrf_merge([lst], k=60, top_n=1)
    expected = 1.0 / (60 + 1)
    assert abs(merged[0].score - expected) < 1e-9


def test_rrf_merge_deduplicates():
    list1 = [_r("c1"), _r("c2")]
    list2 = [_r("c1"), _r("c3")]
    merged = rrf_merge([list1, list2], k=60, top_n=10)
    ids = [r.chunk_id for r in merged]
    assert len(ids) == len(set(ids))


def test_rrf_merge_returns_ranked_results():
    lst = [_r("c1")]
    merged = rrf_merge([lst], k=60, top_n=1)
    assert isinstance(merged[0], RankedResult)


def test_rrf_merge_k_affects_scores():
    lst = [_r("c1")]
    m1 = rrf_merge([lst], k=1, top_n=1)
    m60 = rrf_merge([lst], k=60, top_n=1)
    assert m1[0].score > m60[0].score
