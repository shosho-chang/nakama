"""Single-chapter verbatim verifier for ADR-020 Stage 1.5.

USAGE
-----

    python -m scripts.verify_verbatim <vault_root> <book_id> <chapter_index>
    python -m scripts.verify_verbatim --vault-root "E:\\Shosho LifeOS" \\
        --book-id biochemistry-for-sport-and-exercise-maclaren --chapter-index 1

EXIT CODES
----------
    0  — PASS (all 4 acceptance rules pass)
    1  — FAIL (at least one rule fails)
    2  — ERROR (staged file missing or walker error)
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.run_s8_preflight import (  # noqa: E402
    AcceptanceResult,
    _pick_chapter,
    compute_acceptance,
)
from shared.source_ingest import walk_book_to_chapters  # noqa: E402

_FM_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _staged_path(vault_root: Path, book_id: str, chapter_index: int) -> Path:
    return (
        vault_root / "KB" / "Wiki.staging" / "Sources" / "Books" / book_id / f"ch{chapter_index}.md"
    )


def _load_staged(vault_root: Path, book_id: str, chapter_index: int) -> tuple[dict, str] | None:
    path = _staged_path(vault_root, book_id, chapter_index)
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    m = _FM_RE.match(text)
    if m:
        fm = yaml.safe_load(m.group(1)) or {}
        body = text[m.end() :]
    else:
        fm, body = {}, text
    return fm, body


def _load_payload(vault_root: Path, book_id: str, chapter_index: int):
    raw_path = vault_root / "KB" / "Raw" / "Books" / f"{book_id}.md"
    if not raw_path.exists():
        raise FileNotFoundError(f"raw file not found: {raw_path}")
    chapters = walk_book_to_chapters(raw_path)
    payload, _ = _pick_chapter(chapters, chapter_index)
    return payload


def _format_report(book_id: str, chapter_index: int, acc: AcceptanceResult) -> str:
    rules_ok = sum([acc.verbatim_ok, acc.anchors_match, acc.figures_ok, acc.wikilinks_ok])
    lines = [
        f"=== Verbatim verification: {book_id} / ch{chapter_index} ===",
        (
            f"verbatim_match_pct:     {acc.verbatim_match:.4f}"
            f"  (rule >= 0.99)"
            f"         {'PASS' if acc.verbatim_ok else 'FAIL'}"
        ),
        (
            f"section_anchors_match:  {str(acc.anchors_match):<5}"
            f"  (rule == True)"
            f"         {'PASS' if acc.anchors_match else 'FAIL'}"
        ),
        (
            f"figures_embedded:       {acc.figures_embedded} / {acc.figures_expected}"
            f"   (rule ==)"
            f"              {'PASS' if acc.figures_ok else 'FAIL'}"
        ),
        (
            f"wikilinks:              {acc.wikilinks_count}"
            f"       (rule >= char_count // 2000 = {acc.wikilinks_threshold})"
            f"  {'PASS' if acc.wikilinks_ok else 'FAIL'}"
        ),
        "",
        f"ACCEPTANCE: {'PASS' if acc.acceptance_pass else 'FAIL'}  ({rules_ok}/4 rules)",
    ]
    return "\n".join(lines)


def _run(vault_root: Path, book_id: str, chapter_index: int) -> int:
    staged = _load_staged(vault_root, book_id, chapter_index)
    if staged is None:
        p = _staged_path(vault_root, book_id, chapter_index)
        print(f"MISSING: staged file not found: {p}", file=sys.stderr)
        return 2

    fm, page_body = staged
    wikilinks_introduced = list(fm.get("wikilinks_introduced") or [])

    try:
        payload = _load_payload(vault_root, book_id, chapter_index)
    except Exception as exc:
        print(f"ERROR loading walker data: {exc}", file=sys.stderr)
        return 2

    acc = compute_acceptance(
        page_body=page_body,
        walker_verbatim=payload.verbatim_body,
        walker_section_anchors=payload.section_anchors,
        walker_figures_count=len(payload.figures),
        wikilinks_introduced=wikilinks_introduced,
    )
    print(_format_report(book_id, chapter_index, acc))
    return 0 if acc.acceptance_pass else 1


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        description="Single-chapter verbatim verifier (ADR-020 Stage 1.5)"
    )
    parser.add_argument("vault_root", nargs="?", help="Vault root path")
    parser.add_argument("book_id", nargs="?", help="Book ID")
    parser.add_argument("chapter_index", nargs="?", type=int, help="Chapter index")
    parser.add_argument("--vault-root", dest="vault_root_kw", metavar="VAULT_ROOT")
    parser.add_argument("--book-id", dest="book_id_kw", metavar="BOOK_ID")
    parser.add_argument("--chapter-index", dest="chapter_index_kw", type=int, metavar="N")
    args = parser.parse_args(argv)

    vault_root = args.vault_root or args.vault_root_kw
    book_id = args.book_id or args.book_id_kw
    chapter_index = args.chapter_index if args.chapter_index is not None else args.chapter_index_kw

    if not (vault_root and book_id and chapter_index is not None):
        parser.error("vault_root, book_id, and chapter_index are required")

    sys.exit(_run(Path(vault_root), book_id, chapter_index))


if __name__ == "__main__":
    main()
