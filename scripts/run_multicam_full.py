"""multicam-director：完整節目三機切換（單機直切 timeline → CAM1／CAM2／CAM3）。

意圖層在 `.claude/skills/multicam-director/SKILL.md`；本 script 是實現層，只呼叫既有程式：

  shared.speaker_timing.load_speaker_timing_tokens
      token（legacy words.json 或 ADR-063 Memo release）
  shared.speaker_assign
      mic 軌偵測、能量包絡、詞級說話者、機位偏移量測
  run_short_director.build_shots / inject_reaction_cuts
      shot list（節奏反應鏡頭 + 哈/哇內容反應鏡頭）
  shared.resolve_append.append_checked / delete_checked
      Resolve 上軌與清軌的已知坑

20260901 蘇予昕、20260723 謝伯讓、20260903 程世嘉三集各寫過一次性 driver；本檔是
20260903 程世嘉那版（462 shots 驗證通過）的正式化。

流程：
  1. token → 說話者（mic 軌必須剛好 2 軌）
  2. 三機對 program feed 偏移量測（>1 frame 就 fail——shot 的來源 frame 直接用節目時鐘）
  3. build_shots → inject_reaction_cuts → 開場 N 秒全景
  4. （非 --dry-run）複製原 timeline（帶 A1 與字幕）、清 V1、逐 shot 上軌、全景套 zoom
  5. 驗證：V1 無空隙、A1／字幕前後一致、原 timeline 未動、全景 zoom 全部正確 → SaveProject

用法：
    python scripts/run_multicam_full.py "G:\\Footages\\20260903 程世嘉" \\
        --source-timeline "20260903 程世嘉" --new-timeline "20260903 程世嘉（三機）" \\
        --wide-zoom 1.4 --cams "0=1_CAMERA 1.mp4,1=2_CAMERA 2.mp4" \\
        --wide-cam "3_CAMERA 3.mp4" --dry-run

原 timeline 只接受「單機直切」（V1 = 一整段 program feed）。已剪過／已是三機的 timeline
要先依 skill 的 coalesce 規則推導段落結構——這一版 fail closed，不猜。
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from statistics import median

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_resolve_project import _probe, find_main_video  # noqa: E402
from run_short_director import (  # noqa: E402
    _find_media_item_by_path,
    _media_pool_items,
    _validate_appended_source_range,
    _validate_media_source_range,
    build_shots,
    inject_reaction_cuts,
)

from shared.resolve_append import append_checked, delete_checked  # noqa: E402
from shared.speaker_assign import (  # noqa: E402
    _measure_offset,
    assign_word_speakers,
    detect_mic_tracks,
    load_envelopes,
)
from shared.speaker_timing import load_speaker_timing_tokens  # noqa: E402

logger = logging.getLogger("multicam_full")

# 完整節目節奏（修修已核准的基準值，見 SKILL.md「機位配置」）。從
# run_short_director.FORMAT_CFG["long"] 起跳再放慢——完整節目比精華長得多，
# 長片節奏會嚴重 over-edit。機位檔名與全景倍率每集由 CLI 指定，不在這裡。
DEFAULT_CFG = {
    "zoom_base": 1.0,
    "min_shot": 2.0,  # 短於此的說話者 run 不切鏡（併入前一 shot）
    "reaction_every": 20.0,  # 同人講超過這麼久，插一個聽者反應鏡頭
    "reaction_sec": 2.0,
    "reaction_style": "alternate",  # 聽者特寫／全景交替
    "opener_sec": 5.0,  # 開場（tl=0）用全景
}


# --- 純函數：shot list ---------------------------------------------------------


def parse_cams(text: str) -> dict[str, str]:
    """`0=1_CAMERA 1.mp4,1=2_CAMERA 2.mp4` → {"0": ..., "1": ...}；必須剛好 0 與 1。"""
    cams: dict[str, str] = {}
    for part in text.split(","):
        key, sep, name = part.partition("=")
        key, name = key.strip(), name.strip()
        if not sep or not key or not name:
            raise argparse.ArgumentTypeError(f"--cams 格式是 0=<檔名>,1=<檔名>，收到「{part}」")
        if key in cams:
            raise argparse.ArgumentTypeError(f"--cams 重複指定 speaker {key}")
        cams[key] = name
    if set(cams) != {"0", "1"}:
        raise argparse.ArgumentTypeError(f"--cams 必須剛好指定 speaker 0 與 1，收到 {sorted(cams)}")
    if cams["0"] == cams["1"]:
        raise argparse.ArgumentTypeError("--cams 兩位說話者指到同一台機位")
    return cams


def apply_opener(shots: list[dict], opener_sec: float, zoom: float) -> list[dict]:
    """前 opener_sec 秒改成全景：落在開場內的 shot 丟掉，跨界的 shot 起點夾到 opener_sec。"""
    if opener_sec <= 0 or not shots:
        return shots
    rest: list[dict] = []
    for sh in shots:
        if sh["e"] <= opener_sec:
            continue
        rest.append({**sh, "s": opener_sec} if sh["s"] < opener_sec else sh)
    opener = {"s": 0.0, "e": opener_sec, "spk": shots[0]["spk"], "cam": "wide", "kind": "opener"}
    opener["zoom"] = zoom
    return [opener, *rest]


def build_full_shots(
    tokens: list[dict], speakers: list[int | None], duration: float, cfg: dict
) -> list[dict]:
    """token + 說話者 → 完整節目 shot list（節目時鐘秒數，[0, duration] 無縫）。"""
    assigned = [(t["start"], t["end"], s) for t, s in zip(tokens, speakers) if s is not None]
    if not assigned:
        raise SystemExit("沒有任何 token 判定出說話者——無法導播")
    shots = build_shots([(0.0, duration)], assigned, cfg)
    with_text = [
        {"word": t["word"], "start": t["start"], "end": t["end"], "spk": s}
        for t, s in zip(tokens, speakers)
        if s is not None
    ]
    shots = inject_reaction_cuts(shots, with_text, cfg)
    return apply_opener(shots, cfg["opener_sec"], cfg["zoom_base"])


def shot_frames(
    shots: list[dict], fps: float, total_frames: int, cams: dict[str, str], wide_cam: str
) -> list[dict]:
    """shot（秒）→ 上軌片段 [f0, f1)（frame，exclusive）+ 機位檔名；必須無縫蓋滿 [0, total)。

    相鄰 shot 共用同一個秒數邊界，四捨五入後自然接合；捨入成 0 格的 shot 直接跳過
    （前後片段仍接合）。
    """
    clips: list[dict] = []
    for sh in shots:
        f0 = round(sh["s"] * fps)
        f1 = min(round(sh["e"] * fps), total_frames)
        if f1 <= f0:
            continue
        cam = wide_cam if sh.get("cam") == "wide" else cams[str(sh["spk"])]
        clips.append({"f0": f0, "f1": f1, "cam": cam, "kind": sh.get("kind", "talk")})
    cursor = 0
    for clip in clips:
        if clip["f0"] != cursor:
            raise SystemExit(
                f"shot list 在 frame {cursor} 有空隙／重疊（下一段從 {clip['f0']} 開始）"
            )
        cursor = clip["f1"]
    if cursor != total_frames:
        raise SystemExit(f"shot list 只蓋到 frame {cursor}，節目長 {total_frames}")
    return clips


def summarize(clips: list[dict], fps: float) -> dict:
    kinds: dict[str, int] = {}
    used: dict[str, int] = {}
    for clip in clips:
        kinds[clip["kind"]] = kinds.get(clip["kind"], 0) + 1
        used[clip["cam"]] = used.get(clip["cam"], 0) + 1
    lens = sorted((c["f1"] - c["f0"]) / fps for c in clips)
    return {
        "clips": len(clips),
        "kinds": kinds,
        "cams": used,
        "shot_sec_min": round(lens[0], 2),
        "shot_sec_median": round(lens[len(lens) // 2], 2),
        "shot_sec_max": round(lens[-1], 2),
    }


# --- 素材：mic、機位、偏移 -------------------------------------------------------


def _subdir(episode: Path, name: str) -> Path:
    found = next((d for d in episode.iterdir() if d.is_dir() and d.name.lower() == name), None)
    if found is None:
        raise SystemExit(f"{episode} 底下找不到 {name}/ 資料夾")
    return found


def camera_paths(episode: Path, cams: dict[str, str], wide_cam: str) -> dict[str, Path]:
    video = _subdir(episode, "video")
    paths = {name: video / name for name in (cams["0"], cams["1"], wide_cam)}
    if len(paths) != 3:
        raise SystemExit("--cams 與 --wide-cam 必須是三台不同機位")
    missing = [str(p) for p in paths.values() if not p.is_file()]
    if missing:
        raise SystemExit(f"找不到機位檔：{missing}")
    return paths


def check_alignment(
    program: Path, cams: dict[str, Path], fps: float, measure=_measure_offset
) -> dict:
    """三機對 program feed 必須逐格對齊——shot 的來源 frame 直接用節目時鐘換算。"""
    offsets: dict[str, float] = {}
    for name, path in cams.items():
        off = measure(program, path)
        if off is None:
            raise SystemExit(f"{name} 對 program feed 的偏移量測失敗——不能假設對齊，停")
        if abs(off) > 1.0 / fps:
            raise SystemExit(
                f"{name} 對 program feed 偏移 {off:+.4f}s（> 1 frame）——這一版只處理三機與"
                "program feed 逐格對齊的集數，偏移補償未實作，停"
            )
        offsets[name] = off
    return offsets


def speaker_tokens(episode: Path) -> tuple[list[dict], list[int | None], list[Path]]:
    tokens = load_speaker_timing_tokens(episode)
    if not tokens:
        raise SystemExit("speaker timing token 是空的")
    mics = detect_mic_tracks(_subdir(episode, "audio"))
    if len(mics) != 2:
        raise SystemExit(f"Audio/ 必須偵測到剛好 2 軌人聲 mic，實際：{[m.name for m in mics]}")
    reference = episode / "normalized.wav"
    envelopes = load_envelopes(mics, reference=reference if reference.is_file() else None)
    return tokens, assign_word_speakers(tokens, envelopes), mics


# --- Resolve -----------------------------------------------------------------------


def _track(timeline, kind: str) -> list:
    return timeline.GetItemListInTrack(kind, 1) or []


def _a1_sig(timeline) -> list:
    return [(x.GetName(), x.GetStart(), x.GetEnd()) for x in _track(timeline, "audio")]


def _st_sig(timeline) -> list:
    return [(x.GetStart(), x.GetEnd(), x.GetName()) for x in _track(timeline, "subtitle")]


def _v1_sig(timeline) -> list:
    return [
        (x.GetName(), x.GetStart(), x.GetEnd(), x.GetLeftOffset())
        for x in _track(timeline, "video")
    ]


def preflight(project, *, project_name: str, source: str, new: str, program_name: str, fps: float):
    """動 Resolve 之前的全部拒絕條件；回傳 (原 timeline, 節目總 frame)。"""
    if project is None or project.GetName() != project_name:
        current = project.GetName() if project is not None else None
        raise SystemExit(f"Resolve 目前開的專案是「{current}」，不是「{project_name}」")
    timelines = {}
    for i in range(1, project.GetTimelineCount() + 1):
        tl = project.GetTimelineByIndex(i)
        timelines[tl.GetName()] = tl
    if new in timelines:
        raise SystemExit(f"「{new}」已存在——不覆寫既有 timeline，換一個 --new-timeline 名稱")
    src = timelines.get(source)
    if src is None:
        raise SystemExit(f"找不到原 timeline「{source}」")
    tl_fps = float(src.GetSetting("timelineFrameRate") or 0)
    if abs(tl_fps - fps) > 1e-3:
        raise SystemExit(f"timeline frame rate {tl_fps:g} 與 program feed {fps:g} 不同")
    v1 = _track(src, "video")
    tl0 = src.GetStartFrame()
    single = (
        len(v1) == 1
        and v1[0].GetName() == program_name
        and v1[0].GetStart() == tl0
        and v1[0].GetLeftOffset() == 0
        and not any(
            src.GetItemListInTrack("video", i)
            for i in range(2, int(src.GetTrackCount("video") or 1) + 1)
        )
    )
    if not single:
        raise SystemExit(
            f"原 timeline「{source}」不是單機直切（V1 = 一整段 {program_name}）：{_v1_sig(src)}。"
            "已剪過／已是三機的 timeline 要先依 SKILL.md 的 source 連續性 coalesce 規則"
            "推導真實段落，這一版不處理"
        )
    return src, v1[0].GetEnd() - tl0


def materialize(
    project,
    src,
    *,
    new: str,
    clips: list[dict],
    camera_files: dict[str, Path],
    wide_cam: str,
    wide_zoom: float,
    fps: float,
    save,
) -> dict:
    """複製原 timeline → 清 V1 → 逐 clip 上軌 → 全景 zoom → 驗證 → save()。"""
    before_src = (_v1_sig(src), _a1_sig(src), _st_sig(src))

    media_pool = project.GetMediaPool()
    pool = list(_media_pool_items(media_pool.GetRootFolder()))
    items = {}
    for name, path in camera_files.items():
        item = _find_media_item_by_path(pool, path)
        if item is None:
            raise SystemExit(f"media pool 找不到 {path}（先跑 resolve-project 匯入 Cameras）")
        items[name] = item
    for clip in clips:
        _validate_media_source_range(items[clip["cam"]], clip["f0"], clip["f1"], project_fps=fps)

    project.SetCurrentTimeline(src)
    tl = src.DuplicateTimeline(new)
    if tl is None:
        raise SystemExit("DuplicateTimeline 失敗")
    if not project.SetCurrentTimeline(tl):
        raise SystemExit(f"SetCurrentTimeline「{new}」失敗")
    if _a1_sig(tl) != before_src[1] or _st_sig(tl) != before_src[2]:
        raise SystemExit("複製出來的 timeline A1／字幕跟原 timeline 不同——不動 V1，停")

    delete_checked(project, tl, _track(tl, "video"), "清空 V1")
    if _track(tl, "video"):
        raise SystemExit("V1 清空後仍有 clip")

    tl0 = tl.GetStartFrame()
    for index, clip in enumerate(clips, 1):
        f0, f1, name = clip["f0"], clip["f1"], clip["cam"]
        appended = append_checked(
            media_pool,
            [
                {
                    "mediaPoolItem": items[name],
                    "mediaType": 1,
                    "trackIndex": 1,
                    "startFrame": f0,
                    "endFrame": f1,
                    "recordFrame": tl0 + f0,
                }
            ],
            f"shot {index}/{len(clips)} {name} {f0}-{f1}",
        )
        _validate_appended_source_range(appended[0], f0, f1)
        if index % 100 == 0:
            print(f"  ... {index}/{len(clips)}")

    zoomed = 0
    for item in _track(tl, "video"):
        if item.GetName() == wide_cam:
            item.SetProperty("ZoomX", wide_zoom)
            item.SetProperty("ZoomY", wide_zoom)
            zoomed += 1

    report = verify(tl, src, before_src, clips=clips, wide_cam=wide_cam, wide_zoom=wide_zoom)
    report["wide_zoomed"] = zoomed
    if not save():
        raise SystemExit("SaveProject 失敗")
    return report


def verify(tl, src, before_src, *, clips: list[dict], wide_cam: str, wide_zoom: float) -> dict:
    """重新問 timeline，任何一項不符就 fail（不存檔）。"""
    v1 = _track(tl, "video")
    tl0 = tl.GetStartFrame()
    problems: list[str] = []
    holes = 0
    cursor = tl0
    for item in v1:
        if item.GetStart() != cursor:
            holes += 1
        cursor = item.GetEnd()
    if holes:
        problems.append(f"V1 空隙／重疊 {holes} 處")
    expected_end = tl0 + clips[-1]["f1"]
    if cursor != expected_end:
        problems.append(f"V1 結束在 {cursor}，應為 {expected_end}")
    if [x.GetName() for x in v1] != [c["cam"] for c in clips]:
        problems.append(f"V1 機位順序與 shot list 不符（{len(v1)} vs {len(clips)} clip）")
    bad_zoom = [
        x.GetStart()
        for x in v1
        if x.GetName() == wide_cam
        and not all(
            abs(float(x.GetProperty(k) or 0) - wide_zoom) <= 1e-6 for k in ("ZoomX", "ZoomY")
        )
    ]
    if bad_zoom:
        problems.append(
            f"全景 zoom 不是 {wide_zoom} 的 clip {len(bad_zoom)} 個（起點 {bad_zoom[:5]}）"
        )
    if _a1_sig(tl) != before_src[1]:
        problems.append("A1 跟原 timeline 不同")
    if _st_sig(tl) != before_src[2]:
        problems.append("字幕跟原 timeline 不同")
    if (_v1_sig(src), _a1_sig(src), _st_sig(src)) != before_src:
        problems.append("原 timeline 被改動了")
    if problems:
        raise SystemExit("驗證失敗（未存檔）：" + "；".join(problems))
    return {
        "v1_clips": len(v1),
        "v1_holes": 0,
        "a1_items": len(before_src[1]),
        "subtitle_items": len(before_src[2]),
        "wide_clips": sum(1 for x in v1 if x.GetName() == wide_cam),
    }


# --- CLI ---------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="完整節目三機切換（multicam-director）")
    p.add_argument("episode", type=Path, help="episode 資料夾（如 G:\\Footages\\20260903 程世嘉）")
    p.add_argument("--source-timeline", required=True, help="單機直切的原 timeline（不會被改動）")
    p.add_argument("--new-timeline", required=True, help="新 timeline 名稱（已存在就拒絕）")
    p.add_argument(
        "--wide-zoom",
        type=float,
        required=True,
        help="全景 ZoomX/ZoomY 倍率——每集量黑邊，修修可指定（蘇予昕 1.2、程世嘉 1.4）",
    )
    p.add_argument(
        "--cams",
        type=parse_cams,
        required=True,
        help=(
            'speaker → 機位檔名，如 "0=1_CAMERA 1.mp4,1=2_CAMERA 2.mp4"'
            "（0 = Audio/ 排序第一軌 mic）"
        ),
    )
    p.add_argument("--wide-cam", required=True, help='全景機位檔名，如 "3_CAMERA 3.mp4"')
    p.add_argument("--project", help="Resolve 專案名（預設 = episode 資料夾名）")
    p.add_argument("--dry-run", action="store_true", help="只算 shot list，不連 Resolve")
    return p


def run(args: argparse.Namespace, *, connect=None) -> dict:
    episode: Path = args.episode
    if not episode.is_dir():
        raise SystemExit(f"episode 資料夾不存在：{episode}")
    if args.wide_zoom < 1.0:
        raise SystemExit(f"--wide-zoom {args.wide_zoom} < 1.0 會露黑邊")
    cfg = dict(DEFAULT_CFG)
    program = find_main_video(episode, None)
    info = _probe(program)
    fps = info["fps"]
    cams = camera_paths(episode, args.cams, args.wide_cam)

    project = src = None
    if not args.dry_run:
        # 先擋拒絕條件，再跑耗時的能量分析
        if connect is None:
            from build_resolve_project import connect_resolve as connect
        resolve = connect()
        project = resolve.GetProjectManager().GetCurrentProject()
        src, total = preflight(
            project,
            project_name=args.project or episode.name,
            source=args.source_timeline,
            new=args.new_timeline,
            program_name=program.name,
            fps=fps,
        )
    else:
        total = round(info["duration"] * fps)
    print(f"program feed：{program.name}  {fps:g} fps  {total} frames（{total / fps / 60:.2f} 分）")

    offsets = check_alignment(program, cams, fps)
    print("機位偏移：" + "  ".join(f"{k} {v:+.4f}s" for k, v in offsets.items()))

    tokens, speakers, mics = speaker_tokens(episode)
    durations = [t["end"] - t["start"] for t in tokens]
    print(f"token：{len(tokens)}  中位長度 {median(durations):.3f}s")
    for spk in (0, 1):
        secs = sum(t["end"] - t["start"] for t, s in zip(tokens, speakers) if s == spk)
        print(f"  spk {spk}：mic {mics[spk].name} → {args.cams[str(spk)]}  說話 {secs / 60:.1f} 分")

    shots = build_full_shots(tokens, speakers, total / fps, cfg)
    clips = shot_frames(shots, fps, total, args.cams, args.wide_cam)
    summary = summarize(clips, fps)
    print(f"shot list：{summary}")
    for sh in shots:
        if sh.get("trigger"):
            print(f"  內容反應鏡頭 {sh['s']:.2f}-{sh['e']:.2f}s → spk {sh['spk']}")

    result = {"dry_run": args.dry_run, "fps": fps, "frames": total, "offsets": offsets, **summary}
    if args.dry_run:
        print("（dry run，沒有碰 Resolve）")
        return result

    pm = resolve.GetProjectManager()
    report = materialize(
        project,
        src,
        new=args.new_timeline,
        clips=clips,
        camera_files=cams,
        wide_cam=args.wide_cam,
        wide_zoom=args.wide_zoom,
        fps=fps,
        save=pm.SaveProject,
    )
    print(f"驗證通過：{report}；專案已存檔")
    return {**result, **report}


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    run(_parser().parse_args(argv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
