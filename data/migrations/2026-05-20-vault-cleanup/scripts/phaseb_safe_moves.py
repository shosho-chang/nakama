"""ADR-028 Phase B — safe moves (vault file relocations with no code coupling).

User-authorized 2026-05-20: "把沒做完的做一做吧" + prior "依照你的判斷".

Scope = sub-ops whose target code already points to the new path (Nami/Franky
via PR-A2) or which have zero code dependency. EXCLUDES:
- KB/Attachments/{inbox,pubmed} flatten — live code writes there (pubmed_*.py,
  image_fetcher.py) — needs a paired code-change PR
- Files/ Category A+B migration — needs ref rewrites in source markdowns +
  Journals exception — separate careful PR

Operations:
  VAULT-INTERNAL:
    Nami/Notes/**                          → AgentOutputs/nami/notes/**
    KB/Wiki/Outputs/2026-04-27-seo-acceptance/ → AgentOutputs/brook/seo-audit/2026-04-27/
  VAULT → REPO (data/, gitignored — runtime location):
    AgentReports/franky/2026-W15.md        → data/agent_reports/franky/weekly/2026-W15.md
    AgentReports/dev-backlog.md            → data/agent_reports/franky/dev-backlog.md
  VAULT → REPO WORKTREE (docs/, will be PR'd):
    KB/Wiki/Outputs/style-extractor-prd-draft.md → docs/prds/
    Case Studies/<f>.md                    → docs/case-studies/
    Incidents/2026/04/<f>.md               → docs/incidents/2026/04/
  RECYCLE (after moves, empty folders):
    Nami/  AgentReports/  AgentBriefs/  KB/Wiki/{Outputs,Syntheses,Comparisons}/
    Case Studies/  Incidents/  Schemas/

Manifest -> docs/migrations/2026-05-20-phase-b-vault-cleanup.json (in worktree, PR'd)
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Console may be cp1252 (Windows default) — CJK filenames in log lines would
# crash print(). Force UTF-8 with replacement so logging never aborts a move.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

VAULT = Path(r"E:\Shosho LifeOS")
REPO = Path(r"E:\nakama")              # runtime repo (Franky reads data/ here)
WORKTREE = Path(r"E:\nakama-phaseb")   # PR worktree for docs/ files

ops: list[dict] = []


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def move(src: Path, dst: Path, kind: str) -> None:
    if not src.exists():
        if dst.exists():
            entry = {"op": "ALREADY-AT-DEST", "kind": kind, "src": str(src), "dst": str(dst)}
            if dst.is_file():
                entry["sha256"] = sha256(dst)
            ops.append(entry)
            print(f"  ALREADY-AT-DEST [{kind}]: {dst}")
        else:
            ops.append({"op": "SKIP-missing", "kind": kind, "src": str(src), "dst": str(dst)})
            print(f"  SKIP-missing: {src}")
        return
    if dst.exists():
        ops.append({"op": "SKIP-collision", "kind": kind, "src": str(src), "dst": str(dst)})
        print(f"  SKIP-collision: {dst} exists")
        return
    pre = sha256(src) if src.is_file() else None
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    post = sha256(dst) if dst.is_file() else None
    entry = {"op": "MOVE", "kind": kind, "src": str(src), "dst": str(dst)}
    if pre is not None:
        entry["sha256"] = pre
        entry["verified"] = pre == post
    ops.append(entry)
    flag = "" if pre is None or pre == post else "  ⚠️ SHA MISMATCH"
    print(f"  MOVE [{kind}]: {src.name} -> {dst}{flag}")


def move_tree(src: Path, dst: Path, kind: str) -> None:
    """Move a directory tree, recording per-file sha256."""
    if not src.exists():
        if dst.exists():
            fc = sum(1 for p in dst.rglob("*") if p.is_file())
            ops.append({
                "op": "ALREADY-AT-DEST", "kind": kind,
                "src": str(src), "dst": str(dst), "file_count": fc,
            })
            print(f"  ALREADY-AT-DEST [{kind}]: {dst} ({fc} files)")
        else:
            ops.append({"op": "SKIP-missing", "kind": kind, "src": str(src), "dst": str(dst)})
            print(f"  SKIP-missing: {src}")
        return
    if dst.exists():
        ops.append({"op": "SKIP-collision", "kind": kind, "src": str(src), "dst": str(dst)})
        print(f"  SKIP-collision: {dst} exists")
        return
    files = sorted(p for p in src.rglob("*") if p.is_file())
    pre = {str(p.relative_to(src)): sha256(p) for p in files}
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    post = {
        str(p.relative_to(dst)): sha256(p)
        for p in sorted(dst.rglob("*"))
        if p.is_file()
    }
    ops.append({
        "op": "MOVE-TREE",
        "kind": kind,
        "src": str(src),
        "dst": str(dst),
        "file_count": len(files),
        "verified": pre == post,
    })
    flag = "" if pre == post else "  ⚠️ TREE SHA MISMATCH"
    print(f"  MOVE-TREE [{kind}]: {src.name}/ ({len(files)} files) -> {dst}{flag}")


def recycle(p: Path) -> None:
    if not p.exists():
        ops.append({"op": "SKIP-missing-recycle", "path": str(p)})
        return
    leftover = [c.name for c in p.rglob("*") if c.is_file()]
    if leftover:
        ops.append({"op": "RECYCLE-ABORT-nonempty", "path": str(p), "leftover": leftover})
        print(f"  RECYCLE-ABORT: {p} still has files: {leftover}")
        return
    cmd = [
        "powershell", "-Command",
        "Add-Type -AssemblyName Microsoft.VisualBasic; "
        f"[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory("
        f"'{p}', 'OnlyErrorDialogs', 'SendToRecycleBin')",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode == 0:
        ops.append({"op": "RECYCLE-DIR", "path": str(p)})
        print(f"  RECYCLE: {p}")
    else:
        ops.append({"op": "RECYCLE-FAIL", "path": str(p), "error": r.stderr.strip()})
        print(f"  RECYCLE-FAIL: {p} — {r.stderr.strip()}")


def main() -> None:
    print("=" * 64)
    print("ADR-028 Phase B — safe moves")
    print("=" * 64)

    # ── Vault-internal ────────────────────────────────────────────────────
    print("\n[vault-internal]")
    nami_notes = VAULT / "Nami" / "Notes"
    if nami_notes.exists():
        for f in sorted(nami_notes.rglob("*.md")):
            rel = f.relative_to(nami_notes)
            move(f, VAULT / "AgentOutputs" / "nami" / "notes" / rel, "nami-notes")
    move_tree(
        VAULT / "KB" / "Wiki" / "Outputs" / "2026-04-27-seo-acceptance",
        VAULT / "AgentOutputs" / "brook" / "seo-audit" / "2026-04-27",
        "brook-seo-audit",
    )

    # ── Vault → repo data/ (gitignored runtime location) ──────────────────
    print("\n[vault->repo data/]")
    move(
        VAULT / "AgentReports" / "franky" / "2026-W15.md",
        REPO / "data" / "agent_reports" / "franky" / "weekly" / "2026-W15.md",
        "franky-weekly",
    )
    move(
        VAULT / "AgentReports" / "dev-backlog.md",
        REPO / "data" / "agent_reports" / "franky" / "dev-backlog.md",
        "franky-dev-backlog",
    )

    # ── Vault → repo worktree docs/ (will be PR'd) ────────────────────────
    print("\n[vault->repo docs/ (PR)]")
    move(
        VAULT / "KB" / "Wiki" / "Outputs" / "style-extractor-prd-draft.md",
        WORKTREE / "docs" / "prds" / "style-extractor-prd-draft.md",
        "prd",
    )
    cs_dir = VAULT / "Case Studies"
    if cs_dir.exists():
        for f in sorted(cs_dir.glob("*.md")):
            # Normalize filename: spaces → hyphens
            norm = f.name.replace(" ", "-")
            move(f, WORKTREE / "docs" / "case-studies" / norm, "case-study")
    inc_dir = VAULT / "Incidents"
    if inc_dir.exists():
        for f in sorted(inc_dir.rglob("*.md")):
            rel = f.relative_to(inc_dir)
            move(f, WORKTREE / "docs" / "incidents" / rel, "incident")

    # Also fold in the qa file moved earlier (currently untracked in main repo)
    qa_main = REPO / "docs" / "qa" / "2026-05-04-line2-bugs.md"
    qa_wt = WORKTREE / "docs" / "qa" / "2026-05-04-line2-bugs.md"
    if qa_main.exists() and not qa_wt.exists():
        qa_wt.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(qa_main, qa_wt)
        ops.append({"op": "COPY-into-PR", "src": str(qa_main), "dst": str(qa_wt)})
        print(f"  COPY-into-PR: {qa_main.name}")

    # ── Recycle empty folders ─────────────────────────────────────────────
    print("\n[recycle]")
    for rel in (
        "Nami",
        "AgentReports",
        "AgentBriefs",
        "KB/Wiki/Outputs",
        "KB/Wiki/Syntheses",
        "KB/Wiki/Comparisons",
        "Case Studies",
        "Incidents",
        "Schemas",
    ):
        recycle(VAULT / rel)

    # ── Manifest ──────────────────────────────────────────────────────────
    manifest = {
        "adr": "ADR-028",
        "phase": "B (safe moves)",
        "executed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "vault": str(VAULT),
        "repo": str(REPO),
        "excluded": [
            "KB/Attachments/{inbox,pubmed} flatten — needs paired code-change PR",
            "Files/ Category A+B migration — needs ref rewrites + Journals exception",
        ],
        "ops": ops,
    }
    out = WORKTREE / "docs" / "migrations" / "2026-05-20-phase-b-vault-cleanup.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nManifest -> {out}")
    print(f"Done. {len(ops)} ops.")
    mismatches = [o for o in ops if o.get("verified") is False]
    if mismatches:
        print(f"⚠️  {len(mismatches)} SHA MISMATCH — INVESTIGATE")
    else:
        print("All sha256 verified.")


if __name__ == "__main__":
    main()
