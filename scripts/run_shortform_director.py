"""shortform-director：短片專屬導播——原始機位 × Editorial Master conform map。

**這支只服務短片。** 長片走 `run_short_director.py`（16:9 滿幀、不裁切、
不切鏡），兩條線從這裡開始分家，不再共用同一個入口
（修修 2026-08-30 裁決：長短片流程與呼叫的東西完全獨立）。

## 為什麼可以回到原始機位

ADR-064 曾把「不得從原始機位重建」套到兩線，理由是原始素材裡還留著修修
在完整節目裡剪掉的東西。`shared/editorial_conform.py` 的 conform map 把
**同一組修剪投影到三機與音檔**之後，那個理由消失：被剪掉的段落在任何素材
上都拿不到（`removed_spans()` 就是證據清單）。

於是短片拿回它唯一需要的東西——**知道臉在哪裡**。固定機位的 `face_x` 座標
一集校一次全集通用；成片是切過的混合畫面，人物位置隨鏡頭跳動，永遠沒有
這個保證。

## 內容邊界仍然由 Editorial Master 決定

- 剪點、字幕、時間軸：全部是 Master 時鐘
- **聲音直接取 Master**（已核准的混音），不回頭用 normalized.wav
- 只有**畫面**換成機位，而且逐 shot 經過 conform map 換算

## 畫面語彙（鐘穎 Ep02 校準，2026-08-17）

開場 4 秒上下分割雙人 → 誰講話切誰的機位（<1s 附和不切鏡）→ 同人長 run
每 ~9s 插 1.8s 聽者反應鏡頭 → 內容驅動 punch（`<id>_zoom.json`）。

用法：
    py -3.10 scripts/run_shortform_director.py <episode> --id punch-S02
    py -3.10 scripts/run_shortform_director.py <episode> --id punch-S02 --stills <dir>
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# 幾何、shot 規劃與 Fusion punch 都是與畫面來源無關的機械件，沿用既有實作；
# ADR-067 分家時會把它們搬到中性命名的模組，不再掛在 run_short_* 底下。
from run_short_director import (  # noqa: E402
    _apply_punch_zooms,
    _configure_timeline,
    _find_media_item_by_path,
    _load_cfg,
    _media_item_path,
    _media_pool_items,
    _pan,
    _panel_props,
    _speaker_timing_tokens,
    _validate_appended_source_range,
    _validate_media_source_range,
    build_shots,
)
from run_short_tighten import (  # noqa: E402
    FORMAT_TIGHTEN,
    _assert_cut_master_lineage,
    _keep_segments,
    _open_editorial_master,
    _retime_srt,
    _verified_master_media_pool_item,
    import_srt_tidy,
)

from shared.editorial_conform import (  # noqa: E402
    load_conform_map,
    project_master_range,
    source_to_master_sec,
)
from shared.resolve_append import append_checked  # noqa: E402

logger = logging.getLogger("shortform_director")

TIGHTEN_DIR = Path("highlights") / "tighten"
CONFORM_PATH = Path("editorial-master") / "v1" / "conform-map.v1.json"

#: speaker index → conform map 的來源鍵。0=修修、1=來賓（固定機位配置）。
SPEAKER_SOURCE = {0: "cam1", 1: "cam2"}


def _master_word_speakers(
    episode_dir: Path, cmap: dict, t0: float, t1: float
) -> list[tuple[float, float, int]]:
    """詞級說話者，**投影到成片時間軸**。

    memo／WhisperX 的 token 在原始音檔時鐘上；落在被剪掉區間的 token
    直接丟棄——那些話在成片裡根本不存在，留著會讓切鏡對不上嘴。
    """
    from shared.speaker_assign import assign_word_speakers, detect_mic_tracks, load_envelopes

    tokens = _speaker_timing_tokens(episode_dir)
    mics = detect_mic_tracks(episode_dir / "Audio")
    if len(mics) < 2:
        raise SystemExit("Audio/ 找不到兩軌人聲——沒有分軌就判不出說話者，短片切鏡無法進行")
    envs = load_envelopes(mics, reference=episode_dir / "normalized.wav")
    speakers = assign_word_speakers(tokens, envs)

    out: list[tuple[float, float, int]] = []
    dropped = 0
    last = 0
    for token, spk in zip(tokens, speakers):
        if spk is not None:
            last = spk
        start = source_to_master_sec(cmap, float(token["start"]), source_key="audio")
        end = source_to_master_sec(cmap, float(token["end"]), source_key="audio")
        if start is None or end is None or end <= start:
            dropped += 1
            continue
        if t0 <= start < t1:
            out.append((start, end, spk if spk is not None else last))
    if dropped:
        logger.info("%d 個 token 落在被剪掉的區間，已丟棄", dropped)
    if not out:
        raise SystemExit(f"{t0:.1f}-{t1:.1f}s 內沒有任何說話者 token——無法導播")
    return out


def _load_short_winner(episode_dir: Path, cid: str, lineage: dict) -> tuple[dict, dict]:
    """短片讀 **winners.short.json**——長片的 winners.json 一個字都不碰。

    長短片共用一份 winners.json 的舊做法，寫短片就會洗掉長片那筆
    （2026-08-30 實際發生過，長片的 packaging-plan 與 winners 一度互相矛盾）。
    per-format 檔名是分家的第一步。
    """
    hdir = episode_dir / "highlights"
    winners_path = hdir / "winners.short.json"
    if not winners_path.is_file():
        raise SystemExit(
            f"找不到 {winners_path}——短片的當選名單獨立成檔，"
            "跑 run_cut_shortlist.py --format short --pick 之後改名或另存為它"
        )
    candidates_doc = json.loads((hdir / "candidates.json").read_text(encoding="utf-8"))
    winners_doc = json.loads(winners_path.read_text(encoding="utf-8"))
    candidate = next((x for x in candidates_doc["candidates"] if x["id"] == cid), None)
    winner = next((x for x in winners_doc["winners"] if x["id"] == cid), None)
    if candidate is None or winner is None:
        raise SystemExit(f"{cid} 不在 winners.short.json / candidates.json 中")
    for name, doc in (("candidates.json", candidates_doc), (winners_path.name, winners_doc)):
        if doc.get("editorial_master_lineage") != lineage:
            raise SystemExit(f"{name} 的 Editorial Master lineage 與目前 Master 不符")
    return candidate, winner


def _camera_pieces(cmap: dict, spk: int, master_s: float, master_e: float) -> list[dict]:
    """shot 的成片區間 → 該說話者機位上的來源區間（可能跨修剪接縫而分段）。"""
    key = SPEAKER_SOURCE.get(int(spk))
    if key is None:
        raise SystemExit(f"speaker {spk} 沒有對應機位（短片只用 cam1/cam2）")
    return project_master_range(cmap, master_s, master_e, source_key=key)


def direct(
    episode_dir: Path, cid: str, stills_dir: Path | None = None, opener: bool = True
) -> dict:
    from build_resolve_project import _template_path_short, connect_resolve

    master = _open_editorial_master(episode_dir)
    cmap_path = episode_dir / CONFORM_PATH
    if not cmap_path.is_file():
        raise SystemExit(f"找不到 conform map（{cmap_path}）——先跑 scripts/build_conform_map.py")
    cmap = load_conform_map(cmap_path)
    if cmap.get("editorial_master_lineage", {}).get("content_hash") != master.identity().get(
        "content_hash"
    ):
        raise SystemExit("conform map 綁的不是目前這份 Editorial Master——重建 conform map")

    c, w = _load_short_winner(episode_dir, cid, master.identity())
    if c.get("format") != "short":
        raise SystemExit(f"{cid} 不是短片——長片走 run_short_director.py")
    t0, t1 = float(c["t_start"]), float(c["t_end"])
    cfg = _load_cfg(episode_dir, "short")

    cuts_path = episode_dir / TIGHTEN_DIR / f"{cid}_cuts.json"
    if not cuts_path.exists():
        raise SystemExit(f"{cuts_path} 不存在——先跑 run_short_tighten --detect + 複審")
    cuts_doc = json.loads(cuts_path.read_text(encoding="utf-8"))
    _assert_cut_master_lineage(cuts_doc, master.identity())
    cuts = cuts_doc["cuts"]
    if any(x.get("keep") is None for x in cuts):
        raise SystemExit("cuts.json 有未複審項（keep=null）——先複審")
    segs = _keep_segments(t0, t1, cuts, FORMAT_TIGHTEN["short"]["min_keep_seg"])

    words = _master_word_speakers(episode_dir, cmap, t0, t1)
    shots = build_shots(segs, words, cfg)

    opener_span: tuple[float, float] | None = None
    if opener and segs:
        o_end = min(segs[0][0] + cfg["opener_sec"], segs[0][1])
        if o_end - segs[0][0] >= 2.0:
            opener_span = (segs[0][0], o_end)
            trimmed: list[dict] = []
            for sh in shots:  # 開場那段畫面改由雙 panel 提供，從 shot list 裁掉
                if sh["e"] <= opener_span[1]:
                    continue
                trimmed.append({**sh, "s": max(sh["s"], opener_span[1])})
            shots = trimmed

    resolve = connect_resolve()
    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()
    if project is None or project.GetName() != episode_dir.name:
        project = pm.LoadProject(episode_dir.name)
    if project is None:
        raise SystemExit(f"project「{episode_dir.name}」不存在")
    fps = float(project.GetSetting("timelineFrameRate"))
    mp = project.GetMediaPool()
    root = mp.GetRootFolder()

    clips = list(_media_pool_items(root))
    cams_bin = next(
        (f for f in root.GetSubFolderList() if f.GetName() in {"Cams", "Cameras"}), None
    )

    def _cam(rel: str):
        """以完整路徑綁定機位——只比對檔名會綁到別支短片並讓 Resolve 靜默夾邊界。"""
        nonlocal cams_bin
        expected = episode_dir / rel
        clip = _find_media_item_by_path(clips, expected)
        if clip is None:
            if cams_bin is None:
                cams_bin = mp.AddSubFolder(root, "Cams")
            mp.SetCurrentFolder(cams_bin)
            imported = mp.ImportMedia([str(expected)]) or []
            mp.SetCurrentFolder(root)
            clip = _find_media_item_by_path(imported, expected)
            if clip is None:
                raise SystemExit(
                    f"機位匯入失敗：{expected}（實際 {[_media_item_path(i) for i in imported]}）"
                )
            clips.extend(imported)
        return clip

    cam_items = {spk: _cam(cmap["sources"][key]["path"]) for spk, key in SPEAKER_SOURCE.items()}
    master_item = _verified_master_media_pool_item(mp, root, master.media_path)

    label = f"短{w['rank']} - {c['title']}（緊·導播）"
    stale = [
        t
        for i in range(1, project.GetTimelineCount() + 1)
        if (t := project.GetTimelineByIndex(i)) and t.GetName() == label
    ]
    if stale:
        mp.DeleteTimelines(stale)

    hbin = next(
        (f for f in root.GetSubFolderList() if f.GetName() == "Highlights"), None
    ) or mp.AddSubFolder(root, "Highlights")
    mp.SetCurrentFolder(hbin)
    template = _template_path_short()
    tl = mp.ImportTimelineFromFile(str(template), {}) if template.exists() else None
    if tl:
        tl.SetName(label)
    else:
        logger.warning("字幕樣式模板不存在（%s）——timeline 將是無樣式", template)
        tl = mp.CreateEmptyTimeline(label)
    if tl is None:
        raise SystemExit(f"timeline 建立失敗：{label}")
    project.SetCurrentTimeline(tl)
    _configure_timeline(tl, fmt="short", fps=fps)
    if tl.GetTrackCount("subtitle") == 0:
        tl.AddTrack("subtitle")
    tl_start = tl.GetStartFrame()

    def _set_props(item, props: dict[str, float]) -> None:
        for key, value in props.items():
            item.SetProperty(key, value)

    def _append_cam(clip, src_s: float, src_e: float, extra: dict | None = None):
        f0, f1 = int(round(src_s * fps)), int(round(src_e * fps))
        if f1 <= f0:
            return None
        _validate_media_source_range(clip, f0, f1, project_fps=fps)
        spec = {"mediaPoolItem": clip, "mediaType": 1, "startFrame": f0, "endFrame": f1}
        if extra:
            spec.update(extra)
        append_checked(mp, [spec], f"{label}: cam {src_s:.1f}-{src_e:.1f}")
        track = (extra or {}).get("trackIndex", 1)
        item = (tl.GetItemListInTrack("video", track) or [])[-1]
        _validate_appended_source_range(item, f0, f1)
        return item

    # 開場上下分割：下半＝修修（track 1 先落），上半＝來賓（track 2 後補）
    if opener_span:
        for spk, top, track in ((0, False, 1), (1, True, 2)):
            pieces = _camera_pieces(cmap, spk, opener_span[0], opener_span[1])
            cursor = tl_start
            for piece in pieces:
                extra = {"trackIndex": track, "recordFrame": cursor} if track == 2 else None
                item = _append_cam(
                    cam_items[spk], piece["source_start_sec"], piece["source_end_sec"], extra
                )
                if item is not None:
                    _set_props(item, _panel_props(cfg, spk, top=top))
                cursor += int(round((piece["source_end_sec"] - piece["source_start_sec"]) * fps))

    appended: list[dict] = []
    tl_cursor = (opener_span[1] - opener_span[0]) if opener_span else 0.0
    for sh in shots:
        for piece in _camera_pieces(cmap, sh["spk"], sh["s"], sh["e"]):
            item = _append_cam(
                cam_items[sh["spk"]], piece["source_start_sec"], piece["source_end_sec"]
            )
            if item is None:
                continue
            _set_props(
                item,
                {
                    "ZoomX": sh["zoom"],
                    "ZoomY": sh["zoom"],
                    "Pan": _pan(cfg, sh["spk"], sh["zoom"]),
                },
            )
            span = piece["source_end_sec"] - piece["source_start_sec"]
            appended.append(
                {
                    "item": item,
                    "tl_s": tl_cursor,
                    "tl_e": tl_cursor + span,
                    "kind": sh.get("kind", "talk"),
                    "spk": sh["spk"],
                    "zoom": sh["zoom"],
                    # _grab_stills 用 s/e 算樣張落點；時間軸與來源 1:1，直接對應
                    "s": tl_cursor,
                    "e": tl_cursor + span,
                }
            )
            tl_cursor += span

    zoom_path = episode_dir / TIGHTEN_DIR / f"{cid}_zoom.json"
    n_punch = 0
    if zoom_path.exists():
        punches = json.loads(zoom_path.read_text(encoding="utf-8"))["punches"]
        n_punch = _apply_punch_zooms(appended, punches, fps, cfg)

    # 聲音取已核准的 Master 混音——只有畫面換機位，聲音一格都不動
    offset_frames = 0
    for seg_s, seg_e in segs:
        f0, f1 = int(round(seg_s * fps)), int(round(seg_e * fps))
        append_checked(
            mp,
            [
                {
                    "mediaPoolItem": master_item,
                    "mediaType": 2,
                    "trackIndex": 1,
                    "startFrame": f0,
                    "endFrame": f1,
                    "recordFrame": tl_start + offset_frames,
                }
            ],
            f"{label}: Master audio {seg_s:.1f}-{seg_e:.1f}",
        )
        offset_frames += f1 - f0

    # 短片的字是**獨立製作的動態字卡**（run_shortform_titles），不是 Resolve 的
    # burn-in 字幕軌。字卡企劃宣告 covers_full_transcript 時，底部字幕必須清掉，
    # 否則畫面會同時出現兩層字（修修 2026-08-30 指正）。
    titles_plan_path = episode_dir / TIGHTEN_DIR / f"{cid}_titles.json"
    covers_full_transcript = False
    if titles_plan_path.is_file():
        try:
            covers_full_transcript = bool(
                json.loads(titles_plan_path.read_text(encoding="utf-8")).get(
                    "covers_full_transcript", False
                )
            )
        except (OSError, UnicodeError, json.JSONDecodeError):
            covers_full_transcript = False

    n_cues = 0
    if covers_full_transcript:
        logger.info("%s: 字卡覆蓋全文——不上 Resolve 字幕軌，字全部走動態字卡", cid)
    else:
        mp.SetCurrentFolder(root)
        seg_srt, n_cues = _retime_srt(
            episode_dir,
            cid,
            segs,
            cuts,
            transcript=master.srt_path,
            source_media=master.media_path,
            allow_legacy_words=False,
            fine=True,
        )
        srt_items = import_srt_tidy(mp, root, seg_srt)
        if not (bool(mp.AppendToTimeline(srt_items)) if srt_items else False):
            raise SystemExit(f"{label}: 字幕上軌失敗")
    if not pm.SaveProject():
        raise SystemExit(f"{label}: Resolve SaveProject 失敗")

    result = {
        "status": "directed",
        "format": "short",
        "timeline": label,
        "source_mode": "conformed_cameras",
        "shots": len(appended),
        "cam_switches": sum(1 for a, b in zip(appended, appended[1:]) if a["spk"] != b["spk"]),
        "reaction_shots": sum(1 for a in appended if a["kind"] == "reaction"),
        "split_opener_sec": round(opener_span[1] - opener_span[0], 2) if opener_span else 0.0,
        "punch_ramps": n_punch,
        "burned_subtitles": not covers_full_transcript,
        "cues": n_cues,
        "duration_sec": round(tl_cursor, 2),
    }
    if stills_dir is not None:
        from run_short_director import _grab_stills

        opener_frames = int(round((opener_span[1] - opener_span[0]) * fps)) if opener_span else 0
        result["stills"] = _grab_stills(
            resolve, project, tl, appended, fps, Path(stills_dir), opener_frames
        )
    return result


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="短片導播：原始機位 × conform map")
    parser.add_argument("episode")
    parser.add_argument("--id", required=True, help="winner id（如 punch-S02）")
    parser.add_argument("--stills", help="物化後抓樣張到此資料夾")
    parser.add_argument("--no-opener", action="store_true", help="不做上下分割開場")
    args = parser.parse_args(argv)
    out = direct(
        Path(args.episode),
        args.id,
        Path(args.stills) if args.stills else None,
        opener=not args.no_opener,
    )
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
