"""Vault layout audit — verify vault state matches `docs/VAULT-LAYOUT.md` claims.

Skeleton stub per ADR-028 §11. Full implementation lands in Phase 3 PR-10
(PR-Audit-Script-Full). This stub establishes:

- the entry point + CLI surface
- the expected output format (markdown append to franky weekly digest)
- the four audit dimensions (§6 β of VAULT-LAYOUT.md)

Once fleshed out, runs as a monthly Franky cron (see `agents/franky/...`)
and appends findings to ``AgentOutputs/franky/weekly/{period}.md`` under a
"Vault Audit" section.

Usage::

    python -m scripts.vault_layout_audit             # text report to stdout
    python -m scripts.vault_layout_audit --json      # JSON for programmatic use
    python -m scripts.vault_layout_audit --append-to <path>  # append md to file

Exit codes::

    0 — audit ran (regardless of findings)
    1 — audit could not run (vault unreachable / config missing)
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
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

    def to_markdown(self) -> str:
        if not self.findings:
            return "## Vault Audit\n\nNo drift detected.\n"
        lines = ["## Vault Audit", ""]
        by_dim: dict[str, list[AuditFinding]] = {}
        for f in self.findings:
            by_dim.setdefault(f.dimension, []).append(f)
        for dim, fs in by_dim.items():
            lines.append(f"### {dim}")
            lines.append("")
            for f in fs:
                lines.append(f"- [{f.severity}] `{f.path}` — {f.detail}")
            lines.append("")
        return "\n".join(lines)


def audit_folder_diff(vault_root: Path, layout_doc: Path) -> list[AuditFinding]:
    """Compare actual vault folder tree against §2 of VAULT-LAYOUT.md.

    TODO (Phase 3 PR-10):
    - Parse §2 fenced code block from VAULT-LAYOUT.md into a declared-tree set
    - Walk vault_root one level deep (top-level folders only for now)
    - Report orphans (in vault but not declared) and missing (declared but not in vault)
    """
    return []


def audit_code_path_diff(repo_root: Path, layout_doc: Path) -> list[AuditFinding]:
    """Grep code-asserted vault paths against §3 producer/consumer matrix.

    TODO (Phase 3 PR-10):
    - Grep ``agents/``, ``shared/``, ``gateway/``, ``thousand_sunny/`` for
      string literals matching ``Path("KB/...")``, ``"Inbox/..."``, etc.
    - Cross-reference with §3 table entries.
    - Report code-asserted paths missing from matrix and matrix entries with no
      producer code path.
    """
    return []


def audit_marker_violations(vault_root: Path, layout_doc: Path) -> list[AuditFinding]:
    """Audit collab-page marker convention (§4).

    TODO (Phase 3 PR-10):
    - Grep ``Projects/`` for ``%%agent-(\\w+)-([\\w-]+)-start%%`` patterns
    - Assert each (agent, section) pair appears in §4 Pattern A registered list
    - Assert -start has matching -end
    - Assert no marker pair sits inside §4 human-only section heading
    """
    return []


def audit_drift_status(layout_doc: Path) -> list[AuditFinding]:
    """Verify each §7 drift entry's status is still accurate.

    TODO (Phase 3 PR-10):
    - Parse §7 entries (status [待修] / [已接受] / [已修])
    - For each entry, evaluate its specific check:
      D1: ``shared/kb_writer.upsert_concept_page`` called from textbook-ingest Phase B?
      D2: Entity v2 schema landed?
      D3: ``KB/index.md`` coverage check passing?
      D-promotion-attachments: ``shared/promotion_commit.py`` contains attachment logic?
    - Report status mismatches (e.g. [待修] but the code has been fixed).
    """
    return []


def run_audit(vault_root: Path, repo_root: Path, layout_doc: Path) -> AuditReport:
    report = AuditReport()
    report.findings.extend(audit_folder_diff(vault_root, layout_doc))
    report.findings.extend(audit_code_path_diff(repo_root, layout_doc))
    report.findings.extend(audit_marker_violations(vault_root, layout_doc))
    report.findings.extend(audit_drift_status(layout_doc))
    return report


def _default_vault_root() -> Path:
    # Defer to shared.config.get_vault_path() once we wire it; for now a
    # plain default to the Windows location used by the project owner.
    return Path("E:/Shosho LifeOS")


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit vault layout against docs/VAULT-LAYOUT.md")
    parser.add_argument("--vault-root", type=Path, default=_default_vault_root())
    parser.add_argument("--repo-root", type=Path, default=_default_repo_root())
    parser.add_argument(
        "--layout-doc",
        type=Path,
        default=None,
        help="Defaults to <repo-root>/docs/VAULT-LAYOUT.md",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of markdown")
    parser.add_argument(
        "--append-to",
        type=Path,
        default=None,
        help="Append markdown report to this file (used by Franky cron)",
    )
    args = parser.parse_args(argv)

    layout_doc = args.layout_doc or (args.repo_root / "docs" / "VAULT-LAYOUT.md")
    if not args.vault_root.exists():
        print(f"vault-root not found: {args.vault_root}", file=sys.stderr)
        return 1
    if not layout_doc.exists():
        print(f"layout doc not found: {layout_doc}", file=sys.stderr)
        return 1

    report = run_audit(args.vault_root, args.repo_root, layout_doc)

    if args.json:
        import json

        payload = {
            "findings": [
                {"dimension": f.dimension, "severity": f.severity, "path": f.path, "detail": f.detail}
                for f in report.findings
            ]
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        md = report.to_markdown()
        if args.append_to:
            args.append_to.parent.mkdir(parents=True, exist_ok=True)
            with args.append_to.open("a", encoding="utf-8") as fh:
                fh.write("\n" + md)
        else:
            print(md)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
