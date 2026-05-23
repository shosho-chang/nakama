"""ADR-028 Phase B Files/ migration — RESUME after PermissionError.

First apply run (migrate_files.py --apply) crashed mid-Category-B on a locked
Journal file. State at crash:
  - 35 Cat A: fully migrated (moved + refs rewritten)
  - 12 Cat B: fully migrated
  -  1 Cat B "Pasted image 20240310160615.png": MOVED to journal-pasted/2024-03/
       but its ref in Journals/Daily/2024-03-09.md was NOT rewritten
  - 22 Cat B: still in Files/
  - stale Journals/Daily/2024-03-09.md.tmp left behind

This script:
  1. Removes stale *.md.tmp files under the vault
  2. Migrates the 22 remaining Files/ entries (move + ref rewrite)
  3. Fixes the one missed rewrite (Pasted image 20240310160615.png)
  4. Recycles Files/ when empty
  5. Writes manifest

Rewrite is retry-hardened (3× with backoff) — if a file is still locked the
script records it and continues rather than aborting the whole run.

PREREQUISITE: close Obsidian + pause Syncthing so vault .md files are unlocked.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

VAULT = Path(r"E:\Shosho LifeOS")
FILES = VAULT / "Files"
MANIFEST_OUT = Path(r"E:\nakama\.tmp\files-migration-resume-manifest.json")

# The one figure moved-but-not-rewritten when the first run crashed.
MISSED = {
    "basename": "Pasted image 20240310160615.png",
    "new_path": "Attachments/journal-pasted/2024-03/Pasted image 20240310160615.png",
    "referrer": "Journals/Daily/2024-03-09.md",
}

ops: list[dict] = []
locked: list[str] = []


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def read_md(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def clean_stale_tmp() -> None:
    print("[clean stale .tmp]")
    for tmp in VAULT.rglob("*.md.tmp"):
        try:
            tmp.unlink()
            ops.append({"op": "CLEAN-TMP", "path": tmp.relative_to(VAULT).as_posix()})
            print(f"  removed {tmp.name}")
        except OSError as e:
            print(f"  could not remove {tmp.name}: {e}")


def write_md_retry(md: Path, new_txt: str) -> bool:
    """Write new_txt to md, retrying on Windows lock. Returns success."""
    for attempt in range(3):
        try:
            tmp = md.with_suffix(md.suffix + ".tmp")
            tmp.write_text(new_txt, encoding="utf-8")
            tmp.replace(md)
            return True
        except PermissionError:
            if attempt < 2:
                time.sleep(1.5)
            else:
                # Clean the dangling tmp we just created
                try:
                    md.with_suffix(md.suffix + ".tmp").unlink()
                except OSError:
                    pass
                return False
    return False


def rewrite_ref(md_rel: str, basename: str, new_path: str) -> int:
    md = VAULT / md_rel
    txt = read_md(md)
    if not txt:
        return 0
    esc = re.escape(basename)
    count = 0
    wiki = re.compile(r"!\[\[[^\]]*" + esc + r"\]\]")
    txt2, n1 = wiki.subn(f"![[{new_path}]]", txt)
    count += n1
    md_link = re.compile(r"(\]\()[^()]*" + esc + r"(\))")
    txt2, n2 = md_link.subn(rf"\g<1>{new_path}\g<2>", txt2)
    count += n2
    if txt2 != txt:
        if not write_md_retry(md, txt2):
            locked.append(md_rel)
            ops.append({"op": "REWRITE-LOCKED", "file": md_rel, "basename": basename})
            print(f"  ⚠️ LOCKED, skipped: {md_rel}")
            return 0
    return count


def build_reference_index() -> dict[str, list[str]]:
    basenames = {f.name for f in FILES.iterdir() if f.is_file()}
    refs: dict[str, list[str]] = defaultdict(list)
    for md in VAULT.rglob("*.md"):
        rel = md.relative_to(VAULT).as_posix()
        if rel.startswith((".obsidian/", ".trash/")):
            continue
        try:
            if md.stat().st_size > 3_000_000:
                continue
        except OSError:
            continue
        txt = read_md(md)
        for b in basenames:
            if b in txt:
                refs[b].append(rel)
    return {b: sorted(set(v)) for b, v in refs.items()}


def _slugify(stem: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", stem).strip("-").lower()
    if len(s) <= 60:
        return s or "unsorted"
    digest = hashlib.sha1(stem.encode("utf-8")).hexdigest()[:7]
    return s[:52].rstrip("-") + "-" + digest


def classify(basename: str, referrers: list[str]) -> tuple[str, str]:
    journal_refs = [r for r in referrers if r.startswith("Journals/")]
    if journal_refs:
        ym = "unknown"
        for r in journal_refs:
            m = re.search(r"(\d{4}-\d{2})-\d{2}", r)
            if m:
                ym = m.group(1)
                break
        return "B", f"Attachments/journal-pasted/{ym}/{basename}"
    non_journal = [r for r in referrers if not r.startswith("Journals/")]
    slug = _slugify(Path(non_journal[0]).stem) if non_journal else "unsorted"
    return "A", f"KB/Attachments/{slug}/{basename}"


def recycle(p: Path) -> None:
    if not p.exists():
        return
    leftover = [str(c.relative_to(p)) for c in p.rglob("*") if c.is_file()]
    if leftover:
        ops.append({"op": "RECYCLE-ABORT", "path": str(p), "leftover": leftover})
        print(f"  RECYCLE-ABORT: Files/ still has {len(leftover)} files: {leftover}")
        return
    cmd = [
        "powershell", "-Command",
        "Add-Type -AssemblyName Microsoft.VisualBasic; "
        f"[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory("
        f"'{p}', 'OnlyErrorDialogs', 'SendToRecycleBin')",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    ops.append({"op": "RECYCLE-DIR" if r.returncode == 0 else "RECYCLE-FAIL", "path": str(p)})
    print(f"  {'RECYCLE' if r.returncode == 0 else 'RECYCLE-FAIL'}: Files/")


def main() -> None:
    print("=" * 64)
    print("ADR-028 Phase B — Files/ migration RESUME")
    print("=" * 64)

    clean_stale_tmp()

    # ── Fix the one missed rewrite from the crashed run ───────────────────
    print("\n[fix missed rewrite]")
    moved_dst = VAULT / MISSED["new_path"]
    if moved_dst.exists():
        n = rewrite_ref(MISSED["referrer"], MISSED["basename"], MISSED["new_path"])
        ops.append({
            "op": "FIX-MISSED-REWRITE", "file": MISSED["referrer"],
            "basename": MISSED["basename"], "refs_rewritten": n,
        })
        print(f"  {MISSED['basename']}: {n} ref(s) rewritten in {MISSED['referrer']}")
    else:
        print(f"  ⚠️ expected moved file not found: {moved_dst}")

    # ── Migrate the remaining Files/ entries ──────────────────────────────
    print("\n[migrate remaining Files/]")
    if not FILES.exists():
        print("  Files/ already gone — nothing remaining.")
    else:
        ref_index = build_reference_index()
        remaining = sorted(f for f in FILES.iterdir() if f.is_file())
        print(f"  {len(remaining)} files remaining")
        for f in remaining:
            referrers = ref_index.get(f.name, [])
            if not referrers:
                ops.append({"op": "ORPHAN", "file": f.name})
                print(f"  ORPHAN (left in place): {f.name}")
                continue
            cat, dst_rel = classify(f.name, referrers)
            dst = VAULT / dst_rel
            if dst.exists():
                ops.append({"op": "ABORT-collision", "src": f"Files/{f.name}", "dst": dst_rel})
                print(f"  ABORT-collision: {dst_rel}")
                continue
            pre = sha256(f)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), str(dst))
            verified = pre == sha256(dst)
            total = 0
            for r in referrers:
                total += rewrite_ref(r, f.name, dst_rel)
            ops.append({
                "op": f"MIGRATE-{cat}", "src": f"Files/{f.name}", "dst": dst_rel,
                "sha256": pre, "verified": verified, "refs_rewritten": total,
                "referrers": referrers,
            })
            print(f"  [{cat}] {f.name} -> {dst_rel}  ({total} ref(s))")

    # ── Recycle Files/ ────────────────────────────────────────────────────
    print("\n[recycle]")
    recycle(FILES)

    # ── Manifest ──────────────────────────────────────────────────────────
    manifest = {
        "adr": "ADR-028",
        "phase": "B sub-op 10+11 — Files/ migration (resume)",
        "executed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "vault": str(VAULT),
        "ops": ops,
        "locked_files": sorted(set(locked)),
    }
    MANIFEST_OUT.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_OUT.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nManifest -> {MANIFEST_OUT}")
    migrated = [o for o in ops if o["op"].startswith("MIGRATE")]
    mismatch = [o for o in ops if o.get("verified") is False]
    print(f"Done. resumed-migrations={len(migrated)}  locked={len(set(locked))}")
    if locked:
        print(f"⚠️  {len(set(locked))} file(s) still locked — re-run after closing Obsidian:")
        for f in sorted(set(locked)):
            print(f"     {f}")
    elif mismatch:
        print(f"⚠️  {len(mismatch)} SHA MISMATCH — INVESTIGATE")
    else:
        print("All clear — Files/ migration complete.")


if __name__ == "__main__":
    main()
