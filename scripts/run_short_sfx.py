"""short-sfx：短片音效層 — 修修 2026-07-27 二十輪裁決（SFX 先做、BGM 隨後）。

音效語彙（事件時間戳全在 JSON，落點決定性、零人工）：

| 事件 | 音效 | 落點 | 優先級 |
|---|---|---|---|
| tier1 hero 卡 | ding.wav（亮鈴「叮」） | t0 | 5 |
| ramp punch | riser.wav（swoosh） | t0−0.35（響區蓋住 t0→t0+0.25 放大） | 4 |
| cut punch | impact.wav（低沉「咚」） | t0 | 4 |
| 貼紙 | pop.wav ×2（左右錯拍） | t0、t0+0.18 | 3 |
| 概念卡 | pop.wav | t0 | 3 |
| tier2 卡 | swish.wav（輕掃） | t0 | 2 |

B-roll 切出**不配音效**（二十二輪拿掉）——畫面切換自帶訊號，機械式每切
必響只是噪音；要聲音走 sound.json 的 ambient（跟素材語意走）。

防吵紀律：間距 <1.2s 只留優先級高的（同事件的雙 pop 豁免）。響度在
素材端已烘焙（assets/sfx/*.wav：ding I=-18 / riser -19 / impact -17 /
pop -20 / swish -23，均 TP=-2、去頭部靜音、截尾 fade——不靠 Resolve 手調）。

軌道契約：audio track 1 = 訪談對白（絕不碰）、track 2 = SFX。冪等：
清 track ≥2 上媒體路徑在 assets/sfx/ 底下的 item。檔案預剪好長度，
Append 不帶 start/endFrame（整段落軌）。

用法：
    python scripts/run_short_sfx.py <episode> --id punch-S1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_highlight_cut import FORMAT_LABEL  # noqa: E402
from run_short_tighten import TIGHTEN_DIR, _load_winner  # noqa: E402

logger = logging.getLogger("short_sfx")

SFX_TRACK = 2
MIN_GAP = 1.2  # 秒——比這近的兩個音效只留優先級高的
AMBIENT_TRACK = 3  # 環境音獨立軌（襯底，與事件音效互不搶）
AMBIENT_GAIN_DB = -12.0
SEMANTIC_GAIN_DB = -6.0
AMBIENT_FADE = 0.15  # 尾端 fade，避免與 B-roll 同時切斷時爆音


def build_cues(episode_dir: Path, cid: str) -> list[dict]:
    """titles/zoom/broll JSON → SFX cue 表（含防吵 thinning）。"""
    td = episode_dir / TIGHTEN_DIR
    cues: list[dict] = []

    p = td / f"{cid}_titles.json"
    if p.exists():
        for i, t in enumerate(json.loads(p.read_text(encoding="utf-8"))["titles"]):
            tier = int(t.get("tier", 2))
            if tier == 1:
                cues.append({"t": float(t["t0"]), "sfx": "ding", "prio": 5, "ev": f"title{i}"})
            else:
                cues.append({"t": float(t["t0"]), "sfx": "swish", "prio": 2, "ev": f"title{i}"})

    p = td / f"{cid}_zoom.json"
    if p.exists():
        for i, z in enumerate(json.loads(p.read_text(encoding="utf-8"))["punches"]):
            if z.get("style", "ramp") == "cut":
                # 二十五輪修修裁決：impact（cinematic hit）配在論述型訪談太戲劇
                # ——「有一個很奇怪的音效」。硬切放大本身就是視覺重音，不配聲音
                continue
            else:
                # riser（檔長 1.1s，能量區 0.4–0.95s）提前 0.35s——響區蓋住
                # ramp 的 t0 → t0+0.25 放大過程
                cues.append(
                    {"t": float(z["t0"]) - 0.35, "sfx": "riser", "prio": 4, "ev": f"punch{i}"}
                )

    p = td / f"{cid}_broll.json"
    if p.exists():
        for i, it in enumerate(json.loads(p.read_text(encoding="utf-8"))["items"]):
            ev = f"broll{i}"
            t0 = float(it["t0"])
            if it["kind"] == "sticker":
                cues.append({"t": t0, "sfx": "pop", "prio": 3, "ev": ev})
                if len(it.get("stickers", [])) > 1:
                    cues.append({"t": t0 + 0.18, "sfx": "pop", "prio": 3, "ev": ev})
            elif it["kind"] == "concept":
                cues.append({"t": t0, "sfx": "pop", "prio": 3, "ev": ev})
            # video / photo 切出**不配音效**（修修二十二輪：「進 B-roll 前那個
            # 小音效不知道作用是什麼，可以拿掉」）——畫面切換本身就是訊號，
            # 額外的 swish 只是噪音。B-roll 要聲音就走 sound.json 的 ambient
            # （跟著素材語意，不是機械式每切必響）。

    cues = [c for c in cues if c["t"] >= 0.0]
    cues.sort(key=lambda c: (c["t"], -c["prio"]))

    # thinning：greedy——與上一個保留 cue 太近時留優先級高的（同事件豁免）
    kept: list[dict] = []
    for c in cues:
        if kept and c["ev"] != kept[-1]["ev"] and c["t"] - kept[-1]["t"] < MIN_GAP:
            if c["prio"] > kept[-1]["prio"]:
                kept[-1] = c
            continue
        kept.append(c)
    return kept


def _dur(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        errors="replace",
    ).stdout.strip()
    try:
        return round(float(out), 3)
    except ValueError:
        return 2.0


def _cut_to_span(
    src: Path, span: float, gain_db: float, cache: Path, fade: float, src_in: float = 0.0
) -> Path:
    """把音效裁成指定長度 + 增益 + 尾端 fade，快取到 episode。

    修修二十二輪：環境音「出來的時間必須跟 B-roll 切齊，結束也要一起結束」。
    素材通常比 B-roll 長（引擎音 9.5s vs 跑車 2.2s），**在檔案端裁好**再疊軌
    ——長度由檔案保證，不靠 Resolve 手拉、重跑也一致。
    """
    cache.mkdir(parents=True, exist_ok=True)
    key = hashlib.md5(f"{src}|{span}|{gain_db}|{fade}|{src_in}".encode()).hexdigest()[:10]
    out = cache / f"{src.stem[:24]}_{key}.wav"
    if out.exists():
        return out
    # src_in：跳過素材開頭靜音／無用段（WA WA WA 前 0.5s 是靜音）
    af = f"atrim={src_in}:{src_in + span},asetpts=PTS-STARTPTS,volume={gain_db}dB"
    if fade > 0 and span > fade * 2:
        af += f",afade=t=out:st={max(0.0, span - fade):.3f}:d={fade}"
    proc = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(src),
            "-af",
            af,
            "-ar",
            "48000",
            "-ac",
            "2",
            str(out),
        ],
        capture_output=True,
        text=True,
        errors="replace",
    )
    if proc.returncode != 0 or not out.exists():
        raise SystemExit(f"音效裁切失敗（{src.name}）: {(proc.stderr or '')[-200:]}")
    return out


def _resolve_lib_path(name: str) -> Path:
    """字典檔名 → 音效庫實際路徑（走 sfx_index；未建索引直接報錯）。"""
    from build_sfx_index import INDEX_PATH

    if not INDEX_PATH.exists():
        raise SystemExit("data/sfx_index.json 不存在——先跑 build_sfx_index.py")
    index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    for it in index["items"]:
        if it["name"].lower() == name.lower():
            return Path(it["path"])
    raise SystemExit(f"音效庫找不到「{name}」——確認 sfx-dictionary.yaml 的 file 欄")


def _verified_files() -> dict[str, dict]:
    """sfx-dictionary.yaml → {檔名: 條目}（只收 verified 且非「不用」）。"""
    from build_sfx_index import load_dictionary

    return {
        e["file"]: e for e in load_dictionary() if e.get("verified") and e.get("usage") != "不用"
    }


def build_layered(episode_dir: Path, cid: str) -> list[dict]:
    """<id>_sound.json → 語意層 + 環境層 job（環境層時間戳讀 broll.json）。

    ambient 只填 slug——t0/t1 由 broll.json 決定，**進出點與 B-roll 由結構
    保證切齊**（修修二十二輪要求），不靠人手填時間。
    """
    sound_path = episode_dir / TIGHTEN_DIR / f"{cid}_sound.json"
    if not sound_path.exists():
        return []
    spec = json.loads(sound_path.read_text(encoding="utf-8"))
    ok_files = _verified_files()
    cache = episode_dir / "assets" / "sfx" / "cache"
    jobs: list[dict] = []

    for s in spec.get("semantic", []):
        if s["file"] not in ok_files:
            raise SystemExit(
                f"「{s['file']}」不在字典或未 verified——寧缺勿猜，先走試聽包給修修確認"
            )
        if not s.get("why"):
            raise SystemExit(f"語意音效 @{s.get('t')} 缺 why——沒有理由的音效就是噪音")
        src = _resolve_lib_path(s["file"])
        span = float(s.get("sec") or _dur(src))
        path = _cut_to_span(
            src,
            span,
            float(s.get("gain_db", SEMANTIC_GAIN_DB)),
            cache,
            0.2,
            float(s.get("src_in", 0.0)),
        )
        jobs.append(
            {
                "t": float(s["t"]),
                "path": path,
                "track": SFX_TRACK,
                "kind": "semantic",
                "label": s["file"],
                "why": s["why"],
            }
        )

    broll_path = episode_dir / TIGHTEN_DIR / f"{cid}_broll.json"
    items = (
        {i["slug"]: i for i in json.loads(broll_path.read_text(encoding="utf-8"))["items"]}
        if broll_path.exists()
        else {}
    )
    for a in spec.get("ambient", []):
        if a["file"] not in ok_files:
            raise SystemExit(f"環境音「{a['file']}」不在字典或未 verified")
        item = items.get(a["slug"])
        if item is None:
            raise SystemExit(f"環境音對應的素材「{a['slug']}」不在 {cid}_broll.json")
        t0, t1 = float(item["t0"]), float(item["t1"])
        src = _resolve_lib_path(a["file"])
        path = _cut_to_span(
            src, round(t1 - t0, 3), float(a.get("gain_db", AMBIENT_GAIN_DB)), cache, AMBIENT_FADE
        )
        jobs.append(
            {
                "t": t0,
                "path": path,
                "track": AMBIENT_TRACK,
                "kind": "ambient",
                "label": a["file"],
                "why": f"{a['slug']} 素材環境音（{t0}–{t1}s 切齊）",
            }
        )
    return jobs


def apply(episode_dir: Path, cid: str) -> dict:
    from build_resolve_project import connect_resolve

    c, w = _load_winner(episode_dir, cid)
    sfx_dir = episode_dir / "assets" / "sfx"
    cues = build_cues(episode_dir, cid)
    layered = build_layered(episode_dir, cid)  # 語意層 + 環境層（<id>_sound.json）
    need = sorted({q["sfx"] for q in cues})
    for name in need:
        if not (sfx_dir / f"{name}.wav").exists():
            raise SystemExit(f"assets/sfx/{name}.wav 不存在——先準備音效素材（見 docstring 響度表）")

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

    label = f"{FORMAT_LABEL[c['format']]}{w['rank']} - {c['title']}（緊·導播）"
    timeline = None
    for i in range(1, project.GetTimelineCount() + 1):
        t = project.GetTimelineByIndex(i)
        if t and t.GetName() == label:
            timeline = t
            break
    if timeline is None:
        raise SystemExit(f"「{label}」不存在——先跑 run_short_director")
    project.SetCurrentTimeline(timeline)

    # 冪等清場：track ≥2 上媒體在 assets/sfx/ 的 item（track 1 對白絕不碰）
    sfx_prefix = str(sfx_dir.resolve()).lower()
    for ti in range(2, timeline.GetTrackCount("audio") + 1):
        stale = []
        for it in timeline.GetItemListInTrack("audio", ti) or []:
            try:
                mpi = it.GetMediaPoolItem()
                fp = (mpi.GetClipProperty("File Path") or "") if mpi else ""
            except (AttributeError, TypeError):
                continue
            if fp.lower().startswith(sfx_prefix):
                stale.append(it)
        if stale:
            timeline.DeleteClips(stale)

    sfx_bin = next(
        (f for f in root.GetSubFolderList() if f.GetName() == "SFX"), None
    ) or mp.AddSubFolder(root, "SFX")
    mp.SetCurrentFolder(sfx_bin)
    clips: dict[str, object] = {}
    existing = {cl.GetName(): cl for cl in (sfx_bin.GetClipList() or [])}
    for name in need:
        clip = existing.get(f"{name}.wav") or existing.get(name)
        if clip is None:
            imported = mp.ImportMedia([str(sfx_dir / f"{name}.wav")]) or []
            if not imported:
                raise SystemExit(f"匯入失敗: {name}.wav")
            clip = imported[0]
        clips[name] = clip
    mp.SetCurrentFolder(root)

    need_tracks = max([SFX_TRACK] + [j["track"] for j in layered])
    while timeline.GetTrackCount("audio") < need_tracks:
        timeline.AddTrack("audio", "stereo")
    tl_start = timeline.GetStartFrame()
    placed = []
    for q in cues:
        ok = mp.AppendToTimeline(
            [
                {
                    "mediaPoolItem": clips[q["sfx"]],
                    "mediaType": 2,
                    "trackIndex": SFX_TRACK,
                    "recordFrame": tl_start + int(q["t"] * fps),
                }
            ]
        )
        if not ok:
            logger.warning("SFX 疊軌失敗 @%.2fs（%s）——跳過", q["t"], q["sfx"])
            continue
        placed.append({"at": round(q["t"], 2), "sfx": q["sfx"], "ev": q["ev"]})

    # 語意層 / 環境層（檔案已裁到目標長度——進出點由檔案保證）
    mp.SetCurrentFolder(sfx_bin)
    for j in layered:
        imported = mp.ImportMedia([str(j["path"])]) or []
        if not imported:
            raise SystemExit(f"匯入失敗: {j['path']}")
        ok = mp.AppendToTimeline(
            [
                {
                    "mediaPoolItem": imported[0],
                    "mediaType": 2,
                    "trackIndex": j["track"],
                    "recordFrame": tl_start + int(j["t"] * fps),
                }
            ]
        )
        if not ok:
            raise SystemExit(f"{j['kind']} 疊軌失敗 @{j['t']}s（track {j['track']}）")
        placed.append({"at": round(j["t"], 2), "sfx": j["label"], "ev": j["kind"], "why": j["why"]})
    mp.SetCurrentFolder(root)
    pm.SaveProject()
    return {"status": "sfxed", "timeline": label, "cues": placed, "count": len(placed)}


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="短片音效層（事件驅動 SFX）")
    parser.add_argument("episode", help="episode 資料夾")
    parser.add_argument("--id", required=True, help="winner id（如 punch-S1）")
    args = parser.parse_args(argv)
    print(json.dumps(apply(Path(args.episode), args.id), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
