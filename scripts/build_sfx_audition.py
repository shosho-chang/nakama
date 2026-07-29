"""sfx-audition：音效試聽包 — 我沒有聽覺，聽感判斷交還修修（二十一輪）。

我能量測客觀特徵（時長/峰值/頻段/音色）驗證音效與標籤相符，但**無法判斷
「這是不是台灣觀眾熟悉的那個哇哇哇」**——那是文化記憶。所以：

    build_sfx_index --query 「失望」 → 候選 N 個
    build_sfx_audition                → audition.mp3（候選串接、間隔 1s 靜音）
                                        + audition.md（編號清單）
    修修聽一次 → 回「第 3 個」        → 寫進 sound.json 定案

串接時每個候選前插一個「編號提示音」（N 短 beep）——不看清單也能數。
候選一律先正規化（loudnorm I=-18 TP=-2）再串，聽感才可比。

用法：
    python scripts/build_sfx_audition.py --query "失望" --out <dir>
    python scripts/build_sfx_audition.py --files a.wav b.wav --out <dir>
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_sfx_index import query as index_query  # noqa: E402

logger = logging.getLogger("sfx_audition")

GAP_SEC = 1.0
BEEP_HZ = 880
BEEP_SEC = 0.12


def _run(args: list[str]) -> None:
    proc = subprocess.run(args, capture_output=True, text=True, errors="replace")
    if proc.returncode != 0:
        raise SystemExit(f"ffmpeg 失敗: {(proc.stderr or '')[-300:]}")


def build(cands: list[dict], out_dir: Path, label: str) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / "_parts"
    tmp.mkdir(exist_ok=True)
    parts: list[Path] = []

    for i, c in enumerate(cands, 1):
        # 編號提示：i 聲短 beep（不看清單也數得出來）
        beeps = "".join(
            f"sine=frequency={BEEP_HZ}:duration={BEEP_SEC},apad=pad_dur={BEEP_SEC}[b{k}];"
            for k in range(i)
        )
        cue = tmp / f"{i:02d}_cue.wav"
        _run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-filter_complex",
                beeps + "".join(f"[b{k}]" for k in range(i)) + f"concat=n={i}:v=0:a=1[out]",
                "-map",
                "[out]",
                "-ar",
                "48000",
                "-ac",
                "2",
                str(cue),
            ]
        )
        body = tmp / f"{i:02d}_body.wav"
        _run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-i",
                c["path"],
                "-af",
                f"loudnorm=I=-18:TP=-2,apad=pad_dur={GAP_SEC}",
                "-ar",
                "48000",
                "-ac",
                "2",
                str(body),
            ]
        )
        parts += [cue, body]

    listfile = tmp / "list.txt"
    listfile.write_text("\n".join(f"file '{p.as_posix()}'" for p in parts), encoding="utf-8")
    audition = out_dir / f"audition_{label}.mp3"
    _run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(listfile),
            "-b:a",
            "192k",
            str(audition),
        ]
    )

    md = [f"# 音效試聽包 — {label}", "", "每段前的 beep 數 = 編號。聽完回我編號即可。", ""]
    for i, c in enumerate(cands, 1):
        star = "★常用" if c.get("usual") else ""
        md.append(f"{i}. **{c['name']}** — {c['sec']:.2f}s / {c.get('tone', '?')} {star}")
        md.append(f"   `{c['path']}`")
    (out_dir / f"audition_{label}.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    for p in tmp.iterdir():
        p.unlink()
    tmp.rmdir()
    return {
        "status": "audition",
        "label": label,
        "count": len(cands),
        "mp3": str(audition),
        "md": str(out_dir / f"audition_{label}.md"),
    }


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="音效試聽包（候選串接 + 編號提示音）")
    ap.add_argument("--query", help="語意查詢（走 sfx_index）")
    ap.add_argument("--files", nargs="*", help="直接指定候選檔路徑")
    ap.add_argument("--limit", type=int, default=6)
    ap.add_argument("--out", required=True, help="輸出資料夾")
    ap.add_argument("--label", help="試聽包標籤（預設用 query）")
    args = ap.parse_args(argv)

    if args.query:
        cands = index_query(args.query, args.limit)
    elif args.files:
        from build_sfx_index import probe

        cands = [{"path": f, "name": Path(f).stem, **probe(Path(f))} for f in args.files]
    else:
        raise SystemExit("要給 --query 或 --files")
    if not cands:
        raise SystemExit("沒有候選——換關鍵字或先建索引")
    label = args.label or (args.query or "custom").replace(" ", "-")
    print(json.dumps(build(cands, Path(args.out), label), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
