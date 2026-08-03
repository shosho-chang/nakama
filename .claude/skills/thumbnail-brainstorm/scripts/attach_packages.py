#!/usr/bin/env python3
"""attach_packages.py — 把 render 完的 3 個 package 回填 packages.json（S1 schema 驗證）。

    python attach_packages.py --packaging-dir "G:/footages/20260723 謝伯讓/packaging" \
        --cut-id L1 --episode-slug 20260723-xieboran --specs specs.json

specs.json（3 筆，local thumbnail 絕對路徑 + 顯式 cutout 欄位）：
    [{"title_rank": 1, "thumbnail": "G:/.../pkg-L1-1.png",
      "thumb_archetype_id": "T-V7", "joint_pairing_id": "JP-7",
      "host_cutout": "<vault 絕對或 vault-relative>",
      "guest_cutout": "<同上>"}, ...]

動作：thumbnail PNG 複製進 vault `Attachments/packaging/<episode-slug>/`、
cutout 路徑正規化為 vault-relative、回填 cut.packages、**整檔過
PackagesFileV1 驗證**後寫回 working set + vault（雙落點，ADR-054 D10）。
輸入的 packages.json 允許是 S4 的中間態（packages 未滿 3 — 驗證只在寫出前）。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from shared.config import get_vault_path  # noqa: E402
from shared.schemas.packaging import PackagesFileV1  # noqa: E402


def to_vault_relative(path_str: str, vault_root: Path) -> str:
    """vault 內絕對路徑 → vault-relative（正斜線）；已是 relative 則原樣回傳。"""
    p = Path(path_str)
    if not p.is_absolute():
        return Path(path_str).as_posix()
    try:
        return p.resolve().relative_to(vault_root.resolve()).as_posix()
    except ValueError as e:
        raise ValueError(
            f"cutout path 不在 vault 內，無法轉 vault-relative：{path_str}（vault={vault_root}）"
        ) from e


def attach(packaging_dir: Path, cut_id: str, episode_slug: str, specs: list[dict]) -> Path:
    vault_root = get_vault_path()
    vault_pkg_dir = vault_root / "Attachments" / "packaging" / episode_slug
    vault_pkg_dir.mkdir(parents=True, exist_ok=True)

    packages_path = packaging_dir / "packages.json"
    data = json.loads(packages_path.read_text(encoding="utf-8"))

    cuts = [c for c in data.get("cuts", []) if c.get("cut_id") == cut_id]
    if not cuts:
        raise ValueError(f"cut_id {cut_id!r} not found in {packages_path}")
    cut = cuts[0]

    packages = []
    png_copies: list[tuple[Path, Path]] = []
    for spec in specs:
        src_png = Path(spec["thumbnail"])
        if not src_png.exists():
            raise FileNotFoundError(f"thumbnail PNG not found: {src_png}")
        dst_png = vault_pkg_dir / src_png.name
        if src_png.resolve() != dst_png.resolve():
            png_copies.append((src_png, dst_png))
        packages.append(
            {
                "title_rank": spec["title_rank"],
                "thumbnail_png": f"Attachments/packaging/{episode_slug}/{src_png.name}",
                "thumb_archetype_id": spec["thumb_archetype_id"],
                "joint_pairing_id": spec["joint_pairing_id"],
                "host_cutout": to_vault_relative(spec["host_cutout"], vault_root),
                "guest_cutout": to_vault_relative(spec["guest_cutout"], vault_root),
            }
        )
    cut["packages"] = packages
    data["generated_at"] = datetime.now(timezone.utc).isoformat()

    # 驗證：**本支必須完整**，但同檔內其他還沒配封面的長片（packages 空）是
    # 正常中間態，不該讓本支落不了地。ADR-054 D14 逐支處理 → 一集內同時存在
    # 「已完成」與「只有標題」的 cut 是設計本意；舊版整檔驗證會因為別支
    # packages != 3 而失敗（2026-07-29 謝伯讓集踩到，被迫手動搬檔繞過）。
    pending = [
        c["cut_id"]
        for c in data.get("cuts", [])
        if c.get("cut_id") != cut_id and c.get("format") == "long" and not c.get("packages")
    ]
    to_validate = {
        **data,
        "cuts": [c for c in data.get("cuts", []) if c.get("cut_id") not in pending],
    }
    PackagesFileV1.model_validate(to_validate)  # 失敗即不落任何一份（含 PNG）
    if pending:
        print(f"[note] 尚未配封面的長片（本次不驗證）：{', '.join(pending)}", file=sys.stderr)

    for src_png, dst_png in png_copies:
        shutil.copy2(src_png, dst_png)
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    packages_path.write_text(text, encoding="utf-8")
    (vault_pkg_dir / "packages.json").write_text(text, encoding="utf-8")
    return vault_pkg_dir / "packages.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packaging-dir", type=Path, required=True)
    parser.add_argument("--cut-id", required=True)
    parser.add_argument("--episode-slug", required=True)
    parser.add_argument("--specs", type=Path, required=True)
    args = parser.parse_args()

    specs = json.loads(args.specs.read_text(encoding="utf-8"))
    if len(specs) != 3:
        raise SystemExit(f"長片 package 固定 3 個（拿到 {len(specs)}）— ADR-054 D9")
    out = attach(args.packaging_dir, args.cut_id, args.episode_slug, specs)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
