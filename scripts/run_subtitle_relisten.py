"""字幕 QC 重聽：對指定 cue 裁音檔片段 → 無 prompt 重辨識 → 三方對照。

subtitle-correct 的 QC 紀律要求「聽音檔＝重開 WhisperX」（不是憑語感猜）：
對可疑 cue 裁出 ±context 個 cue 的音檔，**不給 initial_prompt** 重辨識，
比對「原文 / 建議 / 重聽」三方。無 prompt 是關鍵——原本的辨識就是被
initial_prompt 與上下文帶偏，帶著同一個 prompt 重跑只會重現同一個錯。

用法：
    # 從 transcript.qc.md 自動撿 HIGH（預設）
    py -3.10 scripts/run_subtitle_relisten.py "G:/footages/20260415 安吉"

    # 指定行號 + 也收 MEDIUM
    py -3.10 scripts/run_subtitle_relisten.py <episode> --lines 63,67,75 --risk high,medium

行號 = `transcript.qc.md` 的 Line，對應 **subs/raw.srt** 的 cue 序號
（transcript.srt 經 speaker split / gap fill 後序號已位移，不可拿來對照）。

產出 `subs/relisten.json` + stdout 對照表；下游由 agent 判讀後更新
corrections.json 重跑 `--apply`。
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

logger = logging.getLogger("relisten")

_TS = re.compile(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})")


def _sec(ts: str) -> float:
    h, m, s, ms = _TS.match(ts).groups()
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def parse_srt(path: Path) -> dict[int, dict]:
    """SRT → {seq: {start, end, text}}（秒）。"""
    cues: dict[int, dict] = {}
    for block in path.read_text(encoding="utf-8").strip().split("\n\n"):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if len(lines) < 3:
            continue
        try:
            seq = int(lines[0].strip())
        except ValueError:
            continue
        if "-->" not in lines[1]:
            continue
        a, b = lines[1].split("-->")
        cues[seq] = {
            "start": _sec(a.strip()),
            "end": _sec(b.strip()),
            "text": " ".join(lines[2:]).strip(),
        }
    return cues


def parse_qc(path: Path, risks: set[str]) -> list[dict]:
    """transcript.qc.md → [{line, risk, original, suggestion, reason}]。"""
    items: list[dict] = []
    cur: dict | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"###\s*\[(\w+)\]\s*Line\s*(\d+)", raw)
        if m:
            if cur:
                items.append(cur)
            cur = {"risk": m.group(1).lower(), "line": int(m.group(2))}
            continue
        if cur is None:
            continue
        for key, label in (("original", "原文"), ("suggestion", "建議"), ("reason", "理由")):
            m2 = re.match(rf"-\s*\*\*{label}\*\*：(.*)", raw)
            if m2:
                cur[key] = m2.group(1).strip()
    if cur:
        items.append(cur)
    return [i for i in items if i["risk"] in risks]


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="字幕 QC 重聽（無 prompt 重辨識）")
    ap.add_argument("episode")
    ap.add_argument("--lines", help="逗號分隔的 cue 序號（給了就不讀 qc.md）")
    ap.add_argument("--risk", default="high", help="從 qc.md 撿哪些風險等級（逗號分隔）")
    ap.add_argument("--context", type=int, default=1, help="前後各取幾個 cue 當上下文")
    ap.add_argument("--model", default="large-v3")
    ap.add_argument("--audio", help="音檔（預設 <episode>/normalized.wav）")
    args = ap.parse_args(argv)

    ep = Path(args.episode)
    srt = ep / "subs" / "raw.srt"
    if not srt.exists():
        logger.error(f"找不到 {srt}")
        return 1
    cues = parse_srt(srt)
    audio = Path(args.audio) if args.audio else ep / "normalized.wav"

    if args.lines:
        targets = [{"line": int(x), "risk": "manual"} for x in args.lines.split(",") if x.strip()]
    else:
        qc = ep / "transcript.qc.md"
        if not qc.exists():
            logger.error(f"找不到 {qc}（先跑 subtitle-correct --apply），或改用 --lines")
            return 1
        targets = parse_qc(qc, {r.strip().lower() for r in args.risk.split(",")})

    targets = [t for t in targets if t["line"] in cues]
    if not targets:
        logger.error("沒有可重聽的目標")
        return 1
    logger.info(f"重聽 {len(targets)} 個 cue（context ±{args.context}）")

    import whisperx  # noqa: E402

    from shared.transcriber import _get_asr_model  # noqa: E402

    # ⚠️ initial_prompt 明確給空字串：帶原 prompt 重跑會重現同一個偏誤
    model = _get_asr_model(args.model, initial_prompt="")

    results = []
    with tempfile.TemporaryDirectory() as td:
        for i, t in enumerate(targets, 1):
            seq = t["line"]
            lo = min((c for c in cues if c >= seq - args.context), default=seq)
            hi = max((c for c in cues if c <= seq + args.context), default=seq)
            t0, t1 = cues[lo]["start"], cues[hi]["end"]
            clip = Path(td) / f"c{seq}.wav"
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-v",
                    "error",
                    "-i",
                    str(audio),
                    "-ss",
                    f"{t0:.3f}",
                    "-t",
                    f"{max(0.4, t1 - t0):.3f}",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    str(clip),
                ],
                check=True,
                capture_output=True,
            )
            tr = model.transcribe(whisperx.load_audio(str(clip)), batch_size=8, language="zh")
            heard = " ".join(s.get("text", "").strip() for s in tr.get("segments") or []).strip()
            row = {
                "line": seq,
                "risk": t.get("risk"),
                "window": [round(t0, 2), round(t1, 2)],
                "context_original": " / ".join(
                    cues[c]["text"] for c in range(lo, hi + 1) if c in cues
                ),
                "original": t.get("original") or cues[seq]["text"],
                "suggestion": t.get("suggestion"),
                "reason": t.get("reason"),
                "reheard": heard,
            }
            results.append(row)
            logger.info(f"[{i}/{len(targets)}] line {seq}: 重聽「{heard}」")

    out = ep / "subs" / "relisten.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✅ {out}（{len(results)} 項）\n")
    for r in results:
        print(f"--- Line {r['line']} [{r['risk']}] {r['window'][0]}s")
        print(f"  原文  ：{r['original']}")
        if r.get("suggestion"):
            print(f"  建議  ：{r['suggestion']}")
        print(f"  重聽  ：{r['reheard']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
