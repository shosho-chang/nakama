"""畫面搬移：在 Resolve 的 A-roll 片段上，用 Fusion Transform 逐格打關鍵影格，推近或把臉讓到一側。

修修 2026-11-15 定版的三種（都是 0.5 秒 ease-in-out，眼睛高度全程不動；
結束時用同樣的速度退回原畫面）：

| 種類 | 放大 | 臉最後在哪 | 用途 |
|---|---|---|---|
| push  | 1.3 倍 | 原地 | 推近 |
| left  | 1.4 倍 | 畫面寬度 1/3 | 右側要放書封等畫面 |
| right | 1.4 倍 | 畫面寬度 2/3 | 左側要放畫面 |

（1.3 倍時臉最多只能移到 38%，到不了 1/3 線，所以左右移用 1.4 倍。）

做法：Transform 的 Pivot 固定在畫面中心，只打 Size 與 Center 兩條關鍵影格——
同一個片段上有好幾次搬移也不互相干擾。數學上等於「以眼睛為支點放大＋水平平移」，水平平移跟放大走同一條曲線，任何一格都不會露黑邊。
全部用 Python 的 Fusion API（不用 `comp.Execute(lua)`：那是非同步的，寫完當下讀不回來確認）；
寫完逐格讀回比對，對不上就停。

用法（對 Resolve 目前開著的專案、目前的 timeline；時間是 timeline 上的秒數，從 timeline 起點算）：

    python -m agents.brook.youtube_video.visuals.reframe apply \
        --move left@12.0-15.5 --move push@40-43
    python -m agents.brook.youtube_video.visuals.reframe preview <錄影> --start 118 --dur 7 \\
        --move left@1.5-5.0 -o preview.mp4

`--move 種類@開始-結束`：開始＝搬移開始的那一秒；結束＝搭配畫面（例如書封）結束的那一秒。
退回從結束前 0.25 秒開始（跟書封退場同時），0.5 秒回到原畫面。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FPS = 30000 / 1001
RAMP_SEC = 0.5
RETURN_LEAD_SEC = 0.25  # 退回從搭配畫面結束前 0.25 秒開始（＝書封開始退場的時間）
TOOL_NAME = "Reframe"
KINDS: dict[str, tuple[float, float | None]] = {
    # 種類: (放大倍數, 臉最後在畫面寬度的幾成；None＝原地)
    "push": (1.3, None),
    "left": (1.4, 1 / 3),
    "right": (1.4, 2 / 3),
}
# 疊加畫面固定在哪一側 → 臉要讓到另一側。
# 書封、重點條列固定在右側（youtube-book-cover、youtube-side-bullets）。
OVERLAY_MOVES = {"book_cover": "left", "side_bullets": "left"}
FACE_MODEL = Path(os.environ.get("FACE_LANDMARKER_MODEL", r"E:\data\models\face_landmarker.task"))
IRIS_L, IRIS_R = 468, 473
MIN_FACE_SAMPLES = 3
TOLERANCE = 1e-3


class ReframeError(RuntimeError):
    """搬移做不了或做出來不對；訊息直接給修修看。"""


@dataclass(frozen=True)
class Anchor:
    """眼睛的位置，以畫面寬高為 1 的比例（y 往下）。x＝兩眼中點，y＝眼線。"""

    x: float
    y: float


@dataclass(frozen=True)
class Move:
    kind: str
    start: float  # 秒：開始推
    end: float  # 秒：搭配畫面結束的時間

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ReframeError(f"不認得的搬移種類：{self.kind}（只有 {', '.join(KINDS)}）")
        if self.end <= self.start:
            raise ReframeError(f"{self.kind}@{self.start}-{self.end}：結束要在開始之後")

    @property
    def return_at(self) -> float:
        # 搭配畫面太短時，推完就馬上退
        return max(self.start + RAMP_SEC, self.end - RETURN_LEAD_SEC)

    @property
    def finish(self) -> float:
        return self.return_at + RAMP_SEC


def parse_move(text: str) -> Move:
    try:
        kind, span = text.split("@", 1)
        start, end = span.split("-", 1)
        return Move(kind.strip(), float(start), float(end))
    except ValueError as exc:
        raise ReframeError(
            f"看不懂 {text!r}：格式是 種類@開始秒-結束秒，例如 left@12.0-15.5"
        ) from exc


# ── 曲線 ────────────────────────────────────────────────────────────────


def ease(x: float) -> float:
    """ease-in-out（三次）：起步和收尾慢、中間快。"""
    x = min(1.0, max(0.0, x))
    return 4 * x**3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def progress(move: Move, t: float) -> float:
    """0＝原畫面，1＝推到位。"""
    return ease((t - move.start) / RAMP_SEC) - ease((t - move.return_at) / RAMP_SEC)


def final_shift(kind: str, anchor: Anchor) -> float:
    """推到位時的水平平移（畫面寬度的比例，負＝往左），夾在不露黑邊的範圍內。"""
    zoom, face_x = KINDS[kind]
    want = (anchor.x if face_x is None else face_x) - anchor.x * zoom
    return max(1 - zoom, min(0.0, want))


def transform_at(move: Move, anchor: Anchor, t: float) -> tuple[float, float, float]:
    """這一刻的（放大倍數, 水平平移, 垂直平移）：輸出位置＝原位置×倍數＋平移（比例座標，y 往下）。

    垂直平移讓眼線那一列永遠留在原高度；水平平移跟放大走同一條曲線，
    兩者對黑邊的限制都隨進度線性變化，所以推的途中也不會露黑邊。
    """
    zoom, _ = KINDS[move.kind]
    e = progress(move, t)
    z = 1 + (zoom - 1) * e
    return z, final_shift(move.kind, anchor) * e, anchor.y * (1 - z)


def fusion_values(z: float, tx: float, ty: float) -> tuple[float, float, float]:
    """換成 Fusion Transform（Pivot 在中心）的 Size、Center x、Center y（Fusion 的 y 往上）。"""
    return z, tx + 0.5 * z, 1 - 0.5 * z - ty


def move_frames(move: Move, fps: float = FPS) -> range:
    return range(math.floor(move.start * fps), math.ceil(move.finish * fps) + 1)


def check_no_overlap(moves: Iterable[Move]) -> list[Move]:
    ordered = sorted(moves, key=lambda m: m.start)
    for a, b in zip(ordered, ordered[1:]):
        if b.start < a.finish:
            raise ReframeError(
                f"{a.kind}@{a.start}-{a.end} 還沒退回（{a.finish:.2f}s），"
                f"{b.kind}@{b.start} 就要開始——兩次搬移不能重疊"
            )
    return ordered


# ── 臉的位置 ────────────────────────────────────────────────────────────


def _size(path: str) -> tuple[int, int]:
    """讀影格時的輸出尺寸：原尺寸，太大就縮到寬 1920。"""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    w, h = (int(v) for v in probe.stdout.strip().split(",")[:2])
    return (1920, round(1920 * h / w)) if w > 1920 else (w, h)


def _frames(path: str, start: float, dur: float, step: int):
    """從錄影讀 RGB 影格（每 step 格取一格），縮到寬 1920。"""
    ow, oh = _size(path)
    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-i", path, "-t", f"{dur:.3f}",
         "-vf", f"select='not(mod(n\\,{step}))',scale={ow}:{oh}", "-vsync", "0",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE,
    )  # fmt: skip
    import numpy as np

    size = ow * oh * 3
    while True:
        buf = bytearray(size)
        view, got = memoryview(buf), 0
        while got < size:  # pipe 一次可能只給一部分，讀滿一格才算
            n = proc.stdout.readinto(view[got:])
            if not n:
                break
            got += n
        if got < size:
            break
        yield np.frombuffer(buf, np.uint8).reshape(oh, ow, 3)
    proc.wait()


def detect_anchor(path: str, start: float, dur: float, *, step: int = 6) -> Anchor:
    """這段錄影裡眼睛的位置（mediapipe 虹膜中點，取中位數，避免頭動造成逐格抖動）。"""
    if not FACE_MODEL.is_file():
        raise ReframeError(
            f"缺臉部模型 {FACE_MODEL}（設 FACE_LANDMARKER_MODEL 指到 face_landmarker.task）"
        )
    import mediapipe as mp
    import numpy as np
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision

    opts = vision.FaceLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=str(FACE_MODEL)), num_faces=1
    )
    xs, ys = [], []
    with vision.FaceLandmarker.create_from_options(opts) as landmarker:
        for frame in _frames(path, start, dur, step):
            image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(frame))
            faces = landmarker.detect(image).face_landmarks
            if faces:
                lm = faces[0]
                xs.append((lm[IRIS_L].x + lm[IRIS_R].x) / 2)
                ys.append((lm[IRIS_L].y + lm[IRIS_R].y) / 2)
    if len(xs) < MIN_FACE_SAMPLES:
        raise ReframeError(f"{path} {start:.1f}s 起 {dur:.1f}s 找不到臉（只抓到 {len(xs)} 格）")
    return Anchor(float(np.median(xs)), float(np.median(ys)))


# ── 套到 Resolve ─────────────────────────────────────────────────────────


@dataclass
class _ItemKeys:
    item: Any
    keys: dict[int, tuple[float, float, float]]  # 片段內第幾格 → (Size, Center x, Center y)


def plan_keys(timeline, moves: list[tuple[Move, Anchor]], fps: float = FPS, track: int = 1):
    """把每次搬移攤到它經過的片段上（跨剪點也連續）。回傳 {片段 id: _ItemKeys}。"""
    check_no_overlap(m for m, _ in moves)
    tl_start = int(timeline.GetStartFrame())
    items = timeline.GetItemListInTrack("video", track) or []
    out: dict[int, _ItemKeys] = {}
    for move, anchor in moves:
        for n in move_frames(move, fps):
            frame = tl_start + n
            item = next((i for i in items if i.GetStart() <= frame < i.GetEnd()), None)
            if item is None:
                continue  # 這一格 V1 沒有東西（例如插入的滿版畫面）
            values = fusion_values(*transform_at(move, anchor, n / fps))
            out.setdefault(id(item), _ItemKeys(item, {})).keys[frame - item.GetStart()] = values
    return out


def _center(value) -> tuple[float, float]:
    if isinstance(value, Mapping):
        return float(value[1]), float(value[2])
    return float(value[0]), float(value[1])


def _write_item(entry: _ItemKeys) -> None:
    item = entry.item
    comp = item.GetFusionCompByIndex(1) if item.GetFusionCompCount() > 0 else item.AddFusionComp()
    if comp is None:
        raise ReframeError(f"{item.GetName()}：加不上 Fusion comp")
    tools = list((comp.GetToolList(False) or {}).values())
    old = [t for t in tools if t.GetAttrs()["TOOLS_Name"] == TOOL_NAME]
    for tool in old:
        tool.Delete()
    by_id = {t.GetAttrs()["TOOLS_RegID"]: t for t in tools if t not in old}
    others = set(by_id) - {"MediaIn", "MediaOut", "BezierSpline", "XYPath", "PolyPath"}
    if others or "MediaIn" not in by_id or "MediaOut" not in by_id:
        raise ReframeError(
            f"{item.GetName()}：片段已經有別的 Fusion 節點（{sorted(others)}），請手動處理"
        )
    xf = comp.AddTool("Transform", -32768, -32768)
    xf.SetAttrs({"TOOLS_Name": TOOL_NAME})
    xf.ConnectInput("Input", by_id["MediaIn"])
    by_id["MediaOut"].ConnectInput("Input", xf)
    xf.AddModifier("Size", "BezierSpline")
    xf.AddModifier("Center", "XYPath")
    for frame, (size, cx, cy) in sorted(entry.keys.items()):
        xf.SetInput("Size", size, frame)
        xf.SetInput("Center", {1: cx, 2: cy}, frame)
    # 逐格讀回：Python API 是同步的，寫完就讀得到；對不上就停，不要讓錯的推近進成片
    for frame, (size, cx, cy) in entry.keys.items():
        got_size = float(xf.GetInput("Size", frame))
        got_cx, got_cy = _center(xf.GetInput("Center", frame))
        if max(abs(got_size - size), abs(got_cx - cx), abs(got_cy - cy)) > TOLERANCE:
            raise ReframeError(
                f"{item.GetName()} 第 {frame} 格讀回不對：Size {got_size} / 應為 {size:.4f}，"
                f"Center ({got_cx:.4f}, {got_cy:.4f}) / 應為 ({cx:.4f}, {cy:.4f})"
            )


def apply_moves(timeline, moves: list[tuple[Move, Anchor]], fps: float = FPS) -> int:
    """把這條 timeline 的**全部**搬移寫上去（同一片段上舊的 Reframe 會先刪掉重做）。

    回傳動到幾個片段。
    """
    entries = plan_keys(timeline, moves, fps)
    for entry in entries.values():
        _write_item(entry)
    return len(entries)


def anchor_for(timeline, move: Move, fps: float = FPS, track: int = 1) -> Anchor:
    """從搬移開始那一格所在片段的原始錄影，量這段時間的眼睛位置。"""
    tl_start = int(timeline.GetStartFrame())
    frame = tl_start + math.floor(move.start * fps)
    items = timeline.GetItemListInTrack("video", track) or []
    item = next((i for i in items if i.GetStart() <= frame < i.GetEnd()), None)
    if item is None:
        raise ReframeError(f"{move.kind}@{move.start}：V1 這一秒沒有片段")
    path = item.GetMediaPoolItem().GetClipProperty("File Path")
    # 不用 GetLeftOffset：drop-frame 錄影它回的是另一套格數（約多千分之一，片尾差到 1.5 秒）；
    # GetSourceStartFrame 是原始錄影的實際格數（Resolve 21 實測，差 1 格內）
    src_first = item.GetSourceStartFrame() if hasattr(item, "GetSourceStartFrame") else None
    if src_first is None:
        src_first = item.GetLeftOffset()
    src_start = (src_first + frame - item.GetStart()) / fps
    return detect_anchor(path, src_start, min(move.finish, move.end) - move.start)


def connect_resolve():
    api = os.environ.get(
        "RESOLVE_SCRIPT_API",
        r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting",
    )
    os.environ.setdefault(
        "RESOLVE_SCRIPT_LIB", r"C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll"
    )
    os.environ["RESOLVE_SCRIPT_API"] = api
    sys.path.append(str(Path(api) / "Modules"))
    import DaVinciResolveScript as dvr

    resolve = dvr.scriptapp("Resolve")
    if resolve is None:
        raise ReframeError(
            "連不上 DaVinci Resolve：確認它開著，"
            "且 Preferences → General → 外部 scripting 設為 Local"
        )
    return resolve


# ── 規劃表 → 搬移 ─────────────────────────────────────────────────────


def moves_for_receipts(episode_dir: Path) -> list[Move]:
    """依已交件的疊加畫面收據排出搬移（原始錄影的秒數；鋪時間軸時再換成 timeline 秒數）。"""
    moves = []
    for path in sorted((Path(episode_dir) / "visuals").glob("*.json")):
        receipt = json.loads(path.read_text(encoding="utf-8"))
        kind = OVERLAY_MOVES.get(receipt.get("category"))
        if kind:
            start = float(receipt["start_sec"])
            moves.append(Move(kind, start, start + float(receipt["duration_sec"])))
    return check_no_overlap(moves)


# ── 預覽（不開 Resolve，給修修看效果）────────────────────────────────


def render_preview(src: str, start: float, dur: float, moves: list[Move], out: Path) -> Anchor:
    """把一段錄影套上搬移輸出 mp4（OpenCV，跟 Resolve 同一套數學）。moves 的秒數從 start 起算。"""
    import cv2
    import numpy as np

    anchor = detect_anchor(src, start, dur)
    check_no_overlap(moves)
    ow, oh = _size(src)
    enc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{ow}x{oh}",
         "-r", "30000/1001", "-i", "-", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", src,
         "-map", "0:v", "-map", "1:a?", "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(out)],
        stdin=subprocess.PIPE,
    )  # fmt: skip
    for n, frame in enumerate(_frames(src, start, dur, 1)):
        t = n / FPS
        active = next((m for m in moves if m.start <= t <= m.finish), None)
        z, tx, ty = transform_at(active, anchor, t) if active else (1.0, 0.0, 0.0)
        m = np.float32([[z, 0, tx * ow], [0, z, ty * oh]])
        enc.stdin.write(cv2.warpAffine(frame, m, (ow, oh), flags=cv2.INTER_CUBIC).tobytes())
    enc.stdin.close()
    if enc.wait() != 0:
        raise ReframeError("預覽輸出失敗")
    return anchor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="畫面搬移：推近／臉讓到左側或右側")
    sub = parser.add_subparsers(dest="cmd", required=True)
    ap = sub.add_parser("apply", help="寫進 Resolve 目前開著的 timeline")
    ap.add_argument("--move", action="append", required=True, help="種類@開始秒-結束秒")
    ap.add_argument("--timeline", help="timeline 名稱（預設＝目前的）")
    pv = sub.add_parser("preview", help="不開 Resolve，輸出預覽 mp4")
    pv.add_argument("src")
    pv.add_argument("--start", type=float, required=True)
    pv.add_argument("--dur", type=float, required=True)
    pv.add_argument("--move", action="append", required=True)
    pv.add_argument("-o", "--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        moves = [parse_move(text) for text in args.move]
        if args.cmd == "preview":
            anchor = render_preview(args.src, args.start, args.dur, moves, args.out)
            print(f"預覽：{args.out}（眼睛在 x={anchor.x:.3f}、y={anchor.y:.3f}）")
            return 0
        project = connect_resolve().GetProjectManager().GetCurrentProject()
        timeline = project.GetCurrentTimeline()
        if args.timeline:
            found = [
                project.GetTimelineByIndex(i) for i in range(1, project.GetTimelineCount() + 1)
            ]
            timeline = next((t for t in found if t.GetName() == args.timeline), None)
            if timeline is None:
                raise ReframeError(f"專案「{project.GetName()}」沒有 timeline「{args.timeline}」")
        fps = float(timeline.GetSetting("timelineFrameRate"))
        if abs(fps - FPS) < 0.01:  # Resolve 回 "29.97"
            fps = FPS
        pairs = [(m, anchor_for(timeline, m, fps)) for m in check_no_overlap(moves)]
        for move, anchor in pairs:
            print(f"{move.kind}@{move.start}-{move.end}：眼睛在 x={anchor.x:.3f}、y={anchor.y:.3f}")
        count = apply_moves(timeline, pairs, fps)
        print(
            f"「{timeline.GetName()}」寫好 {len(pairs)} 次搬移，動到 {count} 個片段，逐格讀回都對"
        )
        return 0
    except ReframeError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
