"""把音樂庫的曲目落進 episode 的 `assets/bgm/`，並寫授權收據（ADR-067 短片線）。

`run_short_bgm.py` 只吃 `<episode>/assets/bgm/<name>.wav`，而音樂庫給的是 mp3。
這支負責轉檔、命名與留下來源證據——**不要手動複製**，手動複製留不下 SHA-256，
之後要查「這首是哪裡來的、授權是什麼」就沒得查。

命名 `<mood>-<原檔 stem>.wav`。mood 取自 `E:\\data\\music\\short-<mood>` 的資料夾名
（punch／story／value，對應 highlight-cut 的三個 miner 視角），配曲時照 cut id 前綴選。

收據用與 stock 素材同一份契約（`podcast-highlight-asset-acquisition-receipt-v1`）。
Uppbeat 的下載檔名帶曲目 id（`…-main-version-<id>-<mm-ss>.mp3`），那就是 provider
的指標；曲目頁 URL 下載時沒留下來，`source_url` 給 null 並在 note 說明，
**不要憑檔名拼一個 URL 出來**。

用法：
    python scripts/stage_bgm_track.py <episode> --source <mp3> --mood punch
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ACQUISITION_CONTRACT = "podcast-highlight-asset-acquisition-receipt-v1"
MOODS = ("punch", "story", "value")
#: Uppbeat 下載檔名尾巴：`-main-version-<id>-<mm>-<ss>.mp3`（也有 id 在最後的變體）
_ID = re.compile(r"main-version-(?:(\d+)-\d{2}-\d{2}|\d{2}-\d{2}-(\d+))$")


def stage(episode_dir: Path, source: Path, mood: str, provider: str, license_text: str) -> Path:
    out_dir = episode_dir / "assets" / "bgm"
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = f"{mood}-{source.stem}"
    wav = out_dir / f"{slug}.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(source),
            "-ar",
            "48000",
            "-ac",
            "2",
            "-c:a",
            "pcm_s24le",
            str(wav),
            "-y",
        ],
        check=True,
    )
    match = _ID.search(source.stem)
    item_id = (match.group(1) or match.group(2)) if match else None
    receipt = {
        "contract": ACQUISITION_CONTRACT,
        "acquired_at": datetime.fromtimestamp(source.stat().st_mtime, UTC).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "asset_id": slug,
        "episode_id": episode_dir.name,
        "provider": provider,
        "provider_item_id": item_id,
        "source_url": None,
        "note": (
            "曲目頁 URL 在下載時沒有留下來；provider_item_id 是下載檔名裡的曲目 id，"
            "回音樂庫查得到。不從檔名拼 URL。"
        ),
        "license": license_text,
        "source_class": "licensed_music",
        "mood": mood,
        "original_filename": source.name,
        "original_media": {
            "path": f"assets/bgm/{slug}.wav",
            "bytes": wav.stat().st_size,
            "sha256": hashlib.sha256(wav.read_bytes()).hexdigest(),
            "source_mp3_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        },
    }
    (out_dir / f"{slug}.acquisition.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"{slug}.wav  {wav.stat().st_size:,} bytes  id={item_id}  ← {source.name}")
    return wav


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="音樂落地 episode assets/bgm ＋ 授權收據")
    parser.add_argument("episode")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--mood", choices=MOODS, required=True)
    parser.add_argument("--provider", default="uppbeat")
    parser.add_argument("--license", default="Uppbeat subscription license")
    args = parser.parse_args(argv)
    if not args.source.is_file():
        raise SystemExit(f"找不到音樂檔：{args.source}")
    stage(Path(args.episode), args.source, args.mood, args.provider, args.license)
    return 0


if __name__ == "__main__":
    sys.exit(main())
