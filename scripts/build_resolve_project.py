"""resolve-project：episode 資料夾 → DaVinci Resolve 專案（影片 + 校正字幕上 timeline）。

需求（修修 2026-07-25）：字幕校對完成後，一鍵生成可直接打開的 Resolve 專案——
project 名稱 = episode 資料夾名（如「20260723 謝伯讓」），timeline 上已擺好
主影片與校正後字幕。

前提：
- **DaVinci Resolve Studio**（外部 scripting 為 Studio 功能）且 Resolve 正在執行
- episode 已跑完 subtitle-correct（`transcript.srt` 存在）

佈局：
- Media pool：主影片 + `Cameras` bin（Video/ 全部機位）+ `Audio` bin（Live-Mix 等）
- Timeline（同 project 名）：V1 = 主影片（含其音軌）；subtitle 軌 = transcript.srt
- 主影片選擇：episode 根目錄的 `Default_*.mp4`（program feed），沒有則取
  Video/ 中時長最接近字幕音檔的檔案；`--video` 可覆寫

用法：
    python scripts/build_resolve_project.py "G:/footages/20260723 謝伯讓"
    python scripts/build_resolve_project.py <episode> --video <path> --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

logger = logging.getLogger("resolve_project")

DEFAULT_SCRIPT_API = r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting"
DEFAULT_SCRIPT_LIB = r"C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll"
SUBTITLE_NAME = "transcript.srt"
# 版本化 SRT 複本目錄（Resolve 依路徑快取，同路徑重匯拿到舊內容）
RESOLVE_SUBS_DIR = "subs/resolve_subs"
# 字幕樣式模板（DRT）：帶著已套用 preset（如「Shosho YT」）的空字幕軌。
# API 不開放 subtitle style preset，樣式只能靠 DRT 模板攜帶——
# 用 --make-template 從「已手動套好樣式」的 timeline 產生一次，之後全自動
DEFAULT_TEMPLATE = (
    Path(__file__).resolve().parent.parent / "data" / "resolve" / "subtitle-template.drt"
)


def _template_path() -> Path:
    return Path(os.environ.get("RESOLVE_SUBTITLE_TEMPLATE") or DEFAULT_TEMPLATE)


def _versioned_srt(episode_dir: Path) -> Path:
    """把最新 transcript.srt 複製成遞增版本檔，回傳新路徑。

    Resolve 的 media pool 依「檔案路徑」快取——transcript.srt 內容更新後
    用同路徑 ImportMedia 會拿回舊 item。每次匯入都用新路徑的複本繞開快取；
    複本要保留（media pool item 引用該檔），佔用極小（純文字）。
    """
    import shutil

    src = episode_dir / SUBTITLE_NAME
    out_dir = episode_dir / RESOLVE_SUBS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 1
    while (out_dir / f"transcript_r{n:03d}.srt").exists():
        n += 1
    dst = out_dir / f"transcript_r{n:03d}.srt"
    shutil.copy2(src, dst)
    return dst


def _probe(path: Path) -> dict:
    r = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=r_frame_rate,width,height",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    data = json.loads(r.stdout or "{}")
    stream = (data.get("streams") or [{}])[0]
    num, _, den = (stream.get("r_frame_rate") or "30/1").partition("/")
    fps = float(num) / float(den or 1)
    return {
        "fps": fps,
        "width": stream.get("width"),
        "height": stream.get("height"),
        "duration": float((data.get("format") or {}).get("duration") or 0),
    }


def find_main_video(episode_dir: Path, override: Path | None) -> Path:
    if override:
        if not override.exists():
            raise FileNotFoundError(f"--video 指定的檔案不存在: {override}")
        return override
    defaults = sorted(episode_dir.glob("Default_*.mp4"))
    if defaults:
        return defaults[0]
    video_dir = next(
        (d for d in episode_dir.iterdir() if d.is_dir() and d.name.lower() == "video"), None
    )
    candidates = sorted(video_dir.glob("*.mp4")) if video_dir else []
    if not candidates:
        raise FileNotFoundError(f"找不到主影片（{episode_dir} 無 Default_*.mp4 或 Video/*.mp4）")
    return candidates[0]


def connect_resolve():
    """連上執行中的 DaVinci Resolve（Studio）。回傳 resolve 物件。"""
    api = os.environ.get("RESOLVE_SCRIPT_API", DEFAULT_SCRIPT_API)
    lib = os.environ.get("RESOLVE_SCRIPT_LIB", DEFAULT_SCRIPT_LIB)
    os.environ["RESOLVE_SCRIPT_API"] = api
    os.environ["RESOLVE_SCRIPT_LIB"] = lib
    sys.path.append(str(Path(api) / "Modules"))
    try:
        import DaVinciResolveScript as dvr
    except ImportError as e:
        raise SystemExit(f"找不到 DaVinciResolveScript（RESOLVE_SCRIPT_API={api}）: {e}")
    resolve = dvr.scriptapp("Resolve")
    if resolve is None:
        raise SystemExit(
            "無法連上 DaVinci Resolve：請確認 (1) Resolve 正在執行 "
            "(2) 是 Studio 版 (3) Preferences → System → General → "
            "External scripting using 設為 Local"
        )
    product = resolve.GetProductName()
    if "Studio" not in product:
        logger.warning(f"偵測到 {product}（非 Studio），外部 scripting 可能受限")
    logger.info(f"已連上 {product} {resolve.GetVersionString()}")
    return resolve


def build_project(
    episode_dir: Path,
    *,
    video: Path | None = None,
    dry_run: bool = False,
) -> dict:
    project_name = episode_dir.name
    srt_path = episode_dir / SUBTITLE_NAME
    if not srt_path.exists():
        raise FileNotFoundError(f"找不到 {srt_path}（先跑 subtitle-correct）")

    main_video = find_main_video(episode_dir, video)
    info = _probe(main_video)
    video_dir = next(
        (d for d in episode_dir.iterdir() if d.is_dir() and d.name.lower() == "video"), None
    )
    cameras = [p for p in sorted(video_dir.glob("*.mp4")) if p != main_video] if video_dir else []
    audio_dir = next(
        (d for d in episode_dir.iterdir() if d.is_dir() and d.name.lower() == "audio"), None
    )
    audio_files = sorted(audio_dir.glob("*.wav")) if audio_dir else []

    plan = {
        "project": project_name,
        "main_video": str(main_video),
        "fps": info["fps"],
        "resolution": f"{info['width']}x{info['height']}",
        "subtitle": str(srt_path),
        "cameras": [str(p) for p in cameras],
        "audio_files": [str(p) for p in audio_files],
    }
    logger.info(f"計畫: {json.dumps(plan, ensure_ascii=False, indent=2)}")
    if dry_run:
        return plan

    resolve = connect_resolve()
    pm = resolve.GetProjectManager()

    project = pm.LoadProject(project_name) or pm.CreateProject(project_name)
    if project is None:
        raise SystemExit(f"無法建立/載入 project「{project_name}」")
    logger.info(f"project 就緒: {project.GetName()}")

    fps_str = f"{info['fps']:.3f}".rstrip("0").rstrip(".")
    project.SetSetting("timelineFrameRate", fps_str)
    if info["width"] and info["height"]:
        project.SetSetting("timelineResolutionWidth", str(info["width"]))
        project.SetSetting("timelineResolutionHeight", str(info["height"]))

    mp = project.GetMediaPool()
    root = mp.GetRootFolder()

    # 冪等：timeline 已存在（同名）就不重建
    for i in range(1, project.GetTimelineCount() + 1):
        tl = project.GetTimelineByIndex(i)
        if tl and tl.GetName() == project_name:
            logger.info(f"timeline「{project_name}」已存在，跳過重建")
            pm.SaveProject()
            return {**plan, "status": "already-exists"}

    mp.SetCurrentFolder(root)
    main_items = mp.ImportMedia([str(main_video)])
    if not main_items:
        raise SystemExit(f"主影片匯入失敗: {main_video}")

    if cameras:
        cam_bin = next(
            (f for f in root.GetSubFolderList() if f.GetName() == "Cameras"),
            None,
        ) or mp.AddSubFolder(root, "Cameras")
        mp.SetCurrentFolder(cam_bin)
        mp.ImportMedia([str(p) for p in cameras])
    if audio_files:
        audio_bin = next(
            (f for f in root.GetSubFolderList() if f.GetName() == "Audio"),
            None,
        ) or mp.AddSubFolder(root, "Audio")
        mp.SetCurrentFolder(audio_bin)
        mp.ImportMedia([str(p) for p in audio_files])

    mp.SetCurrentFolder(root)
    template = _template_path()
    timeline = None
    if template.exists():
        # 從樣式模板長出 timeline（帶已套 preset 的字幕軌），再改名、填入主影片
        timeline = mp.ImportTimelineFromFile(str(template), {})
        if timeline is not None:
            if not timeline.SetName(project_name):
                logger.warning(f"timeline 改名失敗，保留模板名「{timeline.GetName()}」")
            project.SetCurrentTimeline(timeline)
            if not mp.AppendToTimeline(main_items):
                raise SystemExit("主影片放上模板 timeline 失敗")
            logger.info(f"timeline 由樣式模板建立: {template.name}")
        else:
            logger.warning(f"模板匯入失敗（{template}），退回無樣式建立")
    if timeline is None:
        timeline = mp.CreateTimelineFromClips(project_name, main_items)
        if timeline is None:
            raise SystemExit("timeline 建立失敗")
        project.SetCurrentTimeline(timeline)

    # 字幕：SRT 匯入 media pool 後 append —— Resolve 會放上 subtitle 軌
    # （模板軌已帶樣式，不可刪軌重建）
    if timeline.GetTrackCount("subtitle") == 0:
        timeline.AddTrack("subtitle")
    srt_items = mp.ImportMedia([str(_versioned_srt(episode_dir))])
    subtitle_ok = False
    if srt_items:
        appended = mp.AppendToTimeline(srt_items)
        subtitle_ok = bool(appended)
    if not subtitle_ok:
        logger.warning(
            "字幕自動上軌失敗——media pool 內已有 transcript.srt，"
            "請在 Resolve 對它右鍵 → Insert Selected Subtitles to Timeline"
        )

    pm.SaveProject()
    logger.info(
        f"完成: project「{project_name}」/ timeline 就緒"
        f"（字幕 {'已上軌' if subtitle_ok else '需手動插入'}）"
    )
    return {**plan, "status": "created", "subtitle_on_timeline": subtitle_ok}


def refresh_subtitles(episode_dir: Path) -> dict:
    """把 timeline 的字幕軌整個換成最新的 transcript.srt（QC 裁決迭代用）。"""
    project_name = episode_dir.name
    srt_path = episode_dir / SUBTITLE_NAME
    if not srt_path.exists():
        raise FileNotFoundError(f"找不到 {srt_path}")

    resolve = connect_resolve()
    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()
    if project is None or project.GetName() != project_name:
        project = pm.LoadProject(project_name)
    if project is None:
        raise SystemExit(f"project「{project_name}」不存在，先跑完整建立")

    timeline = None
    for i in range(1, project.GetTimelineCount() + 1):
        tl = project.GetTimelineByIndex(i)
        if tl and tl.GetName() == project_name:
            timeline = tl
            break
    if timeline is None:
        raise SystemExit(f"timeline「{project_name}」不存在")
    project.SetCurrentTimeline(timeline)

    # 清字幕「內容」但**保留軌**——subtitle 軌樣式（如 Shosho YT preset）
    # 掛在軌上，刪軌 = 洗掉樣式
    if timeline.GetTrackCount("subtitle") == 0:
        timeline.AddTrack("subtitle")
    for ti in range(1, timeline.GetTrackCount("subtitle") + 1):
        items = timeline.GetItemListInTrack("subtitle", ti) or []
        if items:
            timeline.DeleteClips(items)

    # 清舊 SRT items（依名稱前綴），改匯版本化複本繞開 Resolve 的路徑快取
    mp = project.GetMediaPool()
    root = mp.GetRootFolder()
    stale = [
        c
        for c in (root.GetClipList() or [])
        if (c.GetName() or "").startswith(("transcript", srt_path.stem))
    ]
    if stale:
        mp.DeleteClips(stale)
    mp.SetCurrentFolder(root)
    srt_items = mp.ImportMedia([str(_versioned_srt(episode_dir))])
    appended = bool(mp.AppendToTimeline(srt_items)) if srt_items else False
    pm.SaveProject()
    count = len(timeline.GetItemListInTrack("subtitle", 1) or [])
    logger.info(f"字幕軌已刷新: {count} 句（{'成功' if appended else '失敗'}）")
    return {"project": project_name, "status": "subtitles-refreshed", "subtitle_items": count}


def make_template(episode_dir: Path) -> dict:
    """從「已手動套好字幕樣式」的 episode timeline 產生 DRT 樣式模板（一次性設定）。

    流程：DuplicateTimeline → 複本清空所有 video/audio/subtitle 內容
    （軌與樣式保留）→ Export DRT → 刪複本。之後 build_project 自動套用。
    """
    project_name = episode_dir.name
    resolve = connect_resolve()
    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()
    if project is None or project.GetName() != project_name:
        project = pm.LoadProject(project_name)
    if project is None:
        raise SystemExit(f"project「{project_name}」不存在")

    source_tl = None
    for i in range(1, project.GetTimelineCount() + 1):
        tl = project.GetTimelineByIndex(i)
        if tl and tl.GetName() == project_name:
            source_tl = tl
            break
    if source_tl is None:
        raise SystemExit(f"timeline「{project_name}」不存在")
    project.SetCurrentTimeline(source_tl)

    dup = source_tl.DuplicateTimeline("__subtitle_template_build__")
    if dup is None:
        raise SystemExit("DuplicateTimeline 失敗")
    project.SetCurrentTimeline(dup)
    for track_type in ("video", "audio", "subtitle"):
        for ti in range(1, dup.GetTrackCount(track_type) + 1):
            items = dup.GetItemListInTrack(track_type, ti) or []
            if items:
                dup.DeleteClips(items)

    template = _template_path()
    template.parent.mkdir(parents=True, exist_ok=True)
    ok = dup.Export(str(template), resolve.EXPORT_DRT)
    project.SetCurrentTimeline(source_tl)
    project.GetMediaPool().DeleteTimelines([dup])
    pm.SaveProject()
    if not ok:
        raise SystemExit("DRT 模板匯出失敗")
    logger.info(f"樣式模板已存: {template}")
    return {"status": "template-saved", "template": str(template)}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="episode → DaVinci Resolve 專案")
    parser.add_argument("episode", help="episode 資料夾")
    parser.add_argument("--video", help="主影片路徑（覆寫自動偵測）")
    parser.add_argument("--dry-run", action="store_true", help="只印計畫，不動 Resolve")
    parser.add_argument(
        "--refresh-subtitles",
        action="store_true",
        help="只刷新既有 timeline 的字幕內容（transcript.srt 更新後用；軌與樣式保留）",
    )
    parser.add_argument(
        "--make-template",
        action="store_true",
        help="從此 episode 已套好字幕樣式的 timeline 產生 DRT 樣式模板（一次性）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    args = _parse_args(argv)
    episode_dir = Path(args.episode)
    if not episode_dir.is_dir():
        logger.error(f"episode 資料夾不存在: {episode_dir}")
        return 1
    started = time.time()
    if args.make_template:
        result = make_template(episode_dir)
    elif args.refresh_subtitles:
        result = refresh_subtitles(episode_dir)
    else:
        result = build_project(
            episode_dir,
            video=Path(args.video) if args.video else None,
            dry_run=args.dry_run,
        )
    result["elapsed_sec"] = round(time.time() - started, 1)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
