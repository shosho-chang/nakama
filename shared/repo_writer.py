"""Repo-rooted report writer (ADR-028 §4 / Phase A PR-A2).

Mirrors the ``shared.obsidian_writer`` API but writes under the **repo
root** (``E:\\nakama`` on dev / ``/home/nakama`` in prod) rather than
the Obsidian vault. Used by Franky for its weekly digest + dev-backlog
artifacts which moved from the vault (``AgentReports/franky/``) to the
repo (``data/agent_reports/franky/``) per ADR-028 §4 E1 escalation
decision (Franky outputs fail the "vault is 修修's brain" test).

API parity with ``obsidian_writer`` is intentional so callers can swap
import paths during the Phase A→B transition.

Boundaries:
- Never writes outside repo root (defensive containment check).
- Never reads vault paths — only repo paths.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent


def repo_path(*parts: str) -> Path:
    """Join repo-relative parts to an absolute path under the repo root."""
    return _REPO_ROOT.joinpath(*parts)


def write_repo_page(
    relative_path: str,
    frontmatter: dict,
    body: str,
    *,
    overwrite: bool = True,
) -> Path:
    """Write a YAML-frontmatter markdown file under the repo root.

    Args:
        relative_path: path relative to repo root, e.g.
            ``data/agent_reports/franky/weekly/2026-W15.md``.
        frontmatter: YAML frontmatter dict.
        body: markdown body (no frontmatter delimiters).
        overwrite: when False, return the existing path without writing
            if it already exists.

    Returns:
        Absolute path to the written file.
    """
    target = _resolve_under_repo(relative_path)
    if target.exists() and not overwrite:
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    frontmatter.setdefault("created", str(date.today()))
    frontmatter["updated"] = str(date.today())

    fm_str = yaml.dump(
        frontmatter,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=10**9,
    ).strip()

    content = f"---\n{fm_str}\n---\n\n{body}\n"
    target.write_text(content, encoding="utf-8")
    return target


def read_repo_page(relative_path: str) -> str | None:
    """Read a markdown file under the repo root. Returns ``None`` if missing."""
    target = _resolve_under_repo(relative_path)
    if not target.exists():
        return None
    return target.read_text(encoding="utf-8")


def list_repo_files(relative_dir: str, suffix: str = ".md") -> list[Path]:
    """List files under a repo-relative directory matching ``suffix``."""
    target = _resolve_under_repo(relative_dir)
    if not target.exists():
        return []
    return sorted(target.glob(f"*{suffix}"))


def _resolve_under_repo(relative_path: str) -> Path:
    """Resolve ``relative_path`` under repo root, refusing escapes."""
    if not relative_path or not relative_path.strip():
        raise ValueError(f"relative_path must be non-empty: {relative_path!r}")
    candidate = (_REPO_ROOT / relative_path).resolve(strict=False)
    root_resolved = _REPO_ROOT.resolve(strict=False)
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(
            f"relative_path={relative_path!r} escapes repo_root={str(_REPO_ROOT)!r}"
        ) from exc
    return candidate
