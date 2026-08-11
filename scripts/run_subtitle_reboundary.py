"""字幕切點重修——把壞斷句「搬」到最近的合法語意邊界（修修 2026-08-06 裁決）。

`subtitle_finalize.find_bad_boundaries` 只**偵測**壞切點（我｜覺得、的｜…、
詞被切一半），本工具負責**修**：對每個被旗標的相鄰 cue 對，在兩句串起來的
文字裡重找切點——只能落在 jieba 詞邊界、必須通過同一套四規則、優先選語音
停頓最大處。文字內容一字不動（只有切點位置移動），時間從字級時間戳取真值。

為什麼不整集重跑 cue_builder：transcript.srt 已經過 LLM 校正 + 說話者切分 +
gap fill，重跑會丟掉那些成果。本工具走「局部重切」——校正文字保留，
只動邊界，靠 difflib 把校正後字元對回 ASR 字級時間戳。

    python scripts/run_subtitle_reboundary.py \
        --srt <transcript.srt> --words <subs/words.json> --out <out.srt>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.subtitle_finalize import (  # noqa: E402
    finalize_cues,
    format_srt,
    parse_srt_text,
)
from shared.subtitle_reboundary import MAX_ROUNDS, repair_cues  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="字幕切點重修（壞斷句搬到合法語意邊界）")
    ap.add_argument("--srt", required=True)
    ap.add_argument("--words", help="字級時間戳（有就用真值算停頓；沒有則 cue 內內插）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--rounds", type=int, default=MAX_ROUNDS)
    ap.add_argument("--no-finalize", action="store_true", help="不跑定版兩規則（工作真值用）")
    args = ap.parse_args()

    cues = parse_srt_text(Path(args.srt).read_text(encoding="utf-8-sig"))
    words = None
    if args.words:
        raw = json.loads(Path(args.words).read_text(encoding="utf-8"))
        words = raw["words"] if isinstance(raw, dict) else raw
    before = len(finalize_cues(cues)[1]["bad_boundaries"])
    cues, rb = repair_cues(cues, words=words, rounds=args.rounds)
    print(
        f"cue {len(cues)} | 搬動 {rb['moved']} 處（{rb['rounds']} 輪）"
        f"| 時間夾正 {rb['sanitized']} 處"
    )

    stats = {}
    if not args.no_finalize:
        cues, stats = finalize_cues(cues)
    after = stats.get("bad_boundaries")
    if after is None:
        after = finalize_cues(cues)[1]["bad_boundaries"]
    Path(args.out).write_text(format_srt(cues), encoding="utf-8")
    print(f"壞切點 {before} → {len(after)}")
    if stats:
        print(f"補齊空隙 {stats['closed']} 處 | 剝尾標點 {stats['stripped']} 句")
    for f in after[:10]:
        print(f"  殘留 …{f['tail']} ｜ {f['head']}…  {f['reason']}")
    print(f"→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
