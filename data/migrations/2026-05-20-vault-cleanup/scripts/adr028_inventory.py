"""ADR-028 Phase A→B gate — live vault inventory + migration manifest preview.

Read-only. Produces two artifacts:
- .tmp/adr028-vault-inventory.md   (human-readable report)
- .tmp/adr028-migration-manifest.json   (machine-readable manifest PR-B1 can consume)

Does NOT touch vault. Purpose: surface surprises before PR-B1 bulk migration runs.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

VAULT = Path(r"E:\Shosho LifeOS")
OUT_REPORT = Path(r"E:\nakama\.tmp\adr028-vault-inventory.md")
OUT_MANIFEST = Path(r"E:\nakama\.tmp\adr028-migration-manifest.json")


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        try:
            return p.read_text(encoding="utf-8-sig")
        except Exception:
            return ""


def vrel(p: Path) -> str:
    return p.relative_to(VAULT).as_posix()


# ────────────────────────────────────────────────────────────────────────────
# Inventory passes
# ────────────────────────────────────────────────────────────────────────────


def inventory_inbox():
    """Inbox/kb/, Inbox/web/, Inbox/attachments/, anything else."""
    inbox = VAULT / "Inbox"
    result = {"kb_md": [], "kb_attachment_dirs": [], "web_md": [], "other": [], "exists": inbox.exists()}
    if not inbox.exists():
        return result
    kb = inbox / "kb"
    if kb.exists():
        for p in sorted(kb.iterdir()):
            if p.is_file() and p.suffix.lower() == ".md":
                result["kb_md"].append(vrel(p))
        att = kb / "attachments"
        if att.exists():
            for d in sorted(att.iterdir()):
                if d.is_dir():
                    files = [f.name for f in d.iterdir() if f.is_file()]
                    result["kb_attachment_dirs"].append({"slug": d.name, "file_count": len(files)})
    web = inbox / "web"
    if web.exists():
        for p in sorted(web.iterdir()):
            if p.is_file() and p.suffix.lower() == ".md":
                result["web_md"].append(vrel(p))
    for p in sorted(inbox.iterdir()):
        if p.is_dir() and p.name not in ("kb", "web", "attachments"):
            result["other"].append({"path": vrel(p), "is_dir": True, "child_count": len(list(p.iterdir()))})
        elif p.is_file():
            result["other"].append({"path": vrel(p), "is_dir": False})
    return result


def inventory_nami_notes():
    folder = VAULT / "Nami" / "Notes"
    result = {"exists": folder.exists(), "files": []}
    if not folder.exists():
        return result
    for p in sorted(folder.rglob("*.md")):
        result["files"].append({"path": vrel(p), "size": p.stat().st_size})
    return result


def inventory_agent_reports():
    folder = VAULT / "AgentReports"
    result = {"exists": folder.exists(), "franky_weekly": [], "dev_backlog": None, "other": []}
    if not folder.exists():
        return result
    fr = folder / "franky"
    if fr.exists():
        for p in sorted(fr.glob("*.md")):
            result["franky_weekly"].append({"path": vrel(p), "size": p.stat().st_size})
    dev = folder / "dev-backlog.md"
    if dev.exists():
        result["dev_backlog"] = {"path": vrel(dev), "size": dev.stat().st_size, "sha256": sha256(dev)}
    for p in sorted(folder.iterdir()):
        if p.is_file() and p.name != "dev-backlog.md":
            result["other"].append({"path": vrel(p), "size": p.stat().st_size})
        elif p.is_dir() and p.name != "franky":
            result["other"].append({"path": vrel(p), "is_dir": True})
    return result


def inventory_agent_briefs():
    folder = VAULT / "AgentBriefs"
    result = {"exists": folder.exists(), "files": []}
    if not folder.exists():
        return result
    for p in sorted(folder.rglob("*.md")):
        result["files"].append({"path": vrel(p), "size": p.stat().st_size})
    return result


def inventory_projects_with_markers():
    folder = VAULT / "Projects"
    result = {"exists": folder.exists(), "total_md": 0, "legacy_kw_marker": [], "new_marker": [], "human_only_marker": []}
    if not folder.exists():
        return result
    for p in sorted(folder.rglob("*.md")):
        result["total_md"] += 1
        txt = read_text(p)
        if "%%KW-START%%" in txt or "%%KW-END%%" in txt:
            result["legacy_kw_marker"].append(vrel(p))
        if "%%agent-zoro-keywords-start%%" in txt:
            result["new_marker"].append(vrel(p))
        if "<!-- vault:human-only-section -->" in txt:
            result["human_only_marker"].append(vrel(p))
    return result


def inventory_files():
    folder = VAULT / "Files"
    result = {"exists": folder.exists(), "files": [], "references": {}}
    if not folder.exists():
        return result
    for p in sorted(folder.iterdir()):
        if p.is_file():
            result["files"].append({"path": vrel(p), "size": p.stat().st_size, "sha256": sha256(p)})
    # Find markdowns that reference these basenames
    basenames = {p["path"].split("/")[-1] for p in result["files"]}
    refs: dict[str, list[str]] = {b: [] for b in basenames}
    for md in VAULT.rglob("*.md"):
        # Skip very large files (>2MB) and .obsidian/.trash
        rel = md.relative_to(VAULT).as_posix()
        if rel.startswith((".obsidian/", ".trash/")):
            continue
        try:
            if md.stat().st_size > 2_000_000:
                continue
        except OSError:
            continue
        txt = read_text(md)
        for b in basenames:
            if b in txt:
                refs[b].append(rel)
    result["references"] = {b: sorted(set(v)) for b, v in refs.items() if v}
    result["orphan_files"] = sorted(b for b in basenames if not refs.get(b))
    return result


def inventory_kb_attachments_nested():
    folder = VAULT / "KB" / "Attachments"
    result = {"exists": folder.exists(), "nested_buckets": {}, "flat_slugs": []}
    if not folder.exists():
        return result
    for p in sorted(folder.iterdir()):
        if not p.is_dir():
            continue
        if p.name in ("inbox", "pubmed"):
            inner = []
            for sub in sorted(p.iterdir()):
                if sub.is_dir():
                    inner.append({"slug": sub.name, "file_count": len(list(sub.iterdir()))})
            result["nested_buckets"][p.name] = inner
        else:
            result["flat_slugs"].append({"slug": p.name, "file_count": len(list(p.iterdir()))})
    return result


def inventory_kill_targets():
    """Folders Phase B will delete after migration."""
    targets = [
        "KB/Wiki/Outputs",
        "KB/Wiki/Syntheses",
        "KB/Wiki/Comparisons",
        "Case Studies",
        "Incidents",
        "Schemas",
        "AgentBriefs",
    ]
    result = {}
    for t in targets:
        p = VAULT / t
        if p.exists():
            files = list(p.rglob("*"))
            result[t] = {"exists": True, "child_count": sum(1 for f in files if f.is_file()), "dir_count": sum(1 for f in files if f.is_dir())}
        else:
            result[t] = {"exists": False}
    return result


def inventory_repo_destination_collisions():
    """Files Phase B will move INTO the repo — check destinations don't already exist."""
    repo = Path(r"E:\nakama")
    targets = [
        "data/agent_reports/franky/weekly",
        "data/agent_reports/franky/dev-backlog.md",
        "docs/prds",
        "docs/case-studies",
        "docs/incidents",
    ]
    result = {}
    for t in targets:
        p = repo / t
        if p.exists():
            if p.is_dir():
                result[t] = {"exists": True, "child_count": len(list(p.iterdir()))}
            else:
                result[t] = {"exists": True, "size": p.stat().st_size}
        else:
            result[t] = {"exists": False}
    return result


# ────────────────────────────────────────────────────────────────────────────
# Manifest builder — produces the JSON PR-B1 will eat
# ────────────────────────────────────────────────────────────────────────────


def build_manifest(inv: dict) -> dict:
    """Construct migration manifest entries for each Phase B sub-op."""
    ops: list[dict] = []

    # 1. Nami/Notes/* → AgentOutputs/nami/notes/*
    for n in inv["nami_notes"]["files"]:
        src = VAULT / n["path"]
        new_rel = "AgentOutputs/nami/notes/" + n["path"].removeprefix("Nami/Notes/")
        ops.append({
            "sub_op": "nami-notes-move",
            "old_path": n["path"],
            "new_path": new_rel,
            "size": n["size"],
            "sha256": sha256(src) if src.exists() else None,
        })

    # 2. AgentBriefs/* → AgentOutputs/nami/briefs/*
    for b in inv["agent_briefs"]["files"]:
        src = VAULT / b["path"]
        new_rel = "AgentOutputs/nami/briefs/" + b["path"].removeprefix("AgentBriefs/")
        ops.append({
            "sub_op": "nami-briefs-move",
            "old_path": b["path"],
            "new_path": new_rel,
            "size": b["size"],
            "sha256": sha256(src) if src.exists() else None,
        })

    # 3. AgentReports/franky/* → repo data/agent_reports/franky/weekly/*
    for fr in inv["agent_reports"]["franky_weekly"]:
        src = VAULT / fr["path"]
        new_rel = "REPO:data/agent_reports/franky/weekly/" + fr["path"].split("/")[-1]
        ops.append({
            "sub_op": "franky-weekly-vault-to-repo",
            "old_path": fr["path"],
            "new_path": new_rel,
            "size": fr["size"],
            "sha256": sha256(src) if src.exists() else None,
        })
    if inv["agent_reports"]["dev_backlog"]:
        d = inv["agent_reports"]["dev_backlog"]
        ops.append({
            "sub_op": "franky-dev-backlog-vault-to-repo",
            "old_path": d["path"],
            "new_path": "REPO:data/agent_reports/franky/dev-backlog.md",
            "size": d["size"],
            "sha256": d["sha256"],
        })

    # 4. Inbox/kb/* → Inbox/web/*
    for m in inv["inbox"]["kb_md"]:
        src = VAULT / m
        new_rel = "Inbox/web/" + m.removeprefix("Inbox/kb/")
        ops.append({
            "sub_op": "inbox-kb-to-web",
            "old_path": m,
            "new_path": new_rel,
            "sha256": sha256(src) if src.exists() else None,
        })
    for a in inv["inbox"]["kb_attachment_dirs"]:
        slug = a["slug"]
        ops.append({
            "sub_op": "inbox-attachments-to-kb",
            "old_path": f"Inbox/kb/attachments/{slug}/",
            "new_path": f"KB/Attachments/{slug}/",
            "file_count": a["file_count"],
            "note": "PR-A1 promotion fix migrates these on next Robin ingest; bulk migration handles unpromoted Inbox state",
        })

    # 5. KB/Attachments/{inbox,pubmed}/{slug}/ → KB/Attachments/{slug}/
    for bucket_name, slugs in inv["kb_attachments_nested"]["nested_buckets"].items():
        for s in slugs:
            ops.append({
                "sub_op": "kb-attachments-flatten",
                "old_path": f"KB/Attachments/{bucket_name}/{s['slug']}/",
                "new_path": f"KB/Attachments/{s['slug']}/",
                "file_count": s["file_count"],
            })

    # 6. Files/ Category A&B — image migration with reference rewrites
    refs = inv["files_inv"]["references"]
    for f in inv["files_inv"]["files"]:
        basename = f["path"].split("/")[-1]
        referring = refs.get(basename, [])
        # Heuristic: if any referring file is in Journals/, Category B; else Category A
        is_journal = any(r.startswith("Journals/") for r in referring)
        if is_journal:
            # Cat B → Attachments/journal-pasted/{YYYY-MM}/
            # YYYY-MM derived from journal filename
            yyyy_mm = "unknown"
            for r in referring:
                m = re.search(r"Journals/Daily/(\d{4}-\d{2})", r)
                if m:
                    yyyy_mm = m.group(1)
                    break
            new_rel = f"Attachments/journal-pasted/{yyyy_mm}/{basename}"
            sub_op = "files-category-b-journal"
        else:
            # Cat A → KB/Attachments/{source-slug}/
            # Source slug = the referring markdown's stem if in KB/Raw/Papers/ or Inbox/kb/
            slug = "unknown"
            for r in referring:
                if "/" in r:
                    slug = r.rsplit("/", 1)[-1].removesuffix(".md")
                    break
            new_rel = f"KB/Attachments/{slug}/{basename}"
            sub_op = "files-category-a-paper"
        ops.append({
            "sub_op": sub_op,
            "old_path": f["path"],
            "new_path": new_rel,
            "size": f["size"],
            "sha256": f["sha256"],
            "referring_md_files": referring,
        })

    # 7. Dev artifacts → repo
    for kill_path, info in inv["kill_targets"].items():
        if not info["exists"]:
            continue
        if kill_path in ("KB/Wiki/Outputs", "KB/Wiki/Syntheses", "KB/Wiki/Comparisons"):
            # Per ADR-028 §9: Outputs orphans relocate by content; Syntheses/Comparisons delete-after-empty
            ops.append({
                "sub_op": "vault-folder-delete-or-relocate",
                "old_path": kill_path,
                "child_count": info["child_count"],
                "note": "Manual inspection in PR-B1 — see ADR-028 §9 (orphan relocation rules)",
            })
        elif kill_path == "Case Studies":
            ops.append({"sub_op": "case-studies-vault-to-repo", "old_path": kill_path, "new_path": "REPO:docs/case-studies/", "child_count": info["child_count"]})
        elif kill_path == "Incidents":
            ops.append({"sub_op": "incidents-vault-to-repo", "old_path": kill_path, "new_path": "REPO:docs/incidents/", "child_count": info["child_count"]})
        elif kill_path == "Schemas":
            ops.append({"sub_op": "schemas-delete", "old_path": kill_path, "child_count": info["child_count"], "note": "ADR-028 §9: delete; keep _alias_map elsewhere"})
        elif kill_path == "AgentBriefs":
            ops.append({"sub_op": "agent-briefs-delete-after-move", "old_path": kill_path, "child_count": info["child_count"], "note": "Folder removed after step 2 nami-briefs-move"})

    return {
        "schema_version": 1,
        "generated_at": "2026-05-20",
        "vault_root": str(VAULT),
        "repo_root": r"E:\nakama",
        "sub_op_counts": dict(_count_sub_ops(ops)),
        "total_ops": len(ops),
        "ops": ops,
    }


def _count_sub_ops(ops):
    c = defaultdict(int)
    for o in ops:
        c[o["sub_op"]] += 1
    return c


# ────────────────────────────────────────────────────────────────────────────
# Report renderer
# ────────────────────────────────────────────────────────────────────────────


def render_report(inv: dict, manifest: dict, surprises: list[str]) -> str:
    L = []
    L.append("# ADR-028 Phase B Pre-Flight Report")
    L.append("")
    L.append(f"Generated: 2026-05-20")
    L.append(f"Vault: `{VAULT}`")
    L.append(f"Repo: `E:\\nakama`")
    L.append("")
    L.append("## TL;DR")
    L.append("")
    L.append(f"- Total migration ops planned: **{manifest['total_ops']}**")
    L.append(f"- Surprises flagged: **{len(surprises)}**")
    L.append("")

    if surprises:
        L.append("## ⚠️ Surprises (review before greenlighting PR-B1)")
        L.append("")
        for s in surprises:
            L.append(f"- {s}")
        L.append("")

    L.append("## Inventory")
    L.append("")

    # Inbox
    inb = inv["inbox"]
    L.append("### Inbox")
    L.append("")
    L.append(f"- `Inbox/kb/` exists: {inb['exists']}")
    L.append(f"- `Inbox/kb/*.md`: **{len(inb['kb_md'])}** files")
    L.append(f"- `Inbox/kb/attachments/{{slug}}/`: **{len(inb['kb_attachment_dirs'])}** slug dirs")
    if inb["kb_attachment_dirs"]:
        total_att = sum(d["file_count"] for d in inb["kb_attachment_dirs"])
        L.append(f"  - Total attachment files: **{total_att}**")
        L.append(f"  - These were in latent-broken state before PR-A1 merged. Phase B handles unpromoted Inbox state; PR-A1 handles future promotions.")
    L.append(f"- `Inbox/web/*.md`: {len(inb['web_md'])} (greenfield target — should be 0)")
    if inb["other"]:
        L.append(f"- Other Inbox children: {len(inb['other'])}")
        for o in inb["other"]:
            L.append(f"  - `{o['path']}`")
    L.append("")

    # Nami Notes
    nn = inv["nami_notes"]
    L.append("### Nami Notes (vault stays; folder renamed)")
    L.append("")
    L.append(f"- `Nami/Notes/` exists: {nn['exists']}")
    L.append(f"- Notes: **{len(nn['files'])}** files")
    if nn["files"]:
        total = sum(f["size"] for f in nn["files"])
        L.append(f"- Total size: {total:,} bytes ({total/1024:.1f} KB)")
    L.append("")

    # AgentReports
    ar = inv["agent_reports"]
    L.append("### AgentReports (Franky → repo)")
    L.append("")
    L.append(f"- `AgentReports/` exists: {ar['exists']}")
    L.append(f"- Franky weekly reports: **{len(ar['franky_weekly'])}** files")
    L.append(f"- `dev-backlog.md`: {'present' if ar['dev_backlog'] else 'MISSING'}")
    if ar["other"]:
        L.append(f"- Other AgentReports children: {len(ar['other'])}")
        for o in ar["other"]:
            L.append(f"  - `{o['path']}`")
    L.append("")

    # AgentBriefs
    ab = inv["agent_briefs"]
    L.append("### AgentBriefs (Nami briefs → AgentOutputs/nami/briefs/)")
    L.append("")
    L.append(f"- `AgentBriefs/` exists: {ab['exists']}")
    L.append(f"- Brief files: **{len(ab['files'])}**")
    L.append("")

    # Projects markers
    pj = inv["projects"]
    L.append("### Projects (marker retro-fit visibility)")
    L.append("")
    L.append(f"- Total `Projects/*.md`: **{pj['total_md']}**")
    L.append(f"- Pages with legacy `%%KW-START%%`: **{len(pj['legacy_kw_marker'])}** (retro-fit candidates)")
    L.append(f"- Pages with new `%%agent-zoro-keywords-start%%`: {len(pj['new_marker'])} (PR-A3 templates haven't been used yet, expected ~0)")
    L.append(f"- Pages with `<!-- vault:human-only-section -->`: {len(pj['human_only_marker'])}")
    if pj["legacy_kw_marker"]:
        L.append("")
        L.append("Legacy-marker pages (need retro-fit in Phase B/C):")
        for p in pj["legacy_kw_marker"][:20]:
            L.append(f"- `{p}`")
        if len(pj["legacy_kw_marker"]) > 20:
            L.append(f"- ...and {len(pj['legacy_kw_marker']) - 20} more")
    L.append("")

    # Files/
    fi = inv["files_inv"]
    L.append("### Files/ (Category A + B migration)")
    L.append("")
    L.append(f"- `Files/` exists: {fi['exists']}")
    L.append(f"- Total files: **{len(fi['files'])}**")
    L.append(f"- Files with references in vault: **{len(fi['references'])}**")
    L.append(f"- Orphan files (no references): **{len(fi['orphan_files'])}**")
    if fi["orphan_files"]:
        L.append("  - Orphan list:")
        for b in fi["orphan_files"][:20]:
            L.append(f"    - `{b}`")
        if len(fi["orphan_files"]) > 20:
            L.append(f"    - ...and {len(fi['orphan_files']) - 20} more")
    L.append("")

    # KB/Attachments nested
    ka = inv["kb_attachments_nested"]
    L.append("### KB/Attachments/ (flatten target)")
    L.append("")
    L.append(f"- Nested buckets to flatten: {list(ka['nested_buckets'].keys())}")
    for name, slugs in ka["nested_buckets"].items():
        L.append(f"  - `{name}/`: {len(slugs)} slug subfolders")
    L.append(f"- Already-flat slugs: {len(ka['flat_slugs'])}")
    L.append("")

    # Kill targets
    kt = inv["kill_targets"]
    L.append("### Kill targets (Phase B deletes after migration)")
    L.append("")
    for path, info in kt.items():
        if info["exists"]:
            L.append(f"- `{path}/`: {info['child_count']} files, {info['dir_count']} dirs")
        else:
            L.append(f"- `{path}/`: not present (no work)")
    L.append("")

    # Repo collisions
    rc = inv["repo_collisions"]
    L.append("### Repo destination collision check")
    L.append("")
    for t, info in rc.items():
        if info["exists"]:
            child = info.get("child_count")
            size = info.get("size")
            detail = f"({child} children)" if child is not None else f"({size} bytes)"
            L.append(f"- `{t}` — **EXISTS** {detail} (PR-B1 merges into existing)")
        else:
            L.append(f"- `{t}` — clean (will be created)")
    L.append("")

    # Manifest summary
    L.append("## Manifest summary")
    L.append("")
    L.append("Sub-op counts:")
    for op, count in sorted(manifest["sub_op_counts"].items(), key=lambda x: -x[1]):
        L.append(f"- `{op}`: {count}")
    L.append("")
    L.append(f"Full manifest: `.tmp/adr028-migration-manifest.json` ({manifest['total_ops']} ops)")
    L.append("")

    return "\n".join(L)


def detect_surprises(inv: dict, manifest: dict) -> list[str]:
    s = []
    inb = inv["inbox"]
    if inb["web_md"]:
        s.append(f"`Inbox/web/` already has {len(inb['web_md'])} .md files (expected 0 in greenfield). Conflict risk during sub-op `inbox-kb-to-web`.")
    if inb["other"]:
        non_standard = [o for o in inb["other"] if o.get("is_dir")]
        if non_standard:
            s.append(f"`Inbox/` has {len(non_standard)} non-standard child dirs (not kb/web/attachments): " + ", ".join(o["path"] for o in non_standard))
    rc = inv["repo_collisions"]
    for t, info in rc.items():
        if info.get("exists") and info.get("child_count", 0) > 0:
            s.append(f"Repo destination `{t}` already has content — PR-B1 will merge (verify no name collisions).")
    fi = inv["files_inv"]
    if fi["orphan_files"]:
        s.append(f"`Files/` has {len(fi['orphan_files'])} orphan files with no vault references — PR-B1 can recycle-bin without rewrite, but confirm none are intentional drops.")
    # Detect missing dev-backlog (Franky pipeline depends on it)
    if inv["agent_reports"]["exists"] and not inv["agent_reports"]["dev_backlog"]:
        s.append("`AgentReports/dev-backlog.md` MISSING — Franky run-loop tolerates this, but verify whether the file should exist before migration.")
    pj = inv["projects"]
    if pj["new_marker"] and not pj["legacy_kw_marker"]:
        s.append("Projects already have NEW markers but no legacy ones — PR-A3 templates may have been used in dev sandbox; verify no double-migration.")
    return s


# ────────────────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────────────────


def main():
    if not VAULT.exists():
        raise SystemExit(f"Vault not found: {VAULT}")
    print("Scanning vault (read-only)...")
    inv = {
        "inbox": inventory_inbox(),
        "nami_notes": inventory_nami_notes(),
        "agent_reports": inventory_agent_reports(),
        "agent_briefs": inventory_agent_briefs(),
        "projects": inventory_projects_with_markers(),
        "files_inv": inventory_files(),
        "kb_attachments_nested": inventory_kb_attachments_nested(),
        "kill_targets": inventory_kill_targets(),
        "repo_collisions": inventory_repo_destination_collisions(),
    }
    print("Building manifest...")
    manifest = build_manifest(inv)
    print("Detecting surprises...")
    surprises = detect_surprises(inv, manifest)
    print(f"Writing report -> {OUT_REPORT}")
    OUT_REPORT.parent.mkdir(parents=True, exist_ok=True)
    OUT_REPORT.write_text(render_report(inv, manifest, surprises), encoding="utf-8")
    print(f"Writing manifest -> {OUT_MANIFEST}")
    OUT_MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Done. Surprises: {len(surprises)}; Ops: {manifest['total_ops']}")


if __name__ == "__main__":
    main()
