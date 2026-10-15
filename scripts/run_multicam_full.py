"""multicam-director：完整節目三機切換（timeline V1 → CAM1／CAM2／CAM3）。

意圖層在 `.claude/skills/multicam-director/SKILL.md`；本 script 是實現層，只呼叫既有程式：

  shared.speaker_timing.load_speaker_timing_tokens
      token（legacy words.json 或 ADR-063 Memo release）
  shared.speaker_assign
      mic 軌偵測、能量包絡、詞級說話者、機位偏移量測
  run_short_director.build_shots / inject_reaction_cuts
      shot list（節奏反應鏡頭 + 哈/哇內容反應鏡頭 + 插話切回）
  shared.resolve_append.append_checked / delete_checked
      Resolve 上軌與清軌的已知坑

20260901 蘇予昕、20260723 謝伯讓、20260903 程世嘉三集各寫過一次性 driver；本檔是
20260903 程世嘉那兩版（三機 462 shots、插話切回 57 個）的正式化。

流程：
  1. token → 說話者（mic 軌必須剛好 2 軌）
  2. 三機對 program feed 偏移量測（>1 frame 就 fail——shot 的來源 frame 直接用節目時鐘）
  3. build_shots → inject_reaction_cuts（哈/哇）→ 插話切回 → 開場 N 秒全景
  4. （非 --dry-run）讀原 timeline V1，用 source 連續性 coalesce 回剪輯段；複製原 timeline
     （帶 A1、字幕、其他軌）成新 timeline，清新 timeline 的 V1，逐段 × 逐 shot 上軌，全景套 zoom
  5. 驗證：V1 每個 clip 跟計畫一致、涵蓋範圍跟原 V1 相同、A1／字幕／其他軌前後一致、
     原 timeline 未動、全景 zoom 全部正確 → SaveProject

原 timeline 可以是單機直切，也可以是已剪過／已是三機的 timeline——V1 只要全部是 program
feed 或這三台機位就行；有其他素材（Intro/Outro 等）就 fail closed。原 timeline 從頭到尾
不動，它就是備份。

用法：
    python scripts/run_multicam_full.py "G:/Footages/20260903 程世嘉" \\
        --source-timeline "20260903 程世嘉" --new-timeline "20260903 程世嘉（三機）" \\
        --wide-zoom 1.4 --cams "0=1_CAMERA 1.mp4,1=2_CAMERA 2.mp4" \\
        --wide-cam "3_CAMERA 3.mp4" --dry-run
"""

from __future__ import annotations

import argparse
import bisect
import logging
import sys
from pathlib import Path
from statistics import median

import numpy as np

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
    FRAME_SEC,
    _measure_offset,
    assign_word_speakers,
    detect_mic_tracks,
    load_envelopes,
)
from shared.speaker_timing import load_speaker_timing_tokens  # noqa: E402

logger = logging.getLogger("multicam_full")

# 完整節目節奏（修修已核准的基準值，見 SKILL.md「節奏參數」）。從
# run_short_director.FORMAT_CFG["long"] 起跳再放慢——完整節目比精華長得多，
# 長片節奏會嚴重 over-edit。機位檔名與全景倍率每集由 CLI 指定，不在這裡。
DEFAULT_CFG = {
    "zoom_base": 1.0,
    "min_shot": 2.0,  # 短於此的說話者 run 不切鏡（併入前一 shot）
    "reaction_every": 20.0,  # 同人講超過這麼久，插一個聽者反應鏡頭
    "reaction_sec": 2.0,
    "reaction_style": "alternate",  # 聽者特寫／全景交替
    "opener_sec": 5.0,  # 開場（第一段來源起點）用全景
}

# 插話切回（修修 2026-10-15，20260903 程世嘉實測定案）。開場來賓講梗圖時修修插
# 「我也是阿伯」沒切回給他：(1) 跟來賓重疊，Memo 句級 token 整句判給來賓；
# (2) 就算判對也 < min_shot 被併掉。所以事件取兩個來源的聯集：
#   token：聽者被判定說了一句 token_min–token_max 秒的話
#   能量：聽者 mic > loud_db 且 ≥ 說話者 mic − near_db（重疊講話），hot frame 之間
#         停頓 ≤ event_bridge 視為同一段，長度 ≥ event_min，事件最長 event_cap
# event_min 0.5s → 114 個太碎、0.7s → 80、0.9s → 57 且修修指出的兩處都抓到。
INTERJECT_DEFAULTS = {
    "token_min": 0.5,
    "token_max": 2.5,
    "loud_db": -36.0,  # 說話時對方 mic 的 bleed 約 -45 ~ -60 dB
    "near_db": 3.0,
    "event_min": 0.9,
    "event_cap": 2.5,
    "event_bridge": 0.3,
    "merge_gap": 0.6,  # 同一人相鄰事件間隔 < 此值合併
}
# 插入沿用 inject_reaction_cuts 的切分邏輯，觸發字換成佔位字元「插」
INTERJECT_CUT_CFG = {
    "reaction_trigger_chars": "插",
    "reaction_trigger_min_sec": 1.5,
    "reaction_trigger_pad_lead": 0.15,
    "reaction_trigger_pad_lag": 0.45,
    "reaction_trigger_min_residual": 1.0,
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


def _talk_shot_at(shots: list[dict], starts: list[float], t: float) -> dict | None:
    """t 所在的 shot——只回 kind=="talk" 且沒有 cam 覆寫的，其他回 None。"""
    i = bisect.bisect_right(starts, t) - 1
    if i < 0 or not shots[i]["s"] <= t < shots[i]["e"]:
        return None
    sh = shots[i]
    return sh if sh.get("kind") == "talk" and not sh.get("cam") else None


def interjection_events(
    shots: list[dict],
    tokens: list[dict],
    speakers: list[int | None],
    envelopes: np.ndarray,
    p: dict,
) -> list[dict]:
    """聽者插話事件（token ∪ mic 能量），同一人相鄰事件合併；依 start 排序。"""
    starts = [sh["s"] for sh in shots]
    events: list[dict] = []
    for t, s in zip(tokens, speakers):
        if s is None:
            continue
        sh = _talk_shot_at(shots, starts, (t["start"] + t["end"]) / 2)
        if p["token_min"] <= t["end"] - t["start"] <= p["token_max"] and sh and sh["spk"] != s:
            events.append({"start": t["start"], "end": t["end"], "spk": s, "src": "token"})

    db = 20 * np.log10(envelopes + 1e-9)
    bridge = int(round(p["event_bridge"] / FRAME_SEC))
    for k in (0, 1):
        hot = (db[k] > p["loud_db"]) & (db[k] >= db[1 - k] - p["near_db"])
        runs: list[list[int]] = []
        for f in np.flatnonzero(hot).tolist():
            if runs and f - runs[-1][1] <= bridge + 1:
                runs[-1][1] = f
            else:
                runs.append([f, f])
        for a, b in runs:
            s0, s1 = a * FRAME_SEC, (b + 1) * FRAME_SEC
            if s1 - s0 < p["event_min"]:
                continue
            sh = _talk_shot_at(shots, starts, (s0 + s1) / 2)
            if sh and sh["spk"] != k:
                end = min(s1, s0 + p["event_cap"])
                events.append({"start": s0, "end": end, "spk": k, "src": "energy"})

    events.sort(key=lambda e: (e["spk"], e["start"]))
    merged: list[dict] = []
    for e in events:
        m = merged[-1] if merged else None
        if m and m["spk"] == e["spk"] and e["start"] - m["end"] < p["merge_gap"]:
            m["end"] = min(max(m["end"], e["end"]), m["start"] + p["event_cap"])
            if m["src"] != e["src"]:
                m["src"] = "token+energy"
        else:
            merged.append(dict(e))
    return sorted(merged, key=lambda e: e["start"])


def inject_interjections(shots: list[dict], events: list[dict], cfg: dict) -> list[dict]:
    """插話事件 → inject_reaction_cuts 切到插話者臉部特寫；新切出的鏡頭標 kind="interject"。"""
    triggers = [
        {"word": "插", "start": e["start"], "end": e["end"], "spk": e["spk"]} for e in events
    ]
    before = {id(sh) for sh in shots}
    out = inject_reaction_cuts(shots, triggers, {**cfg, **INTERJECT_CUT_CFG})
    for sh in out:
        if sh.get("trigger") and id(sh) not in before:
            sh["kind"] = "interject"
    return out


def apply_opener(shots: list[dict], start: float, sec: float, zoom: float) -> list[dict]:
    """[start, start+sec) 改成全景；其他時間的 shot 原樣保留（跨界的切掉重疊部分）。"""
    if sec <= 0 or not shots:
        return shots
    end = start + sec
    out: list[dict] = []
    for sh in shots:
        if sh["e"] <= start or sh["s"] >= end:
            out.append(sh)
            continue
        if sh["s"] < start:
            out.append({**sh, "e": start})
        if sh["e"] > end:
            out.append({**sh, "s": end})
    head = next((sh for sh in shots if sh["e"] > start), shots[-1])
    out.append(
        {"s": start, "e": end, "spk": head["spk"], "cam": "wide", "kind": "opener", "zoom": zoom}
    )
    return sorted(out, key=lambda sh: sh["s"])


def build_full_shots(
    tokens: list[dict],
    speakers: list[int | None],
    duration: float,
    cfg: dict,
    *,
    envelopes: np.ndarray | None = None,
    interject: dict | None = None,
    opener_start: float = 0.0,
) -> list[dict]:
    """token + 說話者 → 完整節目 shot list（節目時鐘秒數，[0, duration] 無縫）。

    interject 給參數 dict（INTERJECT_DEFAULTS 形狀）就做插話切回，None 就不做。
    """
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
    if interject is not None:
        if envelopes is None:
            raise SystemExit("插話切回需要 mic 能量包絡")
        events = interjection_events(shots, tokens, speakers, envelopes, interject)
        shots = inject_interjections(shots, events, cfg)
    return apply_opener(shots, opener_start, cfg["opener_sec"], cfg["zoom_base"])


def coalesce_segments(items: list, allowed: set[str]) -> list[dict]:
    """V1 item → 剪輯段 [{t0, t1, s0}]（timeline frame、program 時鐘來源 frame）。

    三機與 program feed 逐格對齊，所以任一機的 LeftOffset 就是節目時鐘。相鄰 item
    `t` 連續**且** `s` 連續才是同一段；`s` 跳號 = 真的剪過一刀，即使 `t` 是連續的。
    """
    others = sorted({x.GetName() for x in items if x.GetName() not in allowed})
    if others:
        raise SystemExit(
            f"V1 上有非機位／非 program feed 素材（Intro/Outro？），這版不處理：{others}"
        )
    segs: list[dict] = []
    for x in sorted(items, key=lambda x: x.GetStart()):
        t0, t1, s0 = x.GetStart(), x.GetEnd(), x.GetLeftOffset()
        prev = segs[-1] if segs else None
        if prev and prev["t1"] == t0 and prev["s0"] + (prev["t1"] - prev["t0"]) == s0:
            prev["t1"] = t1
        else:
            segs.append({"t0": t0, "t1": t1, "s0": s0})
    if not segs:
        raise SystemExit("原 timeline V1 是空的")
    return segs


def plan_clips(
    shots: list[dict],
    segs: list[dict],
    fps: float,
    cams: dict[str, str],
    wide_cam: str,
) -> list[dict]:
    """剪輯段 × shot → 上軌片段 {rec, f0, f1, cam, kind}；f0/f1 是來源 frame（exclusive）。

    每段內必須無縫蓋滿 [s0, s0 + 段長)。相鄰 shot 共用同一個秒數邊界，四捨五入後
    自然接合；捨入成 0 格的直接跳過（前後片段仍接合）。
    """
    clips: list[dict] = []
    for seg in segs:
        src0, src1 = seg["s0"], seg["s0"] + (seg["t1"] - seg["t0"])
        cursor = src0
        for sh in shots:
            f0 = max(round(sh["s"] * fps), src0)
            f1 = min(round(sh["e"] * fps), src1)
            if f1 <= f0:
                continue
            if f0 != cursor:
                raise SystemExit(f"shot list 在來源 frame {cursor} 有空隙／重疊（下一段從 {f0}）")
            cam = wide_cam if sh.get("cam") == "wide" else cams[str(sh["spk"])]
            kind = sh.get("kind", "talk")
            clips.append(
                {"rec": seg["t0"] + (f0 - src0), "f0": f0, "f1": f1, "cam": cam, "kind": kind}
            )
            cursor = f1
        if cursor != src1:
            raise SystemExit(f"shot list 只蓋到來源 frame {cursor}，剪輯段到 {src1}")
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


def speaker_tokens(episode: Path, expected_mics: list[str] | None = None):
    """→ (tokens, speakers, mic 路徑, envelopes)。mic 必須剛好 2 軌。

    speaker 0 = 偵測到的兩軌裡檔名排序第一軌——Audio/ 多一軌夠活躍的 wav（備援錄音機、
    第二位來賓）就可能擠掉其中一軌、讓說話者跟機位對調。給 expected_mics 就逐名核對。
    """
    tokens = load_speaker_timing_tokens(episode)
    if not tokens:
        raise SystemExit("speaker timing token 是空的")
    mics = detect_mic_tracks(_subdir(episode, "audio"))
    if len(mics) != 2:
        raise SystemExit(f"Audio/ 必須偵測到剛好 2 軌人聲 mic，實際：{[m.name for m in mics]}")
    names = [m.name for m in mics]
    if expected_mics is not None and names != expected_mics:
        raise SystemExit(f"偵測到的 mic {names} 不是 --mics 指定的 {expected_mics}")
    reference = episode / "normalized.wav"
    if not reference.is_file():
        raise SystemExit(f"找不到 {reference}——mic 偏移補償需要它（先跑 audio-prep）")
    envelopes = load_envelopes(mics, reference=reference)
    return tokens, assign_word_speakers(tokens, envelopes), mics, envelopes


# --- Resolve -----------------------------------------------------------------------


def _items(timeline, kind: str, index: int = 1) -> list:
    return timeline.GetItemListInTrack(kind, index) or []


def _clip_sig(items) -> list:
    return [(x.GetName(), x.GetStart(), x.GetEnd(), x.GetLeftOffset()) for x in items]


def _sub_sig(items) -> list:
    return [(x.GetStart(), x.GetEnd(), x.GetName()) for x in items]


def _tracks(timeline, kind: str) -> range:
    return range(1, int(timeline.GetTrackCount(kind) or 0) + 1)


def _snapshot(timeline) -> dict:
    """V1 以外、這次不該被碰的一切：所有音軌、所有字幕軌、V2 以上。"""
    return {
        "audio": [_clip_sig(_items(timeline, "audio", i)) for i in _tracks(timeline, "audio")],
        "subtitle": [
            _sub_sig(_items(timeline, "subtitle", i)) for i in _tracks(timeline, "subtitle")
        ],
        "video_upper": [
            _clip_sig(_items(timeline, "video", i)) for i in _tracks(timeline, "video") if i > 1
        ],
    }


def _coverage(items) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for x in sorted(items, key=lambda x: x.GetStart()):
        if out and out[-1][1] == x.GetStart():
            out[-1][1] = x.GetEnd()
        else:
            out.append([x.GetStart(), x.GetEnd()])
    return [tuple(c) for c in out]


def preflight(
    project,
    *,
    project_name: str,
    source: str,
    new: str,
    allowed: set[str],
    fps: float,
):
    """動 Resolve 之前的全部拒絕條件；回傳 (原 timeline, 剪輯段)。"""
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
    return src, coalesce_segments(_items(src, "video"), allowed)


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
    """複製原 timeline → 清新 timeline 的 V1 → 逐 clip 上軌 → 全景 zoom → 驗證 → save()。"""
    before_v1 = _clip_sig(_items(src, "video"))
    before_cov = _coverage(_items(src, "video"))
    before = _snapshot(src)

    media_pool = project.GetMediaPool()
    pool = list(_media_pool_items(media_pool.GetRootFolder()))
    items = {}
    for name, path in camera_files.items():
        item = _find_media_item_by_path(pool, path)
        if item is None:
            raise SystemExit(f"media pool 找不到 {path}（先跑 resolve-project 匯入 Cameras）")
        if item.GetName() != name:
            # zoom 與驗證都靠 timeline item 名稱認機位；改過名的 clip 會讓兩者都失準
            raise SystemExit(f"media pool 裡 {path} 的名稱是「{item.GetName()}」，不是「{name}」")
        items[name] = item
    for clip in clips:
        _validate_media_source_range(items[clip["cam"]], clip["f0"], clip["f1"], project_fps=fps)

    project.SetCurrentTimeline(src)
    tl = src.DuplicateTimeline(new)
    if tl is None:
        raise SystemExit("DuplicateTimeline 失敗")
    try:
        if not project.SetCurrentTimeline(tl):
            raise SystemExit(f"SetCurrentTimeline「{new}」失敗")
        if _snapshot(tl) != before or _clip_sig(_items(tl, "video")) != before_v1:
            raise SystemExit("複製出來的 timeline 跟原 timeline 不同——不動 V1，停")

        delete_checked(project, tl, _items(tl, "video"), "清空 V1")
        if _items(tl, "video"):
            raise SystemExit("V1 清空後仍有 clip")

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
                        "recordFrame": clip["rec"],
                    }
                ],
                f"shot {index}/{len(clips)} {name} src {f0}-{f1}",
            )
            _validate_appended_source_range(appended[0], f0, f1)
            if index % 100 == 0:
                print(f"  ... {index}/{len(clips)}")

        zoomed = 0
        for item in _items(tl, "video"):
            if item.GetName() == wide_cam:
                item.SetProperty("ZoomX", wide_zoom)
                item.SetProperty("ZoomY", wide_zoom)
                zoomed += 1

        report = verify(
            tl,
            src,
            clips=clips,
            before=before,
            before_v1=before_v1,
            before_cov=before_cov,
            wide_cam=wide_cam,
            wide_zoom=wide_zoom,
        )
        report["wide_zoomed"] = zoomed
    except SystemExit as exc:
        raise SystemExit(
            f"{exc}\n⚠ 新 timeline「{new}」已建立但沒完成（未存檔）——"
            "在 Resolve 刪掉它再重跑；原 timeline 沒動"
        ) from exc
    if not save():
        raise SystemExit("SaveProject 失敗")
    return report


def verify(
    tl,
    src,
    *,
    clips: list[dict],
    before: dict,
    before_v1: list,
    before_cov: list,
    wide_cam: str,
    wide_zoom: float,
) -> dict:
    """重新問 timeline，任何一項不符就 fail（不存檔）。"""
    v1 = sorted(_items(tl, "video"), key=lambda x: x.GetStart())
    problems: list[str] = []
    planned = [(c["cam"], c["rec"], c["rec"] + c["f1"] - c["f0"], c["f0"]) for c in clips]
    actual = _clip_sig(v1)
    if actual != planned:
        diff = next(
            (i for i, (a, b) in enumerate(zip(actual, planned)) if a != b),
            min(len(actual), len(planned)),
        )
        problems.append(
            f"V1 跟計畫不符（{len(actual)} vs {len(planned)} clip，第 {diff} 個起不同）"
        )
    cov = _coverage(v1)
    if cov != before_cov:
        problems.append(f"V1 涵蓋範圍跟原 timeline 不同（{len(cov)} vs {len(before_cov)} 段）")
    bad_zoom = [
        x.GetStart()
        for x in v1
        if x.GetName() == wide_cam
        and any(abs(float(x.GetProperty(k) or 0) - wide_zoom) > 1e-6 for k in ("ZoomX", "ZoomY"))
    ]
    if bad_zoom:
        problems.append(f"全景 zoom 不是 {wide_zoom} 的 clip {len(bad_zoom)} 個：{bad_zoom[:5]}")
    if _snapshot(tl) != before:
        problems.append("A1／字幕／V2 以上跟原 timeline 不同")
    if _snapshot(src) != before or _clip_sig(_items(src, "video")) != before_v1:
        problems.append("原 timeline 被改動了")
    if problems:
        raise SystemExit("驗證失敗（未存檔）：" + "；".join(problems))
    return {
        "v1_clips": len(v1),
        "v1_segments": len(cov),
        "audio_items": sum(len(t) for t in before["audio"]),
        "subtitle_items": sum(len(t) for t in before["subtitle"]),
        "wide_clips": sum(1 for x in v1 if x.GetName() == wide_cam),
    }


# --- CLI ---------------------------------------------------------------------------

_INTERJECT_HELP = {
    "token_min": "token 事件：聽者一句話最短秒數",
    "token_max": "token 事件：聽者一句話最長秒數",
    "loud_db": "能量事件：聽者 mic 要大於此 dB",
    "near_db": "能量事件：聽者 mic ≥ 說話者 mic − 此 dB",
    "event_min": "能量事件最短秒數",
    "event_cap": "事件最長秒數",
    "event_bridge": "能量事件：hot frame 之間停頓 ≤ 此秒數視為同一段",
    "merge_gap": "同一人相鄰事件間隔 < 此秒數合併",
}


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="完整節目三機切換（multicam-director）")
    p.add_argument("episode", type=Path, help="episode 資料夾（如 G:/Footages/20260903 程世嘉）")
    p.add_argument("--source-timeline", required=True, help="原 timeline（不會被改動，即備份）")
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
    p.add_argument(
        "--mics",
        type=lambda text: [x.strip() for x in text.split(",")],
        help='核對偵測到的兩軌 mic（依序 = speaker 0, 1），如 "1_COMBO-1.wav,2_COMBO-2.wav"',
    )
    p.add_argument("--project", help="Resolve 專案名（預設 = episode 資料夾名）")
    p.add_argument(
        "--interject",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="插話切回（預設開）",
    )
    for key, default in INTERJECT_DEFAULTS.items():
        p.add_argument(
            f"--interject-{key.replace('_', '-')}",
            dest=f"interject_{key}",
            type=float,
            default=default,
            help=f"{_INTERJECT_HELP[key]}（預設 {default:g}）",
        )
    p.add_argument("--dry-run", action="store_true", help="只算 shot list，不連 Resolve")
    return p


def run(args: argparse.Namespace, *, connect=None) -> dict:
    episode: Path = args.episode
    if not episode.is_dir():
        raise SystemExit(f"episode 資料夾不存在：{episode}")
    if args.wide_zoom < 1.0:
        raise SystemExit(f"--wide-zoom {args.wide_zoom} < 1.0 會露黑邊")
    cfg = dict(DEFAULT_CFG)
    interject = (
        {key: getattr(args, f"interject_{key}") for key in INTERJECT_DEFAULTS}
        if args.interject
        else None
    )
    program = find_main_video(episode, None)
    info = _probe(program)
    fps = info["fps"]
    program_frames = round(info["duration"] * fps)
    cams = camera_paths(episode, args.cams, args.wide_cam)

    project = src = resolve = None
    if not args.dry_run:
        # 先擋拒絕條件，再跑耗時的能量分析
        if connect is None:
            from build_resolve_project import connect_resolve as connect
        resolve = connect()
        project = resolve.GetProjectManager().GetCurrentProject()
        src, segs = preflight(
            project,
            project_name=args.project or episode.name,
            source=args.source_timeline,
            new=args.new_timeline,
            allowed={program.name, *cams},
            fps=fps,
        )
    else:
        # dry run 不讀 timeline：以完整節目一整段計算
        segs = [{"t0": 0, "t1": program_frames, "s0": 0}]
    total = max([program_frames] + [s["s0"] + s["t1"] - s["t0"] for s in segs])
    print(f"program feed：{program.name}  {fps:g} fps  {program_frames} frames")
    print(f"剪輯段：{len(segs)}  {[(s['t0'], s['t1'], s['s0']) for s in segs][:6]}")

    offsets = check_alignment(program, cams, fps)
    print("機位偏移：" + "  ".join(f"{k} {v:+.4f}s" for k, v in offsets.items()))

    tokens, speakers, mics, envelopes = speaker_tokens(episode, args.mics)
    durations = [t["end"] - t["start"] for t in tokens]
    print(f"token：{len(tokens)}  中位長度 {median(durations):.3f}s")
    for spk in (0, 1):
        secs = sum(t["end"] - t["start"] for t, s in zip(tokens, speakers) if s == spk)
        print(f"  spk {spk}：mic {mics[spk].name} → {args.cams[str(spk)]}  說話 {secs / 60:.1f} 分")

    shots = build_full_shots(
        tokens,
        speakers,
        total / fps,
        cfg,
        envelopes=envelopes,
        interject=interject,
        opener_start=segs[0]["s0"] / fps,
    )
    clips = plan_clips(shots, segs, fps, args.cams, args.wide_cam)
    summary = summarize(clips, fps)
    print(f"shot list：{summary}")
    cuts = [sh for sh in shots if sh.get("kind") == "interject"]
    if interject is not None:
        per = {spk: sum(1 for sh in cuts if sh["spk"] == spk) for spk in (0, 1)}
        print(f"插話切回 {len(cuts)}（spk 0：{per[0]}／spk 1：{per[1]}）")
    for sh in shots:
        if sh.get("trigger") and sh.get("kind") != "interject":
            print(f"  哈/哇反應鏡頭 {sh['s']:.2f}-{sh['e']:.2f}s → spk {sh['spk']}")

    result = {
        "dry_run": args.dry_run,
        "fps": fps,
        "frames": program_frames,
        "segments": len(segs),
        "offsets": offsets,
        "interject_cuts": len(cuts),
        **summary,
    }
    if args.dry_run:
        print("（dry run，沒有碰 Resolve）")
        return result

    report = materialize(
        project,
        src,
        new=args.new_timeline,
        clips=clips,
        camera_files=cams,
        wide_cam=args.wide_cam,
        wide_zoom=args.wide_zoom,
        fps=fps,
        save=resolve.GetProjectManager().SaveProject,
    )
    print(f"驗證通過：{report}；專案已存檔")
    return {**result, **report}


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    run(_parser().parse_args(argv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
