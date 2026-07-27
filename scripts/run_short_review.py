"""short-review：短片自檢 loop 素材包 — 修修 2026-07-27 十七輪裁決。

「剪完 export 低解析版 → 派 subagent 看片找問題 → 回饋 → 修 → loop」的
deterministic 前半段。本 script 產「review packet」，subagent 盲審由
session 主循環 dispatch（見 SKILL Step 10）。

產出（episode `highlights/review/<id>/`）：
- preview.mp4        低解析（540×960）快轉審片用
- contact_sheet.png  1fps 縮圖牆（全片節奏一眼掃）
- ev_XX_<slug>.png   每個視覺事件的抽幀（進場後 0.4s + 中點）
- events.json        事件清單（素材/字卡/punch 合併時間軸）+ 節拍器
                     缺口分析（>12s 無新視覺事件的區段）+ 對應 SRT 行

事件來源：<id>_broll.json + <id>_titles.json + <id>_zoom.json + 最新
<id>_tight_r*.srt。render 走 Resolve render queue（H.264 mp4），抽幀走
ffmpeg（低解析檔上抽，快）。

用法：
    python scripts/run_short_review.py <episode> --id punch-S4
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_highlight_cut import FORMAT_LABEL  # noqa: E402
from run_short_tighten import TIGHTEN_DIR, _load_winner  # noqa: E402

logger = logging.getLogger("short_review")

REVIEW_DIR = "highlights/review"
PREVIEW_W, PREVIEW_H = 540, 960
GAP_SEC = 12.0  # 節拍器：>12s 無新視覺事件 = 缺口（十六輪）


def _load_events(episode_dir: Path, cid: str) -> list[dict]:
    td = episode_dir / TIGHTEN_DIR
    events: list[dict] = []
    p = td / f"{cid}_broll.json"
    if p.exists():
        for it in json.loads(p.read_text(encoding="utf-8"))["items"]:
            events.append(
                {
                    "type": it["kind"],
                    "slug": it.get("slug", ""),
                    "t0": float(it["t0"]),
                    "t1": float(it["t1"]),
                    "note": it.get("note", ""),
                }
            )
    p = td / f"{cid}_titles.json"
    if p.exists():
        for it in json.loads(p.read_text(encoding="utf-8"))["titles"]:
            events.append(
                {
                    "type": f"card-tier{it.get('tier', 2)}",
                    "slug": it["text"].replace("\n", "/"),
                    "t0": float(it["t0"]),
                    "t1": float(it["t1"]),
                    "note": "",
                }
            )
    p = td / f"{cid}_zoom.json"
    if p.exists():
        for it in json.loads(p.read_text(encoding="utf-8"))["punches"]:
            events.append(
                {
                    "type": f"punch-{it.get('style', 'ramp')}",
                    "slug": "",
                    "t0": float(it["t0"]),
                    "t1": float(it["t1"]),
                    "note": it.get("note", ""),
                }
            )
    events.sort(key=lambda x: x["t0"])
    return events


def _load_srt_lines(episode_dir: Path, cid: str) -> list[dict]:
    srts = sorted((episode_dir / "highlights/srt").glob(f"{cid}_tight_r*.srt"))
    if not srts:
        return []
    lines = []
    for block in re.split(r"\n\s*\n", srts[-1].read_text(encoding="utf-8").strip()):
        ls = block.splitlines()
        if len(ls) >= 3:
            m = re.match(r"(\d+):(\d+):(\d+),(\d+) --> (\d+):(\d+):(\d+),(\d+)", ls[1])
            if m:
                s = int(m.group(2)) * 60 + int(m.group(3)) + int(m.group(4)) / 1000
                e = int(m.group(6)) * 60 + int(m.group(7)) + int(m.group(8)) / 1000
                lines.append({"t0": round(s, 2), "t1": round(e, 2), "text": ls[2]})
    return lines


def _render_preview(project, timeline, out_dir: Path, name: str) -> Path:
    """Resolve render queue 出低解析 H.264 preview。"""
    project.SetCurrentRenderFormatAndCodec("mp4", "H264")
    project.SetRenderSettings(
        {
            "MarkIn": timeline.GetStartFrame(),
            "MarkOut": timeline.GetEndFrame(),
            "TargetDir": str(out_dir),
            "CustomName": name,
            "FormatWidth": PREVIEW_W,
            "FormatHeight": PREVIEW_H,
        }
    )
    jid = project.AddRenderJob()
    if not jid:
        raise SystemExit("AddRenderJob 失敗")
    project.StartRendering([jid], isInteractiveMode=False)
    for _ in range(600):
        if not project.IsRenderingInProgress():
            break
        time.sleep(1)
    project.DeleteRenderJob(jid)
    hits = sorted(out_dir.glob(f"{name}*.mp4"), key=lambda p: p.stat().st_mtime)
    if not hits:
        raise SystemExit(f"render 完成但 {out_dir} 找不到 {name}*.mp4")
    return hits[-1]


def _ffmpeg(args: list[str]) -> None:
    proc = subprocess.run(["ffmpeg", "-y", "-v", "error", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"ffmpeg 失敗: {proc.stderr[-300:]}")


def build_packet(episode_dir: Path, cid: str) -> dict:
    from build_resolve_project import connect_resolve

    c, w = _load_winner(episode_dir, cid)
    events = _load_events(episode_dir, cid)
    if not events:
        raise SystemExit(f"{cid} 沒有任何事件 JSON——先跑 broll/titles 企劃")
    srt = _load_srt_lines(episode_dir, cid)

    out_dir = episode_dir / REVIEW_DIR / cid
    out_dir.mkdir(parents=True, exist_ok=True)

    resolve = connect_resolve()
    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()
    if project is None or project.GetName() != episode_dir.name:
        project = pm.LoadProject(episode_dir.name)
    if project is None:
        raise SystemExit(f"project「{episode_dir.name}」不存在")
    label = f"{FORMAT_LABEL[c['format']]}{w['rank']} - {c['title']}（緊·導播）"
    timeline = None
    for i in range(1, project.GetTimelineCount() + 1):
        t = project.GetTimelineByIndex(i)
        if t and t.GetName() == label:
            timeline = t
            break
    if timeline is None:
        raise SystemExit(f"「{label}」不存在")
    project.SetCurrentTimeline(timeline)
    fps = float(project.GetSetting("timelineFrameRate"))
    dur = (timeline.GetEndFrame() - timeline.GetStartFrame()) / fps

    # 舊輪抽幀先清——事件增減會讓編號位移，混輪的舊幀會誤導盲審
    for old in [*out_dir.glob("ev_*.png"), out_dir / "contact_sheet.png"]:
        old.unlink(missing_ok=True)

    logger.info("render preview（540×960 H.264）…")
    preview = _render_preview(project, timeline, out_dir, f"{cid}_preview")

    # 字幕燒錄：Resolve render API 燒不進字幕（僅 ExportSubtitle sidecar，
    # 十七輪實測），改用 ffmpeg 從 tight SRT 燒——同源資料，順帶驗同步。
    srts = sorted((episode_dir / "highlights/srt").glob(f"{cid}_tight_r*.srt"))
    if srts:
        import shutil

        shutil.copy(srts[-1], out_dir / "subs.srt")
        burned = out_dir / f"{cid}_preview_sub.mp4"
        style = "FontName=Microsoft JhengHei,FontSize=14,Outline=1,MarginV=42"
        proc = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-i",
                preview.name,
                "-vf",
                f"subtitles=subs.srt:force_style='{style}'",
                "-c:a",
                "copy",
                burned.name,
            ],
            cwd=str(out_dir),
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0 and burned.exists():
            preview = burned
        else:
            logger.warning("字幕燒錄失敗，preview 無字幕: %s", proc.stderr[-200:])

    # 縮圖牆：1fps、每列 10 張
    import math

    cols, rows = 10, max(1, math.ceil(dur / 10))
    _ffmpeg(
        [
            "-i",
            str(preview),
            "-vf",
            f"fps=1,scale=180:-1,tile={cols}x{rows}",
            "-frames:v",
            "1",
            str(out_dir / "contact_sheet.png"),
        ]
    )

    # 每事件抽幀：進場後 0.4s + 中點（>2s 的事件才抽中點）
    frames = []
    for i, ev in enumerate(events):
        pts = [min(ev["t0"] + 0.4, ev["t1"])]
        if ev["t1"] - ev["t0"] > 2.0:
            pts.append((ev["t0"] + ev["t1"]) / 2)
        for j, t in enumerate(pts):
            tag = re.sub(r"[^\w-]", "", ev["slug"])[:20] or ev["type"]
            name = f"ev_{i:02d}{'b' if j else ''}_{tag}.png"
            _ffmpeg(["-ss", f"{t:.2f}", "-i", str(preview), "-frames:v", "1", str(out_dir / name)])
            frames.append({"event": i, "at": round(t, 2), "file": name})

    # 節拍器缺口：相鄰事件「起點」間距 > GAP_SEC
    gaps = []
    starts = [0.0] + [e["t0"] for e in events] + [dur]
    for a, b in zip(starts, starts[1:]):
        if b - a > GAP_SEC:
            gaps.append({"from": round(a, 1), "to": round(b, 1), "sec": round(b - a, 1)})

    packet = {
        "timeline": label,
        "duration_sec": round(dur, 1),
        "events_per_min": round(len(events) / (dur / 60), 1),
        "events": events,
        "gaps_over_12s": gaps,
        "frames": frames,
        "srt": srt,
        "preview": preview.name,
    }
    (out_dir / "events.json").write_text(
        json.dumps(packet, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return {
        "status": "packet",
        "dir": str(out_dir),
        "events": len(events),
        "events_per_min": packet["events_per_min"],
        "gaps_over_12s": gaps,
        "frames": len(frames),
    }


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="短片自檢 review packet（低解析 preview + 抽幀 + 事件清單）"
    )
    parser.add_argument("episode", help="episode 資料夾")
    parser.add_argument("--id", required=True, help="winner id（如 punch-S4）")
    args = parser.parse_args(argv)
    print(json.dumps(build_packet(Path(args.episode), args.id), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
