"""YouTube 照稿影片：把 DP 交件的素材鋪成 Resolve 新一版時間軸。

規格 §4.5「全部直接鋪成新一版時間軸」。

軌道（修修 2026-11-16 選「用這個預設」）：
- V1／A1：原始錄影照初剪剪點（`rough_cut.json`），套畫面搬移（書封、重點條列 → 臉讓到左 1/3）。
- V2：疊加素材；同一時間已經有一張的，往 V3、V4……放。
- Stock（第 6 類）：收據列出每一鏡用哪個原檔、從第幾秒取多長；原檔照鏡頭一鏡一鏡放 V2，
  只放畫面不放聲音（修修 2026-11-26：「原始素材直接上 Resolve」）。
- A2：素材自己帶聲音的（漫畫的音效，收據 `audio: true`），聲音放 A2；同一時間已經有的往 A3……放。
- 章節卡（插入類）：在插入點把整條時間軸往後推 `insert_sec`（1.7 秒）；卡本身 2 秒放在 V2，
  最後 0.3 秒疊在下一段錄影上（章節卡收據）。插入的地方 V1／A1 是空的（第一版安靜，規格 §3.4）。
- 字幕：每一句照插入往後推，寫成 `transcript.<版本>.srt` 放上字幕軌。
- 每次建一條新的（例：「v2 畫面」），不覆蓋舊的；已經有同名的就停。

時間：收據的 `start_sec` 是規劃表的時間＝初剪（v1）時間軸的時間；這裡換算成新一版的時間。

用法（要開著 DaVinci Resolve；會切換到這一集的專案）：

    <venv-v2 python> -m agents.brook.youtube_video.layout "G:/Footages/<ep>" --dry-run
    <venv-v2 python> -m agents.brook.youtube_video.layout "G:/Footages/<ep>"

（<venv-v2 python>＝E:\\nakama\\.venv-v2\\Scripts\\python.exe，Resolve 只認 3.12）
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents.brook.youtube_video import timeline as tl
from agents.brook.youtube_video import visual_plan as vp
from agents.brook.youtube_video.visuals import delivery
from agents.brook.youtube_video.visuals import reframe as rf

FPS = tl.FPS
DEFAULT_NAME = "v2 畫面"


@dataclass(frozen=True)
class Insert:
    row_id: str
    at: float  # 插在 v1 的第幾秒（之前）
    push: float  # 把後面往後推幾秒
    length: float  # 素材本身多長（章節卡 2 秒，最後 0.3 秒疊在下一段上）
    media: Path


@dataclass(frozen=True)
class Overlay:
    row_id: str
    category: str
    start: float  # v1 秒數
    length: float
    media: Path
    layout: str | None = None  # 滿版 full／右側欄 overlay（版面由 Director 選的分類才有）
    audio: bool = False  # 素材自己帶聲音（漫畫的音效）
    src_in: float = 0.0  # 從原檔第幾秒開始取（stock 原檔；渲好的素材都是 0）
    src_fps: float | None = None  # 原檔格率（stock；None＝跟時間軸一樣）


@dataclass
class Layout:
    inserts: list[Insert] = field(default_factory=list)
    overlays: list[Overlay] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def load_layout(episode: Path, plan: Mapping[str, Any]) -> Layout:
    """讀交件收據：只收規劃表裡還在、素材檔還在的列。"""
    rows = {row["id"]: row for row in plan["rows"]}
    out = Layout()
    for path in sorted((Path(episode) / delivery.VISUALS_DIR).glob("*.json")):
        receipt = delivery.load_receipt(path)
        if receipt is None:
            continue  # dp_report.json 之類
        row_id = receipt["row_id"]
        if row_id not in rows:
            out.skipped.append(f"{row_id}：規劃表已經沒有這一列")
            continue
        if receipt.get("shots"):
            out.overlays.extend(_stock_shots(Path(episode), receipt, out.skipped))
            continue
        media = path.with_name(receipt["media"])
        if not media.is_file():
            out.skipped.append(f"{row_id}：素材檔不見了 {media.name}")
            continue
        if receipt["kind"] == "insert":
            out.inserts.append(
                Insert(row_id, float(receipt["start_sec"]), float(receipt.get("insert_sec") or 0),
                       float(receipt["duration_sec"]), media)
            )  # fmt: skip
        else:
            out.overlays.append(
                Overlay(row_id, receipt["category"], float(receipt["start_sec"]),
                        float(receipt["duration_sec"]), media, receipt.get("layout"),
                        bool(receipt.get("audio")))
            )  # fmt: skip
    out.inserts.sort(key=lambda i: i.at)
    out.overlays.sort(key=lambda o: o.start)
    return out


def _stock_shots(episode: Path, receipt: Mapping[str, Any], skipped: list[str]) -> list[Overlay]:
    """Stock 收據 → 每一鏡一個疊加：原檔在集數資料夾裡，從 src_in 取 frames 格。"""
    row_id = receipt["row_id"]
    shots = receipt["shots"]
    missing = [s["file"] for s in shots if not (episode / s["file"]).is_file()]
    if missing:
        skipped.append(f"{row_id}：原檔不見了 {'、'.join(missing)}")
        return []
    start = float(receipt["start_sec"])
    fps = float(FPS)
    return [
        Overlay(row_id, receipt["category"], start + s["offset_frames"] / fps, s["frames"] / fps,
                episode / s["file"], receipt.get("layout"), False, float(s["src_in"]),
                float(s["src_fps"]))
        for s in shots
    ]  # fmt: skip


def frames(seconds: float) -> int:
    return round(seconds * float(FPS))


def shift(seconds: float, inserts: Sequence[Insert], *, include_at: bool = True) -> float:
    """v1 秒數 → 新一版秒數：前面每一張插入都把它往後推。剛好在插入點上的算被推（include_at）。"""
    pushed = sum(i.push for i in inserts if i.at < seconds or (include_at and i.at == seconds))
    return seconds + pushed


def v1_pieces(cut: tl.RoughCut, inserts: Sequence[Insert]) -> list[tuple[int, int, int]]:
    """V1 的每一段（src_in, src_out, 放在新一版第幾格）：在插入點把片段切開、後面往後推。"""
    cuts = sorted((frames(i.at), frames(i.push)) for i in inserts)
    pieces: list[tuple[int, int, int]] = []
    pos = 0  # v1 的格數
    pushed = 0
    k = 0
    for a, b in cut.segments:
        src = a
        end_v1 = pos + (b - a)
        while k < len(cuts) and cuts[k][0] < end_v1:
            at, push = cuts[k]
            if at > pos:
                pieces.append((src, src + (at - pos), pos + pushed))
                src += at - pos
                pos = at
            pushed += push
            k += 1
        pieces.append((src, b, pos + pushed))
        pos = end_v1
    return pieces


def shifted_srt(srt_text: str, inserts: Sequence[Insert]) -> str:
    cues = tl_cues(srt_text)
    out = []
    for n, (start, end, text) in enumerate(cues, start=1):
        a, b = shift(start, inserts), shift(end, inserts, include_at=False)
        out.append(f"{n}\n{_stamp(a)} --> {_stamp(max(a, b))}\n{text}")
    return "\n\n".join(out) + "\n"


def tl_cues(srt_text: str) -> list[tuple[float, float, str]]:
    cues = []
    for block in re.split(r"\n\s*\n", srt_text.strip().lstrip("﻿")):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if len(lines) >= 3 and "-->" in lines[1]:
            a, b = (_seconds(part) for part in lines[1].split("-->"))
            cues.append((a, b, "\n".join(lines[2:])))
    return cues


def _seconds(stamp: str) -> float:
    h, m, rest = stamp.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest.replace(",", "."))


def _stamp(seconds: float) -> str:
    ms = round(seconds * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


@dataclass(frozen=True)
class Placement:
    row_id: str
    media: Path
    track: int  # 2、3……
    record: int  # 新一版第幾格
    length: int  # 幾格
    src_in: int = 0  # 從素材第幾格開始取（素材自己的格率）
    src_fps: float | None = None  # 素材格率跟時間軸不同時（stock 原檔）才有


def placements(layout: Layout) -> list[Placement]:
    """章節卡與疊加素材放哪一軌、哪一格：從 V2 開始，同一時間已經有東西就往上一軌。"""
    items = [
        (frames(shift(i.at, layout.inserts, include_at=False)), frames(i.length), i.row_id, i.media)
        for i in layout.inserts
    ] + [_overlay_item(o, layout.inserts) for o in layout.overlays]
    return _stack(items)


def _overlay_item(o: Overlay, inserts: Sequence[Insert]) -> tuple:
    record, length = frames(shift(o.start, inserts)), frames(o.length)
    if o.src_fps is None:
        return (record, length, o.row_id, o.media)
    # 原檔格率可能是 25／24……：取用起點換成原檔自己的格數；取幾格放上去時再算（source_frames）
    return (record, length, o.row_id, o.media, round(o.src_in * o.src_fps), o.src_fps)


def audio_placements(layout: Layout) -> list[Placement]:
    """自己帶聲音的素材（漫畫音效）放哪一條音軌：從 A2 開始（A1 是原始錄影），重疊就往上。"""
    return _stack(
        [
            (frames(shift(o.start, layout.inserts)), frames(o.length), o.row_id, o.media)
            for o in layout.overlays
            if o.audio
        ]
    )


def _stack(items: list[tuple]) -> list[Placement]:
    """items：(record, length, row_id, media[, src_in, src_fps])。"""
    busy: dict[int, list[tuple[int, int]]] = {}
    out = []
    for record, length, row_id, media, *src in sorted(items, key=lambda t: t[:3]):
        track = 2
        while any(record < e and s < record + length for s, e in busy.get(track, [])):
            track += 1
        busy.setdefault(track, []).append((record, record + length))
        out.append(Placement(row_id, media, track, record, length, *src))
    return out


def conformed_frames(source: int, src_fps: float) -> int:
    """原檔取 N 格放上 29.97 的時間軸，Resolve 實際蓋住幾格：換算後**無條件捨去**。

    2026-11-26 在 Resolve 實測五個點都吻合：25 fps 取 43／50 格 → 51／59 格、
    24 fps 取 28 格 → 34 格。
    """
    return math.floor(source * float(FPS) / src_fps + 1e-6)


def source_frames(timeline_frames: int, src_fps: float) -> int:
    """要蓋住時間軸 N 格，原檔取幾格：放上去**不超過 N 格**的最多格數。

    不超過 → 不會壓到下一鏡或下一列；取最多 → 最多短一格（格率差越小越準，同格率剛好）。
    每放一鏡讀回實際結束格、下一鏡從那裡接，短掉的那格由下一鏡補回，只有一列最後一鏡可能早一格結束。
    """
    source = math.floor((timeline_frames + 1) * src_fps / float(FPS)) + 1
    while source > 0 and conformed_frames(source, src_fps) > timeline_frames:
        source -= 1
    return source


def moves(layout: Layout) -> list[rf.Move]:
    """書封、重點條列、側欄版的解釋動畫：臉讓到另一側（畫面搬移），時間換算到新一版。"""
    result = []
    for o in layout.overlays:
        kind = rf.overlay_move(o.category, o.layout)
        if kind:
            start = shift(o.start, layout.inserts)
            result.append(rf.Move(kind, round(start, 3), round(start + o.length, 3)))
    return rf.check_no_overlap(result)


# ── Resolve ────────────────────────────────────────────────────────────


def build(resolve: Any, episode: Path, name: str = DEFAULT_NAME) -> dict[str, Any]:
    episode = Path(episode)
    plan = vp.load_plan(vp.plan_path(episode))
    cut = tl.load_rough_cut(episode)
    layout = load_layout(episode, plan)
    project = tl._open_project(resolve, episode.name)
    if name in tl._timeline_names(project):
        raise tl.TimelineError(f"專案「{episode.name}」已經有「{name}」，不覆蓋")
    pool = project.GetMediaPool()
    srt = episode / f"transcript.{name.split()[0]}.srt"
    source = (episode / tl.SRT_FILE).read_text(encoding="utf-8-sig")
    srt.write_text(shifted_srt(source, layout.inserts), encoding="utf-8")
    clip = tl._find_or_import(pool, cut.media)
    srt_item = tl._find_or_import(pool, srt)
    timeline = tl._new_timeline(pool, name)
    project.SetCurrentTimeline(timeline)
    if timeline.GetTrackCount("subtitle") == 0:
        timeline.AddTrack("subtitle")
    if not pool.AppendToTimeline([srt_item]):  # 字幕一定要第一個放（見 timeline.py）
        raise tl.TimelineError("字幕放不上時間軸")
    start = int(timeline.GetStartFrame())
    pieces = v1_pieces(cut, layout.inserts)
    infos = [
        {
            "mediaPoolItem": clip,
            "startFrame": a,
            "endFrame": b,
            "recordFrame": start + rec,
            "trackIndex": 1,
        }
        for a, b, rec in pieces
    ]
    if not all(pool.AppendToTimeline(infos) or [None]):
        raise tl.TimelineError("原始錄影放不上時間軸")
    placed = placements(layout)
    for track in range(2, max((p.track for p in placed), default=1) + 1):
        while timeline.GetTrackCount("video") < track:
            timeline.AddTrack("video")
    ends: dict[tuple[str, int], int] = {}  # stock：這一列在這一軌上一鏡實際結束在第幾格
    for p in placed:
        media = tl._find_or_import(pool, p.media)
        record, src_len = p.record, p.length
        if p.src_fps is not None:
            record = ends.get((p.row_id, p.track), p.record)
            src_len = source_frames(p.record + p.length - record, p.src_fps)
        ok = pool.AppendToTimeline([{
            "mediaPoolItem": media, "startFrame": p.src_in, "endFrame": p.src_in + src_len,
            "recordFrame": start + record, "trackIndex": p.track, "mediaType": 1,
        }])  # fmt: skip
        if not ok or not all(ok):
            raise tl.TimelineError(f"{p.row_id} 放不上 V{p.track}")
        if p.src_fps is not None:
            ends[(p.row_id, p.track)] = int(ok[0].GetEnd()) - start
    sounds = audio_placements(layout)
    for track in range(2, max((p.track for p in sounds), default=1) + 1):
        while timeline.GetTrackCount("audio") < track:
            timeline.AddTrack("audio", "stereo")
    for p in sounds:
        media = tl._find_or_import(pool, p.media)
        ok = pool.AppendToTimeline([{
            "mediaPoolItem": media, "startFrame": 0, "endFrame": p.length,
            "recordFrame": start + p.record, "trackIndex": p.track, "mediaType": 2,
        }])  # fmt: skip
        if not ok or not all(ok):
            raise tl.TimelineError(f"{p.row_id} 的聲音放不上 A{p.track}")
    move_list = moves(layout)
    fps = float(FPS)
    pairs = [(m, rf.anchor_for(timeline, m, fps)) for m in move_list]
    touched = rf.apply_moves(timeline, pairs, fps) if pairs else 0
    resolve.GetProjectManager().SaveProject()
    return {
        "project": episode.name,
        "timeline": name,
        "v1_pieces": len(pieces),
        "inserts": len(layout.inserts),
        "overlays": len(layout.overlays),
        "tracks": max((p.track for p in placed), default=1),
        "audio_clips": len(sounds),
        "moves": len(move_list),
        "clips_reframed": touched,
        "subtitles": str(srt.name),
        "skipped": layout.skipped,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把 DP 素材鋪成 Resolve 新一版時間軸")
    parser.add_argument("episode", type=Path)
    parser.add_argument("--name", default=DEFAULT_NAME, help="新時間軸名稱（預設「v2 畫面」）")
    parser.add_argument("--dry-run", action="store_true", help="只算出要放什麼，不開 Resolve")
    args = parser.parse_args(argv)
    try:
        plan = vp.load_plan(vp.plan_path(args.episode))
        cut = tl.load_rough_cut(args.episode)
        layout = load_layout(args.episode, plan)
        placed = placements(layout)
        print(
            f"插入 {len(layout.inserts)} 張、疊加 {len(layout.overlays)} 張；"
            f"V1 切成 {len(v1_pieces(cut, layout.inserts))} 段；"
            f"用到 V2–V{max((p.track for p in placed), default=1)}；"
            f"畫面搬移 {len(moves(layout))} 次；"
            f"帶聲音的素材 {len(audio_placements(layout))} 張"
        )
        for line in layout.skipped:
            print(f"  略過 {line}")
        if args.dry_run:
            return 0
        result = build(rf.connect_resolve(), args.episode, args.name)
    except (tl.TimelineError, rf.ReframeError) as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
