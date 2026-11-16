"""YouTube 照稿影片的 Resolve 時間軸：從初剪剪點建「版本 1」時間軸（規格 §2 第 5、9 步）。

輸入（都在集數資料夾）：
- `rough_cut.json`：初剪剪點——原始錄影哪幾段、照順序接起來（格數，29.97 fps）。

      {"schema": "nakama.youtube_rough_cut.v1", "fps": "30000/1001",
       "media": "G:/Footages/<ep>/C5507.MP4",
       "segments": [{"src_in": 114, "src_out": 591}, ...]}   # src_out 不含

- `transcript.srt`：字幕，時間＝這條初剪時間軸的時間（交給 Director 的同一份）。

輸出：Resolve 專案（名稱＝集數資料夾名）裡一條新時間軸「v1 初剪」：V1／A1 是原始錄影照剪點接起來，
字幕軌是 transcript.srt。時間軸從字幕樣式模板（DRT）長出來——Resolve 程式介面不能設字幕樣式，
樣式只能靠模板帶進來（規格 §1.3）。已經有同名時間軸就停，不覆蓋（每一版都是新的一條）。

初剪 skill 還沒做之前，《財富階梯》流程測試的 rough_cut.json 是用 B 版剪點聲音比對對回原始錄影做的。

用法（要先開著 DaVinci Resolve；會切換 Resolve 目前的專案）：

    E:\\nakama\\.venv-v2\\Scripts\\python.exe -m agents.brook.youtube_video.timeline \\
        "G:/Footages/<episode>" --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

ROUGH_CUT_SCHEMA = "nakama.youtube_rough_cut.v1"
ROUGH_CUT_FILE = "rough_cut.json"
SRT_FILE = "transcript.srt"
FPS = Fraction(30000, 1001)
V1_NAME = "v1 初剪"
# 字幕樣式模板必須是 29.97 的時間軸：DRT 自帶幀率，匯入後改不動
# （podcast 的 subtitle-template.drt 是 30，YouTube 錄影是 29.97，不能共用）
DEFAULT_TEMPLATE = Path(r"E:\nakama\data\resolve\subtitle-template-2997.drt")
RATE = "29.97"


class TimelineError(RuntimeError):
    """時間軸建不起來或建出來不對；訊息直接給修修看。"""


@dataclass(frozen=True)
class RoughCut:
    media: Path
    segments: tuple[tuple[int, int], ...]  # （src_in, src_out），格數，src_out 不含

    @property
    def frames(self) -> int:
        return sum(b - a for a, b in self.segments)

    @property
    def seconds(self) -> float:
        return float(self.frames / FPS)


def load_rough_cut(episode_dir: Path) -> RoughCut:
    path = Path(episode_dir) / ROUGH_CUT_FILE
    if not path.is_file():
        raise TimelineError(f"找不到初剪剪點 {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != ROUGH_CUT_SCHEMA:
        raise TimelineError(f"{path.name} 的 schema 不是 {ROUGH_CUT_SCHEMA}")
    if data.get("fps") != "30000/1001":
        raise TimelineError(f"{path.name} 的 fps 是 {data.get('fps')}，目前只支援 30000/1001")
    media = Path(data["media"])
    segments = []
    last = -1
    for i, seg in enumerate(data.get("segments") or [], start=1):
        a, b = int(seg["src_in"]), int(seg["src_out"])
        if b <= a:
            raise TimelineError(f"第 {i} 段 src_out 要大於 src_in：{seg}")
        if a < last:
            raise TimelineError(f"第 {i} 段跟前一段重疊或倒退：{seg}")
        segments.append((a, b))
        last = b
    if not segments:
        raise TimelineError(f"{path.name} 沒有任何一段")
    return RoughCut(media=media, segments=tuple(segments))


def clip_infos(item: Any, cut: RoughCut, ends: Sequence[int] | None = None) -> list[dict[str, Any]]:
    """AppendToTimeline 的參數。

    Resolve 20.3 實測：endFrame 不含（startFrame=a、endFrame=b 放上 b−a 格），起點精準；
    但原始錄影是 29.97 drop-frame 時，少數段會多放 1 格——`ends` 用來把那幾段的 endFrame 往回調。
    """
    ends = list(ends) if ends is not None else [b for _, b in cut.segments]
    return [
        {"mediaPoolItem": item, "startFrame": a, "endFrame": e}
        for (a, _), e in zip(cut.segments, ends, strict=True)
    ]


def duration_errors(items: Sequence[Any], cut: RoughCut) -> list[int]:
    """每段放上去的長度比剪點多幾格（0＝剛好）。"""
    if len(items) != len(cut.segments):
        raise TimelineError(f"V1 放上 {len(items)} 段，剪點有 {len(cut.segments)} 段")
    return [int(it.GetDuration()) - (b - a) for it, (a, b) in zip(items, cut.segments, strict=True)]


def check_placed(timeline: Any, cut: RoughCut) -> None:
    """逐段讀回 V1：段數、長度一格不差；每段起點跟第一段的距離（用實際秒數）跟剪點一樣。

    不用 GetLeftOffset：drop-frame 錄影它回的是另一套格數（約多千分之一），不是實際格數。
    """
    items = timeline.GetItemListInTrack("video", 1) or []
    errors = duration_errors(items, cut)
    for i, err in enumerate(errors, start=1):
        if err:
            raise TimelineError(f"第 {i} 段長度差 {err:+d} 格")
    fps = float(FPS)
    first_time, first_frame = float(items[0].GetSourceStartTime()), cut.segments[0][0]
    for i, (item, (a, _)) in enumerate(zip(items, cut.segments, strict=True), start=1):
        frame = first_frame + (float(item.GetSourceStartTime()) - first_time) * fps
        if abs(frame - a) > 0.5:
            raise TimelineError(f"第 {i} 段從原始錄影第 {frame:.1f} 格開始，應為 {a}")


def srt_last_end(srt: Path) -> float:
    """SRT 最後一句的結束秒數。"""
    lines = [ln for ln in srt.read_text(encoding="utf-8-sig").splitlines() if "-->" in ln]
    h, m, rest = lines[-1].split("-->")[1].strip().split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest.replace(",", "."))


def check_subtitles(timeline: Any, srt: Path) -> None:
    """字幕軌最後一句要跟 SRT 同時結束（差 1 格內）——時間軸幀率不對時，字幕越後面越晚。"""
    items = timeline.GetItemListInTrack("subtitle", 1) or []
    if not items:
        raise TimelineError("字幕軌是空的")
    fps = float(FPS)
    placed = (int(items[-1].GetEnd()) - int(timeline.GetStartFrame())) / fps
    want = srt_last_end(srt)
    if abs(placed - want) > 1.5 / fps:
        raise TimelineError(f"字幕最後一句在 {placed:.3f}s 結束，SRT 是 {want:.3f}s")


# ── Resolve ────────────────────────────────────────────────────────────


def _template() -> Path | None:
    path = Path(os.environ.get("RESOLVE_YT_SUBTITLE_TEMPLATE", DEFAULT_TEMPLATE))
    return path if path.is_file() else None


def _is_2997(value: object) -> bool:
    try:
        return abs(float(value) - 29.97) < 0.01
    except (TypeError, ValueError):
        return False


def _open_project(resolve: Any, name: str) -> Any:
    """開（或新建）專案。

    幀率只能在全新、還沒匯入任何東西的專案設；之後 Resolve 就鎖住——SetSetting 回 True，
    存檔重開又變回原值（2026-11-16 實測）。
    """
    pm = resolve.GetProjectManager()
    project = pm.LoadProject(name)
    if project is None:
        project = pm.CreateProject(name)
        if project is None:
            raise TimelineError(f"Resolve 專案「{name}」建不起來")
        for key, value in (
            ("timelineFrameRate", RATE),
            ("timelinePlaybackFrameRate", RATE),  # 播放幀率不跟著設，會用 24 播 29.97 的素材
            ("timelineDropFrameTimecode", "1"),
            ("timelineResolutionWidth", "1920"),
            ("timelineResolutionHeight", "1080"),
            ("timelineInputResMismatchBehavior", "centerCrop"),
        ):
            project.SetSetting(key, value)
    if not _is_2997(project.GetSetting("timelineFrameRate")):
        raise TimelineError(
            f"專案「{name}」的幀率是 {project.GetSetting('timelineFrameRate')}，原始錄影是 29.97"
            "——幀率鎖住改不了，請把這個專案改名或刪掉再重建"
        )
    return project


def _timeline_names(project: Any) -> list[str]:
    count = int(project.GetTimelineCount())
    return [project.GetTimelineByIndex(i).GetName() for i in range(1, count + 1)]


def _find_or_import(media_pool: Any, path: Path) -> Any:
    """媒體池根目錄已經有這個檔就重用，不重複匯入。"""
    want = os.path.normcase(os.path.abspath(path))
    for clip in media_pool.GetRootFolder().GetClipList() or []:
        have = clip.GetClipProperty("File Path") or ""
        if have and os.path.normcase(os.path.abspath(have)) == want:
            return clip
    return _import(media_pool, path)


def _import(media_pool: Any, path: Path) -> Any:
    items = media_pool.ImportMedia([str(path)])
    if not items:
        raise TimelineError(f"Resolve 匯入失敗：{path}")
    return items[0]


def build_v1(resolve: Any, episode_dir: Path, cut: RoughCut, srt: Path) -> dict[str, Any]:
    episode_dir = Path(episode_dir)
    project = _open_project(resolve, episode_dir.name)
    if V1_NAME in _timeline_names(project):
        raise TimelineError(f"專案「{episode_dir.name}」已經有「{V1_NAME}」，不覆蓋")
    pool = project.GetMediaPool()
    clip = _find_or_import(pool, cut.media)
    ends = [b for _, b in cut.segments]
    timeline = None
    for attempt in range(1, 4):
        # 模板 timeline 偶爾整條卡死（append 回 [None]）；drop-frame 錄影少數段會多 1 格。
        # 兩種都是丟掉重來：前者重匯一條，後者把多出來的段 endFrame 往回調
        template = _template()
        if template is not None:
            timeline = pool.ImportTimelineFromFile(str(template), {})
            if timeline is None:
                raise TimelineError(f"字幕樣式模板匯入失敗：{template}")
            timeline.SetName(V1_NAME)
        else:
            timeline = pool.CreateEmptyTimeline(V1_NAME)
            if timeline is None:
                raise TimelineError("時間軸建不起來")
        if not _is_2997(timeline.GetSetting("timelineFrameRate")):
            rate = timeline.GetSetting("timelineFrameRate")
            pool.DeleteTimelines([timeline])
            raise TimelineError(f"時間軸幀率是 {rate}，不是 29.97（字幕樣式模板要用 29.97 的）")
        project.SetCurrentTimeline(timeline)
        placed = pool.AppendToTimeline(clip_infos(clip, cut, ends))
        if placed and all(placed):
            errors = duration_errors(timeline.GetItemListInTrack("video", 1) or [], cut)
            if not any(errors):
                break
            ends = [e - err for e, err in zip(ends, errors, strict=True)]
        pool.DeleteTimelines([timeline])
        timeline = None
    if timeline is None:
        raise TimelineError("原始錄影放不上時間軸或長度對不上（試了 3 次）")
    try:
        check_placed(timeline, cut)
    except TimelineError:
        pool.DeleteTimelines([timeline])  # 不留一條放錯的時間軸
        raise
    if timeline.GetTrackCount("subtitle") == 0:
        timeline.AddTrack("subtitle")
    subtitle_ok = bool(pool.AppendToTimeline([_import(pool, srt)]))
    if subtitle_ok:
        check_subtitles(timeline, srt)
    resolve.GetProjectManager().SaveProject()
    return {
        "project": episode_dir.name,
        "timeline": V1_NAME,
        "segments": len(cut.segments),
        "seconds": round(cut.seconds, 3),
        "subtitle_on_timeline": subtitle_ok,
        "subtitle_style": "template"
        if _template() is not None
        else "none（沒有 29.97 字幕樣式模板）",
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="從初剪剪點建 Resolve「v1 初剪」時間軸")
    parser.add_argument(
        "episode", type=Path, help="集數資料夾（含 rough_cut.json、transcript.srt）"
    )
    parser.add_argument("--dry-run", action="store_true", help="只檢查輸入，不開 Resolve")
    args = parser.parse_args(argv)
    try:
        cut = load_rough_cut(args.episode)
        srt = args.episode / SRT_FILE
        if not srt.is_file():
            raise TimelineError(f"找不到字幕 {srt}")
        if not cut.media.is_file():
            raise TimelineError(f"找不到原始錄影 {cut.media}")
        print(f"{len(cut.segments)} 段，共 {cut.seconds:.2f} 秒；原始錄影 {cut.media}")
        if args.dry_run:
            return 0
        from agents.brook.youtube_video.visuals.reframe import connect_resolve

        result = build_v1(connect_resolve(), args.episode, cut, srt)
    except TimelineError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
