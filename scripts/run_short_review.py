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

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_highlight_cut import FORMAT_LABEL  # noqa: E402
from run_short_tighten import (  # noqa: E402
    TIGHTEN_DIR,
    _load_winner,
    _open_editorial_master,
)

from agents.brook.script_video.editorial_master import EditorialMasterContractError  # noqa: E402
from agents.brook.script_video.highlight_broll import (  # noqa: E402
    BrollContractError,
    verify_broll_receipt,
)
from agents.brook.script_video.highlight_broll import (  # noqa: E402
    receipt_identity as broll_receipt_identity,
)
from agents.brook.script_video.identity_placement import (  # noqa: E402
    IdentityPlacementError,
    verify_identity_placement,
)
from shared.editorial_conform import conform_source_paths  # noqa: E402
from shared.highlight_materialization import (  # noqa: E402
    HighlightSource,
    verify_materialization_receipt,
)

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
    "short": {
        "preview": (540, 960),
        "gap_sec": GAP_SEC,
        "chunk_sec": None,
        "burn_srt": True,
        "content_gap_sec": None,
    },
    "long": {
        "preview": (960, 540),
        "gap_sec": 14.0,
        "chunk_sec": 180.0,
        "burn_srt": False,
        "content_gap_sec": 75.0,
    },
}
# punch zoom 是「同機位縮放」不是新視覺事件——算進去會遮蔽真死區
# （兩位盲審獨立指出）。缺口只計換鏡素材與卡片。
GAP_EXCLUDE_PREFIX = ("punch-",)


def _verify_materialization_receipt(
    episode_dir: Path,
    cid: str,
    timeline,
    editorial_master_lineage: dict,
    *,
    cut_format: str,
    t_start: float,
    t_end: float,
    fps: float,
) -> dict:
    """Bind the selected live Resolve Timeline to its Master source marker."""
    master = _open_editorial_master(episode_dir)
    if master.identity() != editorial_master_lineage:
        raise SystemExit("review packet Editorial Master lineage drift")
    source = HighlightSource(
        srt_path=master.srt_path,
        media_path=master.media_path,
        lineage=master.identity(),
    )
    try:
        return verify_materialization_receipt(
            episode_dir,
            cid,
            source=source,
            timeline=timeline,
            expected_timeline_name=timeline.GetName(),
            expected_format=cut_format,
            expected_source_range={
                "start_sec": float(t_start),
                "end_sec": float(t_end),
                "start_frame": int(float(t_start) * fps),
                "end_frame": int(float(t_end) * fps),
            },
            # 短片的畫面是 conform map 投影出來的機位，不是 Master（ADR-067）。
            # 聲音軌不受影響，仍然只認 Master。
            live_video_sources=(
                conform_source_paths(episode_dir) if cut_format == "short" else None
            ),
        )
    except EditorialMasterContractError as exc:
        raise SystemExit(f"review packet materialization receipt 驗證失敗：{exc}") from exc


def _review_event_display(item: dict, lane: str) -> str:
    """Return the human-facing label carried into finished review."""

    variables = item.get("vars") if isinstance(item.get("vars"), dict) else {}
    if lane == "identity_card":
        name = str(item.get("name") or variables.get("label") or "").strip()
        title = str(item.get("title") or variables.get("sub") or "").strip()
        return "｜".join(part for part in (name, title) if part) or "來賓字卡"
    if lane == "fullscreen_transition":
        return str(
            variables.get("title")
            or item.get("on_screen_text")
            or item.get("note")
            or "Fullscreen transition"
        ).strip()
    if lane == "pacing" and str(item.get("kind") or "").replace("_", "-") in {
        "camera-correction",
        "camera-override",
    }:
        role = str(item.get("camera") or item.get("subject_role") or "").strip().lower()
        camera, subject = {
            "cam1": ("Camera 1", "主持人"),
            "camera1": ("Camera 1", "主持人"),
            "host": ("Camera 1", "主持人"),
            "cam2": ("Camera 2", "來賓"),
            "camera2": ("Camera 2", "來賓"),
            "guest": ("Camera 2", "來賓"),
            "cam3": ("Camera 3", "雙人全景"),
            "camera3": ("Camera 3", "雙人全景"),
            "wide": ("Camera 3", "雙人全景"),
        }.get(role, ("Camera ?", role or "未指定"))
        return f"{camera} · {subject}"
    return str(
        item.get("note")
        or item.get("on_screen_text")
        or variables.get("title")
        or variables.get("label")
        or item.get("slug")
        or item.get("kind")
        or "未命名元件"
    ).strip()


def _review_event_semantics(item: dict) -> tuple[str, str | None, str | None]:
    """Project renderer vocabulary into the canonical finished-review lane."""

    kind = str(item.get("kind") or "").strip().lower().replace("_", "-")
    component = str(item.get("comp") or "").strip().lower().replace("-", "_") or None
    materialization = item.get("visual_materialization")
    implementation = (
        str(materialization.get("implementation_kind") or "").strip().lower()
        if isinstance(materialization, dict)
        else ""
    )
    implementation = implementation or component
    slug = str(item.get("slug") or "").strip().lower().replace("_", "-")
    legacy_guest_namecard = (
        kind == "concept" and component == "chapter_label" and slug == "guest-namecard"
    )
    if kind in {"video", "photo"} or implementation == "stock_video":
        lane = "b_roll"
    elif legacy_guest_namecard or kind in {
        "guest-namecard",
        "host-namecard",
        "identity-card",
        "namecard",
    }:
        lane = "identity_card"
    elif implementation == "transition_title":
        lane = "fullscreen_transition"
    elif implementation in {"hero_title", "punch_card", "punch_card_wide"}:
        lane = "hero_title"
    elif kind == "badge":
        lane = "badge"
    elif kind in {"camera-correction", "camera-override"}:
        lane = "pacing"
    elif kind in {"concept", "sticker", "icon-motion"}:
        lane = "visual_effect"
    else:
        lane = None
    return lane or "", component, implementation


def _uses_transcript_choreography(episode_dir: Path, cid: str) -> bool:
    """True when kinetic titles are the complete, sole subtitle renderer."""
    plan = episode_dir / TIGHTEN_DIR / f"{cid}_titles.json"
    if not plan.exists():
        return False
    try:
        return bool(json.loads(plan.read_text(encoding="utf-8")).get("covers_full_transcript"))
    except (OSError, json.JSONDecodeError):
        return False


def _load_events(episode_dir: Path, cid: str) -> list[dict]:
    td = episode_dir / TIGHTEN_DIR
    events: list[dict] = []
    camera_plan_paths = [
        td / f"{cid}_camera_plan.json",
        td / f"{cid}_camera.json",
    ]
    camera_plan_path = next((path for path in camera_plan_paths if path.is_file()), None)
    p = td / f"{cid}_broll.json"
    if p.exists():
        for it in json.loads(p.read_text(encoding="utf-8"))["items"]:
            if it["kind"] == "badge":
                continue  # 全片常駐 watermark，不是「視覺事件」——進事件表會污染節拍分析
            if camera_plan_path is not None and it["kind"] in {
                "camera-correction",
                "camera-override",
            }:
                continue  # 完整 camera plan 已涵蓋全片，避免開場 correction 重複顯示
            review_lane, component, implementation = _review_event_semantics(it)
            event = {
                "type": it["kind"],
                "kind": it["kind"],
                "slug": it.get("slug", ""),
                "t0": float(it["t0"]),
                "t1": float(it["t1"]),
                "note": it.get("note", ""),
            }
            if review_lane:
                event["review_lane"] = review_lane
                event["display"] = _review_event_display(it, review_lane)
            if component:
                event["component"] = component
            if implementation:
                event["implementation_kind"] = implementation
            for field in ("vars", "name", "title", "on_screen_text"):
                if field in it:
                    event[field] = it[field]
            if it["kind"] == "video":
                event["asset_category"] = "stock_video"
            events.append(event)
    if camera_plan_path is not None:
        plan = json.loads(camera_plan_path.read_text(encoding="utf-8"))
        for shot in plan.get("shots", []):
            item = {
                **shot,
                "kind": "camera-correction",
                "comp": "camera_plan",
            }
            events.append(
                {
                    "type": "camera-correction",
                    "kind": "camera-correction",
                    "component": "camera_plan",
                    "implementation_kind": "camera_plan",
                    "review_lane": "pacing",
                    "display": _review_event_display(item, "pacing"),
                    "slug": "",
                    "t0": float(shot["t0"]),
                    "t1": float(shot["t1"]),
                    "note": str(shot.get("reason") or ""),
                    "camera": shot.get("camera"),
                }
            )
    p = td / f"{cid}_titles.json"
    if p.exists():
        for it in json.loads(p.read_text(encoding="utf-8"))["titles"]:
            if "text" in it:
                label = str(it["text"])
            else:
                states = it.get("states", [])
                label = "\n".join(states[-1].get("lines", [])) if states else ""
            events.append(
                {
                    "type": f"card-tier{it.get('tier', 2)}",
                    "kind": "hero-title",
                    "component": "hero_title",
                    "implementation_kind": "hero_title",
                    "review_lane": "hero_title",
                    "slug": label.replace("\n", "/"),
                    "text": label,
                    "display": label,
                    "t0": float(it["t0"]),
                    "t1": float(it["t1"]),
                    "note": "",
                }
            )
    # 短片線的 zoom 企劃寫的是逐字稿座標（cue／phrase），絕對秒數在導播解出來的
    # `_zoom.resolved.json` 裡（ADR-067）；有解析檔就用它，沒有才讀舊格式。
    resolved = td / f"{cid}_zoom.resolved.json"
    p = resolved if resolved.is_file() else td / f"{cid}_zoom.json"
    if p.exists():
        punches = json.loads(p.read_text(encoding="utf-8"))["punches"]
        for it in punches:
            if "t0" not in it:
                raise SystemExit(
                    f"{p.name} 的 punch 缺絕對秒數——短片線請先跑 run_shortform_director "
                    "產生 _zoom.resolved.json"
                )
            events.append(
                {
                    "type": f"punch-{it.get('style', 'ramp')}",
                    "kind": "pacing",
                    "review_lane": "pacing",
                    "slug": "",
                    "t0": float(it["t0"]),
                    "t1": float(it["t1"]),
                    "note": it.get("why") or it.get("note", ""),
                }
            )
            # 同一個 punch 中途再進一階也是一次視覺重音，逐階列成事件
            for step in it.get("steps") or []:
                events.append(
                    {
                        "type": f"punch-{step.get('style', 'cut')}",
                        "kind": "pacing",
                        "review_lane": "pacing",
                        "slug": "",
                        "t0": float(step["t"]),
                        "t1": float(it["t1"]),
                        "note": f"階梯 ×{step.get('scale')}",
                    }
                )
    events.sort(key=lambda x: x["t0"])
    return events


def _verify_guest_identity_events(
    episode_dir: Path,
    cid: str,
    events: list[dict],
    editorial_master,
) -> dict | None:
    """Fail before render if any guest namecard is not quorum-anchored."""

    guest_cards = [
        event
        for event in events
        if str(event.get("type", "")).lower().replace("_", "-") == "guest-namecard"
    ]
    if not guest_cards:
        return None
    selected = None
    try:
        for event in guest_cards:
            selected = verify_identity_placement(
                episode_dir,
                cut_id=cid,
                guest_namecard_start=float(event["t0"]),
                guest_namecard_end=float(event["t1"]),
                editorial_master=editorial_master,
            )
    except (IdentityPlacementError, KeyError, TypeError, ValueError) as exc:
        raise SystemExit(f"guest identity-card placement 驗證失敗：{exc}") from exc
    if selected is None:  # pragma: no cover - guarded by guest_cards
        raise SystemExit("guest identity-card placement 驗證沒有產生 selection")
    return selected.identity()


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


def _scan_content_gaps(
    events: list[dict], dur: float, srt: list[dict], threshold: float
) -> list[dict]:
    """素材真空段掃描（修修 2026-08-04：「情緒是建構的那段太空」）。

    只算**強事件**（素材/卡片）——cut 是弱事件（同組談話頭來回切，觀眾對
    「有畫面變化」的感受撐不了一分鐘以上），算進去會讓長片 gap 偵測永遠
    沉默：導播每幾秒換鏡一次，14s 門檻掃不到 127→291s 的 164 秒素材真空。
    每段真空附該區間 transcript——下游（LLM/人工）用它找「描述情境」的句子
    提案 stock，走 hero 同款 gate：提案 → 修修裁決 → 才抓素材上軌。"""
    gaps = []
    strong = [
        e for e in events if e["type"] != "cut" and not e["type"].startswith(GAP_EXCLUDE_PREFIX)
    ]

    def _one(f: float, t: float) -> dict:
        text = " ".join(s["text"] for s in srt if s["t0"] < t and s["t1"] > f)
        return {"from": round(f, 1), "to": round(t, 1), "sec": round(t - f, 1), "transcript": text}

    cursor = 0.0
    for e in strong:
        if e["t0"] - cursor > threshold:
            gaps.append(_one(cursor, e["t0"]))
        cursor = max(cursor, e["t1"])
    if dur - cursor > threshold:
        gaps.append(_one(cursor, dur))
    return gaps


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


def build_packet(
    episode_dir: Path,
    cid: str,
    *,
    finished_manifest_cut_ids: set[str] | None = None,
) -> dict:
    from build_resolve_project import connect_resolve

    master = _open_editorial_master(episode_dir)
    master_identity = master.identity()
    c, w = _load_winner(episode_dir, cid, master_identity)
    broll_plan_path = episode_dir / TIGHTEN_DIR / f"{cid}_broll.json"
    broll_plan = json.loads(broll_plan_path.read_text(encoding="utf-8"))["items"]
    stock_video_lineage = None
    if c.get("format") == "long":
        try:
            stock_video_receipt = verify_broll_receipt(
                episode_dir, cid, "long", broll_plan, master_identity
            )
        except BrollContractError as exc:
            raise SystemExit(f"finished review Stock Video gate 失敗：{exc}") from exc
        stock_video_lineage = broll_receipt_identity(stock_video_receipt)
    events = _load_events(episode_dir, cid)
    if not events:
        raise SystemExit(f"{cid} 沒有任何事件 JSON——先跑 broll/titles 企劃")
    identity_placement_lineage = _verify_guest_identity_events(episode_dir, cid, events, master)
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
    fps = float(project.GetSetting("timelineFrameRate"))
    _verify_materialization_receipt(
        episode_dir,
        cid,
        timeline,
        master_identity,
        cut_format=str(c["format"]),
        t_start=float(c["t_start"]),
        t_end=float(c["t_end"]),
        fps=fps,
    )
    project.SetCurrentTimeline(timeline)
    dur = (timeline.GetEndFrame() - timeline.GetStartFrame()) / fps

    # 舊輪產物移入歷史，而非刪除。事件增減會讓抽幀編號位移；主 review
    # 目錄只留本輪，但任何先前 preview 都能回看／回復。
    stale_files = [
        *out_dir.glob("ev_*.png"),
        out_dir / "contact_sheet.png",
        *out_dir.glob("contact_sheet_*.png"),
        *out_dir.glob("*_preview*.mp4"),
        *out_dir.glob("*_raw*.mp4"),
    ]
    archive_dir = out_dir / "history" / time.strftime("%Y%m%dT%H%M%S")
    for stale in stale_files:
        if not stale.exists():
            continue
        try:
            archive_dir.mkdir(parents=True, exist_ok=True)
            stale.replace(archive_dir / stale.name)
        except PermissionError:
            # 修修正在播那支 preview → 檔案被鎖。不擋整條重建，
            # 後續 ffmpeg 會覆寫同名檔（覆寫比刪除寬容）
            logger.warning("無法封存（可能正在播放）：%s", stale.name)

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
    title_choreography = _uses_transcript_choreography(episode_dir, cid)
    if srts and fcfg["burn_srt"] and not title_choreography:
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
            archive_dir.mkdir(parents=True, exist_ok=True)
            preview.replace(archive_dir / preview.name)
            preview = burned
        else:
            logger.warning("字幕燒錄失敗，preview 無字幕: %s", proc.stderr[-200:])
    elif srts:
        # 長片：Resolve 已把字幕軌燒進來，只把 raw 改成交付檔名
        named = out_dir / f"{FORMAT_LABEL[c['format']]}{w['rank']}_preview.mp4"
        if named.exists():
            archive_dir.mkdir(parents=True, exist_ok=True)
            named.replace(archive_dir / named.name)
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
                    "-ss",
                    f"{ss:.2f}",
                    "-t",
                    f"{span:.2f}",
                    "-i",
                    str(preview),
                    "-vf",
                    f"fps=1,scale=180:-1,tile=10x{rows}",
                    "-frames:v",
                    "1",
                    str(out),
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

    # 素材真空段（長片 only）：強事件之間 > content_gap_sec 的區間 + transcript
    content_gaps = (
        _scan_content_gaps(events, dur, srt, fcfg["content_gap_sec"])
        if fcfg["content_gap_sec"]
        else []
    )

    packet = {
        "editorial_master_lineage": master_identity,
        "stock_video_lineage": stock_video_lineage,
        "identity_placement_lineage": identity_placement_lineage,
        "timeline": label,
        "duration_sec": round(dur, 1),
        "events_per_min": round(len(events) / (dur / 60), 1),
        # 換鏡是「弱事件」（同一組談話頭來回切）——素材/卡片才是「強事件」。
        # 兩個數字一起看：只有換鏡撐場的段落，強事件密度會露餡
        "content_per_min": round(len([e for e in events if e["type"] != "cut"]) / (dur / 60), 1),
        "events": events,
        "gap_threshold_sec": fcfg["gap_sec"],
        "gaps": gaps,
        "content_gap_threshold_sec": fcfg["content_gap_sec"],
        "content_gaps": content_gaps,
        "contact_sheets": sheets,
        "frames": frames,
        "srt": srt,
        "preview": preview.name,
        "subtitle_render_mode": "title_choreography" if title_choreography else "srt",
    }
    (out_dir / "events.json").write_text(
        json.dumps(packet, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    finished_manifest = None
    if c.get("format") == "long":
        # Long finished-review is contract-backed.  Rebuild the deterministic
        # inventory immediately after the packet lands so the Bridge never
        # depends on a hand-authored manifest.
        from build_finished_review_manifest import build_manifest

        finished_manifest = str(
            build_manifest(
                episode_dir,
                review_format="long",
                cut_ids=finished_manifest_cut_ids,
            )
        )
    return {
        "status": "packet",
        "dir": str(out_dir),
        "events": len(events),
        "events_per_min": packet["events_per_min"],
        "content_per_min": packet["content_per_min"],
        "gap_threshold_sec": fcfg["gap_sec"],
        "gaps": gaps,
        # transcript 不進 summary（stdout 會爆）——只報時間窗，全文在 packet
        "content_gaps": [{k: g[k] for k in ("from", "to", "sec")} for g in content_gaps],
        "contact_sheets": [x["file"] for x in sheets],
        "frames": len(frames),
        "finished_manifest": finished_manifest,
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
