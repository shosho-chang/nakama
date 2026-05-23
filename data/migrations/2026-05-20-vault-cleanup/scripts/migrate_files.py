"""ADR-028 Phase B sub-op 10+11 — Files/ Category A+B migration.

Scans Files/, classifies each figure by its referring markdown, moves it to
the canonical location, rewrites image refs in the source markdowns.

  Category A — referenced from non-Journal markdown (KB/Raw/Papers/*, Inbox/web/*)
    → KB/Attachments/{source-slug}/{filename}
  Category B — referenced from Journals/Daily/{date}.md
    → Attachments/journal-pasted/{YYYY-MM}/{filename}
    (ADR-028 §8 one-shot Journals write exception — path-only ref rewrites)

Run modes:
  (default)   dry-run — print classification + planned moves, write NOTHING
  --apply     execute moves + ref rewrites + recycle Files/

Safety: sha256 verify; refs rewritten by exact-string replace of the basename;
recycle-bin only; manifest at .tmp/files-migration-manifest.json.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

VAULT = Path(r"E:\Shosho LifeOS")
FILES = VAULT / "Files"
MANIFEST_OUT = Path(r"E:\nakama\.tmp\files-migration-manifest.json")

APPLY = "--apply" in sys.argv

ops: list[dict] = []


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


def build_reference_index() -> dict[str, list[str]]:
    """basename → list of vault-relative .md paths that mention it."""
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
    """ASCII-safe attachment-folder slug. Long paper titles would blow the
    Windows 260-char MAX_PATH once a figure filename is appended, so the
    slug is truncated to 60 chars; truncated slugs get a 7-char sha1 suffix
    of the full stem for collision safety + determinism."""
    s = re.sub(r"[^A-Za-z0-9]+", "-", stem).strip("-").lower()
    if len(s) <= 60:
        return s or "unsorted"
    digest = hashlib.sha1(stem.encode("utf-8")).hexdigest()[:7]
    return s[:52].rstrip("-") + "-" + digest


def classify(basename: str, referrers: list[str]) -> tuple[str, str]:
    """Return (category, destination_vault_relative_path).

    Category B if ANY referrer is under Journals/ (pasted images).
    Else Category A — slug from the first non-Journal referrer's stem.
    """
    journal_refs = [r for r in referrers if r.startswith("Journals/")]
    if journal_refs:
        # YYYY-MM from Journals/Daily/{YYYY-MM-DD}.md
        ym = "unknown"
        for r in journal_refs:
            m = re.search(r"(\d{4}-\d{2})-\d{2}", r)
            if m:
                ym = m.group(1)
                break
        return "B", f"Attachments/journal-pasted/{ym}/{basename}"
    # Category A — slug = slugified stem of first referrer
    non_journal = [r for r in referrers if not r.startswith("Journals/")]
    slug = _slugify(Path(non_journal[0]).stem) if non_journal else "unsorted"
    return "A", f"KB/Attachments/{slug}/{basename}"


def rewrite_ref(md_rel: str, basename: str, new_path: str) -> int:
    """Repoint figure refs in a markdown file to the post-migration path.

    Every Files/ figure is referenced as an Obsidian wikilink embed
    ``![[basename]]`` (or ``![[any/path/basename]]``). Standard markdown
    ``![alt](path/basename)`` is also handled defensively. Returns the
    number of replacements made.
    """
    md = VAULT / md_rel
    txt = read_md(md)
    if not txt:
        return 0
    esc = re.escape(basename)
    count = 0

    # Obsidian embed: ![[ ...basename ]]
    wiki = re.compile(r"!\[\[[^\]]*" + esc + r"\]\]")
    txt2, n1 = wiki.subn(f"![[{new_path}]]", txt)
    count += n1

    # Standard markdown image/link target: ](...basename)
    md_link = re.compile(r"(\]\()[^()]*" + esc + r"(\))")
    txt2, n2 = md_link.subn(rf"\g<1>{new_path}\g<2>", txt2)
    count += n2

    if txt2 != txt and APPLY:
        tmp = md.with_suffix(md.suffix + ".tmp")
        tmp.write_text(txt2, encoding="utf-8")
        tmp.replace(md)
    return count


def recycle(p: Path) -> None:
    if not p.exists():
        return
    leftover = [str(c.relative_to(p)) for c in p.rglob("*") if c.is_file()]
    if leftover:
        ops.append({"op": "RECYCLE-ABORT", "path": str(p), "leftover": leftover})
        print(f"  RECYCLE-ABORT: Files/ still has {len(leftover)} files")
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
    mode = "APPLY" if APPLY else "DRY-RUN"
    print("=" * 64)
    print(f"ADR-028 Phase B — Files/ migration  [{mode}]")
    print("=" * 64)
    if not FILES.exists():
        raise SystemExit(f"Files/ not found: {FILES}")

    print("\nIndexing references...")
    ref_index = build_reference_index()

    files = sorted(f for f in FILES.iterdir() if f.is_file())
    cat_a, cat_b, orphans = [], [], []
    for f in files:
        referrers = ref_index.get(f.name, [])
        if not referrers:
            orphans.append(f)
            continue
        cat, dst = classify(f.name, referrers)
        (cat_a if cat == "A" else cat_b).append((f, dst, referrers))

    print(f"\nFiles: {len(files)}  |  Cat A: {len(cat_a)}  Cat B: {len(cat_b)}  orphan: {len(orphans)}")

    # ── Category A ────────────────────────────────────────────────────────
    print("\n[Category A — paper figures → KB/Attachments/{slug}/]")
    for f, dst, referrers in cat_a:
        _migrate_one(f, dst, referrers, "A")

    # ── Category B ────────────────────────────────────────────────────────
    print("\n[Category B — journal-pasted → Attachments/journal-pasted/{YYYY-MM}/]")
    for f, dst, referrers in cat_b:
        _migrate_one(f, dst, referrers, "B")

    # ── Orphans ───────────────────────────────────────────────────────────
    if orphans:
        print(f"\n[orphans — {len(orphans)} files with no vault reference]")
        for f in orphans:
            ops.append({"op": "ORPHAN", "file": f.name})
            print(f"  ORPHAN (left in place): {f.name}")

    # ── Recycle Files/ ────────────────────────────────────────────────────
    if APPLY and not orphans:
        print("\n[recycle]")
        recycle(FILES)
    elif orphans:
        print("\n[recycle] SKIPPED — orphans remain; Files/ not deleted")

    # ── Manifest ──────────────────────────────────────────────────────────
    manifest = {
        "adr": "ADR-028",
        "phase": "B sub-op 10+11 — Files/ migration",
        "mode": mode,
        "executed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "vault": str(VAULT),
        "counts": {"cat_a": len(cat_a), "cat_b": len(cat_b), "orphan": len(orphans)},
        "ops": ops,
    }
    MANIFEST_OUT.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_OUT.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nManifest -> {MANIFEST_OUT}")
    mismatches = [o for o in ops if o.get("verified") is False]
    if not APPLY:
        print("DRY-RUN — nothing written. Re-run with --apply to execute.")
    elif mismatches:
        print(f"⚠️  {len(mismatches)} SHA MISMATCH — INVESTIGATE")
    else:
        print("APPLY complete — all moves sha256-verified.")


def _migrate_one(src: Path, dst_rel: str, referrers: list[str], cat: str) -> None:
    dst = VAULT / dst_rel
    new_ref = dst_rel  # vault-relative path used in rewritten markdown
    entry: dict = {
        "op": f"MIGRATE-{cat}",
        "src": f"Files/{src.name}",
        "dst": dst_rel,
        "referrers": referrers,
    }
    if APPLY:
        if dst.exists():
            entry["op"] = "ABORT-collision"
            ops.append(entry)
            print(f"  ABORT-collision: {dst_rel}")
            return
        pre = sha256(src)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        entry["sha256"] = pre
        entry["verified"] = pre == sha256(dst)
        total = 0
        for r in referrers:
            total += rewrite_ref(r, src.name, new_ref)
        entry["refs_rewritten"] = total
    ops.append(entry)
    print(f"  [{cat}] {src.name} -> {dst_rel}  ({len(referrers)} referrer(s))")


if __name__ == "__main__":
    main()
