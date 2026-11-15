"""各分類 DP 元件共用的交件格式（YouTube 照稿影片）。

每一類畫面（重點概念 title、書封、人物彈入……）各有自己的 skill 與渲染程式，但交件長一樣，
之後鋪時間軸的程式只認這一種格式：

- 素材：`<episode>/visuals/<row_id>.mov`——1920×1080、29.97 fps、ProRes 4444 帶 alpha，
  長度＝這張畫面要在時間軸上停留的秒數，從第 0 格就是進場的第一格。
- 收據：`<episode>/visuals/<row_id>.json`——這份素材是用什麼做的（分類、元件、變數、
  元件原始檔的雜湊）、要放在原始錄影的哪個時間點、多長。

收據的 `inputs_sha` 把「會影響畫面的一切」雜湊起來：規劃表那一列改了字、元件 HTML 改了，
雜湊就變，鋪時間軸前一比就知道哪幾張要重做。

修修 2026-11-15：「DP 這 16 種功能，我每一個都會跟你合作討論一個 skill 出來」——
這裡是那 16＋個 skill 的共同出口，不放任何一類的設計判斷。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents.brook.youtube_video import visual_plan as vp

RECEIPT_SCHEMA = "nakama.youtube_visual_asset.v1"
VISUALS_DIR = "visuals"
FPS = "30000/1001"  # 修修的錄影是 29.97 fps；素材跟錄影同格率，進 Resolve 不用重新取樣


class DeliveryError(ValueError):
    """這一列不能交件（分類不對、時間不對、必填欄位空的……）。"""


@dataclass(frozen=True)
class AssetPaths:
    media: Path
    receipt: Path


def asset_paths(episode_dir: Path, row_id: str) -> AssetPaths:
    folder = Path(episode_dir) / VISUALS_DIR
    return AssetPaths(media=folder / f"{row_id}.mov", receipt=folder / f"{row_id}.json")


def overlay_window(plan: Mapping[str, Any], row: Mapping[str, Any]) -> tuple[float, float]:
    """疊加類畫面在原始錄影上的起點與它蓋住的那幾句字幕的總長（秒）。"""
    if row.get("kind") != "overlay":
        raise DeliveryError(f"{row.get('id')}: 只有疊加類畫面有字幕範圍")
    cues = vp.cue_range(plan, row["start_cue"], row["end_cue"])
    start = float(cues[0]["start"])
    return start, float(cues[-1]["end"]) - start


def insert_point(plan: Mapping[str, Any], row: Mapping[str, Any]) -> float:
    """插入類畫面（章節卡等）插在原始錄影的哪一秒之前＝它前面那句字幕的開頭。"""
    if row.get("kind") != "insert":
        raise DeliveryError(f"{row.get('id')}: 只有插入類畫面有插入點")
    (cue,) = vp.cue_range(plan, row["before_cue"], row["before_cue"])
    return float(cue["start"])


def file_sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def inputs_sha(*, category: str, component: str, component_sha: str, variables: Mapping) -> str:
    payload = json.dumps(
        {
            "category": category,
            "component": component,
            "component_sha": component_sha,
            "variables": variables,
            "fps": FPS,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_receipt(path: Path) -> dict[str, Any] | None:
    path = Path(path)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != RECEIPT_SCHEMA:
        return None
    return data


def is_current(paths: AssetPaths, expected_inputs_sha: str) -> bool:
    """素材還在、收據對得上目前的輸入——不用重做。"""
    receipt = load_receipt(paths.receipt)
    return (
        receipt is not None
        and receipt.get("inputs_sha") == expected_inputs_sha
        and paths.media.is_file()
    )


def write_receipt(
    paths: AssetPaths,
    *,
    row: Mapping[str, Any],
    component: str,
    component_sha: str,
    variables: Mapping[str, Any],
    start_sec: float,
    duration_sec: float,
    sha: str,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """`extra`：這一類鋪時間軸要多知道的事（例：章節卡最後幾秒疊在下一段錄影上）。"""
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "row_id": row["id"],
        "category": row["category"],
        "kind": row["kind"],
        "layout": row.get("layout"),
        "component": component,
        "component_sha": component_sha,
        "variables": dict(variables),
        "inputs_sha": sha,
        "start_sec": round(start_sec, 3),
        "duration_sec": round(duration_sec, 3),
        "fps": FPS,
        "media": paths.media.name,
        "rendered_at": datetime.now(UTC).isoformat(timespec="seconds"),
        **dict(extra or {}),
    }
    paths.receipt.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=paths.receipt.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(receipt, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, paths.receipt)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return receipt
