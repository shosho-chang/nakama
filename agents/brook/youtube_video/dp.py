"""YouTube 照稿影片的 DP 總調度：把規劃表上核准的列，交給各分類的元件做成素材（規格 §1 第 6 步）。

每一類的設計與規則在各自的 skill／模組（`agents/brook/youtube_video/visuals/*.py`）；這裡只負責：
- 照分類找對的元件、一列一列交件（收據對得上就沿用，不重渲）；
- 做不出來的分類（分類清單 readiness 不是 ready）標「待製作」、要修修提供的標「等素材」；
- 單列失敗不中斷，最後列出每一列的結果，寫進 `<episode>/visuals/dp_report.json`。

正式流程只做**核准**的列（規格 §4.4「全部核准才交 DP」）。`--include-pending` 讓待審的列也做——
只給流程測試用，不會改規劃表上的狀態。

用法：

    python -m agents.brook.youtube_video.dp "G:/Footages/<ep>" --dry-run
    python -m agents.brook.youtube_video.dp "G:/Footages/<ep>" [--category quote_card] [--force]
"""

from __future__ import annotations

import argparse
import importlib
import json
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from agents.brook.youtube_video import visual_plan as vp
from agents.brook.youtube_video.categories import load_categories
from agents.brook.youtube_video.visuals import delivery

# 分類 → 元件模組（都有 deliver_row(episode_dir, plan, row, *, force=...)）
RENDERERS = {
    "chapter_card": "agents.brook.youtube_video.visuals.chapter_card",
    "section_title": "agents.brook.youtube_video.visuals.section_title",
    "concept_title": "agents.brook.youtube_video.visuals.concept_title",
    "person": "agents.brook.youtube_video.visuals.person",
    "book_cover": "agents.brook.youtube_video.visuals.book_cover",
    "doc_highlight": "agents.brook.youtube_video.visuals.doc_highlight",
    "quote_card": "agents.brook.youtube_video.visuals.quote_card",
    "side_bullets": "agents.brook.youtube_video.visuals.side_bullets",
    "explainer": "agents.brook.youtube_video.visuals.explainer",
    "stock": "agents.brook.youtube_video.visuals.stock",  # 不渲染：檢查規格檔、寫收據
}
REPORT_FILE = "dp_report.json"
# 不渲染、只寫收據的分類（stock：原檔直接上 Resolve）——報告寫 delivered，不寫 rendered
NOT_RENDERED = frozenset({"stock"})

Deliver = Callable[..., tuple[dict[str, Any], bool]]


@dataclass(frozen=True)
class Outcome:
    row_id: str
    category: str
    result: str  # rendered／delivered／reused／failed／pending_build／awaiting_material／not_needed
    message: str = ""
    seconds: float = 0.0


def rows_to_build(
    plan: Mapping[str, Any], include_pending: bool = False
) -> list[Mapping[str, Any]]:
    """核准的列；流程測試時連待審的也算（重排的永遠不做）。"""
    wanted = {"approved", None} if include_pending else {"approved"}
    return [row for row in plan["rows"] if row.get("status") in wanted]


def _deliver_for(category: str) -> Deliver:
    return importlib.import_module(RENDERERS[category]).deliver_row


def build(
    episode: Path,
    plan: Mapping[str, Any],
    rows: Iterable[Mapping[str, Any]],
    *,
    force: bool = False,
    dry_run: bool = False,
    deliver_for: Callable[[str], Deliver] = _deliver_for,
    log: Callable[[str], None] = print,
) -> list[Outcome]:
    categories = load_categories()
    outcomes: list[Outcome] = []
    for row in rows:
        cat_id = row.get("category") or ""
        category = categories.get(cat_id)
        if category is None:
            outcome = Outcome(row["id"], cat_id, "failed", "這一列還沒選分類")
        elif category.readiness == "none":
            outcome = Outcome(row["id"], cat_id, "not_needed")
        elif category.readiness == "user":
            outcome = Outcome(row["id"], cat_id, "awaiting_material", "等修修提供素材")
        elif cat_id not in RENDERERS:
            outcome = Outcome(row["id"], cat_id, "pending_build", f"{category.name}還沒有元件")
        elif dry_run:
            outcome = Outcome(row["id"], cat_id, "would_render")
        else:
            started = time.monotonic()
            try:
                _, rendered = deliver_for(cat_id)(episode, plan, row, force=force)
                done = "delivered" if cat_id in NOT_RENDERED else "rendered"
                result, message = (done if rendered else "reused"), ""
            except delivery.DeliveryError as exc:
                result, message = "failed", str(exc)
            except Exception as exc:  # 單列壞掉不能拖垮整集：記下來繼續
                result, message = "failed", f"{type(exc).__name__}: {exc}"[-600:]
            outcome = Outcome(
                row["id"], cat_id, result, message, round(time.monotonic() - started, 1)
            )
        outcomes.append(outcome)
        log(f"{outcome.result:17} {outcome.category:14} {outcome.row_id} {outcome.message}")
    return outcomes


def write_report(episode: Path, outcomes: Sequence[Outcome], include_pending: bool) -> Path:
    path = Path(episode) / delivery.VISUALS_DIR / REPORT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "include_pending": include_pending,
        "summary": dict(Counter(o.result for o in outcomes)),
        "rows": [asdict(o) for o in outcomes],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DP 總調度：把核准的列做成素材")
    parser.add_argument("episode", type=Path)
    parser.add_argument("--category", action="append", help="只做這幾類（可重複）")
    parser.add_argument("--row", action="append", help="只做這幾列（可重複）")
    parser.add_argument("--force", action="store_true", help="收據對得上也重渲")
    parser.add_argument("--dry-run", action="store_true", help="只列出會做什麼")
    parser.add_argument(
        "--include-pending", action="store_true", help="待審的列也做（只給流程測試用）"
    )
    args = parser.parse_args(argv)
    plan = vp.load_plan(vp.plan_path(args.episode))
    rows = rows_to_build(plan, args.include_pending)
    if args.category:
        rows = [r for r in rows if r.get("category") in args.category]
    if args.row:
        rows = [r for r in rows if r["id"] in args.row]
    outcomes = build(args.episode, plan, rows, force=args.force, dry_run=args.dry_run)
    summary = Counter(o.result for o in outcomes)
    print("；".join(f"{k} {v}" for k, v in sorted(summary.items())) or "沒有要做的列")
    if not args.dry_run:
        print(f"報告：{write_report(args.episode, outcomes, args.include_pending)}")
    return 1 if summary.get("failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
