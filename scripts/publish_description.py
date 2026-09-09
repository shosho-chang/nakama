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
    chosen_package,
    find_packaging_dir,
    generate_description_draft,
    load_citations,
    load_footer,
    resolve_chapters,
)
from shared.background_job import atomic_job_write, load_job  # noqa: E402
from shared.config import get_vault_path  # noqa: E402
from shared.release_store import get_release, update_target  # noqa: E402

DESCRIPTION_DRAFT_GENERATING = "DESCRIPTION_DRAFT_GENERATING"
DESCRIPTION_DRAFT_INTERRUPTED = "DESCRIPTION_DRAFT_INTERRUPTED:"


def ensure_description_draft(
    episode_dir: Path,
    cut_id: str,
    *,
    hook_generator=None,
) -> dict:
    """Create the missing Release draft once; preserve any human-edited description."""
    episode_dir = Path(episode_dir)
    episode = episode_dir.name
    release = get_release(episode, cut_id)
    if release is None:
        raise ValueError(f"{cut_id} 尚未登錄——先跑 publish_prep")
    target = next((row for row in release["targets"] if row["platform"] == "youtube"), None)
    if target is None:
        raise ValueError("youtube target 不存在——先跑 publish_prep")
    if str(target.get("description") or "").strip():
        if str(target.get("error") or "").startswith(
            (DESCRIPTION_DRAFT_GENERATING, DESCRIPTION_DRAFT_INTERRUPTED)
        ):
            update_target(target["id"], error=None)
        return {"state": "preserved", "target_id": target["id"]}

    update_target(target["id"], error=DESCRIPTION_DRAFT_GENERATING)
    try:
        packaging_dir = find_packaging_dir(get_vault_path(), episode)
        packages = json.loads((packaging_dir / "packages.json").read_text(encoding="utf-8"))
        approval = json.loads((packaging_dir / "approval.json").read_text(encoding="utf-8"))
        package, description = generate_description_draft(
            episode_dir,
            packages,
            approval,
            cut_id,
            hook_generator=hook_generator,
        )
    except Exception as exc:
        error = f"{DESCRIPTION_DRAFT_INTERRUPTED} {type(exc).__name__}: {exc}"
        update_target(target["id"], error=error)
        return {"state": "interrupted", "target_id": target["id"], "error": error}

    update_target(
        target["id"],
        title=package["title"],
        description=description,
        thumbnail_path=package["thumbnail"],
        error=None,
    )
    return {"state": "ready", "target_id": target["id"], "description": description}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="發布線 Slice 2：描述欄組裝 + 文案回填")
    parser.add_argument("episode", help="episode 資料夾")
    parser.add_argument("--cut", required=True, help="winner id（如 punch-L5）")
    parser.add_argument(
        "--hook", help="hook 文字（規格：templates/video_description_hook_guide.md）"
    )
    parser.add_argument("--hook-file", help="hook 檔案（UTF-8；與 --hook 二選一）")
    parser.add_argument("--auto", action="store_true", help="用 subscription LLM 產生可編輯草稿")
    parser.add_argument("--dry-run", action="store_true", help="只印全文，不寫 DB")
    parser.add_argument("--job-receipt", type=Path, help="Web background attempt receipt")
    parser.add_argument("--attempt-id", help="Web background attempt identity")
    args = parser.parse_args(argv)

    if bool(args.job_receipt) != bool(args.attempt_id):
        raise SystemExit("--job-receipt 與 --attempt-id 必須一起使用")
    if sum((bool(args.hook), bool(args.hook_file), args.auto)) != 1:
        raise SystemExit("--hook、--hook-file、--auto 必須擇一提供")
    episode_dir = Path(args.episode)
    if args.auto:
        if args.dry_run:
            raise SystemExit("--auto 不支援 --dry-run；用 --hook/--hook-file 預覽")
        result = ensure_description_draft(episode_dir, args.cut)
        if args.job_receipt and args.attempt_id:
            job = load_job(args.job_receipt)
            if job and job.get("attempt_id") == args.attempt_id:
                failed = result["state"] == "interrupted"
                atomic_job_write(
                    args.job_receipt,
                    {
                        **job,
                        "status": "failed" if failed else "completed",
                        "exit_code": 2 if failed else 0,
                        "error": result.get("error") if failed else None,
                    },
                )
        if result["state"] == "interrupted":
            print(f"[INTERRUPTED] {result['error']}", file=sys.stderr)
            return 2
        print(f"[OK] description draft {result['state']} · target #{result['target_id']}")
        return 0
    hook = args.hook or Path(args.hook_file).read_text(encoding="utf-8")

    episode = episode_dir.name
    vault = get_vault_path()

    pdir = find_packaging_dir(vault, episode)
    packages = json.loads((pdir / "packages.json").read_text(encoding="utf-8"))
    approval = json.loads((pdir / "approval.json").read_text(encoding="utf-8"))

    pkg = chosen_package(packages, approval, args.cut)
    citations = load_citations(packages, args.cut)

    chapters = resolve_chapters(episode_dir, args.cut)

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
