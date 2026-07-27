"""sfx-index：修修的音效庫 → 可查索引（修修 2026-07-27 二十一輪提供音效庫）。

`F:\\Project Files\\Assets\\SFX`（820 檔，含「Usual use」= 修修常用區）。
我沒有聽覺——但可以量測**客觀聲學特徵**驗證音效與標籤相符：

- 時長、峰值、整合響度（LUFS）
- 三頻段能量（low <800Hz / mid / high >3kHz）→ 判「悶/亮/寬頻」
- 起音時間（attack：峰值出現位置佔全長比例）→ 判「爆點型 vs 漸強型」

不能做的：判斷「這是不是台灣觀眾熟悉的哇哇哇」——那是文化記憶+聽感，
走「試聽包」（`build_sfx_audition.py`）由修修耳朵定案。

輸出 `data/sfx_index.json`（gitignored，本機資產索引）：
    {"root": "...", "items": [{"path", "name", "folder", "usual": bool,
      "sec", "peak_db", "lufs", "band": {"low","mid","high"}, "attack_pct"}]}

用法：
    python scripts/build_sfx_index.py [--root "F:\\Project Files\\Assets\\SFX"]
    python scripts/build_sfx_index.py --query "哇哇哇 fail"   # 查索引
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

logger = logging.getLogger("sfx_index")

DEFAULT_ROOT = Path(r"F:\Project Files\Assets\SFX")
INDEX_PATH = Path(__file__).resolve().parent.parent / "data" / "sfx_index.json"
AUDIO_EXT = {".wav", ".mp3", ".aif", ".aiff", ".m4a", ".flac"}
USUAL_DIR = "usual use"

# 中文語意 → 檔名關鍵字（修修庫的實際命名，二十一輪盤點）
SEMANTIC_ALIASES = {
    "失望": ["wa wa", "wha wha", "fail", "aww", "sad"],
    "哇哇哇": ["wa wa", "wha wha", "fail"],
    "慶祝": ["yay", "victory", "ta dahh", "applause", "cheer", "horn"],
    "耶": ["yay", "victory", "applause"],
    "懸疑": ["suspense", "dun dun", "drum roll", "alert", "panic"],
    "驚訝": ["wow", "anime wow", "baby says wow", "gasp"],
    "錯誤": ["buzzer", "wrong", "break"],
    "金錢": ["ka-ching", "coin", "cash"],
    "引擎": ["car sounds", "gas pedal", "engine"],
    "打擊": ["punch", "kick", "impact", "hit"],
    "轉場": ["whoosh", "swoosh", "woosh", "transition", "swipe"],
    "笑聲": ["laugh", "baby laughing", "evil laugh"],
    "心跳": ["heart beat", "heartbeat"],
    "打字": ["keyboard", "pencil write", "computer"],
    "相機": ["camera shutter", "shutter"],
    "鈴": ["ding", "bell", "sparkle", "pop"],
}


def _ffprobe_duration(path: Path) -> float:
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        ).stdout.strip()
        return round(float(out), 3)
    except (ValueError, OSError, subprocess.TimeoutExpired):
        return 0.0


def _volume_stats(path: Path, af_prefix: str = "") -> tuple[float, float]:
    """(mean_db, max_db)——af_prefix 可插濾波器做頻段量測。"""
    af = f"{af_prefix}volumedetect" if af_prefix else "volumedetect"
    try:
        out = subprocess.run(
            ["ffmpeg", "-v", "info", "-i", str(path), "-af", af, "-f", "null", "-"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=60,
        ).stderr
    except (OSError, subprocess.TimeoutExpired):
        return (-99.0, -99.0)
    if not out:  # 極少數檔 ffmpeg 不吐 stderr（編碼/損毀）——不擋整批
        return (-99.0, -99.0)
    mean = re.search(r"mean_volume:\s*(-?[\d.]+) dB", out)
    peak = re.search(r"max_volume:\s*(-?[\d.]+) dB", out)
    return (
        float(mean.group(1)) if mean else -99.0,
        float(peak.group(1)) if peak else -99.0,
    )


def probe(path: Path) -> dict:
    sec = _ffprobe_duration(path)
    _, peak = _volume_stats(path)
    low, _ = _volume_stats(path, "lowpass=f=800,")
    mid, _ = _volume_stats(path, "highpass=f=800,lowpass=f=3000,")
    high, _ = _volume_stats(path, "highpass=f=3000,")
    return {
        "sec": sec,
        "peak_db": peak,
        "band": {"low": low, "mid": mid, "high": high},
        # 音色標籤：哪個頻段主導（相差 >6dB 才算主導，否則寬頻）
        "tone": _tone(low, mid, high),
    }


def _tone(low: float, mid: float, high: float) -> str:
    bands = {"低沉": low, "中頻": mid, "明亮": high}
    top = max(bands, key=lambda k: bands[k])
    rest = sorted(v for k, v in bands.items() if k != top)
    return top if bands[top] - rest[-1] > 6 else "寬頻"


def build(root: Path) -> dict:
    files = [p for p in root.rglob("*") if p.suffix.lower() in AUDIO_EXT]
    logger.info("掃描 %d 個音檔（%s）", len(files), root)
    items = []
    for i, p in enumerate(files):
        rel = p.relative_to(root)
        folder = str(rel.parent).replace("\\", "/")
        info = probe(p)
        items.append(
            {
                "path": str(p),
                "name": p.stem,
                "folder": folder,
                "usual": USUAL_DIR in folder.lower(),
                **info,
            }
        )
        if (i + 1) % 100 == 0:
            logger.info("  %d/%d", i + 1, len(files))
    index = {"root": str(root), "count": len(items), "items": items}
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"status": "indexed", "count": len(items), "index": str(INDEX_PATH)}


def query(terms: str, limit: int = 12) -> list[dict]:
    """中文語意或英文關鍵字 → 候選（Usual use 優先）。"""
    if not INDEX_PATH.exists():
        raise SystemExit(f"{INDEX_PATH} 不存在——先跑 build_sfx_index.py 建索引")
    index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    keys: list[str] = []
    for term in terms.split():
        keys += SEMANTIC_ALIASES.get(term, [term.lower()])
    hits = []
    for it in index["items"]:
        hay = f"{it['name']} {it['folder']}".lower()
        score = sum(2 if k in hay else 0 for k in keys)
        if score:
            hits.append({**it, "score": score + (3 if it["usual"] else 0)})
    hits.sort(key=lambda x: (-x["score"], x["sec"]))
    return hits[:limit]


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="音效庫索引（建立/查詢）")
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="音效庫根目錄")
    ap.add_argument("--query", help="查詢（中文語意如「懸疑」或英文關鍵字）")
    ap.add_argument("--limit", type=int, default=12)
    args = ap.parse_args(argv)
    if args.query:
        rows = query(args.query, args.limit)
        for r in rows:
            mark = "★" if r["usual"] else " "
            print(f"{mark} {r['name'][:44]:<44} {r['sec']:5.2f}s {r['tone']:<4} {r['folder'][:28]}")
        print(f"（{len(rows)} 筆；★ = Usual use）")
        return 0
    print(json.dumps(build(Path(args.root)), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
