"""Tests for _run_spot_checks alias map path fix (issue #496)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from types import SimpleNamespace

from scripts.run_s8_batch import (
    ChapterResult,
    _real_chapters_in,
    _renumber_to_real,
    _run_spot_checks,
)


def test_renumber_to_real_aligns_with_preflight_convention():
    """Batch must rewrite chapter_index to real chapter num (1..N).

    Regression: 5/8 P0 batch wrote real ch1 to ch3.md because walker yielded
    2 front-matter entries before chapter 1, leaving real ch1 at
    chapter_index=3. Filenames must match real chapter numbers, matching
    run_s8_preflight._pick_chapter behavior.
    """
    payloads = [
        SimpleNamespace(chapter_index=1, chapter_title="Front Matter"),
        SimpleNamespace(chapter_index=2, chapter_title="Preface"),
        SimpleNamespace(chapter_index=3, chapter_title="1 Energy Sources"),
        SimpleNamespace(chapter_index=4, chapter_title="2 Skeletal Muscle"),
        SimpleNamespace(chapter_index=5, chapter_title="3 Biochemical Concepts"),
    ]
    real = _real_chapters_in(payloads)
    assert [p.chapter_title for p in real] == [
        "1 Energy Sources",
        "2 Skeletal Muscle",
        "3 Biochemical Concepts",
    ]
    _renumber_to_real(real)
    assert [p.chapter_index for p in real] == [1, 2, 3]


def _make_pass_result(vault_root: Path, book_id: str, ch_idx: int) -> ChapterResult:
    src_dir = vault_root / "KB" / "Wiki.staging" / "Sources" / "Books" / book_id
    src_dir.mkdir(parents=True, exist_ok=True)
    src_file = src_dir / f"ch{ch_idx}.md"
    src_file.write_text(
        f"# Chapter {ch_idx}\n\n## Section\n\nText with [[SomeConcept]].\n",
        encoding="utf-8",
    )
    return ChapterResult(
        book_id=book_id,
        real_index=ch_idx,
        chapter_title=f"Chapter {ch_idx}",
        source_page_path=str(src_file),
        status="pass",
        verbatim_match_pct=80.0,
        concepts_extracted=1,
    )


def test_spot_check_reads_alias_from_wiki_staging_root(tmp_path):
    """_run_spot_checks must read alias map from Wiki.staging/_alias_map.md.

    Before the fix the code looked in Wiki.staging/Concepts/alias_map.md which
    never exists, so L1 terms were always counted as unresolved.
    """
    vault = tmp_path / "vault"

    # Write alias map at the CORRECT location (Wiki.staging/_alias_map.md)
    staging_root = vault / "KB" / "Wiki.staging"
    staging_root.mkdir(parents=True)
    (staging_root / "_alias_map.md").write_text(
        "# L1 Alias Map\n\nterm | source\n--- | ---\nSomeConcept | [[ch1]]\n",
        encoding="utf-8",
    )

    # Ensure the old wrong path does NOT exist
    wrong_path = vault / "KB" / "Wiki.staging" / "Concepts" / "alias_map.md"
    assert not wrong_path.exists()

    r = _make_pass_result(vault, "test-book", 1)
    checks = _run_spot_checks([r], vault, n=1)

    assert len(checks) == 1
    item = checks[0]
    assert item["wikilinks_total"] == 1
    assert item["wikilinks_resolved"] == 1, (
        f"SomeConcept is in Wiki.staging/_alias_map.md but was not resolved; "
        f"got {item['wikilinks_resolved']}/{item['wikilinks_total']}"
    )
