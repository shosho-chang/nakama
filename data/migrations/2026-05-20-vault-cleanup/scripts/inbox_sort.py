"""ADR-028 Phase B (Inbox portion only) — sort live vault Inbox into canonical layout.

User-authorized 2026-05-20: "Inbox 裡面的東西都是我手動丟過去的，你就把它依照該放的位置放好就好".

Operations (atomic, per-file, with manifest):
- Create Inbox/{books, web, papers, snapshots}/
- For each Inbox/attachments/{slug}/, use shared.attachment_migration to move →
  KB/Attachments/{slug}/ + rewrite refs in matching Inbox/{slug}.md (PR-A1 contract)
- Sort files by extension/role:
    .epub                  → Inbox/books/
    .mhtml                 → Inbox/snapshots/
    .md (Inbox root)       → Inbox/web/
    Inbox/kb/*.md          → Inbox/web/
    qa-line2-bugs-2026-05-04.md → REPO:docs/qa/2026-05-04-line2-bugs.md (dev artifact)
- After sorting, remove empty Inbox/{kb, attachments}/ via recycle bin

NOT yet handled (Phase B PR-B1 owns):
- Robin config still points to Inbox/kb (until A2-like flip)
- News Coo FSA root re-pick

Manifest: .tmp/inbox-sort-manifest.json
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Path to nakama repo (for importing shared.attachment_migration)
REPO = Path(r"E:\nakama")
sys.path.insert(0, str(REPO))

from shared.attachment_migration import migrate_slug_attachments  # noqa: E402

VAULT = Path(r"E:\Shosho LifeOS")
INBOX = VAULT / "Inbox"
NEW_BOOKS = INBOX / "books"
NEW_WEB = INBOX / "web"
NEW_PAPERS = INBOX / "papers"
NEW_SNAPSHOTS = INBOX / "snapshots"

QA_FILE_VAULT = INBOX / "qa-line2-bugs-2026-05-04.md"
QA_FILE_REPO = REPO / "docs" / "qa" / "2026-05-04-line2-bugs.md"

MANIFEST_OUT = Path(r"E:\nakama\.tmp\inbox-sort-manifest.json")

ops: list[dict] = []


def log(op: str, src: str, dst: str, **extra) -> None:
    entry = {"op": op, "src": src, "dst": dst, **extra}
    ops.append(entry)
    print(f"  {op}: {src} -> {dst}")


def safe_move(src: Path, dst: Path) -> None:
    """Move file; refuse to overwrite (manifest records collision)."""
    if dst.exists():
        log("SKIP-collision", str(src), str(dst), reason="destination exists")
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    log("MOVE", str(src), str(dst))


def recycle_dir(p: Path) -> None:
    cmd = [
        "powershell",
        "-Command",
        f"Add-Type -AssemblyName Microsoft.VisualBasic; "
        f"[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory("
        f"'{p}', 'OnlyErrorDialogs', 'SendToRecycleBin')",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        log("RECYCLE-DIR", str(p), "recycle-bin")
    else:
        log("RECYCLE-DIR-FAIL", str(p), "recycle-bin", error=result.stderr.strip())


def main() -> None:
    print("=" * 60)
    print("ADR-028 Inbox sort — read-write on vault")
    print(f"Vault: {VAULT}")
    print("=" * 60)
    if not VAULT.exists():
        raise SystemExit(f"Vault not found: {VAULT}")

    # Step 1 — create canonical sub-folders
    for d in (NEW_BOOKS, NEW_WEB, NEW_PAPERS, NEW_SNAPSHOTS):
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            log("MKDIR", "-", str(d))

    # Step 2 — migrate Inbox/attachments/{slug}/ → KB/Attachments/{slug}/
    # using PR-A1's exact contract. Rewrite refs in matching Inbox/{slug}.md.
    att_dir = INBOX / "attachments"
    if att_dir.exists():
        for slug_dir in sorted(att_dir.iterdir()):
            if not slug_dir.is_dir():
                continue
            slug = slug_dir.name
            # Find matching .md (could be in Inbox/ root or Inbox/kb/)
            candidates = [INBOX / f"{slug}.md", INBOX / "kb" / f"{slug}.md"]
            rewrite_targets = [c for c in candidates if c.exists()]
            print(f"[attachments] slug={slug}, rewrite_in={[str(c) for c in rewrite_targets]}")
            result = migrate_slug_attachments(
                slug=slug,
                inbox_dir=INBOX,
                vault_root=VAULT,
                rewrite_in_files=rewrite_targets,
            )
            log(
                "MIGRATE-ATTACHMENTS",
                f"Inbox/attachments/{slug}/",
                f"KB/Attachments/{slug}/",
                moved=len(result.moved_files),
                skipped=len(result.skipped_files),
                rewritten_md=result.rewritten_markdown,
            )

    # Step 3 — sort files
    # 3a. QA file → repo
    if QA_FILE_VAULT.exists():
        QA_FILE_REPO.parent.mkdir(parents=True, exist_ok=True)
        safe_move(QA_FILE_VAULT, QA_FILE_REPO)

    # 3b. Inbox root: .epub → books, .mhtml → snapshots, .md → web
    for p in sorted(INBOX.iterdir()):
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext == ".epub":
            safe_move(p, NEW_BOOKS / p.name)
        elif ext == ".mhtml":
            safe_move(p, NEW_SNAPSHOTS / p.name)
        elif ext == ".md":
            safe_move(p, NEW_WEB / p.name)
        else:
            log("SKIP-unknown-ext", str(p), "-", reason=f"ext={ext!r} not in {{.epub,.mhtml,.md}}")

    # 3c. Inbox/kb/*.md → Inbox/web/
    kb_dir = INBOX / "kb"
    if kb_dir.exists():
        for p in sorted(kb_dir.iterdir()):
            if p.is_file() and p.suffix.lower() == ".md":
                safe_move(p, NEW_WEB / p.name)
            elif p.is_dir():
                log("SKIP-kb-subdir", str(p), "-", reason="unexpected dir under Inbox/kb")

    # Step 4 — recycle empty Inbox/{kb, attachments}/
    for d in (kb_dir, att_dir):
        if d.exists() and not any(d.iterdir()):
            recycle_dir(d)
        elif d.exists():
            log("KEEP-non-empty", str(d), "-", remaining=[c.name for c in d.iterdir()])

    # Persist manifest
    MANIFEST_OUT.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_OUT.write_text(
        json.dumps(
            {
                "executed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "vault": str(VAULT),
                "ops": ops,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print()
    print("=" * 60)
    print(f"Done. {len(ops)} ops. Manifest: {MANIFEST_OUT}")
    print("=" * 60)


if __name__ == "__main__":
    main()
