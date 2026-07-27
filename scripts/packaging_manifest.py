#!/usr/bin/env python3
"""packaging_manifest.py — packaging 段的 resume 帳本（ADR-054 D14）。

podcast-pipeline 的 packaging 末段逐支影片跑 titles → thumbnails → emitted 三個
stage；本 script 是唯一讀寫 `<packaging_dir>/manifest.json` 的機械層——skill 不
手改 JSON。中斷重跑時 `status` 告訴編排者哪支該續（已完成的 stage 不重生）。

用法：
    python scripts/packaging_manifest.py status "G:/footages/<ep>/packaging"
    python scripts/packaging_manifest.py mark   "G:/footages/<ep>/packaging" \
        --cut punch-L5 --stage titles

規則：
- stage 依序 titles → thumbnails → emitted；mark 冪等（重標同 stage 無害）
- 跳序 mark（titles 未完成就標 thumbnails）→ 直接報錯 — 停段不跳段（D14）
- manifest 壞損（非法 JSON）→ fail loud 附修復指引，不靜默重建
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
    except Exception:
        pass

STAGES = ("titles", "thumbnails", "emitted")


def _load(packaging_dir: Path) -> dict:
    path = packaging_dir / "manifest.json"
    if not path.is_file():
        return {"cuts": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"manifest.json 壞損（{exc}）— 不自動重建以免已完成進度蒸發。"
            f"人工檢查 {path}，確認各 cut 實際產物後修正或移除該檔再重跑。"
        ) from exc
    return data


def _save(packaging_dir: Path, data: dict) -> None:
    path = packaging_dir / "manifest.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def status(packaging_dir: Path) -> dict:
    data = _load(packaging_dir)
    out: dict = {"cuts": {}, "next": None}
    for cut_id, stages in data.get("cuts", {}).items():
        done = [s for s in STAGES if s in stages]
        pending = [s for s in STAGES if s not in stages]
        out["cuts"][cut_id] = {"done": done, "pending": pending}
        if pending and out["next"] is None:
            out["next"] = {"cut_id": cut_id, "stage": pending[0]}
    return out


def mark(packaging_dir: Path, cut_id: str, stage: str) -> dict:
    if stage not in STAGES:
        raise SystemExit(f"unknown stage: {stage!r}（合法：{', '.join(STAGES)}）")
    data = _load(packaging_dir)
    cut = data.setdefault("cuts", {}).setdefault(cut_id, {})
    idx = STAGES.index(stage)
    missing = [s for s in STAGES[:idx] if s not in cut]
    if missing:
        raise SystemExit(
            f"{cut_id} 的 {stage} 不能先標 — 前置 stage 未完成：{', '.join(missing)}"
            "（D14 停段不跳段；照序補跑前置再標）"
        )
    if stage not in cut:  # 冪等：已標過不覆寫時間戳
        cut[stage] = datetime.now(timezone.utc).isoformat()
    _save(packaging_dir, data)
    return {"cut_id": cut_id, "stage": stage, "at": cut[stage]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_status = sub.add_parser("status")
    p_status.add_argument("packaging_dir", type=Path)

    p_mark = sub.add_parser("mark")
    p_mark.add_argument("packaging_dir", type=Path)
    p_mark.add_argument("--cut", required=True)
    p_mark.add_argument("--stage", required=True, choices=STAGES)

    args = parser.parse_args()
    args.packaging_dir.mkdir(parents=True, exist_ok=True)
    if args.cmd == "status":
        print(json.dumps(status(args.packaging_dir), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(mark(args.packaging_dir, args.cut, args.stage), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
