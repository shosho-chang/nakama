"""resolve-project：episode 資料夾 → DaVinci Resolve 專案（影片 + 校正字幕上 timeline）。

需求（修修 2026-07-25）：字幕校對完成後，一鍵生成可直接打開的 Resolve 專案——
project 名稱 = episode 資料夾名（如「20260723 謝伯讓」），timeline 上已擺好
主影片與校正後字幕。

前提：
- **DaVinci Resolve Studio**（外部 scripting 為 Studio 功能）且 Resolve 正在執行
- episode 已跑完 subtitle-correct（`transcript.srt` 存在）

佈局：
- Media pool：主影片 + `Cameras` bin（Video/ 全部機位）+ `Audio` bin（Live-Mix 等）
- Timeline（同 project 名）：V1 = 主影片；A1 = `normalized.wav`（Auphonic 處理後、
  與原始錄影同起點）——episode 根目錄沒有 normalized.wav 時退回影片內嵌音軌；
  subtitle 軌 = transcript.srt。既有 timeline 用 `--swap-audio` 把內嵌音軌換掉
- 主影片選擇：episode 根目錄的 `Default_*.mp4`（program feed），沒有則取
  Video/ 中時長最接近字幕音檔的檔案；`--video` 可覆寫

用法：
    python scripts/build_resolve_project.py "G:/footages/20260723 謝伯讓"
    python scripts/build_resolve_project.py <episode> --video <path> --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.brook.podcast_subtitles.handoff import ProjectionVerifierFactory  # noqa: E402
from agents.brook.script_video.subtitle_handoff import (  # noqa: E402
    HASH_BOUND_RELEASE_MODES,
    Stage5SubtitleContractError,
    Stage5SubtitleRequest,
    Stage5SubtitleSelection,
)
from shared.resolve_append import append_checked  # noqa: E402
from shared.subtitle_finalize import (  # noqa: E402
    finalize_srt_file,
    strip_fillers_srt_file,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

logger = logging.getLogger("resolve_project")

DEFAULT_SCRIPT_API = r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting"
DEFAULT_SCRIPT_LIB = r"C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll"
SUBTITLE_NAME = "transcript.srt"
# 版本化 SRT 複本目錄（Resolve 依路徑快取，同路徑重匯拿到舊內容）
RESOLVE_SUBS_DIR = "subs/resolve_subs"
RESOLVE_LINEAGE_RECEIPT = Path(".stage5") / "resolve-project-lineage.v1.json"
RESOLVE_LINEAGE_CONTRACT = "podcast-resolve-project-subtitle-lineage-v1"
# 字幕樣式模板（DRT）：帶著已套用 preset（如「Shosho YT」）的空字幕軌。
# API 不開放 subtitle style preset，樣式只能靠 DRT 模板攜帶——
# 用 --make-template 從「已手動套好樣式」的 timeline 產生一次，之後全自動
DEFAULT_TEMPLATE = (
    Path(__file__).resolve().parent.parent / "data" / "resolve" / "subtitle-template.drt"
)
# 短片直式專用模板（修修「short」preset：字級 50、位置上移）——與長片
# 樣式分家（2026-07-26 十輪）。缺檔時退回主模板
DEFAULT_TEMPLATE_SHORT = (
    Path(__file__).resolve().parent.parent / "data" / "resolve" / "subtitle-template-short.drt"
)


def _template_path() -> Path:
    return Path(os.environ.get("RESOLVE_SUBTITLE_TEMPLATE") or DEFAULT_TEMPLATE)


def _template_path_short() -> Path:
    env = os.environ.get("RESOLVE_SUBTITLE_TEMPLATE_SHORT")
    if env:
        return Path(env)
    if DEFAULT_TEMPLATE_SHORT.exists():
        return DEFAULT_TEMPLATE_SHORT
    return _template_path()  # 短模板尚未產生 → 退回主模板（樣式舊但不裸奔）


def _subtitle_lineage(subtitle: Stage5SubtitleSelection) -> dict:
    return subtitle.identity()


def _canonical_json(payload: dict) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _resolve_lineage_receipt_path(episode_dir: Path) -> Path:
    return episode_dir.resolve() / RESOLVE_LINEAGE_RECEIPT


def _write_resolve_lineage_receipt(
    episode_dir: Path,
    *,
    project_name: str,
    timeline_name: str,
    subtitle: Stage5SubtitleSelection,
) -> Path:
    unsigned = {
        "schema_version": 1,
        "contract": RESOLVE_LINEAGE_CONTRACT,
        "project_name": project_name,
        "timeline_name": timeline_name,
        "subtitle_lineage": _subtitle_lineage(subtitle),
    }
    payload = {**unsigned, "content_hash": _sha256(_canonical_json(unsigned))}
    encoded = _canonical_json(payload)
    path = _resolve_lineage_receipt_path(episode_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)
    return path


def _verify_resolve_lineage_receipt(
    episode_dir: Path,
    *,
    project_name: str,
    timeline_name: str,
    subtitle: Stage5SubtitleSelection,
) -> Path:
    path = _resolve_lineage_receipt_path(episode_dir)
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Stage5SubtitleContractError(
            "existing Resolve timeline lacks a valid subtitle lineage receipt"
        ) from exc
    required = {
        "schema_version",
        "contract",
        "project_name",
        "timeline_name",
        "subtitle_lineage",
        "content_hash",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise Stage5SubtitleContractError("Resolve subtitle lineage receipt schema drift")
    if _canonical_json(payload) != raw:
        raise Stage5SubtitleContractError("Resolve subtitle lineage receipt is not canonical")
    unsigned = {key: value for key, value in payload.items() if key != "content_hash"}
    if payload.get("content_hash") != _sha256(_canonical_json(unsigned)):
        raise Stage5SubtitleContractError("Resolve subtitle lineage receipt content hash mismatch")
    if (
        payload.get("schema_version") != 1
        or payload.get("contract") != RESOLVE_LINEAGE_CONTRACT
        or payload.get("project_name") != project_name
        or payload.get("timeline_name") != timeline_name
        or payload.get("subtitle_lineage") != _subtitle_lineage(subtitle)
    ):
        raise Stage5SubtitleContractError(
            "existing Resolve timeline subtitle lineage differs from this release"
        )
    return path


def _versioned_srt(
    episode_dir: Path,
    *,
    subtitle: Stage5SubtitleSelection,
) -> Path:
    """把最新 transcript.srt 定版成遞增版本檔（顯示層副本），回傳新路徑。

    Resolve 的 media pool 依「檔案路徑」快取——transcript.srt 內容更新後
    用同路徑 ImportMedia 會拿回舊 item。每次匯入都用新路徑的複本繞開快取；
    複本要保留（media pool item 引用該檔），佔用極小（純文字）。

    複本同時套修修 2026-08-05 字幕定版兩規則（句尾零標點 + cue 間 ≤3s
    空隙補平連續顯示）——transcript.srt 本體不動，工作真值必須貼語音。
    """
    src = subtitle.srt_path
    out_dir = episode_dir / RESOLVE_SUBS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 1
    while (out_dir / f"transcript_r{n:03d}.srt").exists():
        n += 1
    dst = out_dir / f"transcript_r{n:03d}.srt"
    if subtitle.mode in HASH_BOUND_RELEASE_MODES:
        # Hash-bound release 不跑完整定版（標點／空隙屬顯示層處理，會默默改動
        # 已審核文字）。但語助詞清理是修修 2026-09-03 的明示編輯決策，套用並
        # 報數；release.srt 本體不動，lineage 仍綁來源 release identity。
        stats = strip_fillers_srt_file(src, dst)
        logger.info(
            "Hash-bound 字幕：語助詞清理 %d → %d 句（整條刪 %d、刪字保句 %d）",
            stats["cues_in"],
            stats["cues"],
            stats["filler_cues_dropped"],
            stats["filler_stripped"],
        )
        return dst
    stats = finalize_srt_file(src, dst)
    msg = f"字幕定版: 尾標點剝 {stats['stripped']} 句、空隙補平 {stats['closed']} 處"
    if stats.get("reboundary_moved"):
        msg += f"、切點重修 {stats['reboundary_moved']} 處"
    if stats["true_silences"]:
        msg += f"、>3s 真靜默不補 {len(stats['true_silences'])} 處（字幕該消失）"
    if stats.get("bad_boundaries"):
        msg += f"、⚠️ 斷句疑點 {len(stats['bad_boundaries'])} 處（不准默默出貨——列出待修）"
        for f in stats["bad_boundaries"][:5]:
            logger.warning(f"斷句疑點 cue{f['cue']}: …{f['tail']}｜{f['head']}…（{f['reason']}）")
    logger.info(msg)
    return dst


def _versioned_srt_exact(episode_dir: Path, source: Path) -> Path:
    """Return an audited SRT unchanged for a first-time Resolve import.

    The reviewed V2 file already has a unique path.  Importing it directly
    avoids both Resolve's same-path cache and an unnecessary write beside the
    episode media on a different volume.
    """
    if not source.exists():
        raise FileNotFoundError(f"audited subtitle not found: {source}")
    logger.info("Using audited SRT unchanged: %s", source)
    return source


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


def _normalized_audio(episode_dir: Path) -> Path | None:
    """episode 根目錄的 normalized.wav（audio-prep 產出、與原始錄影同起點）。"""
    p = episode_dir / "normalized.wav"
    return p if p.exists() else None


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
    subtitle: Path | None = None,
    dry_run: bool = False,
    subtitle_request: Stage5SubtitleRequest | None = None,
    verifier_factory: ProjectionVerifierFactory | None = None,
) -> dict:
    subtitle = (subtitle_request or Stage5SubtitleRequest()).open(
        episode_dir,
        factory=verifier_factory,
    )
    project_name = episode_dir.name
    srt_path = subtitle.srt_path

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

    normalized = _normalized_audio(episode_dir)
    plan = {
        "project": project_name,
        "main_video": str(main_video),
        "fps": info["fps"],
        "resolution": f"{info['width']}x{info['height']}",
        "subtitle": str(srt_path),
        **_subtitle_lineage(subtitle),
        "timeline_audio": str(normalized) if normalized else "（影片內嵌音軌）",
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

    # Fail closed before mutating project settings or media-pool state.  A same-name
    # timeline is idempotent only when its persisted receipt proves that it was
    # built from the exact subtitle selection opened above.
    for i in range(1, project.GetTimelineCount() + 1):
        tl = project.GetTimelineByIndex(i)
        if tl and tl.GetName() == project_name:
            _verify_resolve_lineage_receipt(
                episode_dir,
                project_name=project_name,
                timeline_name=project_name,
                subtitle=subtitle,
            )
            logger.info(f"timeline「{project_name}」已存在，跳過重建")
            pm.SaveProject()
            return {**plan, "status": "already-exists"}

    fps_str = f"{info['fps']:.3f}".rstrip("0").rstrip(".")
    project.SetSetting("timelineFrameRate", fps_str)
    if info["width"] and info["height"]:
        project.SetSetting("timelineResolutionWidth", str(info["width"]))
        project.SetSetting("timelineResolutionHeight", str(info["height"]))

    mp = project.GetMediaPool()
    root = mp.GetRootFolder()

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
    norm_items = mp.ImportMedia([str(normalized)]) if normalized else None
    if normalized and not norm_items:
        logger.warning(f"normalized.wav 匯入失敗（{normalized}），退回影片內嵌音軌")

    def _fill_av(timeline) -> None:
        """主影片與音軌上 timeline：有 normalized 就純視訊 + normalized 音軌。

        ⚠️ 一律走 append_checked——timeline 剛從模板匯入時 Resolve 常還沒就緒，
        第一次 append 回 `[None]`（truthy，舊的 `if not ok` 判不出來）。
        2026-08-05 安吉集：主影片整支沒上軌卻回報 created，到緊湊化才炸出來。
        """
        if norm_items:
            append_checked(
                mp,
                [{"mediaPoolItem": main_items[0], "mediaType": 1}],
                "主影片（純視訊）",
            )
            append_checked(
                mp,
                [
                    {
                        "mediaPoolItem": norm_items[0],
                        "mediaType": 2,
                        "trackIndex": 1,
                        "recordFrame": timeline.GetStartFrame(),
                    }
                ],
                "normalized.wav",
            )
        else:
            append_checked(mp, main_items, "主影片")
        placed = len(timeline.GetItemListInTrack("video", 1) or [])
        if placed < 1:
            raise SystemExit(f"主影片上軌後 v1 仍是空的（items={placed}）")

    template = _template_path()
    timeline = None
    if template.exists():
        # 從樣式模板長出 timeline（帶已套 preset 的字幕軌），再改名、填入主影片
        timeline = mp.ImportTimelineFromFile(str(template), {})
        if timeline is not None:
            if not timeline.SetName(project_name):
                logger.warning(f"timeline 改名失敗，保留模板名「{timeline.GetName()}」")
            project.SetCurrentTimeline(timeline)
            _fill_av(timeline)
            logger.info(f"timeline 由樣式模板建立: {template.name}")
        else:
            logger.warning(f"模板匯入失敗（{template}），退回無樣式建立")
    if timeline is None:
        timeline = mp.CreateEmptyTimeline(project_name)
        if timeline is None:
            raise SystemExit("timeline 建立失敗")
        project.SetCurrentTimeline(timeline)
        _fill_av(timeline)

    # 字幕：SRT 匯入 media pool 後 append —— Resolve 會放上 subtitle 軌
    # （模板軌已帶樣式，不可刪軌重建）
    if timeline.GetTrackCount("subtitle") == 0:
        timeline.AddTrack("subtitle")
    srt_items = mp.ImportMedia([str(_versioned_srt(episode_dir, subtitle=subtitle))])
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
    if subtitle_ok:
        _write_resolve_lineage_receipt(
            episode_dir,
            project_name=project_name,
            timeline_name=project_name,
            subtitle=subtitle,
        )
    logger.info(
        f"完成: project「{project_name}」/ timeline 就緒"
        f"（字幕 {'已上軌' if subtitle_ok else '需手動插入'}）"
    )
    return {**plan, "status": "created", "subtitle_on_timeline": subtitle_ok}


def refresh_subtitles(
    episode_dir: Path,
    *,
    subtitle_request: Stage5SubtitleRequest | None = None,
    verifier_factory: ProjectionVerifierFactory | None = None,
) -> dict:
    """把 timeline 的字幕軌整個換成最新的 transcript.srt（QC 裁決迭代用）。"""
    subtitle = (subtitle_request or Stage5SubtitleRequest()).open(
        episode_dir,
        factory=verifier_factory,
    )
    project_name = episode_dir.name
    srt_path = subtitle.srt_path

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
    _verify_resolve_lineage_receipt(
        episode_dir,
        project_name=project_name,
        timeline_name=project_name,
        subtitle=subtitle,
    )
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
    srt_items = mp.ImportMedia([str(_versioned_srt(episode_dir, subtitle=subtitle))])
    appended = bool(mp.AppendToTimeline(srt_items)) if srt_items else False
    pm.SaveProject()
    if appended:
        _write_resolve_lineage_receipt(
            episode_dir,
            project_name=project_name,
            timeline_name=project_name,
            subtitle=subtitle,
        )
    count = len(timeline.GetItemListInTrack("subtitle", 1) or [])
    logger.info(f"字幕軌已刷新: {count} 句（{'成功' if appended else '失敗'}）")
    return {"project": project_name, "status": "subtitles-refreshed", "subtitle_items": count}


def swap_audio(episode_dir: Path) -> dict:
    """既有 timeline 的音軌換成 normalized.wav（內嵌音軌移除）。

    修修 2026-07-25：timeline 上的 audio 要用 Auphonic normalize 過的檔案，
    不要影片內嵌音軌。開錄點一致（修修確認），normalized 直接放 timeline 起點。
    """
    project_name = episode_dir.name
    normalized = _normalized_audio(episode_dir)
    if normalized is None:
        raise FileNotFoundError(f"找不到 {episode_dir / 'normalized.wav'}（先跑 audio-prep）")

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

    # 移除既有音軌內容（影片內嵌音軌）：先解除 video-audio link 再刪，
    # 避免連動刪掉 V1 的影片
    old_items = []
    for ti in range(1, timeline.GetTrackCount("audio") + 1):
        old_items.extend(timeline.GetItemListInTrack("audio", ti) or [])
    if old_items:
        timeline.SetClipsLinked(old_items, False)
        if not timeline.DeleteClips(old_items):
            raise SystemExit("移除既有音軌內容失敗")
    video_count = len(timeline.GetItemListInTrack("video", 1) or [])
    if video_count == 0:
        raise SystemExit("V1 影片在刪音軌後消失——請在 Resolve 內 undo（Ctrl+Z）並回報")

    mp = project.GetMediaPool()
    mp.SetCurrentFolder(mp.GetRootFolder())
    norm_items = mp.ImportMedia([str(normalized)])
    if not norm_items:
        raise SystemExit(f"normalized.wav 匯入失敗: {normalized}")
    ok = mp.AppendToTimeline(
        [
            {
                "mediaPoolItem": norm_items[0],
                "mediaType": 2,
                "trackIndex": 1,
                "recordFrame": timeline.GetStartFrame(),
            }
        ]
    )
    if not ok:
        raise SystemExit("normalized.wav 上 timeline 失敗")
    pm.SaveProject()
    new_items = timeline.GetItemListInTrack("audio", 1) or []
    logger.info(f"音軌已換成 normalized.wav（移除 {len(old_items)} 個內嵌音軌 clip）")
    return {
        "project": project_name,
        "status": "audio-swapped",
        "removed_audio_items": len(old_items),
        "audio_items": len(new_items),
        "audio_source": str(normalized),
    }


def make_template(
    episode_dir: Path, *, source_name: str | None = None, out_path: Path | None = None
) -> dict:
    """從「已手動套好字幕樣式」的 timeline 產生 DRT 樣式模板（一次性設定）。

    流程：DuplicateTimeline → 複本清空所有 video/audio/subtitle 內容
    （軌與樣式保留）→ Export DRT → 刪複本。之後 build_project 自動套用。
    source_name 預設主 timeline（= project 名）；out_path 預設主模板路徑
    （短片模板：source 給某支（緊·導播）、out 給 DEFAULT_TEMPLATE_SHORT）。
    """
    project_name = episode_dir.name
    source_name = source_name or project_name
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
        if tl and tl.GetName() == source_name:
            source_tl = tl
            break
    if source_tl is None:
        raise SystemExit(f"timeline「{source_name}」不存在")
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

    template = out_path or _template_path()
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
        "--legacy-v1",
        action="store_true",
        help="明確使用 episode/transcript.srt；正式 V2 流程禁止使用",
    )
    parser.add_argument("--projection-id")
    parser.add_argument("--expected-episode-id")
    parser.add_argument("--expected-generation-id")
    parser.add_argument("--expected-manifest-sha256")
    parser.add_argument("--reference-manifest")
    parser.add_argument(
        "--subtitle-release-handoff",
        help=(
            "episode-local official Memo Dual-Audit Stage 5 handoff JSON; "
            "omitted means subtitle-release/memo-dual-audit-v1/STAGE5-HANDOFF.json"
        ),
    )
    parser.add_argument(
        "--degraded-release-handoff",
        help="episode-local degraded dual-ASR Stage 5 handoff JSON",
    )
    parser.add_argument(
        "--refresh-subtitles",
        action="store_true",
        help="只刷新既有 timeline 的字幕內容（transcript.srt 更新後用；軌與樣式保留）",
    )
    parser.add_argument(
        "--swap-audio",
        action="store_true",
        help="既有 timeline 的音軌換成 normalized.wav（移除影片內嵌音軌）",
    )
    parser.add_argument(
        "--make-template",
        action="store_true",
        help="從此 episode 已套好字幕樣式的 timeline 產生 DRT 樣式模板（一次性）",
    )
    parser.add_argument(
        "--make-template-short",
        metavar="TIMELINE_NAME",
        help="從指定 timeline 產生**短片**字幕樣式模板（修修「short」preset）",
    )
    args = parser.parse_args(argv)
    args.subtitle_request = Stage5SubtitleRequest(
        legacy_v1=args.legacy_v1,
        subtitle_release_handoff=args.subtitle_release_handoff,
        degraded_release_handoff=args.degraded_release_handoff,
        projection_id=args.projection_id,
        expected_episode_id=args.expected_episode_id,
        expected_generation_id=args.expected_generation_id,
        expected_manifest_sha256=args.expected_manifest_sha256,
        reference_manifest=args.reference_manifest,
    )
    return args


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    args = _parse_args(argv)
    episode_dir = Path(args.episode)
    if not episode_dir.is_dir():
        logger.error(f"episode 資料夾不存在: {episode_dir}")
        return 1
    started = time.time()
    if args.make_template_short:
        result = make_template(
            episode_dir,
            source_name=args.make_template_short,
            out_path=DEFAULT_TEMPLATE_SHORT,
        )
    elif args.make_template:
        result = make_template(episode_dir)
    elif args.swap_audio:
        result = swap_audio(episode_dir)
    elif args.refresh_subtitles:
        result = refresh_subtitles(episode_dir, subtitle_request=args.subtitle_request)
    else:
        result = build_project(
            episode_dir,
            video=Path(args.video) if args.video else None,
            dry_run=args.dry_run,
            subtitle_request=args.subtitle_request,
        )
    result["elapsed_sec"] = round(time.time() - started, 1)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
