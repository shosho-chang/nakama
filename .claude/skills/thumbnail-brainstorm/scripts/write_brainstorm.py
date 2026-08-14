#!/usr/bin/env python3
"""write_brainstorm.py — 把標題／大字候選池寫進 packages.json，供 gate 挑選。

    python write_brainstorm.py --packaging-dir "<ep>/packaging" --cut-id full \
        --episode-slug 20260721-zhengguowei < pool.json

修修 2026-08-14：「把好幾個方向的各種不同的標題跟封面大字都在這裡列出來，讓我
自己從裡面挑，挑完之後就可以填到格子裡。」

pool.json：
    {
      "titles":   [{"text": "...", "angle": "反直覺", "note": "選填"}],
      "bigtexts": [{"lines": ["沒有資源", "怎麼活下來"], "highlight": "活下來",
                    "angle": "求生"}]
    }

候選池是 **display-only**：不動 titles 的 5 條（那些帶 panel 推導鏈），也不動
packages。gate 上「填入」只把文字塞進〈組封面〉的格子，出圖仍走 render_request.py。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

_REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_REPO))

from shared.config import get_vault_path  # noqa: E402
from shared.schemas.packaging import BrainstormV1, PackagesFileV1  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--packaging-dir", type=Path, required=True)
    ap.add_argument("--cut-id", required=True)
    ap.add_argument("--episode-slug", required=True)
    args = ap.parse_args()

    pool = BrainstormV1.model_validate(json.loads(sys.stdin.read()))  # 壞形狀立刻擋
    path = args.packaging_dir / "packages.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    hit = False
    for cut in data["cuts"]:
        if cut["cut_id"] == args.cut_id:
            cut["brainstorm"] = pool.model_dump(mode="json")
            hit = True
    if not hit:
        raise SystemExit(f"cut_id {args.cut_id!r} 不在 {path}")

    PackagesFileV1.model_validate(data)  # 整檔驗證過才落盤
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    path.write_text(text, encoding="utf-8")
    vault_copy = (
        get_vault_path() / "Attachments" / "packaging" / args.episode_slug / "packages.json"
    )
    if vault_copy.parent.is_dir():
        vault_copy.write_text(text, encoding="utf-8")
    print(f"OK — 候選池 {len(pool.titles)} 標題／{len(pool.bigtexts)} 大字 → {args.cut_id}")
    print(f"  → {path}")
    print(f"  → {vault_copy}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
