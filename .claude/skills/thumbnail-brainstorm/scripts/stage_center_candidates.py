#!/usr/bin/env python3
"""stage_center_candidates.py — 把圖庫搜到的中央卡候選下載成 gate 上的縮圖池。

    python stage_center_candidates.py --packaging-dir "G:/Footages/<ep>/packaging" \
        --cut-id punch-L04 --episode-slug 20260805-linzhichen --results results.json

`results.json` 是 **Envato 搜尋結果**（agent 用 Elements MCP 的 `search_photos`
跑出來、逐筆抄下來的），每筆四個欄位：

    [{"preview_url": "https://elements-resized.envatousercontent.com/...",
      "item_url":    "https://elements.envato.com/<slug>-22KBKWG",
      "title":       "cute golden retriever lying on bright yellow sofa",
      "author":      "LightFieldStudios",
      "query":       "golden retriever lying on luxury sofa pampered"}]

下載的是**浮水印預覽**，不是授權檔——gate 上只是拿來挑。修修選定後，桌機端才
依 `source` 走既有的 Elements 下載流程取正式檔（見 brook-director SKILL.md）。
挑十張下十張授權檔，九張是白下的。

搜尋時務必帶 `orientation: landscape`：中央卡是橫的，直式素材在
`composition_receipt._assert_center_fits_card` 那關本來就會被擋，讓它進 gate
只是浪費修修一次點擊。本腳本會再驗一次實際下載到的尺寸。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from PIL import Image  # noqa: E402

from shared.config import get_vault_path  # noqa: E402
from shared.schemas.packaging import (  # noqa: E402
    CENTER_CANDIDATES_SCHEMA,
    CenterCandidatesFileV1,
)

# Elements 的品項頁網址結尾就是品項 id（大小寫混用，例如 22KBKWG / ML6MZD5）。
_ITEM_ID_RE = re.compile(r"^https://elements\.envato\.com/[^/\s]+-([A-Za-z0-9]{5,20})$")
_MAX_PREVIEW_BYTES = 8 * 1024 * 1024
_TIMEOUT_SEC = 30


def item_id(item_url: str) -> str:
    match = _ITEM_ID_RE.match(item_url.strip())
    if match is None:
        raise SystemExit(f"不是 Elements 品項網址，拒絕收錄：{item_url}")
    return match.group(1)


def _fetch(url: str) -> bytes:
    if not url.startswith("https://"):
        raise SystemExit(f"預覽圖只收 https：{url}")
    with urllib.request.urlopen(url, timeout=_TIMEOUT_SEC) as response:  # noqa: S310 — https 已驗
        payload = response.read(_MAX_PREVIEW_BYTES + 1)
    if len(payload) > _MAX_PREVIEW_BYTES:
        raise SystemExit(f"預覽圖超過 {_MAX_PREVIEW_BYTES} bytes：{url}")
    return payload


def stage(
    packaging_dir: Path, cut_id: str, episode_slug: str, episode: str, results: list[dict]
) -> Path:
    vault_dir = get_vault_path() / "Attachments" / "packaging" / episode_slug
    pool_dir = vault_dir / "center-candidates"
    pool_dir.mkdir(parents=True, exist_ok=True)

    out = packaging_dir / "center-candidates" / f"{cut_id}.json"

    # **既有候選要留著。** 這一步天生是分批的——修修 2026-08-29「來源的圖要多一點」，
    # 一條概念搜 2–3 個說法、湊到二三十張，不可能一次搜完。舊版每跑一次就用這一批
    # 重建整個池子，第二輪補搜等於把第一輪的成果全部丟掉（2026-09-12 三支長片各只有
    # 9／8／6 張，正要補搜時才發現）。跟 `emit_packages` 的 title_trace 是同一類 bug。
    # 要清空重來就先刪掉 `<cut_id>.json`。
    existing: list[dict] = []
    if out.is_file():
        try:
            existing = list(
                CenterCandidatesFileV1.model_validate_json(
                    out.read_text(encoding="utf-8")
                ).model_dump(by_alias=True)["candidates"]
            )
        except (OSError, ValueError) as error:
            # 壞損不靜默重建——重建等於把既有候選丟掉。
            raise SystemExit(f"{out} 讀不回來（{error}）。修好或改名備份後再跑。") from error

    candidates: list[dict] = list(existing)
    seen = {str(item["candidate_id"]) for item in candidates}
    skipped: list[str] = []
    for row in results:
        identifier = item_id(str(row["item_url"]))
        if identifier in seen:
            skipped.append(f"{identifier}（池子裡已經有了）")
            continue
        payload = _fetch(str(row["preview_url"]))
        with Image.open(BytesIO(payload)) as image:
            width, height = image.size
            suffix = f".{(image.format or 'JPEG').lower().replace('jpeg', 'jpg')}"
        if width <= height:
            # 搜尋時就該帶 orientation=landscape；漏網的在這裡擋掉，不進 gate。
            skipped.append(f"{identifier}（{width}×{height} 直式）")
            continue
        name = f"{cut_id}-{identifier}{suffix}"
        (pool_dir / name).write_bytes(payload)
        candidates.append(
            {
                "candidate_id": identifier,
                "preview_png": f"Attachments/packaging/{episode_slug}/center-candidates/{name}",
                "width": width,
                "height": height,
                "title": str(row["title"])[:200],
                "author": str(row.get("author") or "")[:120],
                "supply": "envato",
                "source": str(row["item_url"]).strip(),
                "query": str(row["query"])[:200],
            }
        )
        seen.add(identifier)

    pool = CenterCandidatesFileV1.model_validate(
        {
            "schema": CENTER_CANDIDATES_SCHEMA,
            "episode": episode,
            "cut_id": cut_id,
            "generated_at": datetime.now(timezone.utc),
            "candidates": candidates,
        }
    )
    text = pool.model_dump_json(indent=2, by_alias=True) + "\n"
    for path in (out, pool_dir / f"{cut_id}.json"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    if skipped:
        print(f"[note] 跳過直式素材：{', '.join(skipped)}", file=sys.stderr)
    added = len(candidates) - len(existing)
    print(f"{len(candidates)} 張候選（既有 {len(existing)} ＋ 這輪新增 {added}） → {out}")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packaging-dir", type=Path, required=True)
    parser.add_argument("--cut-id", required=True)
    parser.add_argument("--episode-slug", required=True)
    parser.add_argument("--results", type=Path, required=True)
    args = parser.parse_args()

    packages = json.loads((args.packaging_dir / "packages.json").read_text(encoding="utf-8"))
    results = json.loads(args.results.read_text(encoding="utf-8"))
    if not isinstance(results, list) or not results:
        raise SystemExit("results.json 要是非空的搜尋結果陣列")
    stage(args.packaging_dir, args.cut_id, args.episode_slug, packages["episode"], results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
