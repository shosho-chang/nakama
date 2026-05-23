"""Vault layout audit — verify vault state matches ``docs/VAULT-LAYOUT.md`` claims.

Per ADR-028 §11, this audit runs four dimensions:

1. **folder_diff** — top-level folders declared in §2 vs actual ``vault_root.iterdir()``.
   Catches forgotten cleanup (e.g. ``Files/`` lingering) and accidental writes outside
   declared scope.

2. **code_path_diff** — code-asserted vault paths (``Path("KB/...")``, ``"Inbox/..."``
   literals across ``agents/`` / ``shared/`` / ``gateway/`` / ``thousand_sunny/``) cross-
   referenced against §3 producer/consumer matrix. Catches code writing to paths the
   doc forgot to declare.

3. **marker_violations** — ``Projects/*.md`` collab pages: agent markers
   (``%%agent-{name}-{section}-{start|end}%%``) must come in balanced pairs, every
   ``(name, section)`` must be registered in §4 Pattern A, and no agent marker may sit
   inside a heading flagged ``<!-- vault:human-only-section -->``.

4. **drift_status** — §7 known-drift entries: for entries whose programmatic check is
   feasible, evaluate the check and flag if the recorded status (``[待修]`` / ``[已修]``
   / ``[已接受]``) no longer matches reality.

CLI::

    python -m scripts.vault_layout_audit             # markdown report to stdout
    python -m scripts.vault_layout_audit --json      # JSON for programmatic use
    python -m scripts.vault_layout_audit --append-to <path>  # markdown append to file

Exit codes::

    0 — audit ran (any severity findings); 1 — could not run; 2 — error-severity findings present

Per ADR-028 §11 β, this runs monthly via Franky weekly digest; output appended to
``data/agent_reports/franky/weekly/{period}.md`` under ``## Vault Audit``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuditFinding:
    """One drift discovered by the audit."""

    dimension: str  # "folder_diff" | "code_path_diff" | "marker_violation" | "drift_status"
    severity: str  # "info" | "warn" | "error"
    path: str
    detail: str


@dataclass
class AuditReport:
    findings: list[AuditFinding] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(f.severity == "error" for f in self.findings)

    @property
    def error_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "error")

    @property
    def warn_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "warn")

    def to_markdown(self) -> str:
        if not self.findings:
            return "## Vault Audit\n\n_No drift detected._\n"
        lines: list[str] = ["## Vault Audit", ""]
        info_count = len(self.findings) - self.error_count - self.warn_count
        lines.append(
            f"_{len(self.findings)} finding(s): {self.error_count} error, "
            f"{self.warn_count} warn, {info_count} info._"
        )
        lines.append("")
        by_dim: dict[str, list[AuditFinding]] = {}
        for f in self.findings:
            by_dim.setdefault(f.dimension, []).append(f)
        for dim in ("folder_diff", "code_path_diff", "marker_violation", "drift_status"):
            fs = by_dim.get(dim, [])
            if not fs:
                continue
            lines.append(f"### {dim}")
            lines.append("")
            for f in fs:
                lines.append(f"- [{f.severity}] `{f.path}` — {f.detail}")
            lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _nfc(s: str) -> str:
    """Normalize Unicode to NFC (per VAULT-LAYOUT.md §7 D-unicode-norm)."""
    return unicodedata.normalize("NFC", s)


# Top-level entries that legitimately exist in the vault but are NOT declared in
# §2 (Obsidian / OS / dev-tool dotfiles + desktop.ini). Not findings.
_VAULT_TOPLEVEL_IGNORE = frozenset(
    {
        ".DS_Store",
        ".claude",
        ".obsidian",
        ".pandoc",
        ".promotion-manifests",
        ".stfolder",
        ".stignore",
        ".vscode",
        "desktop.ini",
    }
)

# Top-level folders/files §2 explicitly declares present in the vault.
_DECLARED_TOPLEVEL = frozenset(
    {
        "CLAUDE.md",
        "Journals",
        "OKRs",
        "Projects",
        "TaskNotes",
        "Dashboards",
        "Inbox",
        "KB",
        "Attachments",
        "AgentOutputs",
        "Templates",
        "Scripts",
    }
)

# Per §2 "Notable absences" — these top-level folders must NOT exist post-Phase B.
_NOTABLE_ABSENCES = frozenset(
    {
        "Schemas",
        "Files",
        "Nami",
        "AgentBriefs",
        "AgentReports",
        "Case Studies",
        "Incidents",
    }
)


_NOTABLE_ABSENCES_HINT = {
    "Files": "ADR-028 §8 migration target",
    "Nami": "ADR-028 §4 → AgentOutputs/nami/",
    "AgentBriefs": "ADR-028 §4 → AgentOutputs/nami/briefs/",
    "AgentReports": "ADR-028 §4 → data/agent_reports/franky/ (repo)",
    "Schemas": "ADR-028 §3 deleted (orphan)",
    "Case Studies": "ADR-028 §9 → docs/case-studies/ (repo)",
    "Incidents": "ADR-028 §9 → docs/incidents/ (repo)",
}


# ---------------------------------------------------------------------------
# Dimension 1 — folder_diff
# ---------------------------------------------------------------------------


def audit_folder_diff(vault_root: Path, layout_doc: Path) -> list[AuditFinding]:
    """Compare actual vault top-level against §2 declared set + notable absences.

    Walks one level only — does not recurse. Hidden dotfiles + desktop.ini are
    ignored. Notable absences (Files/, Schemas/, etc.) found in vault = error.
    Anything else undeclared = warn. Anything declared but missing = warn.
    """
    if not vault_root.exists():
        return [
            AuditFinding(
                dimension="folder_diff",
                severity="error",
                path=str(vault_root),
                detail="vault root does not exist",
            )
        ]

    actual = {
        _nfc(p.name) for p in vault_root.iterdir() if _nfc(p.name) not in _VAULT_TOPLEVEL_IGNORE
    }
    findings: list[AuditFinding] = []

    for name in sorted(actual & _NOTABLE_ABSENCES):
        hint = _NOTABLE_ABSENCES_HINT.get(name, "see §2 Notable absences")
        findings.append(
            AuditFinding(
                dimension="folder_diff",
                severity="error",
                path=name,
                detail=f"§2 declares absent ({hint}); found in vault",
            )
        )

    undeclared = sorted(actual - _DECLARED_TOPLEVEL - _NOTABLE_ABSENCES)
    for name in undeclared:
        findings.append(
            AuditFinding(
                dimension="folder_diff",
                severity="warn",
                path=name,
                detail="present in vault but not declared in §2 (update VAULT-LAYOUT.md or remove)",
            )
        )

    missing = sorted(_DECLARED_TOPLEVEL - actual)
    for name in missing:
        findings.append(
            AuditFinding(
                dimension="folder_diff",
                severity="warn",
                path=name,
                detail="declared in §2 but missing from vault",
            )
        )

    return findings


# ---------------------------------------------------------------------------
# Dimension 2 — code_path_diff
# ---------------------------------------------------------------------------


# Vault path string literal (single or double quoted) starting with a known
# top-level prefix. Captures everything up to the closing quote.
# Caveat: string-literal scan only — runtime-constructed paths (``vault / "KB"
# / pmid``) won't be detected, but those are constrained by
# ``shared.config.get_vault_path()`` already.
_CODE_PATH_PREFIXES = (
    "KB",
    "Inbox",
    "Projects",
    "Journals",
    "OKRs",
    "TaskNotes",
    "Dashboards",
    "Attachments",
    "AgentOutputs",
    "Templates",
    "Scripts",
    # Notable absences — should not appear in code, but we scan for them.
    "AgentReports",
    "AgentBriefs",
    "Files",
    "Nami",
    "Schemas",
)
_CODE_PATH_RE = re.compile(
    r"""["'](?P<path>(?:""" + "|".join(_CODE_PATH_PREFIXES) + r""")/[^"'\s]{1,200})["']"""
)


def _read_matrix_paths(layout_doc: Path) -> set[str]:
    """Extract path prefixes claimed by §3 producer/consumer matrix.

    Returns the set of normalized path patterns (placeholders collapsed to
    ``{}``) drawn from each table row's first-column code-quoted path.
    Skips ``(repo) ...`` rows.
    """
    if not layout_doc.exists():
        return set()
    content = layout_doc.read_text(encoding="utf-8")
    m = re.search(r"## 3\.[^\n]*\n(.*?)(?=\n## )", content, re.S)
    if not m:
        return set()
    section = m.group(1)
    paths: set[str] = set()
    for row_match in re.finditer(r"^\|\s*`([^`]+)`", section, re.M):
        raw = row_match.group(1).strip()
        if raw.startswith("(repo)"):
            continue
        normalized = re.sub(r"\{[^}]+\}", "{}", raw)
        paths.add(normalized)
    return paths


def _path_covered_by_matrix(literal_path: str, matrix_paths: set[str]) -> bool:
    """Whether a literal path matches any matrix entry (``{}`` = one segment)."""
    for entry in matrix_paths:
        pattern = re.escape(entry).replace(r"\{\}", r"[^/]+")
        if re.match(pattern + r"(/|$)", literal_path):
            return True
    return False


def audit_code_path_diff(repo_root: Path, layout_doc: Path) -> list[AuditFinding]:
    """Find vault path literals in code not covered by §3 matrix."""
    findings: list[AuditFinding] = []
    matrix_paths = _read_matrix_paths(layout_doc)
    if not matrix_paths:
        return [
            AuditFinding(
                dimension="code_path_diff",
                severity="error",
                path=str(layout_doc),
                detail="could not parse §3 producer/consumer matrix",
            )
        ]

    scan_dirs = ["agents", "shared", "gateway", "thousand_sunny"]
    skip_path_parts = {"tests", "__pycache__"}

    seen: dict[str, list[str]] = {}
    for top in scan_dirs:
        root = repo_root / top
        if not root.exists():
            continue
        for py in root.rglob("*.py"):
            if any(part in skip_path_parts for part in py.parts):
                continue
            try:
                text = py.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                for m in _CODE_PATH_RE.finditer(line):
                    p = _nfc(m.group("path"))
                    seen.setdefault(p, []).append(f"{py.relative_to(repo_root)}:{lineno}")

    for literal in sorted(seen):
        first_seg = literal.split("/")[0]
        if first_seg in _NOTABLE_ABSENCES:
            hint = _NOTABLE_ABSENCES_HINT.get(first_seg, "see §2")
            findings.append(
                AuditFinding(
                    dimension="code_path_diff",
                    severity="error",
                    path=literal,
                    detail=(
                        f"code references deleted folder ({hint}); first match: {seen[literal][0]}"
                    ),
                )
            )
            continue
        if not _path_covered_by_matrix(literal, matrix_paths):
            findings.append(
                AuditFinding(
                    dimension="code_path_diff",
                    severity="warn",
                    path=literal,
                    detail=(
                        f"code references path not in §3 matrix; first match: {seen[literal][0]}"
                    ),
                )
            )

    return findings


# ---------------------------------------------------------------------------
# Dimension 3 — marker_violations
# ---------------------------------------------------------------------------

_MARKER_RE = re.compile(r"%%agent-([a-z0-9_]+)-([a-z0-9_\-]+)-(start|end)%%", re.I)
_HUMAN_ONLY_RE = re.compile(r"<!--\s*vault:human-only-section\s*-->")
_HEADING_RE = re.compile(r"^(#{1,6})\s")


def _read_registered_markers(layout_doc: Path) -> set[tuple[str, str]]:
    """Parse §4 Pattern A registered markers."""
    if not layout_doc.exists():
        return set()
    content = layout_doc.read_text(encoding="utf-8")
    m = re.search(r"## 4\.[^\n]*\n(.*?)(?=\n## )", content, re.S)
    if not m:
        return set()
    section = m.group(1)
    registered: set[tuple[str, str]] = set()
    for m2 in re.finditer(r"`%%agent-([a-z0-9_]+)-([a-z0-9_\-]+)-(?:start|end)%%", section, re.I):
        registered.add((m2.group(1).lower(), m2.group(2).lower()))
    return registered


def _human_only_section_ranges(text: str) -> list[tuple[int, int]]:
    """Return (start_line_0based, end_line_exclusive) for each human-only section.

    A human-only section starts at a heading line containing
    ``<!-- vault:human-only-section -->`` and ends at the next heading of equal
    or lower depth (or EOF).
    """
    lines = text.splitlines()
    sections: list[tuple[int, int]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        heading = _HEADING_RE.match(line)
        if heading and _HUMAN_ONLY_RE.search(line):
            depth = len(heading.group(1))
            j = i + 1
            while j < len(lines):
                hj = _HEADING_RE.match(lines[j])
                if hj and len(hj.group(1)) <= depth:
                    break
                j += 1
            sections.append((i, j))
            i = j
            continue
        i += 1
    return sections


def audit_marker_violations(vault_root: Path, layout_doc: Path) -> list[AuditFinding]:
    """Audit ``Projects/*.md`` for marker-convention violations."""
    findings: list[AuditFinding] = []
    projects_dir = vault_root / "Projects"
    if not projects_dir.exists():
        return findings

    registered = _read_registered_markers(layout_doc)

    for md in sorted(projects_dir.glob("*.md")):
        try:
            text = md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        rel = f"Projects/{_nfc(md.name)}"

        markers: list[tuple[str, str, str, int]] = []
        for lineno, line in enumerate(text.splitlines(), start=1):
            for m in _MARKER_RE.finditer(line):
                markers.append((m.group(1).lower(), m.group(2).lower(), m.group(3).lower(), lineno))

        # 1) Pair balance: each (agent, section) must have exactly one start + one end.
        by_section: dict[tuple[str, str], dict[str, list[int]]] = {}
        for agent, section, kind, lineno in markers:
            d = by_section.setdefault((agent, section), {"start": [], "end": []})
            d[kind].append(lineno)
        for (agent, section), kinds in by_section.items():
            if len(kinds["start"]) != 1 or len(kinds["end"]) != 1:
                all_lines = kinds["start"] + kinds["end"]
                anchor_lineno = all_lines[0] if all_lines else 0
                findings.append(
                    AuditFinding(
                        dimension="marker_violation",
                        severity="error",
                        path=f"{rel}:{anchor_lineno}",
                        detail=f"marker pair imbalance for ({agent}, {section}): "
                        f"{len(kinds['start'])} start, {len(kinds['end'])} end",
                    )
                )

        # 2) Registry: each unique (agent, section) must be registered in §4.
        for agent, section in by_section:
            if registered and (agent, section) not in registered:
                findings.append(
                    AuditFinding(
                        dimension="marker_violation",
                        severity="warn",
                        path=rel,
                        detail=f"({agent}, {section}) not registered in §4 Pattern A — "
                        f"register or remove",
                    )
                )

        # 3) No marker inside a human-only section.
        ho_ranges = _human_only_section_ranges(text)
        if ho_ranges and markers:
            for agent, section, kind, lineno in markers:
                idx = lineno - 1
                for start, end in ho_ranges:
                    if start <= idx < end:
                        findings.append(
                            AuditFinding(
                                dimension="marker_violation",
                                severity="error",
                                path=f"{rel}:{lineno}",
                                detail=f"agent marker ({agent}, {section}, {kind}) inside "
                                f"human-only section (starts line {start + 1})",
                            )
                        )

    return findings


# ---------------------------------------------------------------------------
# Dimension 4 — drift_status
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DriftEntry:
    name: str
    status: str  # "待修" | "已修" | "已接受"


def _parse_drift_entries(layout_doc: Path) -> list[DriftEntry]:
    """Parse §7 drift entries.

    Heuristic: each entry starts with ``#### {name}`` and the first ``[xxx]``
    backticked token on that header line is the recorded status.
    """
    if not layout_doc.exists():
        return []
    content = layout_doc.read_text(encoding="utf-8")
    m = re.search(r"## 7\.[^\n]*\n(.*?)(?=\n## )", content, re.S)
    if not m:
        return []
    section = m.group(1)
    entries: list[DriftEntry] = []
    for hdr in re.finditer(r"^####\s+([\w\-]+).*?`\[([^\]]+)\]`", section, re.M):
        entries.append(DriftEntry(name=hdr.group(1), status=hdr.group(2).strip()))
    return entries


def _check_promotion_attachments_fixed(repo_root: Path) -> bool | None:
    """D-promotion-attachments: does promotion code do attachment migration?"""
    p = repo_root / "shared" / "promotion_commit.py"
    if not p.exists():
        return None
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return ("attachments" in text.lower()) and (("shutil" in text) or ("move" in text.lower()))


def _check_audit_stub(repo_root: Path) -> bool | None:
    """D-audit-stub: is this script still a stub?

    Sentinel is built at runtime to avoid the audit's own source containing
    the literal we search for (self-reference loop). The stub's docstring
    banner had a specific phrase that the real implementation does not.
    """
    p = repo_root / "scripts" / "vault_layout_audit.py"
    if not p.exists():
        return None
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    sentinel = chr(0x26A0) + chr(0xFE0F) + "  " + "STUB" + " IMPLEMENTATION"
    return sentinel not in text


def _check_files_pending(vault_root: Path) -> bool | None:
    """D-files-pending: ``Files/`` should be absent."""
    return not (vault_root / "Files").exists() if vault_root.exists() else None


def _check_agentoutputs_pending(vault_root: Path) -> bool | None:
    """D-agentoutputs-pending: ``AgentOutputs/`` present, legacy folders absent."""
    if not vault_root.exists():
        return None
    return (
        (vault_root / "AgentOutputs").exists()
        and not (vault_root / "Nami").exists()
        and not (vault_root / "AgentReports").exists()
        and not (vault_root / "AgentBriefs").exists()
    )


def _check_inbox_pending(vault_root: Path) -> bool | None:
    """D-inbox-pending: ``Inbox/web/`` present, ``Inbox/kb/`` absent."""
    if not vault_root.exists():
        return None
    inbox = vault_root / "Inbox"
    return (inbox / "web").exists() and not (inbox / "kb").exists()


_DriftChecker = Callable[[Path, Path], "bool | None"]

# Map of drift entry name → checker. True = fix landed, False = still broken,
# None = can't determine. Entries without a checker are skipped (no audit).
_DRIFT_CHECKERS: dict[str, _DriftChecker] = {
    "D-promotion-attachments": lambda vault, repo: _check_promotion_attachments_fixed(repo),
    "D-audit-stub": lambda vault, repo: _check_audit_stub(repo),
    "D-files-pending": lambda vault, repo: _check_files_pending(vault),
    "D-agentoutputs-pending": lambda vault, repo: _check_agentoutputs_pending(vault),
    "D-inbox-pending": lambda vault, repo: _check_inbox_pending(vault),
}


def audit_drift_status(layout_doc: Path, vault_root: Path, repo_root: Path) -> list[AuditFinding]:
    """Compare each §7 drift entry's recorded status to programmatic reality."""
    findings: list[AuditFinding] = []
    for entry in _parse_drift_entries(layout_doc):
        checker = _DRIFT_CHECKERS.get(entry.name)
        if checker is None:
            continue
        fixed = checker(vault_root, repo_root)
        if fixed is None:
            continue
        if entry.status == "待修" and fixed:
            findings.append(
                AuditFinding(
                    dimension="drift_status",
                    severity="warn",
                    path=entry.name,
                    detail="recorded as [待修] but programmatic check passes — flip to [已修]",
                )
            )
        elif entry.status == "已修" and not fixed:
            findings.append(
                AuditFinding(
                    dimension="drift_status",
                    severity="error",
                    path=entry.name,
                    detail="recorded as [已修] but programmatic check fails — regression?",
                )
            )
    return findings


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def run_audit(vault_root: Path, repo_root: Path, layout_doc: Path) -> AuditReport:
    report = AuditReport()
    report.findings.extend(audit_folder_diff(vault_root, layout_doc))
    report.findings.extend(audit_code_path_diff(repo_root, layout_doc))
    report.findings.extend(audit_marker_violations(vault_root, layout_doc))
    report.findings.extend(audit_drift_status(layout_doc, vault_root, repo_root))
    return report


def _default_vault_root() -> Path:
    try:
        from shared.config import get_vault_path

        return get_vault_path()
    except Exception:
        return Path("E:/Shosho LifeOS")


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit vault layout against docs/VAULT-LAYOUT.md")
    parser.add_argument("--vault-root", type=Path, default=None)
    parser.add_argument("--repo-root", type=Path, default=None)
    parser.add_argument("--layout-doc", type=Path, default=None)
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of markdown")
    parser.add_argument(
        "--append-to",
        type=Path,
        default=None,
        help="Append markdown report to this file (used by Franky cron)",
    )
    parser.add_argument(
        "--exit-on-error",
        action="store_true",
        help="Exit with code 2 if any finding has severity=error",
    )
    args = parser.parse_args(argv)

    vault_root = args.vault_root or _default_vault_root()
    repo_root = args.repo_root or _default_repo_root()
    layout_doc = args.layout_doc or (repo_root / "docs" / "VAULT-LAYOUT.md")

    if not vault_root.exists():
        print(f"vault-root not found: {vault_root}", file=sys.stderr)
        return 1
    if not layout_doc.exists():
        print(f"layout doc not found: {layout_doc}", file=sys.stderr)
        return 1

    report = run_audit(vault_root, repo_root, layout_doc)

    if args.json:
        payload = {"findings": [asdict(f) for f in report.findings]}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        md = report.to_markdown()
        if args.append_to:
            args.append_to.parent.mkdir(parents=True, exist_ok=True)
            with args.append_to.open("a", encoding="utf-8") as fh:
                fh.write("\n" + md)
        else:
            print(md)

    if args.exit_on_error and report.has_errors:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
