"""short-review：短片自檢 loop 素材包 — 修修 2026-07-27 十七輪裁決。

「剪完 export 低解析版 → 派 subagent 看片找問題 → 回饋 → 修 → loop」的
deterministic 前半段。本 script 產「review packet」，subagent 盲審由
session 主循環 dispatch（見 SKILL Step 10）。

產出（episode `highlights/review/<id>/`）：
- preview.mp4        低解析快轉審片用（短片 540×960 / 長片 960×540）
- contact_sheet.png  1fps 縮圖牆（全片節奏一眼掃）；長片切成
                     contact_sheet_NN.png，每包 180s——單張 7000px 高的
                     長條盲審讀不到細節
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
GAP_SEC = 8.0  # 節拍器缺口門檻（二十四輪盲審下修：12s 太寬鬆）
# 格式參數（修修 2026-08-03 長片線）。短片欄 = 既有已驗收值。
#
# 長片除了換畫幅，還必須**分段出縮圖牆**：752s 全片 1fps ×10 欄 = 1800×7676px
# 的長條，盲審 agent 讀進去會被縮到看不清任何一格。切成 180s 一包 →
# 每包 1800×1818，接近正方形、細節讀得到。
# 缺口門檻也放寬：長片密度目標 4.5–5.5 事件/分（剪輯文法 §2.1），
# 短片是 6–9，用同一把尺會把長片的正常呼吸判成死區。
FORMAT_REVIEW = {
    "short": {"preview": (540, 960), "gap_sec": GAP_SEC, "chunk_sec": None, "burn_srt": True},
    "long": {"preview": (960, 540), "gap_sec": 14.0, "chunk_sec": 180.0, "burn_srt": False},
}
# punch zoom 是「同機位縮放」不是新視覺事件——算進去會遮蔽真死區
# （兩位盲審獨立指出）。缺口只計換鏡素材與卡片。
GAP_EXCLUDE_PREFIX = ("punch-",)


def _load_events(episode_dir: Path, cid: str) -> list[dict]:
    td = episode_dir / TIGHTEN_DIR
    events: list[dict] = []
    p = td / f"{cid}_broll.json"
    if p.exists():
        for it in json.loads(p.read_text(encoding="utf-8"))["items"]:
            if it["kind"] == "badge":
                continue  # 全片常駐 watermark，不是「視覺事件」——進事件表會污染節拍分析
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


def _render_preview(
    project, timeline, out_dir: Path, name: str, size: tuple[int, int] | None = None
) -> Path:
    """Resolve render queue 出低解析 H.264 preview。"""
    pw, ph = size or (PREVIEW_W, PREVIEW_H)
    project.SetCurrentRenderFormatAndCodec("mp4", "H264")
    project.SetRenderSettings(
        {
            "MarkIn": timeline.GetStartFrame(),
            "MarkOut": timeline.GetEndFrame(),
            "TargetDir": str(out_dir),
            "CustomName": name,
            "FormatWidth": pw,
            "FormatHeight": ph,
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

    # 舊輪產物先清——事件增減會讓抽幀編號位移；舊 preview 留著會讓修修
    # 分不清要看哪個（十九輪）
    for stale in [
        *out_dir.glob("ev_*.png"),
        out_dir / "contact_sheet.png",
        *out_dir.glob("contact_sheet_*.png"),
        *out_dir.glob("*_preview*.mp4"),
        *out_dir.glob("*_raw*.mp4"),
    ]:
        try:
            stale.unlink(missing_ok=True)
        except PermissionError:
            # 修修正在播那支 preview → 檔案被鎖。不擋整條重建，
            # 後續 ffmpeg 會覆寫同名檔（覆寫比刪除寬容）
            logger.warning("刪不掉（可能正在播放）：%s", stale.name)

    fcfg = FORMAT_REVIEW[c.get("format", "short")]
    pw, ph = fcfg["preview"]
    logger.info("render preview（%d×%d H.264）…", pw, ph)
    preview = _render_preview(project, timeline, out_dir, f"{cid}_raw", (pw, ph))

    # 字幕燒錄：短片的 Resolve render 燒不進字幕（僅 ExportSubtitle sidecar，
    # 十七輪實測），改用 ffmpeg 從 tight SRT 燒——同源資料，順帶驗同步。
    #
    # ⚠️ **長片不燒**（2026-08-04 實測）：長片走主字幕模板，Resolve render
    # 會把字幕軌燒進 preview，再疊一層 ffmpeg 會出現**兩條字幕**（上面灰底
    # 是模板樣式、下面白字描邊是 ffmpeg）。長片的 preview 直接用 Resolve
    # 出的那條，樣式還更接近成品。
    srts = sorted((episode_dir / "highlights/srt").glob(f"{cid}_tight_r*.srt"))
    if srts:
        import shutil

        shutil.copy(srts[-1], out_dir / "subs.srt")
    if srts and fcfg["burn_srt"]:
        # 交付檔名用「短N」開頭——修修不用對 punch-SN ↔ 短N 對照表（十九輪）
        burned = out_dir / f"{FORMAT_LABEL[c['format']]}{w['rank']}_preview.mp4"
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
            preview.unlink(missing_ok=True)  # raw 無字幕版燒完即棄
            preview = burned
        else:
            logger.warning("字幕燒錄失敗，preview 無字幕: %s", proc.stderr[-200:])
    elif srts:
        # 長片：Resolve 已把字幕軌燒進來，只把 raw 改成交付檔名
        named = out_dir / f"{FORMAT_LABEL[c['format']]}{w['rank']}_preview.mp4"
        named.unlink(missing_ok=True)
        preview = preview.rename(named)

    # 縮圖牆：1fps、每列 10 張。長片切成 chunk_sec 一包——單張 7000px 高的
    # 長條，盲審讀進去每格會小到看不出東西。
    import math

    chunk = fcfg["chunk_sec"]
    sheets = []
    if chunk:
        n_chunks = max(1, math.ceil(dur / chunk))
        for k in range(n_chunks):
            ss = k * chunk
            span = min(chunk, dur - ss)
            if span <= 0:
                break
            rows = max(1, math.ceil(span / 10))
            out = out_dir / f"contact_sheet_{k:02d}.png"
            _ffmpeg(
                [
                    "-ss", f"{ss:.2f}", "-t", f"{span:.2f}",
                    "-i", str(preview),
                    "-vf", f"fps=1,scale=180:-1,tile=10x{rows}",
                    "-frames:v", "1", str(out),
                ]
            )
            if out.exists():
                sheets.append({"file": out.name, "t0": round(ss, 1), "t1": round(ss + span, 1)})
    else:
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
        sheets.append({"file": "contact_sheet.png", "t0": 0.0, "t1": round(dur, 1)})

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

    # 換鏡也是視覺事件（二十五輪：只算素材與卡片會誤判——導播的反應鏡頭
    # 與機位切換觀眾是看得到的）。直接讀 track 1 的 item 邊界＝真實切點。
    cut_events = []
    for it in timeline.GetItemListInTrack("video", 1) or []:
        ts = (it.GetStart() - timeline.GetStartFrame()) / fps
        if ts > 0.05:
            cut_events.append(
                {"type": "cut", "slug": "", "t0": round(ts, 2), "t1": round(ts, 2), "note": "換鏡"}
            )
    events = sorted(events + cut_events, key=lambda x: x["t0"])

    # 節拍器缺口：**前事件結束 → 後事件開始**的真空（二十四輪修正：
    # 舊版用 t0→t0 會高估缺口長度、又漏抓尾段空窗），且排除 punch zoom
    gaps = []
    visual = [e for e in events if not e["type"].startswith(GAP_EXCLUDE_PREFIX)]
    cursor = 0.0
    for e in visual:
        if e["t0"] - cursor > fcfg["gap_sec"]:
            gaps.append(
                {
                    "from": round(cursor, 1),
                    "to": round(e["t0"], 1),
                    "sec": round(e["t0"] - cursor, 1),
                }
            )
        cursor = max(cursor, e["t1"])
    if dur - cursor > fcfg["gap_sec"]:
        gaps.append({"from": round(cursor, 1), "to": round(dur, 1), "sec": round(dur - cursor, 1)})

    packet = {
        "timeline": label,
        "duration_sec": round(dur, 1),
        "events_per_min": round(len(events) / (dur / 60), 1),
        # 換鏡是「弱事件」（同一組談話頭來回切）——素材/卡片才是「強事件」。
        # 兩個數字一起看：只有換鏡撐場的段落，強事件密度會露餡
        "content_per_min": round(len([e for e in events if e["type"] != "cut"]) / (dur / 60), 1),
        "events": events,
        "gap_threshold_sec": fcfg["gap_sec"],
        "gaps": gaps,
        "contact_sheets": sheets,
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
        "content_per_min": packet["content_per_min"],
        "gap_threshold_sec": fcfg["gap_sec"],
        "gaps": gaps,
        "contact_sheets": [x["file"] for x in sheets],
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
