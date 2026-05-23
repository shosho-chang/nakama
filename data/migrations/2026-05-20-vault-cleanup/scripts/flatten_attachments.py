"""ADR-028 Phase B sub-op 9 (vault half) — flatten KB/Attachments.

Code half landed in PR #656. This moves existing vault files out of the
pubmed/ and inbox/ buckets and rewrites references.

Operations:
  MOVE: KB/Attachments/pubmed/*  → KB/Attachments/*   (96 files + 8 {pmid}/ dirs)
  MOVE: KB/Attachments/inbox/*   → KB/Attachments/*    (1 slug dir)
  REWRITE: every vault .md — "Attachments/pubmed/" → "Attachments/"
                             "Attachments/inbox/"  → "Attachments/"
  RECYCLE: emptied KB/Attachments/{pubmed,inbox}/

Safety: per-file sha256 verify on move; collision-abort; recycle-bin only.
Manifest -> .tmp/flatten-attachments-manifest.json
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

VAULT = Path(r"E:\Shosho LifeOS")
ATT = VAULT / "KB" / "Attachments"
MANIFEST_OUT = Path(r"E:\nakama\.tmp\flatten-attachments-manifest.json")

ops: list[dict] = []


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def move_entry(src: Path, dst: Path) -> None:
    """Move a file or directory; sha256-verify files; abort on collision."""
    if not src.exists():
        return
    if dst.exists():
        ops.append({"op": "ABORT-collision", "src": str(src), "dst": str(dst)})
        print(f"  ABORT-collision: {dst.name} already exists at destination")
        return
    if src.is_file():
        pre = sha256(src)
        shutil.move(str(src), str(dst))
        post = sha256(dst)
        ops.append({
            "op": "MOVE-FILE", "src": str(src.relative_to(VAULT).as_posix()),
            "dst": str(dst.relative_to(VAULT).as_posix()),
            "sha256": pre, "verified": pre == post,
        })
        if pre != post:
            print(f"  ⚠️ SHA MISMATCH: {src.name}")
    else:
        files = sorted(p for p in src.rglob("*") if p.is_file())
        pre = {str(p.relative_to(src)): sha256(p) for p in files}
        shutil.move(str(src), str(dst))
        post = {str(p.relative_to(dst)): sha256(p) for p in sorted(dst.rglob("*")) if p.is_file()}
        ops.append({
            "op": "MOVE-DIR", "src": str(src.relative_to(VAULT).as_posix()),
            "dst": str(dst.relative_to(VAULT).as_posix()),
            "file_count": len(files), "verified": pre == post,
        })
        if pre != post:
            print(f"  ⚠️ TREE SHA MISMATCH: {src.name}/")


def recycle(p: Path) -> None:
    if not p.exists():
        return
    leftover = [str(c.relative_to(p)) for c in p.rglob("*") if c.is_file()]
    if leftover:
        ops.append({"op": "RECYCLE-ABORT", "path": str(p), "leftover": leftover})
        print(f"  RECYCLE-ABORT: {p.name}/ still has files: {leftover}")
        return
    cmd = [
        "powershell", "-Command",
        "Add-Type -AssemblyName Microsoft.VisualBasic; "
        f"[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory("
        f"'{p}', 'OnlyErrorDialogs', 'SendToRecycleBin')",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    ok = r.returncode == 0
    ops.append({"op": "RECYCLE-DIR" if ok else "RECYCLE-FAIL", "path": str(p)})
    print(f"  {'RECYCLE' if ok else 'RECYCLE-FAIL'}: {p.name}/")


def rewrite_refs() -> None:
    """Rewrite Attachments/{pubmed,inbox}/ → Attachments/ in every vault .md."""
    rewritten = 0
    for md in VAULT.rglob("*.md"):
        rel = md.relative_to(VAULT).as_posix()
        if rel.startswith((".obsidian/", ".trash/")):
            continue
        try:
            t = md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        o = t
        t = t.replace("Attachments/pubmed/", "Attachments/")
        t = t.replace("Attachments/inbox/", "Attachments/")
        if t != o:
            tmp = md.with_suffix(md.suffix + ".tmp")
            tmp.write_text(t, encoding="utf-8")
            tmp.replace(md)
            n_pm = o.count("Attachments/pubmed/")
            n_ib = o.count("Attachments/inbox/")
            ops.append({
                "op": "REWRITE-REFS", "file": rel,
                "pubmed_refs": n_pm, "inbox_refs": n_ib,
            })
            rewritten += 1
    print(f"  rewrote refs in {rewritten} markdown files")


def main() -> None:
    print("=" * 64)
    print("ADR-028 Phase B — flatten KB/Attachments (vault half)")
    print("=" * 64)
    if not ATT.exists():
        raise SystemExit(f"KB/Attachments not found: {ATT}")

    # ── Move pubmed/* → KB/Attachments/* ──────────────────────────────────
    print("\n[move pubmed/*]")
    pubmed = ATT / "pubmed"
    if pubmed.exists():
        for entry in sorted(pubmed.iterdir()):
            move_entry(entry, ATT / entry.name)

    # ── Move inbox/* → KB/Attachments/* ───────────────────────────────────
    print("\n[move inbox/*]")
    inbox = ATT / "inbox"
    if inbox.exists():
        for entry in sorted(inbox.iterdir()):
            move_entry(entry, ATT / entry.name)

    # ── Rewrite references across the vault ───────────────────────────────
    print("\n[rewrite refs]")
    rewrite_refs()

    # ── Recycle emptied buckets ───────────────────────────────────────────
    print("\n[recycle]")
    recycle(pubmed)
    recycle(inbox)

    # ── Manifest ──────────────────────────────────────────────────────────
    manifest = {
        "adr": "ADR-028",
        "phase": "B sub-op 9 — flatten KB/Attachments (vault half)",
        "code_pr": 656,
        "executed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "vault": str(VAULT),
        "ops": ops,
    }
    MANIFEST_OUT.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_OUT.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    moves = [o for o in ops if o["op"].startswith("MOVE")]
    rewrites = [o for o in ops if o["op"] == "REWRITE-REFS"]
    aborts = [o for o in ops if "ABORT" in o["op"]]
    mismatches = [o for o in ops if o.get("verified") is False]
    print(f"\nManifest -> {MANIFEST_OUT}")
    print(f"Done. moves={len(moves)} rewrites={len(rewrites)} aborts={len(aborts)}")
    if mismatches:
        print(f"⚠️  {len(mismatches)} SHA MISMATCH — INVESTIGATE")
    elif aborts:
        print(f"⚠️  {len(aborts)} collisions aborted — INVESTIGATE")
    else:
        print("All moves sha256-verified, no collisions.")


if __name__ == "__main__":
    main()
