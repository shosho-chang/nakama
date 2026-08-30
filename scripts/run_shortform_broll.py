"""shortform-broll：短片素材層——授權照驗，語意改用逐字稿錨定（ADR-067）。

**這支只服務短片。** 長片的素材走 ADR-065 的 Director → DP → 語意稽核收據鏈
（`run_short_broll.py` 的預設路徑）。短片走不了那條，理由與門檻寫在
`shared/shortform_broll.py` 的模組 docstring 裡；一句話是：那條鏈會把**字卡**
一起拉回 DP 稽核，而短片的字卡是逐字稿保證。

這支替短片備妥三份上下文再交給同一個物化器：

- **cues**：最新 tight SRT。素材的 `source_cues` 與落點都對著它驗
- **punches**：`<id>_zoom.resolved.json`。SKILL Step 9「punch zoom 與具象比喻
  衝突時，縮短 punch 讓位 footage」——所以這裡壓到 punch 是 error，要你改企劃
- **opener_sec**：字卡企劃的 `split_opener_sec`。開場上下分割佔的是 track 2，
  素材不能疊上去

素材放 `assets/broll/<slug>.<ext>`，授權收據放 `assets/broll/<slug>.acquisition.json`
（`podcast-highlight-asset-acquisition-receipt-v1`，sha256 要對得上檔案）。

用法：
    py -3.10 scripts/run_shortform_broll.py <episode> --id punch-S02 --validate-only
    py -3.10 scripts/run_shortform_broll.py <episode> --id punch-S02 [--stills <dir>]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_short_broll import apply as _apply  # noqa: E402
from run_short_broll import validate_plan as _validate_plan  # noqa: E402
from run_shortform_director import _cue_index  # noqa: E402

TIGHTEN_DIR = Path("highlights") / "tighten"
SRT_DIR = Path("highlights") / "srt"

logger = logging.getLogger("shortform_broll")


def shortform_context(episode_dir: Path, cid: str) -> dict:
    """素材 gate 要對照的三份東西：逐字稿、punch 區間、開場分割秒數。"""
    srts = sorted((episode_dir / SRT_DIR).glob(f"{cid}_tight_r*.srt"))
    if not srts:
        raise SystemExit(f"找不到 {cid} 的 tight SRT——先跑 run_shortform_director")
    punches: list[dict] = []
    resolved = episode_dir / TIGHTEN_DIR / f"{cid}_zoom.resolved.json"
    if resolved.is_file():
        payload = json.loads(resolved.read_text(encoding="utf-8"))
        if payload.get("srt") != srts[-1].name:
            raise SystemExit(
                f"{resolved.name} 是對著 {payload.get('srt')} 解出來的，最新的是 "
                f"{srts[-1].name}——先重跑 run_shortform_director，不要拿舊的 punch 區間驗"
            )
        punches = payload.get("punches") or []
    opener_sec = 0.0
    titles = episode_dir / TIGHTEN_DIR / f"{cid}_titles.json"
    if titles.is_file():
        opener_sec = float(
            json.loads(titles.read_text(encoding="utf-8")).get("split_opener_sec", 0.0)
        )
    return {
        "cues": _cue_index(srts[-1]),
        "punches": punches,
        "opener_sec": opener_sec,
        "srt": srts[-1].name,
    }


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="短片素材層（stock 直式 ＋ 逐字稿錨定）")
    parser.add_argument("episode")
    parser.add_argument("--id", required=True, help="winner id（如 punch-S02）")
    parser.add_argument("--stills", help="物化後渲樣張到此資料夾")
    parser.add_argument("--validate-only", action="store_true", help="只驗企劃，不連 Resolve")
    args = parser.parse_args(argv)

    episode_dir = Path(args.episode)
    context = shortform_context(episode_dir, args.id)
    logger.info(
        "%s: 對照 %s（%d 句）、%d 個 punch、開場分割 %.1fs",
        args.id,
        context["srt"],
        len(context["cues"]),
        len(context["punches"]),
        context["opener_sec"],
    )
    if args.validate_only:
        out = _validate_plan(episode_dir, args.id, shortform=context)
    else:
        out = _apply(
            episode_dir,
            args.id,
            Path(args.stills) if args.stills else None,
            shortform=context,
        )
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
