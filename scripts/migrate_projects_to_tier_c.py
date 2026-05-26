"""Migrate vault Projects to Tier C γ frontmatter (ADR-031 D9).

Idempotent migration:

- Validates ``type: project`` (skips agent-workspace / unknown types, e.g.
  `Brook 風格訓練.md` which is ``type: agent-workspace``).
- Lifts legacy ``## 👄 One Sentence About This Video`` (or ``## 👄 Episode Sentence``)
  H2 prose → frontmatter ``one_sentence``.
- Strips legacy ``%%KW-START%%`` / ``%%KW-END%%`` AND ``%%agent-zoro-keywords-start/end%%``
  Zoro keyword research blobs from md body (Web UI takes over; vault md becomes prose-only).
- Strips legacy dataviewjs and base blocks (``## 🎯 對應 OKR`` / ``## ✅ Tasks`` /
  ``## 📊 番茄統計`` / ``## 📚 KB Research`` / ``## 🗝️ Keyword Research & Title Ideas``)
  — Web replicates each.
- Recomputes ``pomodoro.{est_total, actual_total}`` from a TaskNotes scan.
- Preserves prose human-only sections verbatim (``## 專案描述`` / ``## 預期成果`` /
  ``## Draft Outline`` / ``## Script / Outline`` / ``## 專案筆記`` / ``## Show Notes``).
- Does **not** change ``content_type`` (legacy `research` projects keep their type;
  ADR-031 D9.c slim was reverted after Codex panel audit verified vault has
  research-type projects in active use).

NFC-normalizes every filename comparison (ADR-031 D9.a; macOS NFD vs Win/Linux NFC).

Usage:

    # Dry run (no writes; emits diff per file)
    python -m scripts.migrate_projects_to_tier_c --dry-run

    # Apply to single project
    python -m scripts.migrate_projects_to_tier_c --write --target 肌酸的妙用

    # Apply to all (skips non-project type files)
    python -m scripts.migrate_projects_to_tier_c --write
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
import unicodedata
from dataclasses import dataclass

# Windows cp1252 stdout can't encode CJK — flip to UTF-8 at module load.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import yaml

# Allow ``python -m scripts.X`` AND ``python scripts/X.py``
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.config import get_vault_path  # noqa: E402

PROJECTS_DIR = "Projects"
TASKS_DIR = "TaskNotes/Tasks"
POMODORO_MINUTES = 25

# ── Body-strip regex ─────────────────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)

# H2 sections to strip entirely (heading + body up to next H2 or EOF).
# These are the Bases / dataviewjs blocks that Tier C lifts to Web.
SECTIONS_TO_STRIP = (
    "🎯 對應 OKR",
    "✅ Tasks",
    "📊 番茄統計",
    "📚 KB Research",
    "🗝️ Keyword Research & Title Ideas",
)

# Legacy Pattern A markers (Zoro keyword research blob) — both old + new families.
_LEGACY_MARKER_BLOCKS = (
    re.compile(r"%%KW-START%%[\s\S]*?%%KW-END%%\n?", re.MULTILINE),
    re.compile(
        r"%%agent-zoro-keywords-start%%[\s\S]*?%%agent-zoro-keywords-end%%\n?", re.MULTILINE
    ),
)

# One-sentence H2 (youtube + podcast variants)
_ONE_SENTENCE_HEADINGS = ("👄 One Sentence About This Video", "👄 Episode Sentence")


@dataclass
class MigrationResult:
    slug: str
    path: Path
    skipped: bool
    skip_reason: str
    diff: str
    new_content: str


def _normalize(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def _split_section(body: str, heading_text: str) -> tuple[Optional[int], Optional[int]]:
    """Return (start_idx, end_idx) of an H2 section in body, or (None, None)."""
    pat = re.compile(
        r"(^##\s+" + re.escape(heading_text) + r"\s*(?:<!--[^\n]*-->)?\s*\n)",
        re.MULTILINE,
    )
    m = pat.search(body)
    if not m:
        return None, None
    start = m.start()
    # Next H2 or EOF
    after = body[m.end() :]
    next_h2 = re.search(r"\n##\s+", after, re.MULTILINE)
    end = m.end() + next_h2.start() if next_h2 else len(body)
    return start, end


def _extract_one_sentence(body: str) -> tuple[Optional[str], str]:
    """If body has `## 👄 One Sentence About This Video` (or Episode Sentence) H2,
    return (prose_content, new_body_with_section_removed).
    """
    for heading in _ONE_SENTENCE_HEADINGS:
        start, end = _split_section(body, heading)
        if start is None:
            continue
        pat = re.compile(
            r"^##\s+" + re.escape(heading) + r"\s*(?:<!--[^\n]*-->)?\s*\n",
            re.MULTILINE,
        )
        m = pat.search(body, start)
        if not m:
            continue
        section_body = body[m.end() : end]
        prose = section_body.strip()
        # Remove section heading + body. Preserve newline boundary.
        new_body = (body[:start] + body[end:]).rstrip() + "\n"
        return (prose or None), new_body
    return None, body


def _strip_sections(body: str) -> str:
    """Remove H2 sections whose heading text matches SECTIONS_TO_STRIP."""
    for heading in SECTIONS_TO_STRIP:
        start, end = _split_section(body, heading)
        while start is not None:
            body = body[:start] + body[end:]
            start, end = _split_section(body, heading)
    return body


def _strip_legacy_markers(body: str) -> str:
    for pat in _LEGACY_MARKER_BLOCKS:
        body = pat.sub("", body)
    return body


def _strip_separator(body: str) -> str:
    """Remove stray `---` horizontal-rule separators that sit alone after section strips."""
    # Match `---` on its own line surrounded by blank lines
    body = re.sub(r"\n\n---\n\n", "\n\n", body)
    body = re.sub(r"^---\n", "", body, count=1)  # leading
    return body


def _compute_pomodoro_rollup(vault: Path, slug: str) -> tuple[int, int]:
    """Scan TaskNotes/Tasks/ for tasks belonging to this slug; return (est_total, actual_total)."""
    tasks_dir = vault / TASKS_DIR
    if not tasks_dir.exists():
        return 0, 0
    prefix = f"{_normalize(slug)} - "
    est = 0
    total_min = 0.0
    for p in tasks_dir.iterdir():
        name = _normalize(p.name)
        if not (name.startswith(prefix) and name.endswith(".md")):
            continue
        try:
            raw = p.read_text(encoding="utf-8")
        except OSError:
            continue
        m = _FRONTMATTER_RE.match(raw)
        if not m:
            continue
        try:
            fm = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            continue
        if not isinstance(fm, dict):
            continue
        e = fm.get("預估🍅", 0)
        try:
            est += int(e) if e is not None else 0
        except (TypeError, ValueError):
            pass
        time_entries = fm.get("timeEntries") or []
        if isinstance(time_entries, list):
            for entry in time_entries:
                if not isinstance(entry, dict):
                    continue
                s = entry.get("startTime")
                en = entry.get("endTime")
                if not (s and en):
                    continue
                try:
                    sdt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
                    edt = datetime.fromisoformat(str(en).replace("Z", "+00:00"))
                    total_min += (edt - sdt).total_seconds() / 60
                except ValueError:
                    continue
    return est, int(total_min // POMODORO_MINUTES)


class _BlankNoneDumper(yaml.SafeDumper):
    pass


def _none_as_blank(dumper, _v):
    return dumper.represent_scalar("tag:yaml.org,2002:null", "")


_BlankNoneDumper.add_representer(type(None), _none_as_blank)


def _dump_frontmatter(fm: dict[str, Any]) -> str:
    return yaml.dump(
        fm,
        Dumper=_BlankNoneDumper,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        indent=2,
        width=10**9,
    ).rstrip()


def migrate_file(path: Path, *, vault: Path) -> MigrationResult:
    """Apply the migration plan to a single Projects/*.md file."""
    slug = _normalize(path.stem)
    raw = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        return MigrationResult(
            slug=slug,
            path=path,
            skipped=True,
            skip_reason="no frontmatter",
            diff="",
            new_content="",
        )
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as exc:
        return MigrationResult(
            slug=slug,
            path=path,
            skipped=True,
            skip_reason=f"invalid frontmatter: {exc}",
            diff="",
            new_content="",
        )
    if not isinstance(fm, dict):
        return MigrationResult(
            slug=slug,
            path=path,
            skipped=True,
            skip_reason="frontmatter not a mapping",
            diff="",
            new_content="",
        )
    if fm.get("type") != "project":
        return MigrationResult(
            slug=slug,
            path=path,
            skipped=True,
            skip_reason=f"type={fm.get('type')!r} (not 'project')",
            diff="",
            new_content="",
        )

    # Idempotency probe — if one_sentence already present, skip migration.
    if "one_sentence" in fm and isinstance(fm.get("one_sentence"), str):
        return MigrationResult(
            slug=slug,
            path=path,
            skipped=True,
            skip_reason="already migrated (one_sentence present)",
            diff="",
            new_content="",
        )

    body = m.group(2)

    # 1. Lift one_sentence H2 prose to frontmatter (and remove the section).
    prose, body = _extract_one_sentence(body)

    # 2. Strip dataviewjs / base / legacy markers.
    body = _strip_sections(body)
    body = _strip_legacy_markers(body)
    body = _strip_separator(body)

    # 3. Build γ frontmatter additions.
    fm["one_sentence"] = prose or ""

    if "hook_text" not in fm:
        fm["hook_text"] = ""
    if "title_candidates" not in fm:
        fm["title_candidates"] = []
    if "thumbnail_concept" not in fm:
        fm["thumbnail_concept"] = ""

    est_total, actual_total = _compute_pomodoro_rollup(vault, slug)
    fm["pomodoro"] = {"est_total": est_total, "actual_total": actual_total}

    # 4. Re-serialize.
    fm_str = _dump_frontmatter(fm)
    body_clean = body.lstrip("\n").rstrip() + "\n"
    new_content = f"---\n{fm_str}\n---\n\n{body_clean}"
    if not new_content.endswith("\n"):
        new_content += "\n"

    diff = "".join(
        difflib.unified_diff(
            raw.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=str(path) + " (before)",
            tofile=str(path) + " (after)",
            n=2,
        )
    )

    return MigrationResult(
        slug=slug,
        path=path,
        skipped=False,
        skip_reason="",
        diff=diff,
        new_content=new_content,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate vault Projects to Tier C γ (ADR-031)")
    parser.add_argument("--dry-run", action="store_true", help="print diff only (default)")
    parser.add_argument("--write", action="store_true", help="apply changes")
    parser.add_argument("--target", type=str, help="single project slug (filename without .md)")
    parser.add_argument(
        "--vault",
        type=str,
        default=None,
        help="override vault path (default: shared.config.get_vault_path)",
    )
    args = parser.parse_args()

    vault = Path(args.vault) if args.vault else get_vault_path()
    proj_dir = vault / PROJECTS_DIR
    if not proj_dir.exists():
        print(f"vault Projects/ directory not found: {proj_dir}", file=sys.stderr)
        return 1

    # Backup dir
    ts = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d-%H%M%S")
    backup_dir = Path(".tmp") / f"project-migration-backup-{ts}"

    targets: list[Path] = []
    if args.target:
        path = proj_dir / f"{_normalize(args.target)}.md"
        if not path.exists():
            print(f"target not found: {path}", file=sys.stderr)
            return 1
        targets = [path]
    else:
        targets = sorted(
            (p for p in proj_dir.iterdir() if p.is_file() and p.suffix == ".md"),
            key=lambda p: _normalize(p.name),
        )

    n_applied = 0
    n_skipped = 0
    n_failed = 0
    for path in targets:
        res = migrate_file(path, vault=vault)
        if res.skipped:
            print(f"SKIP {res.slug}: {res.skip_reason}")
            n_skipped += 1
            continue

        print(f"\n=== {res.slug} ===")
        print(res.diff)

        if args.write:
            try:
                backup_dir.mkdir(parents=True, exist_ok=True)
                backup_path = backup_dir / path.name
                backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
                path.write_text(res.new_content, encoding="utf-8")
                print(f"  → APPLIED (backup: {backup_path})")
                n_applied += 1
            except OSError as exc:
                print(f"  → FAILED: {exc}", file=sys.stderr)
                n_failed += 1
        else:
            print("  → DRY RUN (use --write to apply)")
            n_applied += 1

    print(
        f"\nSummary: {n_applied} {'applied' if args.write else 'pending'}, "
        f"{n_skipped} skipped, {n_failed} failed"
    )
    return 0 if n_failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
