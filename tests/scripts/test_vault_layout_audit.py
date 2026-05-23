"""Tests for ``scripts/vault_layout_audit.py``."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.vault_layout_audit import (
    AuditFinding,
    AuditReport,
    _human_only_section_ranges,
    _parse_drift_entries,
    _path_covered_by_matrix,
    _read_matrix_paths,
    _read_registered_markers,
    audit_code_path_diff,
    audit_drift_status,
    audit_folder_diff,
    audit_marker_violations,
    run_audit,
)

# ---------------------------------------------------------------------------
# Fixture: a minimal VAULT-LAYOUT.md that exercises §2 / §3 / §4 / §7 parsers
# ---------------------------------------------------------------------------


_FIXTURE_LAYOUT = """\
# Vault Layout — Fixture

## 1. Conceptual model

placeholder

## 2. Top-level folder map

```
E:\\Shosho LifeOS\\
├── CLAUDE.md
├── Journals/
├── KB/
└── Projects/
```

**Notable absences**:
- `Files/` — see §8
- `Nami/` — see §4

## 3. Producer / Consumer matrix

| Path | Tier | Producer | Consumer |
|---|---|---|---|
| `KB/Wiki/Sources/{slug}.md` | A | x | y |
| `Projects/{title}.md` | C | bootstrap | none |
| `Inbox/web/*.md` | A | News Coo | Robin |
| (repo) `data/agent_reports/` | A | x | y |

## 4. Marker convention

### Pattern A

| Section | Marker pair |
|---|---|
| Keywords | `%%agent-zoro-keywords-start%% / -end%%` |

## 5. Lifecycle

placeholder

## 6. Audit

placeholder

## 7. Known drift

### Pre-Phase 3

#### D-files-pending — Files/ not yet migrated `[待修]`

resolution: Phase 3 PR-B1

#### D-promotion-attachments — broken `[待修]` → `[已修]` post-PR-A1

resolution detail

#### D-audit-stub — stub `[待修]` → `[已修]` post-PR-C1

resolution detail

#### D2 — Entity v1 frozen `[已接受]`

accepted as wontfix

## 8. Files migration

placeholder
"""


@pytest.fixture
def layout_doc(tmp_path: Path) -> Path:
    p = tmp_path / "VAULT-LAYOUT.md"
    p.write_text(_FIXTURE_LAYOUT, encoding="utf-8")
    return p


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    v = tmp_path / "vault"
    v.mkdir()
    (v / "CLAUDE.md").write_text("# vault claude")
    (v / "Journals").mkdir()
    (v / "KB").mkdir()
    (v / "Projects").mkdir()
    return v


# ---------------------------------------------------------------------------
# AuditReport.to_markdown
# ---------------------------------------------------------------------------


def test_to_markdown_empty():
    report = AuditReport()
    md = report.to_markdown()
    assert "Vault Audit" in md
    # Italic-wrapped per the markdown render contract; substring match avoids
    # coupling to the underscore wrapper.
    assert "No drift detected" in md


def test_to_markdown_groups_by_dimension():
    report = AuditReport(
        findings=[
            AuditFinding("folder_diff", "error", "Files", "bad"),
            AuditFinding("folder_diff", "warn", "Foo", "unknown"),
            AuditFinding("drift_status", "warn", "D-x", "flip"),
        ]
    )
    md = report.to_markdown()
    assert "3 finding(s): 1 error, 2 warn, 0 info" in md
    assert "### folder_diff" in md
    assert "### drift_status" in md
    # folder_diff appears before drift_status (canonical order)
    assert md.index("### folder_diff") < md.index("### drift_status")


def test_has_errors():
    report = AuditReport(findings=[AuditFinding("folder_diff", "warn", "x", "y")])
    assert not report.has_errors
    report.findings.append(AuditFinding("folder_diff", "error", "x", "y"))
    assert report.has_errors


# ---------------------------------------------------------------------------
# Dimension 1 — folder_diff
# ---------------------------------------------------------------------------


def test_folder_diff_clean_vault(vault_root: Path, layout_doc: Path):
    findings = audit_folder_diff(vault_root, layout_doc)
    # CLAUDE.md, Journals, KB, Projects are declared in _DECLARED_TOPLEVEL.
    # Missing-from-fixture: OKRs, TaskNotes, Dashboards, Inbox, Attachments,
    # AgentOutputs, Templates, Scripts → 8 missing warns.
    assert all(f.severity == "warn" for f in findings)
    assert {f.path for f in findings} == {
        "OKRs",
        "TaskNotes",
        "Dashboards",
        "Inbox",
        "Attachments",
        "AgentOutputs",
        "Templates",
        "Scripts",
    }


def test_folder_diff_flags_notable_absences(vault_root: Path, layout_doc: Path):
    (vault_root / "Files").mkdir()
    (vault_root / "Schemas").mkdir()
    findings = audit_folder_diff(vault_root, layout_doc)
    errors = [f for f in findings if f.severity == "error"]
    assert {f.path for f in errors} == {"Files", "Schemas"}


def test_folder_diff_ignores_obsidian_dotfiles(vault_root: Path, layout_doc: Path):
    (vault_root / ".obsidian").mkdir()
    (vault_root / ".stfolder").mkdir()
    (vault_root / "desktop.ini").touch()
    findings = audit_folder_diff(vault_root, layout_doc)
    paths = {f.path for f in findings}
    assert ".obsidian" not in paths
    assert ".stfolder" not in paths
    assert "desktop.ini" not in paths


def test_folder_diff_warns_undeclared(vault_root: Path, layout_doc: Path):
    (vault_root / "RandomFolder").mkdir()
    findings = audit_folder_diff(vault_root, layout_doc)
    warns = [f for f in findings if f.path == "RandomFolder"]
    assert len(warns) == 1
    assert warns[0].severity == "warn"


def test_folder_diff_missing_vault(tmp_path: Path, layout_doc: Path):
    missing = tmp_path / "nonexistent"
    findings = audit_folder_diff(missing, layout_doc)
    assert len(findings) == 1
    assert findings[0].severity == "error"
    assert "does not exist" in findings[0].detail


# ---------------------------------------------------------------------------
# Dimension 2 — code_path_diff
# ---------------------------------------------------------------------------


def test_read_matrix_paths(layout_doc: Path):
    paths = _read_matrix_paths(layout_doc)
    assert "KB/Wiki/Sources/{}.md" in paths
    assert "Projects/{}.md" in paths
    assert "Inbox/web/*.md" in paths
    # (repo)-prefixed rows are skipped (audit covers vault paths only)
    assert not any("data/agent_reports" in p for p in paths)


def test_path_covered_by_matrix():
    matrix = {"KB/Wiki/Sources/{}.md", "Inbox/web/{}"}
    assert _path_covered_by_matrix("KB/Wiki/Sources/foo.md", matrix)
    assert _path_covered_by_matrix("Inbox/web/anything", matrix)
    assert not _path_covered_by_matrix("KB/Wiki/Concepts/foo.md", matrix)
    # {} matches one segment only — "foo/bar" should not match Inbox/web/{}
    # because "foo/bar" has a slash. (Behavior: prefix-with-trailing-slash is
    # OK; deeper paths under matrix entry are considered covered.)
    assert _path_covered_by_matrix("Inbox/web/foo/bar", matrix)


def test_path_covered_by_matrix_glob_star():
    """Literal ``*`` in matrix entries (e.g. ``Inbox/web/*.md``) acts as a
    one-segment wildcard, not a regex-escaped literal. Real §3 uses this
    form for News Coo Inbox writes."""
    matrix = {"Inbox/web/*.md"}
    assert _path_covered_by_matrix("Inbox/web/foo.md", matrix)
    assert _path_covered_by_matrix("Inbox/web/slug-123.md", matrix)
    # Wrong extension or wrong path → not covered
    assert not _path_covered_by_matrix("Inbox/web/foo.txt", matrix)
    assert not _path_covered_by_matrix("KB/Raw/foo.md", matrix)


def test_path_covered_by_matrix_concrete_prefix():
    """Bare-prefix literals (``"KB/Wiki/Concepts"``) are covered when a
    matrix row has them as its concrete prefix before any placeholder."""
    matrix = {"KB/Wiki/Concepts/{}.md", "KB/Annotations/{}.md"}
    assert _path_covered_by_matrix("KB/Wiki/Concepts", matrix)
    assert _path_covered_by_matrix("KB/Annotations", matrix)
    # Trailing slash on the literal is ignored.
    assert _path_covered_by_matrix("KB/Wiki/Concepts/", matrix)
    assert _path_covered_by_matrix("KB/Annotations/", matrix)
    # Deeper-than-prefix without explicit row → still uncovered, audit
    # remains useful for catching undocumented writers.
    assert not _path_covered_by_matrix("KB/Wiki/Concepts/foo/bar.md", matrix)


def test_path_covered_by_matrix_normalizes_literal_placeholders():
    """Python f-string placeholders in literals (``"KB/Wiki/Sources/{slug}/whole.md"``)
    are normalized the same way matrix rows are, so writer-template literals
    can match the matrix row that documents them."""
    matrix = {"KB/Wiki/Sources/{}/whole.md"}
    assert _path_covered_by_matrix("KB/Wiki/Sources/{slug}/whole.md", matrix)
    assert _path_covered_by_matrix("KB/Wiki/Sources/{book_id}/whole.md", matrix)
    # Different filename → not covered (need a different row)
    assert not _path_covered_by_matrix("KB/Wiki/Sources/{slug}/index.md", matrix)


def test_code_path_diff_flags_notable_absences(tmp_path: Path, layout_doc: Path):
    repo = tmp_path / "repo"
    (repo / "agents").mkdir(parents=True)
    bad_py = repo / "agents" / "x.py"
    bad_py.write_text('PATH = "Files/old-image.png"\n')
    findings = audit_code_path_diff(repo, layout_doc)
    errors = [f for f in findings if f.severity == "error"]
    assert any("Files/old-image.png" in f.path for f in errors)


def test_code_path_diff_flags_undeclared_warns(tmp_path: Path, layout_doc: Path):
    repo = tmp_path / "repo"
    (repo / "shared").mkdir(parents=True)
    (repo / "shared" / "y.py").write_text(
        'WEIRD = "KB/Wiki/Comparisons/foo.md"\nOK = "KB/Wiki/Sources/foo.md"\n'
    )
    findings = audit_code_path_diff(repo, layout_doc)
    warn_paths = {f.path for f in findings if f.severity == "warn"}
    assert "KB/Wiki/Comparisons/foo.md" in warn_paths
    # KB/Wiki/Sources/foo.md is covered by matrix → no finding
    assert "KB/Wiki/Sources/foo.md" not in warn_paths


def test_code_path_diff_skips_tests_dir(tmp_path: Path, layout_doc: Path):
    repo = tmp_path / "repo"
    (repo / "agents" / "tests").mkdir(parents=True)
    (repo / "agents" / "tests" / "t.py").write_text('SYN = "Files/test.png"\n')
    findings = audit_code_path_diff(repo, layout_doc)
    assert not findings  # tests/ excluded


# ---------------------------------------------------------------------------
# Dimension 3 — marker_violations
# ---------------------------------------------------------------------------


def test_read_registered_markers(layout_doc: Path):
    registered = _read_registered_markers(layout_doc)
    assert ("zoro", "keywords") in registered


def test_human_only_section_ranges():
    text = "\n".join(
        [
            "# Title",
            "",
            "## A <!-- vault:human-only-section -->",
            "human content",
            "",
            "## B",
            "agent content",
        ]
    )
    ranges = _human_only_section_ranges(text)
    assert len(ranges) == 1
    start, end = ranges[0]
    assert text.splitlines()[start].startswith("## A")
    # ends at "## B" line (exclusive)
    assert text.splitlines()[end].startswith("## B")


def test_marker_violations_balanced(vault_root: Path, layout_doc: Path):
    (vault_root / "Projects").mkdir(exist_ok=True)
    p = vault_root / "Projects" / "good.md"
    p.write_text(
        "## Keywords\n%%agent-zoro-keywords-start%%\ncontent\n%%agent-zoro-keywords-end%%\n"
    )
    findings = audit_marker_violations(vault_root, layout_doc)
    assert findings == []


def test_marker_violations_imbalanced(vault_root: Path, layout_doc: Path):
    (vault_root / "Projects").mkdir(exist_ok=True)
    p = vault_root / "Projects" / "bad.md"
    p.write_text("%%agent-zoro-keywords-start%%\ncontent without end\n")
    findings = audit_marker_violations(vault_root, layout_doc)
    assert len(findings) == 1
    assert findings[0].severity == "error"
    assert "1 start, 0 end" in findings[0].detail


def test_marker_violations_unregistered_section(vault_root: Path, layout_doc: Path):
    (vault_root / "Projects").mkdir(exist_ok=True)
    p = vault_root / "Projects" / "exotic.md"
    p.write_text("%%agent-foobar-experimental-start%%\nx\n%%agent-foobar-experimental-end%%\n")
    findings = audit_marker_violations(vault_root, layout_doc)
    warns = [f for f in findings if f.severity == "warn"]
    assert any("not registered in §4 Pattern A" in f.detail for f in warns)


def test_marker_violations_inside_human_only(vault_root: Path, layout_doc: Path):
    (vault_root / "Projects").mkdir(exist_ok=True)
    p = vault_root / "Projects" / "trap.md"
    p.write_text(
        "\n".join(
            [
                "# Title",
                "## 專案描述 <!-- vault:human-only-section -->",
                "%%agent-zoro-keywords-start%%",
                "agent invaded",
                "%%agent-zoro-keywords-end%%",
                "## Other",
                "",
            ]
        )
    )
    findings = audit_marker_violations(vault_root, layout_doc)
    errors = [f for f in findings if f.severity == "error"]
    assert any("human-only section" in f.detail for f in errors)


# ---------------------------------------------------------------------------
# Dimension 4 — drift_status
# ---------------------------------------------------------------------------


def test_parse_drift_entries(layout_doc: Path):
    entries = _parse_drift_entries(layout_doc)
    names = {e.name for e in entries}
    assert names == {"D-files-pending", "D-promotion-attachments", "D-audit-stub", "D2"}
    by_name = {e.name: e.status for e in entries}
    assert by_name["D-files-pending"] == "待修"
    assert by_name["D-promotion-attachments"] == "待修"  # FIRST [xxx] token
    assert by_name["D2"] == "已接受"


def test_drift_status_flip_signal(vault_root: Path, layout_doc: Path, tmp_path: Path):
    """When D-files-pending recorded as [待修] but Files/ is absent → warn flip."""
    repo = tmp_path / "repo"
    repo.mkdir()
    # Files/ is absent in fixture vault → checker returns True (fixed).
    findings = audit_drift_status(layout_doc, vault_root, repo)
    flip_warns = [f for f in findings if f.path == "D-files-pending"]
    assert len(flip_warns) == 1
    assert "flip to [已修]" in flip_warns[0].detail


def test_drift_status_regression_signal(vault_root: Path, tmp_path: Path):
    """If layout says [已修] but check fails → error (regression)."""
    layout = tmp_path / "layout.md"
    layout.write_text(
        """\
## 2. Top-level folder map

```
└── KB/
```

## 7. Known drift

#### D-files-pending — Files/ migrated `[已修]`

ok

## 8. End
"""
    )
    (vault_root / "Files").mkdir()  # regression — Files/ came back
    repo = tmp_path / "repo"
    repo.mkdir()
    findings = audit_drift_status(layout, vault_root, repo)
    errors = [f for f in findings if f.severity == "error"]
    assert any("regression" in f.detail for f in errors)


# ---------------------------------------------------------------------------
# run_audit end-to-end
# ---------------------------------------------------------------------------


def test_run_audit_smoke(vault_root: Path, layout_doc: Path, tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    report = run_audit(vault_root, repo, layout_doc)
    # The fixture vault is clean (no Files/, no Nami/) but missing 8 declared
    # folders — so we expect 8 warn folder_diff + some drift_status flips.
    # Just assert run completes and returns a report.
    assert isinstance(report, AuditReport)
    md = report.to_markdown()
    assert "Vault Audit" in md
