"""publish_description — 發布線 Slice 2：組裝描述欄 + 回填 release target 文案。

    python scripts/publish_description.py "G:\\footages\\20260723 謝伯讓" \\
        --cut punch-L5 --hook-file hook.txt [--dry-run]

流程：packaging 交接檔（vault，只讀）給已決定的標題+縮圖+citations →
broll.json 給分章 → hook 由 LLM（Claude session 吃 voice profile）寫進
--hook-file → 四段組裝 → 寫 release_targets（title/description/thumbnail_path）。

--dry-run 只印全文不寫 DB（給修修過目用）。
固定段模板佔位符（{{TODO_*}}）未填時 fail loud——不把 TODO 發上平台。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.usopp.video_description import (  # noqa: E402
    build_description,
    chapters_from_broll,
    chosen_package,
    find_packaging_dir,
    load_citations,
    load_footer,
)
from shared.config import get_vault_path  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="發布線 Slice 2：描述欄組裝 + 文案回填")
    parser.add_argument("episode", help="episode 資料夾")
    parser.add_argument("--cut", required=True, help="winner id（如 punch-L5）")
    parser.add_argument("--hook", help="hook 文字（兩三句）")
    parser.add_argument("--hook-file", help="hook 檔案（UTF-8；與 --hook 二選一）")
    parser.add_argument("--dry-run", action="store_true", help="只印全文，不寫 DB")
    args = parser.parse_args(argv)

    if bool(args.hook) == bool(args.hook_file):
        raise SystemExit("--hook 與 --hook-file 必須擇一提供")
    hook = args.hook or Path(args.hook_file).read_text(encoding="utf-8")

    episode_dir = Path(args.episode)
    episode = episode_dir.name
    vault = get_vault_path()

    pdir = find_packaging_dir(vault, episode)
    packages = json.loads((pdir / "packages.json").read_text(encoding="utf-8"))
    approval = json.loads((pdir / "approval.json").read_text(encoding="utf-8"))

    pkg = chosen_package(packages, approval, args.cut)
    citations = load_citations(packages, args.cut)

    broll_path = episode_dir / "highlights" / "tighten" / f"{args.cut}_broll.json"
    chapters = []
    if broll_path.exists():
        items = json.loads(broll_path.read_text(encoding="utf-8"))["items"]
        chapters = chapters_from_broll(items)

    footer = load_footer()
    todos = sorted(set(re.findall(r"\{\{TODO_[A-Z_]+\}\}", footer)))
    if todos and not args.dry_run:
        raise SystemExit(
            f"固定段模板還有未填佔位符 {todos}——先填 agents/usopp/templates/"
            "video_description_footer.md（--dry-run 可先看排版）"
        )

    description = build_description(hook, chapters, citations, footer)

    print("=== 標題（primary package）===")
    print(pkg["title"])
    print("\n=== 縮圖 ===")
    print(pkg["thumbnail"] or "（無）")
    print("\n=== 描述全文 ===")
    print(description)

    if args.dry_run:
        print("\n[dry-run] 未寫 DB")
        return 0

    from shared.release_store import get_release, update_target

    rel = get_release(episode, args.cut)
    if rel is None:
        raise SystemExit(f"{args.cut} 尚未登錄——先跑 publish_prep")
    target = next((t for t in rel["targets"] if t["platform"] == "youtube"), None)
    if target is None:
        raise SystemExit("youtube target 不存在——先跑 publish_prep")
    update_target(
        target["id"],
        title=pkg["title"],
        description=description,
        thumbnail_path=pkg["thumbnail"],
    )
    print(f"\n[OK] 已回填 youtube target #{target['id']}（title/description/thumbnail）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
