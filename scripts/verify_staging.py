"""Aggregate staging verifier for ADR-020 Stage 5.0.

Auto-discovers staged chapters and emits a JSON report.

USAGE
-----

    python -m scripts.verify_staging --vault-root "E:\\Shosho LifeOS"

EXIT CODES
----------
    0  — all chapters PASS
    1  — at least one chapter FAIL, or no chapters found
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.run_s8_preflight import _pick_chapter, compute_acceptance  # noqa: E402
from shared.source_ingest import walk_book_to_chapters  # noqa: E402

_FM_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _collect_chapters(vault_root: Path) -> list[tuple[str, int, Path]]:
    result = []
    for p in sorted(vault_root.glob("KB/Wiki.staging/Sources/Books/*/ch*.md")):
        book_id = p.parent.name
        m = re.match(r"ch(\d+)\.md$", p.name)
        if m:
            result.append((book_id, int(m.group(1)), p))
    return result


def _run(vault_root: Path, *, report_dir: Path | None = None) -> int:
    chapters = _collect_chapters(vault_root)
    if not chapters:
        print("ERROR: no staged chapters found under KB/Wiki.staging", file=sys.stderr)
        return 1

    walker_cache: dict[str, list | None] = {}
    records: list[dict] = []

    for book_id, chapter_index, staged_path in chapters:
        text = staged_path.read_text(encoding="utf-8")
        m = _FM_RE.match(text)
        fm: dict
        page_body: str
        if m:
            fm = yaml.safe_load(m.group(1)) or {}
            page_body = text[m.end() :]
        else:
            fm, page_body = {}, text
        wikilinks_introduced = list(fm.get("wikilinks_introduced") or [])

        if book_id not in walker_cache:
            raw_path = vault_root / "KB" / "Raw" / "Books" / f"{book_id}.md"
            if not raw_path.exists():
                walker_cache[book_id] = None
            else:
                try:
                    walker_cache[book_id] = walk_book_to_chapters(raw_path)
                except Exception:
                    walker_cache[book_id] = None

        record: dict = {
            "book_id": book_id,
            "chapter_index": chapter_index,
            "acceptance_pass": False,
            "rules": {},
        }
        payloads = walker_cache[book_id]

        if payloads is None:
            record["error"] = f"raw file not found: KB/Raw/Books/{book_id}.md"
            records.append(record)
            continue

        # Match walker payload by chapter_title from frontmatter (filename idx
        # ambiguous: batch writes use walker_pos, CLI single-chapter writes
        # use real_ch_num after _pick_chapter D4 reassign).
        title = (fm.get("chapter_title") or "").strip()
        payload = None
        if title:
            for p in payloads:
                if (p.chapter_title or "").strip() == title:
                    payload = p
                    break
        if payload is None:
            try:
                payload, _ = _pick_chapter(payloads, chapter_index)
            except Exception as exc:
                record["error"] = f"no walker match for title='{title}': {exc}"
                records.append(record)
                continue

        acc = compute_acceptance(
            page_body=page_body,
            walker_verbatim=payload.verbatim_body,
            walker_section_anchors=payload.section_anchors,
            walker_figures_count=len(payload.figures),
            wikilinks_introduced=wikilinks_introduced,
        )
        record["acceptance_pass"] = acc.acceptance_pass
        record["rules"] = {
            "verbatim_match_pct": {
                "value": round(acc.verbatim_match, 4),
                "threshold": 0.99,
                "pass": acc.verbatim_ok,
            },
            "section_anchors_match": {
                "value": acc.anchors_match,
                "pass": acc.anchors_match,
            },
            "figures_embedded": {
                "value": acc.figures_embedded,
                "expected": acc.figures_expected,
                "pass": acc.figures_ok,
            },
            "wikilinks": {
                "value": acc.wikilinks_count,
                "threshold": acc.wikilinks_threshold,
                "pass": acc.wikilinks_ok,
            },
        }
        records.append(record)

    passed = sum(1 for r in records if r["acceptance_pass"])
    failed = len(records) - passed

    summary = {
        "run_at": datetime.now().isoformat(),
        "vault_root": str(vault_root),
        "summary": {"total": len(records), "passed": passed, "failed": failed},
        "chapters": records,
    }

    out_dir = report_dir if report_dir is not None else _REPO_ROOT / "docs" / "runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"{date.today().isoformat()}-staging-verify.json"
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"{'Book':<50} {'Ch':>4}  Status")
    print("-" * 64)
    for r in records:
        if r["acceptance_pass"]:
            status = "PASS"
        elif r.get("error"):
            status = f"FAIL ({r['error']})"
        else:
            status = "FAIL"
        print(f"{r['book_id']:<50} {r['chapter_index']:>4}  {status}")

    print(f"\nTotal: {len(records)}  Passed: {passed}  Failed: {failed}")
    print(f"Report: {report_path}")

    return 0 if failed == 0 else 1


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Aggregate staging verifier (ADR-020 Stage 5.0)")
    parser.add_argument("--vault-root", required=True, metavar="VAULT_ROOT")
    parser.add_argument("--report-dir", default=None, metavar="DIR")
    args = parser.parse_args(argv)

    report_dir = Path(args.report_dir) if args.report_dir else None
    sys.exit(_run(Path(args.vault_root), report_dir=report_dir))


if __name__ == "__main__":
    main()
