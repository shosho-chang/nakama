#!/usr/bin/env python3
"""fetch_licensed_center.py — 把 gate 挑的候選預覽換成正式授權檔，並讓 render 接手。

修修 2026-08-29：「我不要人工下載，所有的素材下載都要你幫我做。」

**這一步為什麼不能做成背景程序**：Envato Elements 沒有給訂閱者下載用的 API，
取得授權檔必須在**已登入的瀏覽器工作階段**裡按下 Download。無人看管的 watcher
沒有那個工作階段。所以這支把「點擊」以外的每一件事都腳本化，點擊由 agent 在
對話中用修修的瀏覽器完成——他不必自己動手，也不必自己接線。

    # 1) 這一集還有哪些封面卡在等授權檔？印出要開的品項網址
    python fetch_licensed_center.py --episode-slug 20260805-linzhichen --pending

    # 2) （agent 在瀏覽器按下 Download）

    # 3) 收線：驗檔 → 安裝 → 改指 → 讓 watcher 重新撿起來
    python fetch_licensed_center.py --episode-slug 20260805-linzhichen \
        --cut-id punch-L04 --package-rank 1 --install

`--install` 不給 `--from` 時，會在下載目錄找最近下載的圖檔。下載目錄預設
`E:\\`（修修的瀏覽器預設落點，不是 ~/Downloads——2026-08-29 在這裡浪費過一輪，
還因此在他的付費帳號上多按了一次 Download），可用 NAKAMA_DOWNLOAD_DIR 覆寫。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from PIL import Image  # noqa: E402

from shared.config import get_vault_path  # noqa: E402

CANDIDATE_MARKER = "/center-candidates/"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
DEFAULT_DOWNLOAD_DIR = Path(os.environ.get("NAKAMA_DOWNLOAD_DIR") or r"E:\\")
# 候選預覽是 600px 級的浮水印圖；授權原檔動輒 6000px。門檻取封面畫布寬，
# 低於它就不可能是原檔。
MIN_LONG_EDGE = 1280
DOWNLOAD_WINDOW_SEC = 900


class CenterFetchError(RuntimeError):
    """驗不過就停——不讓浮水印預覽或低解析度素材混進成品線。"""


def _episode_dir(episode_slug: str) -> Path:
    return get_vault_path() / "Attachments" / "packaging" / episode_slug


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _candidate_pool(episode_dir: Path, cut_id: str) -> list[dict]:
    path = episode_dir / "center-candidates" / f"{cut_id}.json"
    if not path.is_file():
        return []
    return _read(path).get("candidates") or []


def _is_candidate_preview(asset: str | None) -> bool:
    return bool(asset) and CANDIDATE_MARKER in str(asset).replace("\\", "/")


def pending_rows(episode_dir: Path) -> list[dict]:
    """每一個中央圖還指著候選預覽的 package——就是還在等授權檔的那些。"""
    packages_path = episode_dir / "packages.json"
    if not packages_path.is_file():
        raise CenterFetchError(f"找不到 {packages_path}")
    rows: list[dict] = []
    for cut in _read(packages_path).get("cuts", []):
        cut_id = cut.get("cut_id")
        pool = {row["preview_png"]: row for row in _candidate_pool(episode_dir, cut_id)}
        for package in cut.get("packages", []):
            recipe = package.get("render_recipe") or {}
            asset = recipe.get("center_visual_asset")
            if not _is_candidate_preview(asset):
                continue
            candidate = pool.get(asset, {})
            rows.append(
                {
                    "cut_id": cut_id,
                    "package_rank": package.get("title_rank"),
                    "preview": asset,
                    "source": candidate.get("source", ""),
                    "title": candidate.get("title", ""),
                }
            )
    return rows


def newest_download(
    download_dir: Path = DEFAULT_DOWNLOAD_DIR, *, window_sec: int = DOWNLOAD_WINDOW_SEC
) -> Path:
    """下載目錄第一層最近落地的圖檔。"""
    if not download_dir.is_dir():
        raise CenterFetchError(f"下載目錄不存在：{download_dir}")
    now = time.time()
    files = [
        path
        for path in download_dir.iterdir()
        if path.is_file()
        and path.suffix.lower() in IMAGE_SUFFIXES
        and now - path.stat().st_mtime < window_sec
    ]
    if not files:
        raise CenterFetchError(
            f"{download_dir} 裡沒有 {window_sec // 60} 分鐘內下載的圖檔。\n"
            "  瀏覽器的 Download 按了嗎？或用 --from 直接指路徑。"
        )
    return max(files, key=lambda path: path.stat().st_mtime)


def verify_licensed_original(path: Path) -> tuple[int, int]:
    """授權原檔的三道驗證：讀得開、橫式、長邊夠大。"""
    if _is_candidate_preview(str(path)):
        raise CenterFetchError(f"{path} 還在候選池裡——那是浮水印預覽，不是授權檔")
    try:
        with Image.open(path) as image:
            width, height = image.size
    except OSError as exc:
        raise CenterFetchError(f"{path} 讀不開，不是有效圖檔：{exc}") from exc
    if width <= height:
        raise CenterFetchError(f"{path.name} 是 {width}×{height} 直式——中央卡必須橫式")
    if max(width, height) < MIN_LONG_EDGE:
        raise CenterFetchError(
            f"{path.name} 長邊只有 {max(width, height)}px，低於 {MIN_LONG_EDGE}"
            "——這看起來還是預覽圖，不是授權原檔"
        )
    return width, height


def install(
    episode_slug: str,
    cut_id: str,
    package_rank: int,
    source_file: Path,
    *,
    working_dir: Path | None = None,
) -> dict:
    """安裝授權檔並改指；回傳這次做了什麼。"""
    episode_dir = _episode_dir(episode_slug)
    width, height = verify_licensed_original(source_file)

    packages_path = episode_dir / "packages.json"
    packages = _read(packages_path)
    cut = next((row for row in packages.get("cuts", []) if row.get("cut_id") == cut_id), None)
    if cut is None:
        raise CenterFetchError(f"{packages_path} 沒有 cut {cut_id}")
    package = next(
        (row for row in cut.get("packages", []) if row.get("title_rank") == package_rank), None
    )
    if package is None or not package.get("render_recipe"):
        raise CenterFetchError(
            f"{cut_id} rank {package_rank} 還沒有 render_recipe——先在 gate 存配方"
        )
    recipe = package["render_recipe"]
    previous = recipe.get("center_visual_asset")

    # 來歷：配方裡有就用配方的；沒有就從候選池補回來。授權檔一旦換掉檔名，池子
    # 就是唯一還記得「這張哪來的」的地方，所以要趁現在抄過去。
    provenance = recipe.get("center_provenance")
    if provenance is None and _is_candidate_preview(previous):
        row = next(
            (c for c in _candidate_pool(episode_dir, cut_id) if c["preview_png"] == previous),
            None,
        )
        if row is not None:
            provenance = {
                "supply": row["supply"],
                "source": row["source"],
                "query": row["query"],
                "why": f"修修在 gate 上親自挑選（未附理由）：{row['title']}",
            }
    if provenance is None:
        raise CenterFetchError(
            f"{cut_id} rank {package_rank} 沒有中央圖來歷，候選池也查不到——"
            "不把來歷不明的素材放進成品線"
        )

    target_name = f"center-{cut_id}-r{package_rank}{source_file.suffix.lower()}"
    episode_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_file, episode_dir / target_name)
    asset = f"Attachments/packaging/{episode_slug}/{target_name}"
    requested_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def _apply(recipe_obj: dict) -> None:
        recipe_obj["center_visual_asset"] = asset
        recipe_obj["center_provenance"] = provenance
        recipe_obj["rendered_png"] = None  # 換了素材，舊成品不算數
        # 動 requested_at 是為了讓 render watcher 認得這是一份新請求；沿用舊值
        # 它會判定「已經做過」而不再撿。
        recipe_obj["requested_at"] = requested_at

    _apply(recipe)
    _write(packages_path, packages)

    # approval.json 的 render_request 才是 watcher 撿的那一份。2026-08-29 只改
    # packages.json，結果 watcher 照樣拿浮水印預覽去 render。
    approval_path = episode_dir / "approval.json"
    if approval_path.is_file():
        approval = _read(approval_path)
        entry = next(
            (row for row in approval.get("approvals", []) if row.get("cut_id") == cut_id), None
        )
        if entry and entry.get("render_request"):
            _apply(entry["render_request"])
            entry["center_search_request"] = None  # 這輪找圖需求已經滿足
            _write(approval_path, approval)

    # 桌機工作區那一份（render_request.py 出圖後也會寫它）
    if working_dir is not None:
        working_packages = working_dir / "packages.json"
        if working_packages.is_file():
            data = _read(working_packages)
            for row in data.get("cuts", []):
                if row.get("cut_id") != cut_id:
                    continue
                for item in row.get("packages", []):
                    if item.get("title_rank") == package_rank and item.get("render_recipe"):
                        _apply(item["render_recipe"])
            _write(working_packages, data)

    return {
        "asset": asset,
        "size": f"{width}×{height}",
        "replaced": previous,
        "requested_at": requested_at,
        "provenance": provenance,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode-slug", required=True)
    parser.add_argument("--pending", action="store_true", help="列出還在等授權檔的封面")
    parser.add_argument("--cut-id")
    parser.add_argument("--package-rank", type=int)
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--from", dest="source", type=Path, help="授權檔路徑（不給就抓最近下載的）")
    parser.add_argument("--download-dir", type=Path, default=DEFAULT_DOWNLOAD_DIR)
    parser.add_argument("--working-dir", type=Path, help="桌機端 packaging 工作區")
    args = parser.parse_args(argv)

    try:
        if args.pending:
            rows = pending_rows(_episode_dir(args.episode_slug))
            if not rows:
                print("沒有封面在等授權檔。")
                return 0
            print(f"{len(rows)} 個封面在等授權檔：")
            for row in rows:
                print(f"  {row['cut_id']} r{row['package_rank']}  {row['title']}")
                print(f"     開這個網址按 Download：{row['source']}")
            return 0

        if not (args.install and args.cut_id and args.package_rank):
            raise CenterFetchError("要給 --pending，或 --install 加上 --cut-id 與 --package-rank")

        source = args.source or newest_download(args.download_dir)
        print(f"授權檔：{source}")
        result = install(
            args.episode_slug,
            args.cut_id,
            args.package_rank,
            source,
            working_dir=args.working_dir,
        )
        print(f"  尺寸    ：{result['size']}")
        print(f"  取代    ：{result['replaced']}")
        print(f"  改指為  ：{result['asset']}")
        print(f"  來歷    ：{result['provenance']['source']}")
        print(f"  新請求  ：{result['requested_at']}（watcher 會重新撿起來）")
        return 0
    except CenterFetchError as exc:
        print(f"停下來了：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
