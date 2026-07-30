"""mistake-removal 產物 → DaVinci Resolve 專案 timeline（cleanup v2）。

讀 episode 目錄的 ``out/clean_segments.json``（script_coverage 產出的保留
時間段）與 ``transcript.srt``，在 Resolve 建（或重建）與 episode 同名的
project + timeline：V1/A1 = 主影片的保留段依序接上（jump-cut 成品，
NG 與停頓已移除）、subtitle 軌 = 乾淨 timeline 的字幕。

為什麼不走 FCPXML：實測 ``ImportTimelineFromFile(cleanup.fcpxml)`` 在
Resolve 20.3 回 None（匯入失敗），且 ``ripple_fcpxml`` 硬寫 30fps 對
29.97 素材會累積漂移。MediaPool API 直接 append subclip
（``startFrame``/``endFrame``，同 run_highlight_cut 的 proven pattern）
用素材原生 fps 計算，切點準確。

用法（**Python310** — Resolve scripting bindings 所在環境）：
    python scripts/build_cleanup_timeline.py "data/script_video/<ep>" \
        --video "G:/Footages/<ep>/<ep>.MP4" [--rebuild]

前提：DaVinci Resolve Studio 執行中、External scripting = Local。
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent))

logger = logging.getLogger("cleanup_timeline")

SEGMENTS_NAME = "out/clean_segments.json"


def build(episode_dir: Path, *, video: Path | None, rebuild: bool) -> dict:
    from build_resolve_project import _probe, _versioned_srt, connect_resolve

    seg_path = episode_dir / SEGMENTS_NAME
    if not seg_path.exists():
        raise SystemExit(f"找不到 {seg_path}（先跑 run_mistake_removal.py）")
    payload = json.loads(seg_path.read_text(encoding="utf-8"))
    segments: list[list[float]] = payload["segments"]
    if not segments:
        raise SystemExit("clean_segments.json 沒有任何保留段")

    srt_path = episode_dir / "transcript.srt"
    if not srt_path.exists():
        raise SystemExit(f"找不到 {srt_path}")

    if video is None:
        candidates = sorted(episode_dir.glob("*.mp4")) + sorted(episode_dir.glob("*.MP4"))
        if not candidates:
            raise SystemExit("episode 目錄無主影片，請用 --video 指定原始檔")
        video = candidates[0]
    if not video.exists():
        raise SystemExit(f"主影片不存在: {video}")

    info = _probe(video)
    fps = info["fps"]
    name = episode_dir.name

    resolve = connect_resolve()
    pm = resolve.GetProjectManager()
    project = pm.LoadProject(name) or pm.CreateProject(name)
    if project is None:
        raise SystemExit(f"無法建立/載入 project「{name}」")

    existing = None
    for i in range(1, project.GetTimelineCount() + 1):
        tl = project.GetTimelineByIndex(i)
        if tl and tl.GetName() == name:
            existing = tl
            break
    if existing is not None:
        if not rebuild:
            logger.info("timeline「%s」已存在；--rebuild 才會重建", name)
            return {"project": name, "status": "already-exists"}
        mp0 = project.GetMediaPool()
        if not mp0.DeleteTimelines([existing]):
            raise SystemExit("既有 timeline 刪除失敗")
        logger.info("已刪除舊 timeline，重建中")

    fps_str = f"{fps:.3f}".rstrip("0").rstrip(".")
    project.SetSetting("timelineFrameRate", fps_str)
    if info["width"] and info["height"]:
        project.SetSetting("timelineResolutionWidth", str(info["width"]))
        project.SetSetting("timelineResolutionHeight", str(info["height"]))

    mp = project.GetMediaPool()
    root = mp.GetRootFolder()
    mp.SetCurrentFolder(root)

    vid = next(
        (c for c in (root.GetClipList() or []) if (c.GetName() or "") == video.name), None
    )
    if vid is None:
        items = mp.ImportMedia([str(video)])
        if not items:
            raise SystemExit(f"主影片匯入失敗: {video}")
        vid = items[0]

    timeline = mp.CreateEmptyTimeline(name)
    if timeline is None:
        raise SystemExit("timeline 建立失敗")
    project.SetCurrentTimeline(timeline)

    clip_infos = [
        {
            "mediaPoolItem": vid,
            "startFrame": int(round(s * fps)),
            "endFrame": int(round(e * fps)),
        }
        for s, e in segments
    ]
    if not mp.AppendToTimeline(clip_infos):
        raise SystemExit("保留段上 timeline 失敗")

    # 字幕（版本化複本繞 Resolve 路徑快取；清舊 transcript* 媒體項）
    stale = [
        c
        for c in (root.GetClipList() or [])
        if (c.GetName() or "").startswith("transcript")
    ]
    if stale:
        mp.DeleteClips(stale)
    if timeline.GetTrackCount("subtitle") == 0:
        timeline.AddTrack("subtitle")
    # Resolve 21 實測：同一 process 內「刪舊 timeline 重建」後 append
    # 字幕會回報成功但實際 0 items，且同連線重試也不會好；換一條
    # 新連線（新 process）append 就成功 — 所以字幕階段隔離到子行程。
    project.SetCurrentTimeline(timeline)
    srt_items = mp.ImportMedia([str(_versioned_srt(episode_dir))])
    if srt_items:
        mp.AppendToTimeline(srt_items)
    sub_ok = len(timeline.GetItemListInTrack("subtitle", 1) or []) > 0
    pm.SaveProject()
    if not sub_ok:
        logger.warning("字幕 append 落空（同 process 刪建限制）— 子行程新連線重試")
        r = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), str(episode_dir), "--subs-only"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        logger.info("subs-only 子行程: rc=%d %s", r.returncode, (r.stdout or "").strip()[-200:])
        sub_ok = r.returncode == 0

    v_items = timeline.GetItemListInTrack("video", 1) or []
    a_items = timeline.GetItemListInTrack("audio", 1) or []
    s_items = timeline.GetItemListInTrack("subtitle", 1) or []  # 子行程補上後重讀
    duration = (timeline.GetEndFrame() - timeline.GetStartFrame()) / fps
    return {
        "project": name,
        "status": "rebuilt" if existing is not None else "created",
        "fps": fps_str,
        "video_clips": len(v_items),
        "audio_clips": len(a_items),
        "subtitle_items": len(s_items),
        "subtitles_on_timeline": sub_ok,
        "timeline_duration_sec": round(duration, 1),
        "expected_duration_sec": round(sum(e - s for s, e in segments), 1),
        "first_sub": s_items[0].GetName() if s_items else None,
        "last_sub": s_items[-1].GetName() if s_items else None,
    }


def subs_only(episode_dir: Path) -> int:
    """新連線把 transcript.srt 上到既有 timeline 的字幕軌（子行程模式）。

    實測 Resolve 21 在 DeleteTimelines + 重建後短時間內 append 字幕會
    靜默落空（回傳成功、track 0 items），過幾十秒就正常 — 所以這裡
    用 current-timeline handle（實測成功的 pattern）+ 帶延遲重試。
    """
    import time

    from build_resolve_project import _versioned_srt, connect_resolve

    name = episode_dir.name
    resolve = connect_resolve()
    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()
    if project is None or project.GetName() != name:
        project = pm.LoadProject(name)
    if project is None:
        logger.error("project「%s」不存在", name)
        return 1
    mp = project.GetMediaPool()

    count = 0
    for attempt in range(4):
        timeline = project.GetCurrentTimeline()
        if timeline is None or timeline.GetName() != name:
            for i in range(1, project.GetTimelineCount() + 1):
                tl = project.GetTimelineByIndex(i)
                if tl and tl.GetName() == name:
                    project.SetCurrentTimeline(tl)
                    break
            timeline = project.GetCurrentTimeline()
        if timeline is None:
            logger.error("timeline「%s」不存在", name)
            return 1
        if timeline.GetTrackCount("subtitle") == 0:
            timeline.AddTrack("subtitle")
        srt_items = mp.ImportMedia([str(_versioned_srt(episode_dir))])
        if srt_items:
            mp.AppendToTimeline(srt_items)
        count = len(timeline.GetItemListInTrack("subtitle", 1) or [])
        if count > 0:
            break
        logger.warning("subs-only attempt %d：track 仍為空，%s", attempt + 1,
                       "2s 後重試" if attempt < 3 else "放棄")
        time.sleep(2)
    pm.SaveProject()
    print(json.dumps({"subs_only": count}, ensure_ascii=False))
    return 0 if count > 0 else 1


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="clean_segments.json → Resolve timeline")
    parser.add_argument("episode", help="episode 目錄（data/script_video/<ep>）")
    parser.add_argument("--video", default=None, help="主影片路徑（預設 episode 目錄內找）")
    parser.add_argument("--rebuild", action="store_true", help="同名 timeline 存在時刪除重建")
    parser.add_argument(
        "--subs-only", action="store_true",
        help="只把字幕上軌（內部用：刪建後同 process append 落空時的子行程重試）",
    )
    args = parser.parse_args(argv)
    episode_dir = Path(args.episode)
    if not episode_dir.is_dir():
        logger.error("episode 目錄不存在: %s", episode_dir)
        return 1
    if args.subs_only:
        return subs_only(episode_dir)
    result = build(
        episode_dir,
        video=Path(args.video) if args.video else None,
        rebuild=args.rebuild,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
