"""第 3 類「人物彈入」：把規劃表上核准的列渲成透明 overlay 素材。

設計（修修 2026-11-15 定版）見 `video/compositions/person_pop/compositions/person_pop.html`：
去背頭肩照從畫面下方跳上來（縱向 motion blur），名字放白色字牌。
（repo 裡另有舊的 `video/compositions/sticker_pair/`，是長精華用的貼紙，不是這個。）

比例照《財富階梯》成片 50.5s 剪輯夥伴的版本：**人物的臉跟主持人的臉一樣大**——眼睛到下巴 218px，
眼睛落在 (262, 607)；右側左右對稱。所以照片不是固定大小，而是先量照片裡的臉再縮放、定位。

照片從哪裡來：規劃表那一列的「出處」欄填**本機圖檔路徑**（規格 §3.8：Wikimedia Commons →
本人官網／出版社 → 本人公開大頭照；禁止直接抓搜尋引擎結果；找不到就標「等你提供素材」）。
已經去背的 PNG 直接用；沒去背的先跑 `hyperframes remove-background`，結果存在
`<episode>/visuals/people/<row_id>.png`。

名字＝「畫面上的字」；空的就不放字牌。左右：預設左側；「給 DP 的指示」寫「右側」就放右側。
時間：從這一列蓋住的第一句字幕開始，停留＝那幾句的總長，夾在 2–6 秒（暫定）。

用法：

    python -m agents.brook.youtube_video.visuals.person "G:/Footages/<episode>"
    python -m agents.brook.youtube_video.visuals.person "G:/Footages/<episode>" --row <id> --force
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents.brook.youtube_video import visual_plan as vp
from agents.brook.youtube_video.visuals import delivery
from shared.quiet_subprocess import quiet_kwargs

CATEGORY = "person"
COMPONENT = "person_pop"
REPO_ROOT = Path(__file__).resolve().parents[4]
COMPOSITION_DIR = REPO_ROOT / "video" / "compositions" / "person_pop"
COMPOSITION_HTML = COMPOSITION_DIR / "compositions" / "person_pop.html"
HYPERFRAMES = "hyperframes@0.7.72"
FACE_MODEL = Path(os.environ.get("FACE_LANDMARKER_MODEL", r"E:\data\models\face_landmarker.task"))

# 《財富階梯》成片 50.5s 量到的（1920×1080）：Nick 的眼睛位置與眼睛到下巴的距離
EYE_X, EYE_Y, EYE_TO_CHIN = 262.5, 606.6, 218.3
FRAME_W, FRAME_H = 1920, 1080
MIN_SEC = 2.0
MAX_SEC = 6.0
MAX_NAME_UNITS = 14  # 52px 字牌，全形算 1、半形 0.55；放在 x=560 起，超過會壓到主持人
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
PEOPLE_DIR = "people"

Runner = Callable[[list[str], Path], None]


@dataclass(frozen=True)
class Face:
    """照片裡的臉（照片像素）：兩眼中點、眼睛到下巴的距離，以及照片寬高。"""

    eye_x: float
    eye_y: float
    eye_to_chin: float
    width: int
    height: int


Measure = Callable[[Path], Face]


def show_seconds(span_sec: float) -> float:
    return round(min(MAX_SEC, max(MIN_SEC, span_sec)), 3)


def text_width_units(text: str) -> float:
    return sum(1.0 if unicodedata.east_asian_width(ch) in ("W", "F") else 0.55 for ch in text)


def person_name(row: Mapping[str, Any]) -> str:
    name = (row.get("onscreen_text") or "").strip()
    if "\n" in name:
        raise delivery.DeliveryError(f"{row['id']}: 名字只能一行：{name!r}")
    if text_width_units(name) > MAX_NAME_UNITS:
        raise delivery.DeliveryError(f"{row['id']}: 名字「{name}」太長，請在 Bridge 縮短")
    return name


def side_for(row: Mapping[str, Any]) -> str:
    return "right" if "右側" in str(row.get("dp_instruction") or "") else "left"


def photo_source(row: Mapping[str, Any]) -> Path:
    source = str(row.get("source") or "").strip().strip('"')
    if not source:
        raise delivery.DeliveryError(
            f"{row['id']}: 「出處」是空的——請填人物照片的本機路徑（等你提供素材）"
        )
    path = Path(source)
    if path.suffix.lower() not in IMAGE_SUFFIXES or not path.is_file():
        raise delivery.DeliveryError(f"{row['id']}: 找不到人物照片：{source}")
    return path


def has_cutout(path: Path) -> bool:
    """已經去背：PNG 帶透明，而且至少 5% 的像素是透明的。"""
    from PIL import Image

    with Image.open(path) as img:
        if img.mode != "RGBA":
            return False
        alpha = img.getchannel("A")
        hist = alpha.histogram()
        return sum(hist[:20]) >= 0.05 * img.width * img.height


def cutout_path(episode_dir: Path, row_id: str) -> Path:
    return Path(episode_dir) / delivery.VISUALS_DIR / PEOPLE_DIR / f"{row_id}.png"


def ensure_cutout(source: Path, dest: Path, runner: Runner) -> Path:
    if has_cutout(source):
        return source
    dest.parent.mkdir(parents=True, exist_ok=True)
    runner(
        ["npx", "--yes", HYPERFRAMES, "remove-background", str(source), "-o", str(dest)],
        COMPOSITION_DIR,
    )
    if not dest.is_file():
        raise delivery.DeliveryError(f"去背沒有產出檔案：{dest}")
    return dest


def measure_face(path: Path) -> Face:
    """mediapipe：兩眼虹膜中點與下巴（landmark 152）。透明處先合成中灰再偵測。"""
    if not FACE_MODEL.is_file():
        raise delivery.DeliveryError(f"缺臉部模型 {FACE_MODEL}（FACE_LANDMARKER_MODEL）")
    import mediapipe as mp
    import numpy as np
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision
    from PIL import Image

    with Image.open(path) as img:
        rgba = img.convert("RGBA")
    flat = Image.new("RGB", rgba.size, (128, 128, 128))
    flat.paste(rgba, mask=rgba.getchannel("A"))
    arr = np.ascontiguousarray(np.array(flat))
    opts = vision.FaceLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=str(FACE_MODEL)), num_faces=2
    )
    with vision.FaceLandmarker.create_from_options(opts) as landmarker:
        faces = landmarker.detect(
            mp.Image(image_format=mp.ImageFormat.SRGB, data=arr)
        ).face_landmarks
    if len(faces) != 1:
        raise delivery.DeliveryError(f"{path.name}：照片裡要剛好一張臉（偵測到 {len(faces)} 張）")
    lm, (w, h) = faces[0], rgba.size
    eye_x = (lm[468].x + lm[473].x) / 2 * w
    eye_y = (lm[468].y + lm[473].y) / 2 * h
    return Face(eye_x, eye_y, lm[152].y * h - eye_y, w, h)


def layout(face: Face, side: str) -> dict[str, float]:
    """照片放大縮小到臉跟主持人一樣大，眼睛對到參考位置。回傳照片左上角與寬（px）。"""
    if face.eye_to_chin <= 0:
        raise delivery.DeliveryError("量不到眼睛到下巴的距離")
    scale = EYE_TO_CHIN / face.eye_to_chin
    eye_x = EYE_X if side == "left" else FRAME_W - EYE_X
    x = eye_x - face.eye_x * scale
    y = EYE_Y - face.eye_y * scale
    if y + face.height * scale < FRAME_H - 0.5:
        raise delivery.DeliveryError(
            "照片太短：放到臉跟主持人一樣大之後，下緣到不了畫面底邊（需要到胸口以下的頭肩照）"
        )
    return {"photo_x": round(x, 1), "photo_y": round(y, 1), "photo_w": round(face.width * scale, 1)}


def card_variables(
    episode_dir: Path,
    plan: Mapping[str, Any],
    row: Mapping[str, Any],
    *,
    runner: Runner,
    measure: Measure = measure_face,
) -> tuple[dict, float, float]:
    if row.get("category") != CATEGORY:
        raise delivery.DeliveryError(f"{row.get('id')}: 不是人物彈入")
    start, span = delivery.overlay_window(plan, row)
    seconds = show_seconds(span)
    name = person_name(row)
    side = side_for(row)
    try:
        photo = ensure_cutout(photo_source(row), cutout_path(episode_dir, row["id"]), runner)
        place = layout(measure(photo), side)
    except delivery.DeliveryError as exc:
        msg = str(exc)
        raise delivery.DeliveryError(
            msg if msg.startswith(row["id"]) else f"{row['id']}: {msg}"
        ) from None
    data = base64.b64encode(photo.read_bytes()).decode("ascii")
    variables = {
        "photo": f"data:image/png;base64,{data}",
        "name": name,
        "side": side,
        **place,
        "show_sec": seconds,
    }
    return variables, start, seconds


def _hyperframes_render(argv: list[str], cwd: Path) -> None:
    proc = subprocess.run(
        argv,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=sys.platform == "win32",  # npx 在 Windows 是 .cmd
        **quiet_kwargs(),
    )
    if proc.returncode != 0:
        raise RuntimeError(f"{argv[3]} 失敗：{(proc.stderr or proc.stdout or '')[-600:]}")


def _render(
    variables: Mapping[str, Any], seconds: float, paths: delivery.AssetPaths, runner: Runner
) -> None:
    paths.media.parent.mkdir(parents=True, exist_ok=True)
    raw = paths.media.with_name(paths.media.stem + ".full.mov")
    vars_file = paths.media.with_name(paths.media.stem + ".vars.json")
    vars_file.write_text(json.dumps(variables, ensure_ascii=False), encoding="utf-8")
    try:
        runner(
            [
                "npx",
                "--yes",
                HYPERFRAMES,
                "render",
                ".",
                "-c",
                "compositions/person_pop.html",
                "-o",
                str(raw),
                "--format",
                "mov",
                "--fps",
                delivery.FPS,
                "-q",
                "standard",
                "--quiet",
                "--no-browser-gpu",
                "--variables-file",
                str(vars_file),
            ],
            COMPOSITION_DIR,
        )
        # 元件固定渲 8 秒；交件長度＝停留秒數。ProRes 每格都是關鍵格，copy 截斷是逐格精準的。
        runner(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                str(raw),
                "-t",
                f"{seconds:.3f}",
                "-c",
                "copy",
                str(paths.media),
            ],
            COMPOSITION_DIR,
        )
    finally:
        raw.unlink(missing_ok=True)
        vars_file.unlink(missing_ok=True)


def receipt_variables(variables: Mapping[str, Any]) -> dict[str, Any]:
    """收據不存整張照片（太大）；照片內容已經算進 inputs_sha。"""
    slim = dict(variables)
    slim["photo"] = f"<{len(variables['photo'])} chars data URI>"
    return slim


def deliver_row(
    episode_dir: Path,
    plan: Mapping[str, Any],
    row: Mapping[str, Any],
    *,
    force: bool = False,
    runner: Runner = _hyperframes_render,
    measure: Measure = measure_face,
) -> tuple[dict[str, Any], bool]:
    """渲一列並寫收據。回傳（收據, 這次有沒有真的渲）。"""
    variables, start, seconds = card_variables(
        episode_dir, plan, row, runner=runner, measure=measure
    )
    component_sha = delivery.file_sha(COMPOSITION_HTML)
    sha = delivery.inputs_sha(
        category=CATEGORY, component=COMPONENT, component_sha=component_sha, variables=variables
    )
    paths = delivery.asset_paths(episode_dir, row["id"])
    if not force and delivery.is_current(paths, sha):
        return delivery.load_receipt(paths.receipt) or {}, False
    _render(variables, seconds, paths, runner)
    receipt = delivery.write_receipt(
        paths,
        row=row,
        component=COMPONENT,
        component_sha=component_sha,
        variables=receipt_variables(variables),
        start_sec=start,
        duration_sec=seconds,
        sha=sha,
    )
    return receipt, True


def approved_rows(plan: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [
        row
        for row in plan.get("rows", [])
        if row.get("category") == CATEGORY and row.get("status") == "approved"
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="渲人物彈入（第 3 類）")
    parser.add_argument("episode", type=Path, help="集數資料夾（含 visual_plan.json）")
    parser.add_argument("--row", help="只渲這一列")
    parser.add_argument("--force", action="store_true", help="收據對得上也重渲")
    args = parser.parse_args(argv)

    plan = vp.load_plan(vp.plan_path(args.episode))
    rows = approved_rows(plan)
    if args.row:
        rows = [row for row in rows if row["id"] == args.row]
        if not rows:
            print(f"找不到核准的人物彈入：{args.row}", file=sys.stderr)
            return 2
    failures = 0
    for row in rows:
        try:
            receipt, rendered = deliver_row(args.episode, plan, row, force=args.force)
        except delivery.DeliveryError as exc:
            failures += 1
            print(f"✗ {exc}", file=sys.stderr)
            continue
        label = "渲好" if rendered else "沿用"
        print(
            f"{label} {row['id']}：{receipt['variables']['name'] or '（不放名字）'}"
            f" {receipt['variables']['side']} {receipt['start_sec']:.2f}s 起"
            f" {receipt['duration_sec']:.2f}s"
        )
    print(f"人物彈入：{len(rows) - failures}/{len(rows)} 張完成")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
