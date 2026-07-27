"""short-bgm：背景音樂墊底 — 修修 2026-07-27 二十三輪裁決「試試看 B」。

方案 B = **極輕 ambient 墊底**：聽不出來、但感覺得到，不與對白爭注意力。

響度基準（實測，本集）：
- 對白 preview 整合響度 **−15.4 LUFS**
- BGM 目標 **−43 LUFS**（低 28 dB）——「幾乎聽不到只感覺得到」
- 這個差距下 **不需要 ducking**：墊底本來就在對白底下 28 dB，
  再壓只會消失。（若日後改方案 C 有存在感的墊底，才需要 ducking——
  屆時用對白區間 JSON 算音量關鍵影格。）

機制與紀律：
- **響度烘焙在檔案端**（loudnorm 到目標 LUFS + 頭尾 fade + 裁到 timeline
  長度），不靠 Resolve clip gain——重跑可重現（與 SFX 同一條紀律）
- 音樂比 timeline 短時**自動循環**（stream_loop）再裁；長時直接裁
- 走 audio **track 4**（1 對白 / 2 SFX / 3 環境 / 4 BGM）
- 冪等清場：track ≥4 上媒體路徑在 episode assets/bgm/ 底下的 item
- 參考片實測：鐘穎波旬集片尾能掉到 −35.8 dB → 該集**沒有連續 BGM**；
  我們是有意識地加，不是模仿

用法：
    python scripts/run_short_bgm.py <episode> --id punch-S1 [--track <name>]
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
from run_short_tighten import _load_winner  # noqa: E402

logger = logging.getLogger("short_bgm")

BGM_TRACK = 4
TARGET_LUFS = -43.0  # 對白 −15.4 LUFS − 28 dB（方案 B：感覺得到、聽不出來）
FADE_IN = 1.2
FADE_OUT = 2.0
DEFAULT_TRACK = "calming-minimal-ambient"


def _bake(src: Path, total: float, cache: Path, lufs: float) -> Path:
    """裁到片長 + 循環補足 + 頭尾 fade + loudnorm 到目標 LUFS。"""
    cache.mkdir(parents=True, exist_ok=True)
    key = hashlib.md5(f"{src}|{total}|{lufs}|{FADE_IN}|{FADE_OUT}".encode()).hexdigest()[:10]
    out = cache / f"bgm_{src.stem[:20]}_{key}.wav"
    if out.exists():
        return out
    af = (
        f"atrim=0:{total},asetpts=PTS-STARTPTS,"
        f"afade=t=in:st=0:d={FADE_IN},"
        f"afade=t=out:st={max(0.0, total - FADE_OUT):.3f}:d={FADE_OUT},"
        f"loudnorm=I={lufs}:TP=-6:LRA=7"
    )
    proc = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-stream_loop",
            "-1",
            "-i",
            str(src),  # 音樂短於片長時自動接續
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
        raise SystemExit(f"BGM 烘焙失敗: {(proc.stderr or '')[-300:]}")
    return out


def apply(episode_dir: Path, cid: str, track_name: str = DEFAULT_TRACK) -> dict:
    from build_resolve_project import connect_resolve

    c, w = _load_winner(episode_dir, cid)
    src = episode_dir / "assets" / "bgm" / f"{track_name}.wav"
    if not src.exists():
        raise SystemExit(f"{src} 不存在——先把 BGM 放進 episode assets/bgm/")

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
    tl_start = timeline.GetStartFrame()
    total = round((timeline.GetEndFrame() - tl_start) / fps, 3)

    baked = _bake(src, total, episode_dir / "assets" / "bgm" / "cache", TARGET_LUFS)

    # 冪等清場：track ≥4 上媒體在 assets/bgm/ 的 item
    bgm_prefix = str((episode_dir / "assets" / "bgm").resolve()).lower()
    for ti in range(BGM_TRACK, timeline.GetTrackCount("audio") + 1):
        stale = []
        for it in timeline.GetItemListInTrack("audio", ti) or []:
            try:
                mpi = it.GetMediaPoolItem()
                fp = (mpi.GetClipProperty("File Path") or "") if mpi else ""
            except (AttributeError, TypeError):
                continue
            if fp.lower().startswith(bgm_prefix):
                stale.append(it)
        if stale:
            timeline.DeleteClips(stale)

    bgm_bin = next(
        (f for f in root.GetSubFolderList() if f.GetName() == "BGM"), None
    ) or mp.AddSubFolder(root, "BGM")
    mp.SetCurrentFolder(bgm_bin)
    imported = mp.ImportMedia([str(baked)]) or []
    if not imported:
        raise SystemExit(f"匯入失敗: {baked}")
    while timeline.GetTrackCount("audio") < BGM_TRACK:
        timeline.AddTrack("audio", "stereo")
    ok = mp.AppendToTimeline(
        [
            {
                "mediaPoolItem": imported[0],
                "mediaType": 2,
                "trackIndex": BGM_TRACK,
                "recordFrame": tl_start,
            }
        ]
    )
    mp.SetCurrentFolder(root)
    if not ok:
        raise SystemExit(f"BGM 疊軌失敗（track {BGM_TRACK}）")
    pm.SaveProject()
    return {
        "status": "bgm",
        "timeline": label,
        "track": track_name,
        "sec": total,
        "target_lufs": TARGET_LUFS,
        "file": baked.name,
    }


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="短片 BGM 墊底（方案 B：極輕 ambient）")
    ap.add_argument("episode", help="episode 資料夾")
    ap.add_argument("--id", required=True, help="winner id（如 punch-S1）")
    ap.add_argument("--track", default=DEFAULT_TRACK, help="assets/bgm/<name>.wav")
    args = ap.parse_args(argv)
    print(json.dumps(apply(Path(args.episode), args.id, args.track), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
