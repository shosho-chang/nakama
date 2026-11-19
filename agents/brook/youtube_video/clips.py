"""第 11 類「他人影片引用」：到 YouTube 找片段、標起訖、只下載核准的那一段。

修修 2026-11-17：「到 YouTube 上面去找影片並且截取，而且能夠標記是要從哪裡截取到哪裡」——
例：講到某個名人，找三段他接受不同訪問的片段；講到動畫角色（忍者龜的 Krang），截那個角色的片段；
畫面上附來源。規格 §1 第 11 類：「下載指定片段＋出處；上時間軸靜音、下載保留原聲」。

流程（一列＝一個 `<episode>/visual_sources/<row_id>.json` 候選清單）：

1. DP（agent）用 `search` 找影片、`captions`／`locate` 用字幕找到講那件事的地方，
   `propose` 把候選片段（網址＋建議起訖＋為什麼）寫進清單。
2. 修修在 Bridge（`/bridge/video-plan/<集>/clips/<row_id>`）直接播 YouTube，
   按鍵改起點／終點，標「要／不要」——**播放不下載**。
3. `download` 只下載標「要」的片段的那一段（yt-dlp `--download-sections`，連聲音），
   先 `--dry-run` 列出檔名、來源、大小給修修看。

只下載核准的片段：搜尋、讀字幕、看資訊都不下載影片。
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from filelock import FileLock

from shared.youtube_ingest import (
    NoCaptionAvailable,
    YtDlpError,
    _run_yt_dlp,
    extract_video_id,
    fetch_caption,
)

SCHEMA = "nakama.youtube_clip_picks.v1"
SOURCES_DIR = "visual_sources"
CLIPS_DIR = "clips"
STATUSES = ("proposed", "approved", "rejected")
MIN_CLIP_SEC = 0.5
MAX_CLIP_SEC = 60.0
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
# 1080p 以下、mp4 優先；下載保留原聲（上時間軸時靜音，規格 §1 第 11 類）
# 影像優先 HLS（m3u8）：https 的 DASH 影像常被 YouTube 擋 403（要 PO token），HLS 不用。
FORMAT = (
    "bv*[height<=1080][protocol^=m3u8]+ba[ext=m4a]/bv*[height<=1080][protocol^=m3u8]+ba"
    "/bv*[height<=1080]+ba/b[height<=1080]/b"
)


class ClipError(ValueError):
    """候選清單不對或片段做不了；訊息直接給修修看。"""


# ── 候選清單 ────────────────────────────────────────────────────────────


def picks_path(episode_dir: Path, row_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", row_id or ""):
        raise ClipError(f"列的 id 不對：{row_id!r}")
    return Path(episode_dir) / SOURCES_DIR / f"{row_id}.json"


def picks_lock(path: Path) -> FileLock:
    return FileLock(str(path) + ".lock", timeout=10)


def new_picks(row_id: str, need: int = 3) -> dict[str, Any]:
    return {"schema": SCHEMA, "row_id": row_id, "need": need, "candidates": []}


def load_picks(path: Path, row_id: str | None = None) -> dict[str, Any]:
    if not path.is_file():
        if row_id is None:
            raise ClipError(f"找不到候選清單：{path}")
        return new_picks(row_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    validate_picks(data)
    return data


def save_picks(path: Path, picks: Mapping[str, Any]) -> None:
    validate_picks(picks)
    data = dict(picks)
    data["updated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".clips-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def validate_picks(picks: Mapping[str, Any]) -> None:
    if picks.get("schema") != SCHEMA:
        raise ClipError(f"候選清單格式不對（schema 應為 {SCHEMA}）")
    seen = set()
    for cand in picks.get("candidates", []):
        cid = cand.get("id")
        if not cid or cid in seen:
            raise ClipError(f"候選片段 id 重複或空的：{cid!r}")
        seen.add(cid)
        check_candidate(cand)


def check_candidate(cand: Mapping[str, Any]) -> None:
    if not VIDEO_ID.fullmatch(str(cand.get("video_id", ""))):
        raise ClipError(f"{cand.get('id')}: YouTube 影片 id 不對")
    if cand.get("status") not in STATUSES:
        raise ClipError(f"{cand.get('id')}: 狀態只能是 {'／'.join(STATUSES)}")
    start, end = float(cand["start"]), float(cand["end"])
    if start < 0 or end - start < MIN_CLIP_SEC:
        raise ClipError(f"{cand.get('id')}: 終點要在起點之後至少 {MIN_CLIP_SEC:g} 秒")
    if end - start > MAX_CLIP_SEC:
        raise ClipError(f"{cand.get('id')}: 一段最長 {MAX_CLIP_SEC:g} 秒")
    duration = cand.get("duration")
    if duration and end > float(duration) + 0.5:
        raise ClipError(f"{cand.get('id')}: 終點超過影片長度（{fmt(float(duration))}）")
    if not str(cand.get("source_label", "")).strip():
        raise ClipError(f"{cand.get('id')}: 畫面上的來源（source_label）是空的")


def _next_id(picks: Mapping[str, Any]) -> str:
    used = {c["id"] for c in picks.get("candidates", [])}
    n = 1
    while f"c{n}" in used:
        n += 1
    return f"c{n}"


def add_candidate(
    picks: dict[str, Any],
    meta: Mapping[str, Any],
    start: float,
    end: float,
    note: str = "",
    source_label: str | None = None,
) -> dict[str, Any]:
    cand = {
        "id": _next_id(picks),
        "video_id": meta["video_id"],
        "title": meta.get("title", ""),
        "channel": meta.get("channel", ""),
        "duration": meta.get("duration"),
        "start": round(float(start), 2),
        "end": round(float(end), 2),
        "source_label": (source_label or meta.get("channel") or "").strip(),
        "note": note,
        "status": "proposed",
    }
    check_candidate(cand)
    picks.setdefault("candidates", []).append(cand)
    return cand


EDITABLE = frozenset({"start", "end", "status", "source_label", "note"})


def update_candidate(
    picks: dict[str, Any], cand_id: str, changes: Mapping[str, Any]
) -> dict[str, Any]:
    cand = next((c for c in picks.get("candidates", []) if c["id"] == cand_id), None)
    if cand is None:
        raise ClipError(f"找不到候選片段 {cand_id}")
    unknown = set(changes) - EDITABLE
    if unknown:
        raise ClipError(f"不能改：{', '.join(sorted(unknown))}")
    trial = {**cand, **changes}
    if "start" in changes or "end" in changes:
        trial["start"] = round(float(trial["start"]), 2)
        trial["end"] = round(float(trial["end"]), 2)
    check_candidate(trial)
    cand.update(trial)
    return cand


def approved(picks: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [c for c in picks.get("candidates", []) if c["status"] == "approved"]


# ── YouTube：搜尋、資訊、字幕（都不下載影片）────────────────────────────


def _json_lines(text: str) -> Iterable[dict[str, Any]]:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def search(query: str, n: int = 10) -> list[dict[str, Any]]:
    result = _run_yt_dlp(
        ["--flat-playlist", "--dump-json", "--no-warnings", f"ytsearch{int(n)}:{query}"],
        timeout=120,
    )
    if result.returncode != 0 and not result.stdout.strip():
        raise ClipError(f"YouTube 搜尋失敗：{result.stderr.strip()[-300:]}")
    out = []
    for d in _json_lines(result.stdout):
        if not VIDEO_ID.fullmatch(str(d.get("id", ""))):
            continue
        out.append(
            {
                "video_id": d["id"],
                "title": d.get("title") or "",
                "channel": d.get("channel") or d.get("uploader") or "",
                "duration": d.get("duration"),
                "views": d.get("view_count"),
            }
        )
    return out


def info(video: str) -> dict[str, Any]:
    """影片資訊（標題、頻道、長度、章節、最多人重播的地方）。"""
    vid = video if VIDEO_ID.fullmatch(video) else extract_video_id(video)
    result = _run_yt_dlp(
        ["-J", "--skip-download", "--no-warnings", f"https://www.youtube.com/watch?v={vid}"],
        timeout=120,
    )
    if result.returncode != 0:
        raise ClipError(f"讀不到影片資訊 {vid}：{result.stderr.strip()[-300:]}")
    d = json.loads(result.stdout)
    heat = d.get("heatmap") or []
    peaks = sorted(heat, key=lambda h: h.get("value", 0), reverse=True)[:5]
    return {
        "video_id": vid,
        "title": d.get("title") or "",
        "channel": d.get("channel") or d.get("uploader") or "",
        "duration": d.get("duration"),
        "upload_date": d.get("upload_date"),
        "chapters": [
            {"start": c["start_time"], "title": c.get("title", "")} for c in d.get("chapters") or []
        ],
        "replay_peaks": [round(float(p["start_time"]), 1) for p in peaks],
        "has_captions": bool(d.get("subtitles") or d.get("automatic_captions")),
        # 畫面規格：低 fps＋方形多半是純音訊 podcast（只有封面圖），沒有人可以截
        "width": d.get("width"),
        "height": d.get("height"),
        "fps": d.get("fps"),
    }


def raw_cues(vtt: str) -> list[dict[str, Any]]:
    """WebVTT → 一行一行的字幕（不併成句子）。

    `shared.webvtt.parse_webvtt` 會把句子併起來，給閱讀用；但 YouTube 自動字幕沒有標點，
    併起來常常一句 30 秒，拿來定位片段太粗。這裡每一行字幕各自保留時間（YouTube 一行約 2–4 秒）。
    """
    cues: list[dict[str, Any]] = []
    start = end = None
    lines: list[str] = []

    def flush() -> None:
        if start is not None and lines:
            text = next(
                (
                    t
                    for t in (html.unescape(_TAG.sub("", ln)).strip() for ln in reversed(lines))
                    if t
                ),
                "",
            )
            if text and not (cues and cues[-1]["text"] == text):
                cues.append({"start": start, "end": end, "text": text})
            elif text and cues:
                cues[-1]["end"] = end

    for raw in vtt.splitlines():
        m = _TIME.match(raw.strip())
        if m:
            flush()
            lines = []
            start, end = _secs(m.group(1)), _secs(m.group(2))
            continue
        if start is None or not raw.strip():
            flush()
            lines = []
            if not raw.strip():
                start = None
            continue
        lines.append(raw)
    flush()
    return cues


_TIME = re.compile(
    r"^(\d+:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}\.\d{3})\s+-->\s+(\d+:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}\.\d{3})"
)
_TAG = re.compile(r"<[^>]+>")


def _secs(stamp: str) -> float:
    parts = [float(x) for x in stamp.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    return round(parts[0] * 3600 + parts[1] * 60 + parts[2], 3)


def captions(video_id: str, work_dir: Path) -> list[dict[str, Any]]:
    """字幕（自動字幕也可）→ 一行一行 [{start, end, text}]。"""
    try:
        path, _lang = fetch_caption(video_id, Path(work_dir))
    except (NoCaptionAvailable, YtDlpError) as exc:
        raise ClipError(
            f"{video_id} 抓不到字幕（{exc}）：改用章節、最多人重播的地方，或請修修在播放器上找"
        ) from exc
    return raw_cues(path.read_text(encoding="utf-8"))


def locate(cues: Sequence[Mapping[str, Any]], keywords: Sequence[str]) -> list[int]:
    """字幕裡提到任一關鍵字的句子位置（不分大小寫）。"""
    kws = [k.lower() for k in keywords if k.strip()]
    return [i for i, c in enumerate(cues) if any(k in str(c["text"]).lower() for k in kws)]


def window(cues: Sequence[Mapping[str, Any]], hit: int, target_sec: float) -> tuple[float, float]:
    """從提到關鍵字那句開頭起，往後接整句直到大約 target 秒（只切在句子邊界）。"""
    start = float(cues[hit]["start"])
    end = float(cues[hit]["end"])
    i = hit
    while end - start < target_sec and i + 1 < len(cues):
        i += 1
        if float(cues[i]["end"]) - start > target_sec * 1.5:
            break
        end = float(cues[i]["end"])
    return round(start, 2), round(end, 2)


# ── 下載（只下載核准的片段）────────────────────────────────────────────


def clip_path(episode_dir: Path, row_id: str, cand: Mapping[str, Any]) -> Path:
    """檔名帶起訖：修修改了起訖就是另一個檔，不會沿用舊的。"""
    span = f"{float(cand['start']):.2f}-{float(cand['end']):.2f}".replace(".", "_")
    return Path(episode_dir) / SOURCES_DIR / CLIPS_DIR / f"{row_id}_{cand['id']}_{span}.mp4"


def section_arg(start: float, end: float) -> str:
    return f"*{start:.2f}-{end:.2f}"


def download_argv(cand: Mapping[str, Any], out: Path) -> list[str]:
    return [
        "-f",
        FORMAT,
        "--merge-output-format",
        "mp4",
        "--download-sections",
        section_arg(float(cand["start"]), float(cand["end"])),
        "--force-keyframes-at-cuts",
        "--no-playlist",
        "--no-warnings",
        "-o",
        str(out),
        f"https://www.youtube.com/watch?v={cand['video_id']}",
    ]


def estimate_mb(cand: Mapping[str, Any]) -> float | None:
    """依選到的格式的平均位元率 × 片段秒數估（YouTube 給的是近似值）。

    1080p 常是串流格式（HLS），沒有整支檔案大小，只有位元率（tbr，kbit/s）——
    用大小推會漏掉影像只剩聲音，所以一律用位元率算。
    """
    result = _run_yt_dlp(
        [
            "-f",
            FORMAT,
            "-J",
            "--skip-download",
            "--no-warnings",
            f"https://www.youtube.com/watch?v={cand['video_id']}",
        ],
        timeout=120,
    )
    if result.returncode != 0:
        return None
    d = json.loads(result.stdout)
    fmts = d.get("requested_formats") or [d]
    kbps = sum(float(f.get("tbr") or 0) for f in fmts)
    if not kbps:
        return None
    seconds = float(cand["end"]) - float(cand["start"])
    return round(kbps * 1000 / 8 * seconds / 1e6, 1)


# ── 切畫面（一段片段裡不能有太短的鏡頭）──────────────────────────────
# 修修 2026-11-17：「第一個訪談他馬上就切畫面了，會讓觀眾覺得很奇怪……
# 三個不同的訪談，其實只要三個不同的畫面就好了，如果畫面切得太快，反而會讓觀眾覺得奇怪」。
# 實測：Rational Reminder 4:30.25 起頭 0.3 秒是四格畫面才切到三人畫面（畫面變化分數 0.67）；
# Moneywise 書封動畫 0.33；人講話、卡通動作只有 0.0x–0.17。
CUT_SCENE = 0.25
MIN_SHOT_SEC = 1.5


def shot_cuts(path: Path) -> list[float]:
    """片段裡切畫面的秒數（ffmpeg 畫面變化分數 > CUT_SCENE）。"""
    proc = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(path),
            "-vf",
            f"select='gt(scene,{CUT_SCENE})',showinfo",
            "-an",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    return [round(float(t), 2) for t in re.findall(r"pts_time:([0-9.]+)", proc.stderr)]


def shots(cuts: Sequence[float], length: float) -> list[tuple[float, float]]:
    """切點把片段分成幾個鏡頭（秒數從片段開頭算）。"""
    marks = [0.0, *[c for c in cuts if 0 < c < length], length]
    return [(round(a, 2), round(b, 2)) for a, b in zip(marks, marks[1:], strict=False) if b > a]


def shot_problem(cand: Mapping[str, Any], cuts: Sequence[float]) -> str | None:
    """有比 MIN_SHOT_SEC 短的鏡頭 → 說明＋建議起訖（片段裡最長的那一鏡）；沒問題回 None。"""
    length = float(cand["end"]) - float(cand["start"])
    parts = shots(cuts, length)
    short = [(a, b) for a, b in parts if b - a < MIN_SHOT_SEC]
    if not short:
        return None
    a, b = max(parts, key=lambda p: p[1] - p[0])
    start = float(cand["start"])
    cut_text = "、".join(f"{c:.2f}" for c in cuts if 0 < c < length)
    if b - a < MIN_SHOT_SEC:
        return (
            f"{cand['id']} 在第 {cut_text} 秒切畫面，片段裡沒有一鏡超過 {MIN_SHOT_SEC:g} 秒——換一段"
        )
    return (
        f"{cand['id']} 在第 {cut_text} 秒切畫面，"
        f"有鏡頭不到 {MIN_SHOT_SEC:g} 秒（觀眾會覺得畫面閃一下）；"
        f"建議起訖改成 {fmt(start + a)}–{fmt(start + b)}（片段裡最長的一鏡，{b - a:.1f} 秒）"
    )


PEEK_FORMAT = "bv*[height<=360][protocol^=m3u8]/bv*[height<=360]/b[height<=360]"


def peek(video_id: str, times: Sequence[float], out_dir: Path) -> list[Path]:
    """在指定秒數各抓一張 360p 小圖，讓 DP 看畫面確認（字幕對上不代表畫面是那個人）。

    只讀串流的那一格，不下載整支影片。
    """
    result = _run_yt_dlp(
        ["-f", PEEK_FORMAT, "-g", "--no-warnings", f"https://www.youtube.com/watch?v={video_id}"],
        timeout=120,
    )
    url = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    if result.returncode != 0 or not url:
        raise ClipError(f"{video_id} 抓不到畫面：{result.stderr.strip()[-300:]}")
    out_dir.mkdir(parents=True, exist_ok=True)
    shots = []
    for t in times:
        out = out_dir / f"{video_id}_{float(t):08.2f}.jpg"
        proc = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-ss",
                f"{float(t):.2f}",
                "-i",
                url,
                "-frames:v",
                "1",
                str(out),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode != 0 or not out.is_file():
            raise ClipError(f"{video_id} {fmt(float(t))} 抓不到畫面：{proc.stderr.strip()[-200:]}")
        shots.append(out)
    return shots


def download(cand: Mapping[str, Any], out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    result = _run_yt_dlp(download_argv(cand, out), timeout=600)
    if result.returncode != 0 or not out.is_file():
        raise ClipError(f"{cand['id']} 下載失敗：{result.stderr.strip()[-400:]}")
    return out


def fmt(seconds: float) -> str:
    m, s = divmod(max(0.0, float(seconds)), 60)
    return f"{int(m)}:{s:05.2f}"


# ── CLI ────────────────────────────────────────────────────────────────


def _print(obj: Any) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="他人影片引用：找片、標起訖、下載核准的片段")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search", help="搜 YouTube（不下載）")
    s.add_argument("query")
    s.add_argument("-n", type=int, default=10)
    i = sub.add_parser("info", help="影片資訊：章節、最多人重播的地方、有沒有字幕")
    i.add_argument("video")
    lo = sub.add_parser("locate", help="用字幕找提到關鍵字的地方，給建議起訖")
    lo.add_argument("video")
    lo.add_argument("--keywords", required=True, help="逗號分開")
    lo.add_argument("--target", type=float, default=4.0, help="想要的片段長度（秒）")
    pr = sub.add_parser("propose", help="把一段加進這一列的候選清單")
    pr.add_argument("episode", type=Path)
    pr.add_argument("row_id")
    pr.add_argument("video")
    pr.add_argument("--start", type=float, required=True)
    pr.add_argument("--end", type=float, required=True)
    pr.add_argument("--note", default="", help="為什麼選這段（給修修看）")
    pr.add_argument("--label", help="畫面上的來源（預設＝頻道名）")
    pr.add_argument("--need", type=int, help="這一列要幾段")
    pk = sub.add_parser("peek", help="在幾個秒數各抓一張小圖，看畫面是不是那個人（不下載整支）")
    pk.add_argument("video")
    pk.add_argument("--at", required=True, help="秒數，逗號分開")
    pk.add_argument("-o", "--out", type=Path, required=True, help="圖放哪裡")
    dl = sub.add_parser("download", help="下載標「要」的片段（先 --dry-run 列檔名、來源、大小）")
    dl.add_argument("episode", type=Path)
    dl.add_argument("row_id")
    dl.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    try:
        if args.cmd == "search":
            _print(search(args.query, args.n))
        elif args.cmd == "info":
            _print(info(args.video))
        elif args.cmd == "locate":
            vid = args.video if VIDEO_ID.fullmatch(args.video) else extract_video_id(args.video)
            with tempfile.TemporaryDirectory() as tmp:
                cues = captions(vid, Path(tmp))
            hits = locate(cues, args.keywords.split(","))
            _print(
                [
                    {
                        "start": window(cues, h, args.target)[0],
                        "end": window(cues, h, args.target)[1],
                        "at": fmt(cues[h]["start"]),
                        "text": cues[h]["text"],
                    }
                    for h in hits
                ]
            )
        elif args.cmd == "propose":
            path = picks_path(args.episode, args.row_id)
            with picks_lock(path):
                picks = load_picks(path, args.row_id)
                if args.need:
                    picks["need"] = args.need
                cand = add_candidate(
                    picks, info(args.video), args.start, args.end, args.note, args.label
                )
                save_picks(path, picks)
            _print(cand)
        elif args.cmd == "peek":
            vid = args.video if VIDEO_ID.fullmatch(args.video) else extract_video_id(args.video)
            for shot in peek(vid, [float(x) for x in args.at.split(",")], args.out):
                print(shot)
        elif args.cmd == "download":
            path = picks_path(args.episode, args.row_id)
            picks = load_picks(path)
            todo = approved(picks)
            if not todo:
                raise ClipError("這一列還沒有標「要」的片段")
            for cand in todo:
                out = clip_path(args.episode, args.row_id, cand)
                line = (
                    f"{out.name}｜{cand['title']}（{cand['channel']}）"
                    f" {fmt(cand['start'])}–{fmt(cand['end'])}｜https://youtu.be/{cand['video_id']}"
                )
                if args.dry_run:
                    mb = estimate_mb(cand)
                    print(line + f"｜約 {mb} MB" if mb is not None else line + "｜大小不明")
                    continue
                if out.is_file():
                    print(f"已存在，略過：{out.name}")
                    problem = shot_problem(cand, shot_cuts(out))
                    if problem:
                        print(f"⚠ {problem}")
                    continue
                download(cand, out)
                print(f"下載好：{line}")
                problem = shot_problem(cand, shot_cuts(out))
                if problem:
                    print(f"⚠ {problem}")
        return 0
    except ClipError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
