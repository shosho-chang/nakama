"""選段 gate：盲審結果彙整成候選表給修修挑，挑完才寫 winners.json。

    python scripts/run_cut_shortlist.py <episode> [--format long]        # 出表
    python scripts/run_cut_shortlist.py <episode> --pick SL4,SL3,SL7     # 寫 winners

修修 2026-08-11 裁決（安吉集 review 後）：**panel 排完不要自動 top-3 進製作**。
panel 是讀逐字稿評分的，評的是素材強度，不是成片吸引力、也不是修修的品味——
安吉集三支做完他才說「其中一兩個主題好像不是特別吸引人」，那時候製作與
packaging 的成本已經付掉了。他自己的比較：「做 5 支挑 3 支」要多付兩支的
製作＋packaging（packaging 100% 線性、是 LLM 用量最大的一塊），而把 HITL
移到**排完之後、製作之前**幾乎零成本——那張表的料在 panel 跑完時就已經齊了。

輸入（highlight-cut Step 1/2 的產物）：
    highlights/candidates.json      — id/format/variant_group/hook/rationale/時長
    highlights/review_<persona>.json — 三位評分 persona（scores[].total）
    highlights/lens_brand.json       — 品牌 lens（severity: veto/caution）
    highlights/lens_renee.json       — 留存 lens（開頭診斷，選配）

輸出：
    highlights/選段候選表.md         — 貼給修修的表（群組、中位數、hook、警示）
    highlights/winners.json          — 只有 --pick 才寫（schema 由本 script 保證）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from shared.highlight_shortlist import (
    HighlightDataError,
    SCORERS,
    collect as _collect,
    write_winners as _write_winners,
)

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

HIGHLIGHTS = "highlights"


def collect(hl_dir: Path, fmt: str) -> list[dict]:
    """Compatibility wrapper for the shared ranking implementation."""
    try:
        return _collect(hl_dir, fmt)
    except HighlightDataError as exc:
        raise SystemExit(str(exc)) from exc


def render_table(rows: list[dict], fmt: str) -> str:
    out = [
        f"# 選段候選表（{fmt}）— 等修修挑",
        "",
        "panel 評的是**素材強度**，不是成片吸引力，也不是你的品味。挑幾支都可以",
        "（預設 3 支），指定 id 給我，我才寫 winners.json 進製作。",
        "",
        "| 排名 | id | 群組 | 中位數 | 阿哲/凱文/淑芬 | 長度 | 主題 | 品牌 lens |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        s = r["scores"]
        trio = "/".join(str(s.get(w) if s.get(w) is not None else "-") for w in SCORERS)
        flag = {"veto": "⛔ 否決", "caution": "⚠️ 注意"}.get(r["brand_severity"], "")
        rank = str(r["rank"]) if r["rank"] else "（同群組落選）"
        mins = f"{int(r['duration_sec'] // 60)}:{int(r['duration_sec'] % 60):02d}"
        out.append(
            f"| {rank} | **{r['id']}** | {r['group']} | {r['median']:.0f} | {trio} | "
            f"{mins} | {r['title']} | {flag} |"
        )
    out += ["", "## 各支 hook 與品牌 lens 細節", ""]
    for r in rows:
        out.append(f"### {r['id']} — {r['title']}（中位數 {r['median']:.0f}）")
        if r["hook"]:
            out.append(f"- **hook**：{r['hook']}")
        if r["brand_severity"]:
            out.append(f"- **品牌 lens {r['brand_severity']}**：{r['brand_issue']}")
            if r["brand_mitigation"]:
                out.append(f"  - 對策：{r['brand_mitigation']}")
        out.append("")
    return "\n".join(out) + "\n"


def write_winners(hl_dir: Path, rows: list[dict], picks: list[str]) -> Path:
    """Compatibility wrapper preserving the CLI's SystemExit error contract."""
    try:
        picked_veto = [p for p in picks if any(r["id"] == p and r["brand_severity"] == "veto" for r in rows)]
        if picked_veto:
            print(f"⚠️ 注意：{picked_veto} 是 brand-lens 否決段，仍照你的指定寫入", file=sys.stderr)
        return _write_winners(hl_dir, rows, picks)
    except HighlightDataError as exc:
        raise SystemExit(str(exc)) from exc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="選段 gate：候選表 → 修修挑 → winners.json")
    ap.add_argument("episode", help="episode 資料夾")
    ap.add_argument("--format", default="long", choices=("long", "short"))
    ap.add_argument("--pick", help="修修挑定的 id（逗號分隔，順序＝rank）")
    args = ap.parse_args(argv)

    hl_dir = Path(args.episode) / HIGHLIGHTS
    rows = collect(hl_dir, args.format)
    if not rows:
        raise SystemExit(f"candidates.json 裡沒有 format={args.format} 的候選")

    if args.pick:
        picks = [x.strip() for x in args.pick.split(",") if x.strip()]
        out = write_winners(hl_dir, rows, picks)
        print(f"winners.json 已寫入（{len(picks)} 支）→ {out}")
        return 0

    table = render_table(rows, args.format)
    out = hl_dir / "選段候選表.md"
    out.write_text(table, encoding="utf-8")
    print(table)
    print(f"→ {out}")
    print("\n把表貼給修修，等他指定 id 後跑 --pick 才進製作（不要自己選 top 3）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
