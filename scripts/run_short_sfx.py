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
| B-roll 切出 | swish.wav | t0−0.05 | 1 |

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
import json
import logging
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
                cues.append({"t": float(z["t0"]), "sfx": "impact", "prio": 4, "ev": f"punch{i}"})
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
            else:  # video / photo 切出
                cues.append({"t": t0 - 0.05, "sfx": "swish", "prio": 1, "ev": ev})

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


def apply(episode_dir: Path, cid: str) -> dict:
    from build_resolve_project import connect_resolve

    c, w = _load_winner(episode_dir, cid)
    sfx_dir = episode_dir / "assets" / "sfx"
    cues = build_cues(episode_dir, cid)
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

    while timeline.GetTrackCount("audio") < SFX_TRACK:
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
