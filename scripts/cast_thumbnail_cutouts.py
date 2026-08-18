"""Run pose-aware cutout casting and write review artifacts.

Example:
    python scripts/cast_thumbnail_cutouts.py --emotion thoughtful \
      --visual "research evidence sticky note" \
      --manifest E:/nakama/data/thumbnail_cutouts/shosho_pose_manifest.json \
      --vault-root "E:/Shosho LifeOS"
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.cutout_casting import (  # noqa: E402
    build_cast_request_from_idea,
    cast_cutouts,
    default_pose_manifest_path,
    write_candidate_contact_sheet,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--emotion", required=True, help="Canonical legacy emotion key.")
    parser.add_argument("--hook", default="", help="Thumbnail hook text.")
    parser.add_argument("--visual", default="", help="Visual brief text.")
    parser.add_argument("--decoration", default="", help="Decoration or asset brief text.")
    parser.add_argument("--bg", default="", help="Background brief text.")
    parser.add_argument("--manifest", type=Path, default=default_pose_manifest_path())
    parser.add_argument("--vault-root", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=6)
    args = parser.parse_args()

    idea = {
        "emotion_key": args.emotion,
        "hook": args.hook,
        "visual": args.visual,
        "decoration": args.decoration,
        "bg": args.bg,
    }
    request = build_cast_request_from_idea(idea, limit=args.limit)
    candidates = cast_cutouts(
        request,
        manifest_path=args.manifest,
        vault_root=args.vault_root,
        require_existing=True,
    )
    if not candidates:
        print("No eligible cutout candidates.")
        return 2

    out_dir = args.out_dir or _default_out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "cutout_candidates.json"
    sheet_path = out_dir / "cutout_candidates.png"

    json_path.write_text(
        json.dumps(
            {
                "request": request.to_manifest(),
                "manifest_path": args.manifest.as_posix(),
                "candidates": [candidate.to_manifest() for candidate in candidates],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    write_candidate_contact_sheet(candidates, sheet_path)

    print(f"Wrote {json_path}")
    print(f"Wrote {sheet_path}")
    print("Top candidates: " + ", ".join(c.cutout_id for c in candidates))
    return 0


def _default_out_dir() -> Path:
    ts = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y%m%dT%H%M%S")
    return Path("data") / "thumbnail_cutouts" / "casting_runs" / ts


if __name__ == "__main__":
    raise SystemExit(main())
