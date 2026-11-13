"""長片 tight SRT 重建——從（斷句修好的）逐字稿重出，**不碰 Resolve、不重算影片**。

長片不燒字幕（ADR-055 Q4b）：SRT 只當 CC 上傳、審核頁當字幕軌顯示。所以斷句
修好之後只要換 SRT，影片檔完全不用重出，這支工具就是走這條路。

    # 1) 先在逐字稿上修斷句（單一時鐘，不需任何映射）
    python scripts/run_subtitle_reboundary.py \
        --srt "<ep>/transcript.srt" --words "<ep>/subs/words.json" \
        --audio "<ep>/program_v2/normalized.wav" \
        --out "<ep>/subs/transcript_silence.srt" --no-finalize

    # 2) 再把三支長片的 tight SRT 重建出來
    python scripts/run_tight_reboundary.py "<ep>" --id SL3 \
        --transcript "<ep>/subs/transcript_silence.srt" --apply

## 兩個時鐘（2026-08-12 血淚）

本集 `normalized.wav` 有兩份：episode 根目錄（8/04）與 `program_v2/`（8/07），
差 **71.010 秒**。`cuts.json` 用根目錄時鐘，`transcript.srt` 用 program_v2 時鐘。
先前是靠「暫時把 transcript.srt 平移再跑」的一次性 script 繞過去，硬寫魔術數字。
現在偏移由 `pause_map.detect_audio_offset` 直接量兩個音檔（包絡互相關）——不靠
文字、不靠逐字稿版本，逐字稿重跑過也不影響。量完再用停頓圖自檢驗收。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.run_short_tighten import (  # noqa: E402
    _keep_segments,
    _retime_srt,
    tight_to_feed,
)
from shared.pause_map import (  # noqa: E402
    PauseMap,
    build_envelope,
    cache_path_for,
    detect_audio_offset,
)
from shared.subtitle_finalize import find_bad_boundaries, parse_srt_text  # noqa: E402

logger = logging.getLogger(__name__)
Seg = tuple[float, float]


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="長片 tight SRT 重建（不碰影片）")
    ap.add_argument("episode")
    ap.add_argument("--id", required=True)
    ap.add_argument("--transcript", help="要用的逐字稿（預設 <ep>/transcript.srt）")
    ap.add_argument(
        "--cuts-audio", default="normalized.wav", help="與 cuts.json 同時鐘的音檔（相對 episode）"
    )
    ap.add_argument(
        "--transcript-audio",
        default="program_v2/normalized.wav",
        help="與逐字稿同時鐘的音檔（相對 episode）",
    )
    ap.add_argument("--apply", action="store_true", help="寫出新 revision（預設只報告）")
    args = ap.parse_args(argv)

    ep = Path(args.episode)
    cid = args.id
    cuts = json.loads((ep / "highlights" / "tighten" / f"{cid}_cuts.json").read_text("utf-8"))
    segs = _keep_segments(float(cuts["t_start"]), float(cuts["t_end"]), cuts["cuts"])

    cuts_audio, tr_audio = ep / args.cuts_audio, ep / args.transcript_audio
    offset = 0.0
    if tr_audio.exists() and cuts_audio.exists() and tr_audio.resolve() != cuts_audio.resolve():
        offset = detect_audio_offset(cuts_audio, tr_audio)
    logger.info("逐字稿時鐘 − cuts 時鐘 = %+.3fs", offset)

    srt_dir = ep / "highlights" / "srt"
    existing = sorted(srt_dir.glob(f"{cid}_tight_r*.srt"))
    if not args.apply:
        print(f"（未加 --apply）目前最新 = {existing[-1].name if existing else '無'}")
        print(f"  保留段 {len(segs)} 段 / {sum(b - a for a, b in segs):.1f}s")
        print(f"  時鐘偏移 {offset:+.3f}s")
        return 0

    dst, n_cues = _retime_srt(
        ep,
        cid,
        segs,
        cuts["cuts"],
        transcript=Path(args.transcript) if args.transcript else None,
        clock_offset=offset,
    )

    # 驗收：cue 切點必須整體落在靜音上（用 cuts 時鐘的音檔，零映射猜測）
    fresh = parse_srt_text(dst.read_text(encoding="utf-8-sig"))
    env = build_envelope(cuts_audio, cache_path_for(cuts_audio, ep / "subs"))
    pause = PauseMap(env, to_audio=lambda t: tight_to_feed(t, segs))
    ratio = pause.sanity_check([c[0] for c in fresh[1:]], [(c[0] + c[1]) / 2 for c in fresh])
    bad = find_bad_boundaries(fresh, pause=pause)

    print(f"\n{dst.name}：{n_cues} cue｜時鐘自檢比值 {ratio:.2f}")
    print(f"  壞切點 {len(bad)}")
    for f in bad[:10]:
        print(f"    cue{f['cue']}：…{f['tail']} ｜ {f['head']}…  {f['reason']}")
    if len(bad) > 10:
        print(f"    …另有 {len(bad) - 10} 個")
    print(f"\n→ {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
