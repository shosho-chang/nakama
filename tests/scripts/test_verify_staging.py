"""Tests for scripts.verify_staging aggregate CLI (Stage 1.5)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.verify_staging import _run

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_raw(
    vault_root: Path,
    book_id: str,
    title: str,
    sections: list[tuple[str, str]],
) -> None:
    raw_dir = vault_root / "KB" / "Raw" / "Books"
    raw_dir.mkdir(parents=True, exist_ok=True)
    body = f"# {title}\n\n"
    for section_name, section_text in sections:
        body += f"## {section_name}\n\n{section_text}\n\n"
    content = f"---\nbook_id: {book_id}\ntitle: Test Book\n---\n\n{body}"
    (raw_dir / f"{book_id}.md").write_text(content, encoding="utf-8")


def _write_staged(
    vault_root: Path,
    book_id: str,
    chapter_index: int,
    body: str,
    wikilinks: list[str] | None = None,
    dispatch_log: list[dict] | None = None,
) -> Path:
    staged_dir = vault_root / "KB" / "Wiki.staging" / "Sources" / "Books" / book_id
    staged_dir.mkdir(parents=True, exist_ok=True)
    wl_items = "\n".join(f"  - {wl}" for wl in (wikilinks or []))
    fm = f"---\nwikilinks_introduced:\n{wl_items}\n---\n"
    staged_path = staged_dir / f"ch{chapter_index}.md"
    staged_path.write_text(fm + body, encoding="utf-8")
    if dispatch_log is not None:
        staged_path.with_suffix(".coverage.json").write_text(
            json.dumps({"concept_dispatch_log": dispatch_log}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return staged_path


def _write_concept(vault_root: Path, slug: str, *, definition: str | None = None) -> None:
    concepts_dir = vault_root / "KB" / "Wiki.staging" / "Concepts"
    concepts_dir.mkdir(parents=True, exist_ok=True)
    text = (
        f"# {slug}\n\n"
        "## Definition\n\n"
        f"{definition or 'A concrete definition for the staged concept.'}\n\n"
        "## Core Principles\n\n"
        "Substantive body content for aggregate staging verification.\n"
    )
    (concepts_dir / f"{slug}.md").write_text(text, encoding="utf-8")


def _passing_staged_body(title: str, section: str, content: str) -> str:
    return (
        f"# {title}\n\n"
        f"## {section}\n\n"
        f"{content}\n\n"
        "---\n\n"
        "## Section Concept Maps\n\n"
        f"### {section}\n\n"
        "```mermaid\nflowchart LR\n  A --> B\n```\n\n"
        "## Wikilinks Introduced\n\n"
        "- [[TermA]]\n"
    )


# ---------------------------------------------------------------------------
# test_aggregate_two_chapters_one_fail
# ---------------------------------------------------------------------------


def test_aggregate_two_chapters_one_fail(tmp_path, capsys):
    vault_root = tmp_path / "vault"
    report_dir = tmp_path / "reports"

    # Book1 ch1 — PASS
    _write_raw(vault_root, "book-pass", "Chapter One", [("Section A", "Content A.")])
    _write_staged(
        vault_root,
        "book-pass",
        1,
        _passing_staged_body("Chapter One", "Section A", "Content A."),
        wikilinks=["TermA"],
        dispatch_log=[{"slug": "terma", "term": "TermA", "level": "L2", "action": "create"}],
    )
    _write_concept(vault_root, "terma")

    # Book2 ch1 — FAIL: section anchors mismatch (staged has wrong section order)
    _write_raw(
        vault_root,
        "book-fail",
        "Chapter Two",
        [("Alpha", "Text Alpha."), ("Beta", "Text Beta.")],
    )
    failing_body = "# Chapter Two\n\n## Beta\n\nText Beta.\n\n## Alpha\n\nText Alpha.\n"
    _write_staged(vault_root, "book-fail", 1, failing_body, wikilinks=[], dispatch_log=[])

    exit_code = _run(vault_root, report_dir=report_dir)
    capsys.readouterr()

    assert exit_code == 1

    report_file = next(report_dir.glob("*-staging-verify.json"))
    data = json.loads(report_file.read_text())
    assert data["summary"]["total"] == 2
    assert data["summary"]["failed"] == 1
    assert data["summary"]["passed"] == 1


# ---------------------------------------------------------------------------
# test_aggregate_all_pass
# ---------------------------------------------------------------------------


def test_aggregate_all_pass(tmp_path, capsys):
    vault_root = tmp_path / "vault"
    report_dir = tmp_path / "reports"

    for book_id, section in [("book-a", "Section A"), ("book-b", "Section B")]:
        _write_raw(vault_root, book_id, "Chapter One", [(section, "Content here.")])
        _write_staged(
            vault_root,
            book_id,
            1,
            _passing_staged_body("Chapter One", section, "Content here."),
            wikilinks=["TermA"],
            dispatch_log=[{"slug": "terma", "term": "TermA", "level": "L2", "action": "create"}],
        )
    _write_concept(vault_root, "terma")

    exit_code = _run(vault_root, report_dir=report_dir)
    capsys.readouterr()

    assert exit_code == 0

    report_file = next(report_dir.glob("*-staging-verify.json"))
    data = json.loads(report_file.read_text())
    assert data["summary"]["failed"] == 0
    assert data["summary"]["passed"] == 2


# ---------------------------------------------------------------------------
# test_no_chapters_found
# ---------------------------------------------------------------------------


def test_no_chapters_found(tmp_path, capsys):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()

    exit_code = _run(vault_root)
    err = capsys.readouterr().err

    assert exit_code == 1
    assert "no staged chapters found" in err


def test_aggregate_fails_invalid_staged_concept(tmp_path, capsys):
    vault_root = tmp_path / "vault"
    report_dir = tmp_path / "reports"

    _write_raw(vault_root, "book-a", "Chapter One", [("Section A", "Content here.")])
    _write_staged(
        vault_root,
        "book-a",
        1,
        _passing_staged_body("Chapter One", "Section A", "Content here."),
        wikilinks=["TermA"],
        dispatch_log=[{"slug": "terma", "term": "TermA", "level": "L2", "action": "create"}],
    )
    _write_concept(
        vault_root,
        "terma",
        definition="Bad fallback --- ### keywords ## Section captured as definition.",
    )

    exit_code = _run(vault_root, report_dir=report_dir)
    capsys.readouterr()

    assert exit_code == 1

    report_file = next(report_dir.glob("*-staging-verify.json"))
    data = json.loads(report_file.read_text())
    chapter = data["chapters"][0]
    assert chapter["rules"]["artifact_gate_7"]["pass"] is False
    assert any("C5" in reason for reason in chapter["rules"]["artifact_gate_7"]["reasons"])


def test_aggregate_fails_missing_dispatch_sidecar(tmp_path, capsys):
    vault_root = tmp_path / "vault"
    report_dir = tmp_path / "reports"

    _write_raw(vault_root, "book-a", "Chapter One", [("Section A", "Content here.")])
    _write_staged(
        vault_root,
        "book-a",
        1,
        _passing_staged_body("Chapter One", "Section A", "Content here."),
        wikilinks=["TermA"],
    )
    _write_concept(vault_root, "terma")

    exit_code = _run(vault_root, report_dir=report_dir)
    capsys.readouterr()

    assert exit_code == 1

    report_file = next(report_dir.glob("*-staging-verify.json"))
    data = json.loads(report_file.read_text())
    chapter = data["chapters"][0]
    assert chapter["rules"]["artifact_gate_7"]["pass"] is False
    assert any(
        "missing dispatch sidecar" in reason
        for reason in chapter["rules"]["artifact_gate_7"]["reasons"]
    )
