"""N521 migration — re-render the three already-read books from their annotation
store into unified Literature Notes (``KB/Literature/{slug}.md``).

Centaur Literature 規格 §6 step 5 / 規格 v0.2 §11 (種子語料 D-24)：

  財富階梯 / 發現我的多重職涯組合 / 讓你的思緒平靜下來安然入睡

是 migration tool — **不在 CI/測試跑** (writer 單元測試用 fixture)。執行時：

1. 對每本書 (slug) 呼叫 ``write_literature_note`` 從 ``KB/Annotations/{slug}.md``
   重 render 出 ``KB/Literature/{slug}.md`` (機器檔零改動，雙檔制 D1)。
2. **不刪舊檔**。舊 ``notes.md`` / ``digest.md`` 的清理用 PowerShell 回收桶
   (CLAUDE.md「檔案刪除規則」，禁 ``rm``)。本 script 只列出待清理路徑，由修修在
   shell 用 recycle-bin 指令手動處理 — 避免自動化誤刪 + 繞過回收桶。

Usage:
    python -m scripts.migrate_books_to_literature           # render all seed books
    python -m scripts.migrate_books_to_literature --slug 財富階梯
    python -m scripts.migrate_books_to_literature --dry-run # 只報告，不寫
"""

from __future__ import annotations

import argparse
import sys

from shared.config import get_vault_path
from shared.literature_writer import write_literature_note

#: 種子語料 (規格 v0.2 §11 D-24)。slug = annotation-set slug = Literature 檔名 stem。
SEED_BOOK_SLUGS = [
    "財富階梯",
    "發現我的多重職涯組合",
    "讓你的思緒平靜下來安然入睡",
]


def _legacy_artifacts(slug: str) -> list[str]:
    """退役舊檔的 vault-relative 路徑 (供修修用回收桶手動清)。"""
    base = f"KB/Wiki/Sources/Books/{slug}"
    return [f"{base}/notes.md", f"{base}/digest.md"]


def migrate(slugs: list[str], *, dry_run: bool = False) -> int:
    vault = get_vault_path()
    print(f"vault: {vault}")
    rendered = 0
    cleanup_hints: list[str] = []

    for slug in slugs:
        ann_path = vault / "KB" / "Annotations" / f"{slug}.md"
        if not ann_path.exists():
            print(f"  ⚠ {slug}: no annotation store at {ann_path} — skipped")
            continue

        if dry_run:
            print(f"  · {slug}: would render KB/Literature/{slug}.md (dry-run)")
            continue

        report = write_literature_note(slug, source_kind="book")
        if report.rendered:
            counts = report.items_rendered
            print(
                f"  ✓ {slug}: rendered (h={counts['h']} a={counts['a']} r={counts['r']}, "
                f"status={report.status})"
            )
            rendered += 1
        else:
            print(f"  ✗ {slug}: render failed — {report.errors}")
            continue

        for rel in _legacy_artifacts(slug):
            if (vault / rel).exists():
                cleanup_hints.append(rel)

    if cleanup_hints:
        print("\n退役舊檔 — 用 PowerShell 回收桶手動清 (禁 rm，見 CLAUDE.md):")
        print("  Add-Type -AssemblyName Microsoft.VisualBasic")
        for rel in cleanup_hints:
            abs_path = vault / rel
            print(
                "  [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile("
                f"'{abs_path}', 'OnlyErrorDialogs', 'SendToRecycleBin')"
            )

    print(f"\ndone: {rendered} book(s) rendered into KB/Literature/")
    return 0 if rendered or dry_run else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--slug",
        action="append",
        dest="slugs",
        help="只 migrate 指定 slug (可重複)；省略則跑全部種子語料。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只報告要做什麼，不寫檔。",
    )
    args = parser.parse_args(argv)
    slugs = args.slugs or SEED_BOOK_SLUGS
    return migrate(slugs, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
