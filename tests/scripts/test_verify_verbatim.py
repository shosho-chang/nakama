"""Tests for scripts.verify_verbatim single-chapter CLI (Stage 1.5)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.verify_verbatim import _run

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BOOK_ID = "test-book"
_CH = 1


def _write_raw(tmp_path: Path, book_id: str, title: str, sections: list[tuple[str, str]]) -> None:
    raw_dir = tmp_path / "KB" / "Raw" / "Books"
    raw_dir.mkdir(parents=True, exist_ok=True)
    body = f"# {title}\n\n"
    for section_name, section_text in sections:
        body += f"## {section_name}\n\n{section_text}\n\n"
    content = f"---\nbook_id: {book_id}\ntitle: Test Book\n---\n\n{body}"
    (raw_dir / f"{book_id}.md").write_text(content, encoding="utf-8")


def _write_staged(
    tmp_path: Path,
    book_id: str,
    chapter_index: int,
    body: str,
    wikilinks: list[str] | None = None,
) -> None:
    staged_dir = tmp_path / "KB" / "Wiki.staging" / "Sources" / "Books" / book_id
    staged_dir.mkdir(parents=True, exist_ok=True)
    wl_items = "\n".join(f"  - {wl}" for wl in (wikilinks or []))
    fm = f"---\nwikilinks_introduced:\n{wl_items}\n---\n"
    (staged_dir / f"ch{chapter_index}.md").write_text(fm + body, encoding="utf-8")


def _concept_map_wrapper() -> str:
    return (
        "\n\n### Section concept map\n\n"
        "```mermaid\nflowchart LR\n  A --> B\n```\n\n"
        "### Wikilinks introduced\n\n"
        "- [[TermA]]\n"
    )


# ---------------------------------------------------------------------------
# test_pass_path
# ---------------------------------------------------------------------------


def test_pass_path(tmp_path, capsys):
    _write_raw(
        tmp_path,
        _BOOK_ID,
        "Introduction to Metabolism",
        [("Section One", "Paragraph A.")],
    )
    staged_body = (
        "# Introduction to Metabolism\n\n## Section One\n\nParagraph A." + _concept_map_wrapper()
    )
    _write_staged(tmp_path, _BOOK_ID, _CH, staged_body, wikilinks=["TermA"])

    exit_code = _run(tmp_path, _BOOK_ID, _CH)
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "ACCEPTANCE: PASS" in out
    assert "PASS" in out


# ---------------------------------------------------------------------------
# test_fail_verbatim_pct
# ---------------------------------------------------------------------------


def test_fail_verbatim_pct(tmp_path, capsys):
    _write_raw(
        tmp_path,
        _BOOK_ID,
        "Introduction to Metabolism",
        [("Section One", "Paragraph A.")],
    )
    # Staged page is missing "Paragraph A." — verbatim match drops below 0.99
    staged_body = "# Introduction to Metabolism\n\n## Section One\n\n"
    _write_staged(tmp_path, _BOOK_ID, _CH, staged_body, wikilinks=[])

    exit_code = _run(tmp_path, _BOOK_ID, _CH)
    out = capsys.readouterr().out

    assert exit_code == 1
    assert "FAIL" in out
    assert "verbatim_match_pct" in out


# ---------------------------------------------------------------------------
# test_fail_section_mismatch
# ---------------------------------------------------------------------------


def test_fail_section_mismatch(tmp_path, capsys):
    _write_raw(
        tmp_path,
        _BOOK_ID,
        "Introduction to Metabolism",
        [("Section A", "Content A."), ("Section B", "Content B.")],
    )
    # Staged page has sections in reversed order
    staged_body = (
        "# Introduction to Metabolism\n\n## Section B\n\nContent B.\n\n## Section A\n\nContent A.\n"
    )
    _write_staged(tmp_path, _BOOK_ID, _CH, staged_body, wikilinks=[])

    exit_code = _run(tmp_path, _BOOK_ID, _CH)
    out = capsys.readouterr().out

    assert exit_code == 1
    assert "FAIL" in out
    assert "section_anchors_match" in out


# ---------------------------------------------------------------------------
# test_missing_file
# ---------------------------------------------------------------------------


def test_missing_file(tmp_path, capsys):
    # No staged file created — raw file also absent, but staged is checked first
    exit_code = _run(tmp_path, _BOOK_ID, _CH)
    err = capsys.readouterr().err

    assert exit_code == 2
    assert "MISSING" in err
