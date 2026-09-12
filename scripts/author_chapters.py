#!/usr/bin/env python3
"""author_chapters.py — 把 agent 切好的章節表驗過、落成上架用的檔。

    python scripts/author_chapters.py "G:/Footages/<ep>" --cut full < chapters.json

stdin 吃的 JSON：

    {
      "source": "editorial-master/v1/master.srt",
      "chapters": [
        {"t0": 0.0,    "title": "開場：這集在聊什麼"},
        {"t0": 318.0,  "title": "「AI 用到極致」長什麼樣"}
      ]
    }

`t0` 是**成品時鐘的秒數**，跟上架的那支影片同一個時鐘。切章本身是語意工作，由當下
執行的 agent 做；本腳本只驗規則、只落檔，**不會替你想章節**。

驗的是 YouTube 的硬性規則（首章 0:00、至少 3 章、遞增、每章至少 10 秒）。違反其中
任何一條，YouTube 會整份忽略而且不報錯——所以在這裡擋下來。`--duration-sec` 給了
就順便驗最後一章沒有超出片長。

落點：`<episode>/publish/chapters/<cut_id>.json`，`resolve_chapters` 會讀它。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001 - 舊 Python 沒有 reconfigure
        pass

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.usopp.video_description import AUTHORED_CHAPTERS_RELDIR, fmt_ts  # noqa: E402
from shared.schemas.publish_chapters import (  # noqa: E402
    PUBLISH_CHAPTERS_SCHEMA,
    PublishChaptersFileV1,
)


def author(
    episode_dir: Path,
    *,
    cut_id: str,
    payload: dict,
    duration_sec: float | None = None,
) -> Path:
    episode_dir = Path(episode_dir)
    if not episode_dir.is_dir():
        raise SystemExit(f"episode 資料夾不存在：{episode_dir}")

    source = payload.get("source")
    if not isinstance(source, str) or not source.strip():
        raise SystemExit("缺 `source`——要記下這份章節是讀哪一份逐字稿切的，換稿就該重切")

    document = PublishChaptersFileV1.model_validate(
        {
            "schema": PUBLISH_CHAPTERS_SCHEMA,
            "episode": episode_dir.name,
            "cut_id": cut_id,
            "generated_at": datetime.now(timezone.utc),
            "source": source.strip(),
            "chapters": payload.get("chapters") or [],
        }
    )

    if duration_sec is not None:
        last = document.chapters[-1]
        if last.t0 >= duration_sec:
            raise SystemExit(
                f"最後一章 {fmt_ts(last.t0)}「{last.title}」超出片長 {fmt_ts(duration_sec)}"
            )

    out = episode_dir / AUTHORED_CHAPTERS_RELDIR / f"{cut_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        document.model_dump_json(indent=2, by_alias=True) + "\n", encoding="utf-8", newline="\n"
    )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("episode", type=Path, help="episode 資料夾")
    parser.add_argument("--cut", required=True, help="cut id（完整版用 full）")
    parser.add_argument(
        "--duration-sec", type=float, default=None, help="片長；給了就驗最後一章沒超出"
    )
    args = parser.parse_args(argv)

    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as error:
        raise SystemExit(f"stdin 不是合法 JSON：{error}") from error
    if not isinstance(payload, dict):
        raise SystemExit("stdin 要的是一個 JSON object")

    out = author(args.episode, cut_id=args.cut, payload=payload, duration_sec=args.duration_sec)
    document = PublishChaptersFileV1.model_validate_json(out.read_text(encoding="utf-8"))
    print(f"{len(document.chapters)} 章 → {out}")
    for row in document.chapters:
        print(f"  {fmt_ts(row.t0)} {row.title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
