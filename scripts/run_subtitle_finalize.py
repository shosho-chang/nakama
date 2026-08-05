"""bespoke SRT 定版 CLI——套修修 2026-08-05 兩條字幕顯示規則。

① 句尾零標點（閉合符保留、其前標點剝除）② cue 間 ≤3s 空隙補平（連續顯示）。

主 pipeline **不用手跑**——resolve-project / highlight-cut 上軌時自動套在
顯示層副本上。這支給繞過 pipeline 的 SRT 用（翻譯精選、外部工具產物、
episode 外的獨立剪輯）。

用法：
    python scripts/run_subtitle_finalize.py <in.srt>              # → <in>_display.srt
    python scripts/run_subtitle_finalize.py <in.srt> -o out.srt
    python scripts/run_subtitle_finalize.py <in.srt> --in-place
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.subtitle_finalize import finalize_srt_file  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SRT 顯示層定版（句尾零標點 + 空隙補平）")
    parser.add_argument("srt", help="來源 SRT")
    parser.add_argument("-o", "--out", help="輸出路徑（預設 <in>_display.srt）")
    parser.add_argument("--in-place", action="store_true", help="原地覆寫來源檔")
    parser.add_argument(
        "--gap-close-max", type=float, default=3.0,
        help="補平上限秒數；更長視為真靜默不補（預設 3.0）",
    )
    args = parser.parse_args(argv)
    src = Path(args.srt)
    if not src.exists():
        print(f"找不到 {src}")
        return 1
    if args.in_place and src.name == "transcript.srt":
        print(
            "拒絕：transcript.srt 是工作真值（highlight-cut 等靠 cue 時間切片），"
            "不可原地 gap close。上軌時 resolve-project 會自動在顯示層副本套定版。"
        )
        return 1
    dst = src if args.in_place else Path(args.out) if args.out else src.with_name(f"{src.stem}_display.srt")
    stats = finalize_srt_file(src, dst, gap_close_max=args.gap_close_max)
    print(
        f"{dst.name}: {stats['cues']} cues | 尾標點剝 {stats['stripped']} 句 | "
        f"空隙補平 {stats['closed']} 處"
    )
    for idx, gap in stats["true_silences"]:
        print(f"  >「{args.gap_close_max}s 真靜默不補（字幕該消失）: cue{idx} 後 {gap}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
