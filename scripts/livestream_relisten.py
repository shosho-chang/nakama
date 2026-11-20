"""社群直播：把幾句字幕截出原音，用 faster-whisper 重聽，給「待確認字詞」當證據。

稽核／仲裁留下的 unresolved 項目，在丟給修修之前先用這支重聽（2026-11-20 教訓：14 處裡
13 處重聽＋查網路＋翻聊天室就能定）。輸出每句的 Memo 原文、前後文，以及兩種重聽結果：
不給提示（plain）與給本場專有名詞提示（hint）。

用法：
    python scripts/livestream_relisten.py \
        --srt "<episode>/subtitle-release/memo-dual-audit-v1/release.srt" \
        --wav "<episode>/normalized.wav" \
        --cues 1041,1142,2969 \
        --hint "這是一場關於 AI Agent 的直播，提到 Claude、Grok Bot、Zoom" \
        --out "<scratch>/relisten.json"
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

_BLOCK = re.compile(r"(\d+)\n(\d\d:\d\d:\d\d,\d+) --> (\d\d:\d\d:\d\d,\d+)\n(.*?)(?:\n\n|\Z)", re.S)


def _sec(ts: str) -> float:
    h, m, rest = ts.split(":")
    s, _, ms = rest.partition(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--srt", required=True)
    ap.add_argument("--wav", required=True)
    ap.add_argument("--cues", required=True, help="逗號分隔的 cue 編號")
    ap.add_argument("--hint", default=None, help="給 faster-whisper 的專有名詞提示")
    ap.add_argument("--pad", type=float, default=3.0, help="前後多截幾秒")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    text = Path(args.srt).read_text(encoding="utf-8").lstrip("\ufeff")
    cues = {int(n): (a, b, t) for n, a, b, t in _BLOCK.findall(text)}
    wanted = [int(x) for x in args.cues.split(",") if x.strip()]

    from faster_whisper import WhisperModel

    model = WhisperModel("Systran/faster-whisper-large-v3", device="cuda", compute_type="float16")
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        for n in wanted:
            a, b, t = cues[n]
            s, e = max(0.0, _sec(a) - args.pad), _sec(b) + args.pad
            clip = Path(tmp) / f"c{n}.wav"
            subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{s:.2f}",
                 "-to", f"{e:.2f}", "-i", args.wav, "-ac", "1", "-ar", "16000", str(clip)],
                check=True,
            )  # fmt: skip
            heard = {}
            for tag, prompt in (("plain", None), ("hint", args.hint)):
                if tag == "hint" and not prompt:
                    continue
                segs, _ = model.transcribe(
                    str(clip), language="zh", beam_size=5, initial_prompt=prompt
                )
                heard[tag] = "".join(x.text for x in segs)
            context = " / ".join(
                cues[i][2].replace("\n", " ") for i in range(n - 3, n + 4) if i in cues
            )
            row = {"cue": n, "start": a, "memo": t, "context": context, **heard}
            results.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
