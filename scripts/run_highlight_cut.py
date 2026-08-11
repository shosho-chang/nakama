"""highlight-cut：訪談集精華選段 — 候選驗證 + Resolve 物化。

修修 2026-07-25（grill 凍結，見 docs/plans/2026-07-25-highlight-cut-plan.md）：
整集訪談切長片（8–12min 橫式）與短片（60–120s 直式）候選，persona 盲審選
各 top 3。本 script 承擔管線的機械段：

- `--validate`：候選邊界吸附 cue、長度帶檢查、同格式重疊去重（>50% 留強者）
- `--materialize`：當選段建獨立 timeline（長片 16:9 帶字幕樣式模板／短片
  1080×1920 直式）+ 全部候選在主 timeline 打 marker（當選紅/落選藍）。冪等：
  同名 timeline 先刪重建、marker 先清再打

開採與盲審由 highlight-cut skill 的 Cowork subagent 完成（零 API 錢），
本 script 不呼叫任何 LLM。

用法：
    python scripts/run_highlight_cut.py <episode> --validate
    python scripts/run_highlight_cut.py <episode> --materialize
    python scripts/run_highlight_cut.py <episode> --materialize --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from shared.resolve_append import append_checked  # noqa: E402
from shared.subtitle_finalize import finalize_cues  # noqa: E402

logger = logging.getLogger("highlight_cut")

HIGHLIGHTS_DIR = "highlights"
CANDIDATES_NAME = "candidates.json"
WINNERS_NAME = "winners.json"
SEG_SRT_DIR = "highlights/srt"
BIN_NAME = "Highlights"
# 長度帶（grill Q4b）：目標帶 miner 已把關，script 只擋容忍帶外 + 平台硬上限
BANDS = {"long": (6 * 60, 18 * 60, None), "short": (40, 180, 180)}
FORMAT_LABEL = {"long": "長", "short": "短"}
_TS = re.compile(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})")


def _parse_srt(path: Path) -> list[tuple[float, float, str]]:
    cues = []
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8").strip()):
        lines = [x for x in block.splitlines() if x.strip()]
        if len(lines) < 2:
            continue
        m = _TS.search(lines[1])
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        cues.append(
            (
                g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000,
                g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000,
                "\n".join(lines[2:]),
            )
        )
    return cues


def _ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _fmt_min(seconds: float) -> str:
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def validate(episode_dir: Path) -> dict:
    """候選正規化：吸附 cue 邊界、長度帶檢查、同格式重疊去重。原地改寫 candidates.json。"""
    cand_path = episode_dir / HIGHLIGHTS_DIR / CANDIDATES_NAME
    data = json.loads(cand_path.read_text(encoding="utf-8"))
    cues = _parse_srt(episode_dir / "transcript.srt")
    starts = [c[0] for c in cues]
    ends = [c[1] for c in cues]

    issues: list[str] = []
    for c in data["candidates"]:
        # 吸附：start → 不晚於原值的最近 cue 開頭；end → 不早於原值的最近 cue 結尾
        s0, e0 = float(c["t_start"]), float(c["t_end"])
        c["t_start"] = max((s for s in starts if s <= s0 + 0.5), default=s0)
        c["t_end"] = min((e for e in ends if e >= e0 - 0.5), default=e0)
        dur = c["t_end"] - c["t_start"]
        c["duration_sec"] = round(dur, 1)
        lo, hi, hard = BANDS[c["format"]]
        if hard and dur > hard:
            issues.append(f"{c['id']}: {_fmt_min(dur)} 超過平台硬上限 {hard}s — 必須修")
        elif not (lo <= dur <= hi):
            issues.append(f"{c['id']}: {_fmt_min(dur)} 落在容忍帶外（{lo}-{hi}s）")

    # 同格式重疊 >50% → **不淘汰**，標 variant 群組（修修 2026-07-26 裁決：
    # 去重在評分前用 rationale 長度決生死，害「數位排毒+睡眠運動」整塊從未
    # 被評分就消失。重疊候選是同素材的不同切法——全部進盲審，評分後同群組
    # 只取最高分者佔排名（規則在 SKILL.md Step 2）。
    groups = _variant_groups(data["candidates"])
    for c in data["candidates"]:
        c["variant_group"] = groups[c["id"]]
    data["candidates"].sort(key=lambda x: (x["format"], x["t_start"]))
    cand_path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    counts = {f: sum(1 for c in data["candidates"] if c["format"] == f) for f in ("long", "short")}
    n_groups = len(set(groups.values()))
    return {
        "status": "validated",
        "kept": counts,
        "variant_groups": n_groups,
        "band_issues": issues,
    }


def _variant_groups(candidates: list[dict]) -> dict[str, str]:
    """同格式、重疊 >50%（相對較短者）的候選歸同一 variant 群組（連通分量）。"""
    parent: dict[str, str] = {c["id"]: c["id"] for c in candidates}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(candidates):
        for b in candidates[i + 1 :]:
            if a["format"] != b["format"]:
                continue
            overlap = min(a["t_end"], b["t_end"]) - max(a["t_start"], b["t_start"])
            shorter = min(a["t_end"] - a["t_start"], b["t_end"] - b["t_start"])
            if overlap > 0.5 * shorter:
                parent[find(a["id"])] = find(b["id"])
    return {cid: find(cid) for cid in parent}


def _segment_srt(episode_dir: Path, cid: str, t_start: float, t_end: float) -> Path:
    """裁出段落字幕（時間平移到 0 起點），版本化路徑繞 Resolve 路徑快取。

    副本套修修 2026-08-05 字幕定版兩規則（句尾零標點 + cue 間 ≤3s 空隙補平）
    ——transcript.srt 本體不動。
    """
    cues = _parse_srt(episode_dir / "transcript.srt")
    out_dir = episode_dir / SEG_SRT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 1
    while (out_dir / f"{cid}_r{n:03d}.srt").exists():
        n += 1
    dst = out_dir / f"{cid}_r{n:03d}.srt"
    seg_cues = [
        (max(0.0, s - t_start), min(t_end, e) - t_start, text)
        for s, e, text in cues
        if e > t_start and s < t_end
    ]
    from shared.subtitle_reboundary import repair_cues

    seg_cues, rb = repair_cues(seg_cues)
    if rb["moved"]:
        logger.info(f"{cid}: 切點重修 {rb['moved']} 處（壞斷句搬到合法語意邊界）")
    seg_cues, stats = finalize_cues(seg_cues)
    if stats["true_silences"]:
        logger.info(f"{cid}: >3s 真靜默不補 {len(stats['true_silences'])} 處（字幕該消失）")
    for f in stats.get("bad_boundaries", [])[:5]:
        logger.warning(f"{cid} 斷句疑點 cue{f['cue']}: …{f['tail']}｜{f['head']}…（{f['reason']}）")
    blocks = (f"{i}\n{_ts(s)} --> {_ts(e)}\n{text}\n" for i, (s, e, text) in enumerate(seg_cues, 1))
    dst.write_text("\n".join(blocks), encoding="utf-8")
    return dst


def materialize(episode_dir: Path, *, dry_run: bool = False) -> dict:
    from build_resolve_project import _template_path, connect_resolve, find_main_video

    hdir = episode_dir / HIGHLIGHTS_DIR
    cands = json.loads((hdir / CANDIDATES_NAME).read_text(encoding="utf-8"))["candidates"]
    winners = json.loads((hdir / WINNERS_NAME).read_text(encoding="utf-8"))["winners"]
    by_id = {c["id"]: c for c in cands}
    missing = [w["id"] for w in winners if w["id"] not in by_id]
    if missing:
        raise SystemExit(f"winners 引用不存在的候選 id: {missing}（candidates.json 去重後失效？）")
    winners = sorted(winners, key=lambda x: (by_id[x["id"]]["format"], x.get("rank", 9)))
    win_ids = {w["id"] for w in winners}

    plan = []
    for w in winners:
        c = by_id[w["id"]]
        label = f"{FORMAT_LABEL[c['format']]}{w['rank']} - {c['title']}"
        plan.append(
            {
                "timeline": label,
                "format": c["format"],
                "range": f"{_fmt_min(c['t_start'])}–{_fmt_min(c['t_end'])}",
            }
        )
    if dry_run:
        return {"status": "dry-run", "timelines": plan, "markers": len(cands)}

    resolve = connect_resolve()
    pm = resolve.GetProjectManager()
    project_name = episode_dir.name
    project = pm.GetCurrentProject()
    if project is None or project.GetName() != project_name:
        project = pm.LoadProject(project_name)
    if project is None:
        raise SystemExit(f"project「{project_name}」不存在，先跑 resolve-project")
    fps = float(project.GetSetting("timelineFrameRate"))
    mp = project.GetMediaPool()
    root = mp.GetRootFolder()

    # 素材 item：主影片 + normalized 音檔（都已在 media pool，依名尋回）
    main_video = find_main_video(episode_dir, None)
    clips = {(c.GetName() or ""): c for c in (root.GetClipList() or [])}
    vid = clips.get(main_video.name)
    aud = clips.get("normalized.wav")
    if vid is None:
        raise SystemExit(f"media pool 找不到主影片 {main_video.name}")

    # 主 timeline：清舊 marker（紅/藍）→ 全候選重打
    main_tl = None
    for i in range(1, project.GetTimelineCount() + 1):
        tl = project.GetTimelineByIndex(i)
        if tl and tl.GetName() == project_name:
            main_tl = tl
            break
    if main_tl is None:
        raise SystemExit(f"主 timeline「{project_name}」不存在")
    for color in ("Red", "Blue"):
        main_tl.DeleteMarkersByColor(color)
    for c in cands:
        color = "Red" if c["id"] in win_ids else "Blue"
        frame = int(c["t_start"] * fps)
        dur = int((c["t_end"] - c["t_start"]) * fps)
        main_tl.AddMarker(frame, color, f"[{c['id']}] {c['title']}", c.get("hook", ""), dur)

    # Highlights bin（timeline 物件落在建立當下的 current folder）
    hbin = next(
        (f for f in root.GetSubFolderList() if f.GetName() == BIN_NAME), None
    ) or mp.AddSubFolder(root, BIN_NAME)

    # 冪等：刪同名舊 timeline
    existing_names = {p["timeline"] for p in plan}
    stale = [
        project.GetTimelineByIndex(i)
        for i in range(1, project.GetTimelineCount() + 1)
        if (t := project.GetTimelineByIndex(i)) and t.GetName() in existing_names
    ]
    if stale:
        mp.DeleteTimelines(stale)

    made = []
    template = _template_path()
    for w in winners:
        c = by_id[w["id"]]
        label = f"{FORMAT_LABEL[c['format']]}{w['rank']} - {c['title']}"
        f0, f1 = int(c["t_start"] * fps), int(c["t_end"] * fps)
        mp.SetCurrentFolder(hbin)
        # 長短片都從樣式模板長 timeline（短片再覆寫成直式解析度）——
        # 模板是本機檔（data/* gitignored），不存在時退回無樣式並大聲警告
        tl = None
        if template.exists():
            tl = mp.ImportTimelineFromFile(str(template), {})
            if tl:
                tl.SetName(label)
        else:
            logger.warning(
                f"字幕樣式模板不存在（{template}）——timeline 將是無樣式！"
                "從 E:\\nakama 跑本 script，或設 RESOLVE_SUBTITLE_TEMPLATE"
            )
        if tl is None:
            tl = mp.CreateEmptyTimeline(label)
        if tl is None:
            raise SystemExit(f"timeline 建立失敗: {label}")
        project.SetCurrentTimeline(tl)
        if c["format"] == "short":
            tl.SetSetting("useCustomSettings", "1")
            tl.SetSetting("timelineResolutionWidth", "1080")
            tl.SetSetting("timelineResolutionHeight", "1920")
        if tl.GetTrackCount("subtitle") == 0:
            tl.AddTrack("subtitle")
        # ⚠️ 走 append_checked：新建的 timeline 上第一次 append 常回 `[None]`
        # （truthy，`if not ok_v` 判不出來）——2026-08-05 安吉集三條 timeline
        # 全部 v1 空、卻回報 materialized。判 [None] + 重試才擋得住。
        append_checked(
            mp,
            [{"mediaPoolItem": vid, "mediaType": 1, "startFrame": f0, "endFrame": f1}],
            f"{label} 影片",
        )
        if aud is not None:
            append_checked(
                mp,
                [
                    {
                        "mediaPoolItem": aud,
                        "mediaType": 2,
                        "trackIndex": 1,
                        "startFrame": f0,
                        "endFrame": f1,
                        "recordFrame": tl.GetStartFrame(),
                    }
                ],
                f"{label} 音軌",
            )
        placed = len(tl.GetItemListInTrack("video", 1) or [])
        if placed < 1:
            raise SystemExit(f"{label}: 影片上軌後 v1 仍是空的")
        mp.SetCurrentFolder(root)
        seg_srt = _segment_srt(episode_dir, c["id"], c["t_start"], c["t_end"])
        srt_items = mp.ImportMedia([str(seg_srt)])
        sub_ok = bool(mp.AppendToTimeline(srt_items)) if srt_items else False
        made.append(
            {
                "timeline": label,
                "subtitles": sub_ok,
                "items": len(tl.GetItemListInTrack("subtitle", 1) or []),
            }
        )

    project.SetCurrentTimeline(main_tl)
    pm.SaveProject()
    return {"status": "materialized", "timelines": made, "markers": len(cands)}


def refresh_subs(episode_dir: Path) -> dict:
    """精華 timeline **只換字幕不動剪輯**（transcript.srt 更新後用）。

    修修可能已在精華 timeline 上剪輯——materialize 重建會毀掉他的工作，
    本模式只清字幕內容重上（軌與樣式保留，比照 build_resolve_project
    refresh_subtitles）。
    """
    from build_resolve_project import connect_resolve

    hdir = episode_dir / HIGHLIGHTS_DIR
    cands = json.loads((hdir / CANDIDATES_NAME).read_text(encoding="utf-8"))["candidates"]
    winners = json.loads((hdir / WINNERS_NAME).read_text(encoding="utf-8"))["winners"]
    by_id = {c["id"]: c for c in cands}

    resolve = connect_resolve()
    pm = resolve.GetProjectManager()
    project_name = episode_dir.name
    project = pm.GetCurrentProject()
    if project is None or project.GetName() != project_name:
        project = pm.LoadProject(project_name)
    if project is None:
        raise SystemExit(f"project「{project_name}」不存在")
    mp = project.GetMediaPool()
    root = mp.GetRootFolder()

    timelines = {}
    for i in range(1, project.GetTimelineCount() + 1):
        tl = project.GetTimelineByIndex(i)
        if tl:
            timelines[tl.GetName()] = tl

    done = []
    for w in winners:
        c = by_id.get(w["id"])
        if c is None:
            continue
        label = f"{FORMAT_LABEL[c['format']]}{w['rank']} - {c['title']}"
        tl = timelines.get(label)
        if tl is None:
            done.append({"timeline": label, "status": "not-found（跳過）"})
            continue
        project.SetCurrentTimeline(tl)
        for ti in range(1, tl.GetTrackCount("subtitle") + 1):
            items = tl.GetItemListInTrack("subtitle", ti) or []
            if items:
                tl.DeleteClips(items)
        stale = [
            clip
            for clip in (root.GetClipList() or [])
            if (clip.GetName() or "").startswith(f"{c['id']}_r")
        ]
        if stale:
            mp.DeleteClips(stale)
        mp.SetCurrentFolder(root)
        seg_srt = _segment_srt(episode_dir, c["id"], c["t_start"], c["t_end"])
        srt_items = mp.ImportMedia([str(seg_srt)])
        ok = bool(mp.AppendToTimeline(srt_items)) if srt_items else False
        done.append(
            {
                "timeline": label,
                "status": "refreshed" if ok else "append-failed",
                "items": len(tl.GetItemListInTrack("subtitle", 1) or []),
            }
        )
    pm.SaveProject()
    return {"status": "subs-refreshed", "timelines": done}


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="highlight-cut 候選驗證 + Resolve 物化")
    parser.add_argument("episode", help="episode 資料夾")
    parser.add_argument(
        "--validate", action="store_true", help="候選吸附/長度帶/去重（改寫 candidates.json）"
    )
    parser.add_argument(
        "--materialize", action="store_true", help="當選段建 timeline + 全候選打 marker"
    )
    parser.add_argument(
        "--refresh-subs",
        action="store_true",
        help="精華 timeline 只換字幕不動剪輯（transcript.srt 更新後用）",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    episode_dir = Path(args.episode)
    if not episode_dir.is_dir():
        logger.error(f"episode 資料夾不存在: {episode_dir}")
        return 1
    started = time.time()
    if args.validate:
        result = validate(episode_dir)
    elif args.materialize:
        result = materialize(episode_dir, dry_run=args.dry_run)
    elif args.refresh_subs:
        result = refresh_subs(episode_dir)
    else:
        logger.error("指定 --validate 或 --materialize")
        return 2
    result["elapsed_sec"] = round(time.time() - started, 1)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
