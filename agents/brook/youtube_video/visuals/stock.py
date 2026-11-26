"""Stock footage（第 6 類）：DP 從 Envato 抓來的實拍素材，原檔直接鋪上 Resolve。

修修 2026-11-26 定：
- **只做滿版**（「只做滿版」）——跳接首選的「滿版 B-roll」也是這個。
- **原始素材直接上 Resolve**，不先轉成一支成品檔——修修在 Resolve 裡可以直接拖動取用的段落、換鏡頭。
- **下載下來的素材要移動到該專案的 folder 裡面**——規格檔只收 `<episode>/assets/stock/` 裡的檔。

所以這一類不渲染：程式只檢查 DP 的規格檔、寫收據；收據列出每個鏡頭用哪個原檔、從第幾秒取多長，
鋪時間軸（`layout.py`）照收據把原檔一鏡一鏡放上 V2，只放畫面不放聲音。

DP 寫的規格檔 `<episode>/visual_sources/<row_id>.json`（格式見 skill youtube-stock）：

    {"shots": [
      {"file": "assets/stock/coffee-pour.mov", "in": 3.2, "dur": 2.4,
       "source": "https://app.envato.com/search/stock-video/<uuid>", "note": "倒咖啡"},
      {"file": "assets/stock/latte-art.mov", "in": 0.5}
    ]}

- 鏡頭照播放順序；`in`＝從原檔第幾秒開始取；`dur`＝這一鏡多長，最後一鏡可以不寫（＝剩下的秒數）。
- 全部鏡頭加起來＝這一列蓋住的秒數（誤差一格內，最後一鏡補齊）。
- 單鏡不超過 3 秒（youtube-director：「逐名詞、單鏡 ≤3 秒、只蓋那個詞」）。
- 原檔只收 16:9、高度至少 1080；全片一片一用（同一支原檔整集只出現一次）。

用法：

    python -m agents.brook.youtube_video.visuals.stock "G:/Footages/<ep>" --check
    python -m agents.brook.youtube_video.visuals.stock "G:/Footages/<ep>" [--row <id>]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path, PurePosixPath
from typing import Any

from agents.brook.youtube_video import visual_plan as vp
from agents.brook.youtube_video.visuals import delivery

CATEGORY = "stock"
COMPONENT = "stock/raw"
SOURCES_DIR = "visual_sources"
STOCK_DIR = PurePosixPath("assets/stock")
MAX_SHOT_SEC = 3.0
MIN_HEIGHT = 1080
TIMELINE_FPS = float(Fraction(delivery.FPS))
EPS = 1e-3


class StockError(delivery.DeliveryError):
    """規格檔或素材不合格；訊息直接給 DP／修修看。"""


@dataclass(frozen=True)
class Probe:
    width: int
    height: int
    fps: float
    duration: float


@dataclass(frozen=True)
class Shot:
    file: str  # 相對集數資料夾，例 assets/stock/x.mov
    src_in: float  # 原檔第幾秒開始取
    offset_frames: int  # 從這一列開頭算第幾格（時間軸 29.97）
    frames: int  # 這一鏡幾格（時間軸 29.97）
    src_fps: float
    width: int
    height: int
    source: str = ""
    note: str = ""

    @property
    def seconds(self) -> float:
        return self.frames / TIMELINE_FPS


Prober = Callable[[Path], Probe]


def ffprobe(path: Path) -> Probe:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,r_frame_rate:format=duration", "-of", "json", str(path)],
        capture_output=True, check=False,
    )  # fmt: skip
    if out.returncode != 0:
        raise StockError(f"讀不了 {path.name}：{out.stderr.decode('utf-8', 'replace')[-300:]}")
    data = json.loads(out.stdout.decode("utf-8", "replace"))
    streams = data.get("streams") or []
    if not streams:
        raise StockError(f"{path.name} 沒有畫面")
    s = streams[0]
    return Probe(
        width=int(s["width"]),
        height=int(s["height"]),
        fps=float(Fraction(s["r_frame_rate"])),
        duration=float(data["format"]["duration"]),
    )


def spec_path(episode_dir: Path, row_id: str) -> Path:
    return Path(episode_dir) / SOURCES_DIR / f"{row_id}.json"


def load_spec(episode_dir: Path, row_id: str) -> list[Mapping[str, Any]]:
    path = spec_path(episode_dir, row_id)
    if not path.is_file():
        raise StockError(f"{row_id}: 還沒有規格檔 {path}（DP 要先寫，格式見 skill youtube-stock）")
    spec = json.loads(path.read_text(encoding="utf-8"))
    shots = spec.get("shots") if isinstance(spec, dict) else None
    if not isinstance(shots, list) or not shots:
        raise StockError(f"{row_id}: 規格檔要有 shots（至少一鏡）")
    return shots


def _episode_file(episode_dir: Path, row_id: str, name: object) -> Path:
    """規格檔裡的檔名 → 集數資料夾裡 assets/stock/ 底下的實際檔案。"""
    hint = f"下載的素材要先搬進這一集的資料夾 {Path(episode_dir) / STOCK_DIR}"
    if not isinstance(name, str) or not name.strip():
        raise StockError(f"{row_id}: 每一鏡都要寫 file")
    rel = PurePosixPath(name.replace("\\", "/"))
    if rel.is_absolute() or ":" in name or ".." in rel.parts or rel.parent != STOCK_DIR:
        raise StockError(f"{row_id}: {name} 不在 {STOCK_DIR}/ 裡——{hint}")
    path = Path(episode_dir) / rel
    if not path.is_file():
        raise StockError(f"{row_id}: 找不到 {path}——{hint}")
    return path


def _number(row_id: str, n: int, key: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise StockError(f"{row_id} 第 {n} 鏡: {key} 要是秒數")
    return float(value)


def frames(seconds: float) -> int:
    return round(seconds * TIMELINE_FPS)


def used_elsewhere(episode_dir: Path, plan: Mapping[str, Any], row_id: str) -> dict[str, str]:
    """其他 stock 列的規格檔已經用掉的原檔 → 哪一列（全片一片一用）。"""
    taken: dict[str, str] = {}
    for other in plan.get("rows", []):
        if other.get("category") != CATEGORY or other["id"] == row_id:
            continue
        path = spec_path(episode_dir, other["id"])
        if not path.is_file():
            continue
        try:
            shots = json.loads(path.read_text(encoding="utf-8")).get("shots") or []
        except (ValueError, AttributeError):
            continue
        for shot in shots:
            if isinstance(shot, dict) and isinstance(shot.get("file"), str):
                taken[shot["file"].replace("\\", "/")] = other["id"]
    return taken


def check_row(
    episode_dir: Path,
    plan: Mapping[str, Any],
    row: Mapping[str, Any],
    *,
    probe: Prober = ffprobe,
) -> tuple[float, float, list[Shot]]:
    """檢查一列 stock：回傳（起點秒、長度秒、每一鏡）。不合格就丟 StockError。"""
    row_id = row["id"]
    if row.get("category") != CATEGORY:
        raise StockError(f"{row_id}: 不是 stock 列")
    if row.get("layout") != "full":
        raise StockError(f"{row_id}: stock 只做滿版，這一列的版面要是滿版")
    start, span = delivery.overlay_window(plan, row)
    total = frames(span)
    raw = load_spec(episode_dir, row_id)
    taken = used_elsewhere(episode_dir, plan, row_id)
    seen: set[str] = set()
    shots: list[Shot] = []
    offset = 0
    for n, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise StockError(f"{row_id} 第 {n} 鏡: 要是一個物件")
        path = _episode_file(episode_dir, row_id, item.get("file"))
        name = str(PurePosixPath(item["file"].replace("\\", "/")))
        if name in seen:
            raise StockError(f"{row_id}: {name} 用了兩次——全片一片一用")
        if name in taken:
            raise StockError(f"{row_id}: {name} 已經用在 {taken[name]}——全片一片一用")
        seen.add(name)
        src_in = _number(row_id, n, "in", item.get("in", 0))
        last = n == len(raw)
        if "dur" in item:
            count = frames(_number(row_id, n, "dur", item["dur"]))
            if last:  # 最後一鏡補齊到這一列的長度（差一格內）
                if abs(offset + count - total) > 1:
                    have = (offset + count) / TIMELINE_FPS
                    raise StockError(f"{row_id}: 鏡頭加起來 {have:.2f} 秒，這一列是 {span:.2f} 秒")
                count = total - offset
        elif last:
            count = total - offset
        else:
            raise StockError(f"{row_id} 第 {n} 鏡: 只有最後一鏡可以不寫 dur")
        seconds = count / TIMELINE_FPS
        if count <= 0:
            raise StockError(f"{row_id} 第 {n} 鏡: 前面的鏡頭已經超過這一列的 {span:.2f} 秒")
        if count > frames(MAX_SHOT_SEC):
            raise StockError(
                f"{row_id} 第 {n} 鏡: {seconds:.2f} 秒，單鏡不超過 {MAX_SHOT_SEC:g} 秒"
            )
        info = probe(path)
        if abs(info.width * 9 - info.height * 16) > info.height * 0.16:
            raise StockError(f"{row_id}: {name} 是 {info.width}×{info.height}，只收 16:9")
        if info.height < MIN_HEIGHT:
            raise StockError(f"{row_id}: {name} 只有 {info.height}p，至少要 {MIN_HEIGHT}p")
        if src_in < 0 or src_in + seconds > info.duration + EPS:
            raise StockError(
                f"{row_id} 第 {n} 鏡: 從 {src_in:.2f} 秒取 {seconds:.2f} 秒，"
                f"超出 {name} 的長度 {info.duration:.2f} 秒"
            )
        shots.append(
            Shot(name, round(src_in, 3), offset, count, info.fps, info.width, info.height,
                 str(item.get("source") or ""), str(item.get("note") or ""))
        )  # fmt: skip
        offset += count
    if offset != total:
        have = offset / TIMELINE_FPS
        raise StockError(f"{row_id}: 鏡頭加起來 {have:.2f} 秒，這一列是 {span:.2f} 秒")
    return start, span, shots


def _variables(shots: Sequence[Shot], episode_dir: Path) -> dict[str, Any]:
    """會影響畫面的一切：用哪個檔（含大小）、從哪裡取、多長。"""
    return {
        "shots": [
            {
                "file": s.file,
                "bytes": (Path(episode_dir) / s.file).stat().st_size,
                "in": s.src_in,
                "frames": s.frames,
            }
            for s in shots
        ]
    }


def deliver_row(
    episode_dir: Path,
    plan: Mapping[str, Any],
    row: Mapping[str, Any],
    *,
    force: bool = False,
    probe: Prober = ffprobe,
) -> tuple[dict[str, Any], bool]:
    """檢查一列並寫收據（不渲染）。回傳（收據, 這次有沒有重寫）。"""
    start, span, shots = check_row(episode_dir, plan, row, probe=probe)
    variables = _variables(shots, episode_dir)
    sha = delivery.inputs_sha(
        category=CATEGORY, component=COMPONENT, component_sha="", variables=variables
    )
    paths = delivery.asset_paths(episode_dir, row["id"])
    current = delivery.load_receipt(paths.receipt)
    if not force and current is not None and current.get("inputs_sha") == sha:
        return current, False
    receipt = delivery.write_receipt(
        paths,
        row=row,
        component=COMPONENT,
        component_sha="",
        variables=variables,
        start_sec=start,
        duration_sec=span,
        sha=sha,
        extra={"media": None, "audio": False, "shots": [asdict(s) for s in shots]},
    )
    return receipt, True


def approved_rows(plan: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [
        row
        for row in plan.get("rows", [])
        if row.get("category") == CATEGORY and row.get("status") == "approved"
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stock footage（第 6 類）：檢查規格檔、寫收據")
    parser.add_argument("episode", type=Path, help="集數資料夾（含 visual_plan.json）")
    parser.add_argument("--row", help="只做這一列")
    parser.add_argument("--check", action="store_true", help="只檢查，不寫收據")
    parser.add_argument("--include-pending", action="store_true", help="待審的列也做（流程測試）")
    args = parser.parse_args(argv)

    plan = vp.load_plan(vp.plan_path(args.episode))
    rows = approved_rows(plan)
    if args.include_pending:
        rows = [r for r in plan["rows"] if r.get("category") == CATEGORY and r.get("status") in
                ("approved", None)]  # fmt: skip
    if args.row:
        rows = [row for row in rows if row["id"] == args.row]
        if not rows:
            print(f"找不到 stock 列：{args.row}", file=sys.stderr)
            return 2
    failures = 0
    for row in rows:
        try:
            if args.check:
                start, span, shots = check_row(args.episode, plan, row)
                label = "可以交件"
            else:
                receipt, wrote = deliver_row(args.episode, plan, row)
                start, span = receipt["start_sec"], receipt["duration_sec"]
                shots = [Shot(**s) for s in receipt["shots"]]
                label = "寫好收據" if wrote else "沿用"
        except delivery.DeliveryError as exc:
            failures += 1
            print(f"✗ {exc}", file=sys.stderr)
            continue
        print(f"{label} {row['id']}：{start:.2f}s 起 {span:.2f}s，{len(shots)} 鏡")
        for s in shots:
            print(f"    {s.seconds:4.2f}s  {s.file} 從 {s.src_in:.2f}s（{s.width}×{s.height} "
                  f"{s.src_fps:.3f}fps）{s.note}")  # fmt: skip
    print(f"stock：{len(rows) - failures}/{len(rows)} 列完成")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
