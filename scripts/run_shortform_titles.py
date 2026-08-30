"""shortform-titles：短片動態字卡——逐字稿保證取代 DP 稽核鏈。

**這支只服務短片。** 長片的字卡走 ADR-065／066 的 Director → DP → 語意稽核
收據鏈（`run_short_titles.py`），每張卡都帶著一份 15 欄的
`visual_materialization`。

## 為什麼短片可以不走那條鏈

那條鏈要防的是「畫面上出現逐字稿裡沒有的字」。短片的字卡企劃宣告
``covers_full_transcript``，意思是**每一個 state 的字都逐 cue 承接該支的
tight SRT 原話**——沒有創作、沒有改寫、沒有 LLM 生成的句子。

這個保證是**機械可驗**的，而且驗證器早就寫好了，就在同一支 script 裡：

- ``_validate_transcript_driven_title``：每個 state 的字必須是其
  ``source_cues`` 的逐字稿精確片段
- ``_validate_full_transcript_coverage``：全片逐字稿必須被 state 完整覆蓋，
  不能挑著顯示
- ``_validate_short_motion_grammar`` / ``_validate_brand_pattern_usage`` /
  ``_validate_rendered_frame_safety``：動態文法、品牌樣式、逐幀安全區

所以短片線用「逐字稿保證」換掉「DP 稽核」，防的是同一件事，成本低一個量級。
需要創作性文字（概念卡、標語）的短片仍然要走完整視覺產線。

## 底部字幕互斥

``covers_full_transcript`` 的短片**不上 Resolve burn-in 字幕軌**——字全部是
動態字卡，兩層一起出現會疊字（修修 2026-08-30 指正）。導播端已據此不上字幕軌。

用法：
    py -3.10 scripts/run_shortform_titles.py <episode> --id punch-S02
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_short_titles import apply as _apply  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="短片動態字卡（逐字稿保證）")
    parser.add_argument("episode")
    parser.add_argument("--id", required=True, help="winner id（如 punch-S02）")
    parser.add_argument("--stills", help="render 後抓樣張到此資料夾")
    parser.add_argument("--validate-only", action="store_true", help="只驗企劃，不 render")
    args = parser.parse_args(argv)

    # `run_short_titles.validate_plan` 是**長片**的 preflight——它驗的是
    # Director／DP／語意稽核的收據鏈。短片線用逐字稿保證換掉那條鏈，所以
    # 這裡走同一支 apply 的 validate_only 模式，驗的是同一批短片驗證器。
    out = _apply(
        Path(args.episode),
        args.id,
        Path(args.stills) if args.stills else None,
        transcript_guarantee=True,
        validate_only=args.validate_only,
    )
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
