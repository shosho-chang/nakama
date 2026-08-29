"""short-broll：短片 B-roll / 貼紙 / 概念卡 — 對標鐘穎波旬集（修修 2026-07-27 通宵裁決）。

波旬範本的五種素材語彙（`docs` 見 SKILL Step 7.6）：
1. stock video 切出（比喻具象化：黑暗隧道剪影、山頂雲海）→ video track 2 全幅
2. stock photo（Ken Burns 慢推）→ video track 2 全幅
3. 雙貼紙（irasutoya 風插畫貼講者兩側，講故事時）→ hyperframes alpha → track 4
4. 概念圖解卡（兩插畫+雙向箭頭+橘塊標題）→ hyperframes alpha → track 4
5. 情境 icon 動畫（圖像進場、淘汰、聚焦、位移）→ hyperframes alpha → track 4

輸入：highlights/tighten/<id>_broll.json
    {"items": [
      {"t0": 10.0, "t1": 13.5, "kind": "video", "slug": "doomscroll-dark", ...},
      {"t0": 0.8, "t1": 3.9, "kind": "photo", "slug": "science-journal", ...},
      {"t0": 2.2, "t1": 8.7, "kind": "sticker", "slug": "s1-x",
       "stickers": [{"file": "brain.png", "side": "left"}, ...]},
      {"t0": 40.1, "t1": 44.0, "kind": "concept", "slug": "causal", "comp": "concept_card",
       "vars": {"title": "相關 ≠ 因果", "left_icon": "smartphone.png", ...}}
      {"t0": 10.0, "t1": 13.2, "kind": "icon_motion", "slug": "many-to-one",
       "icons": [{"id": "a", "file": "sushi-a.png", "x": 15, "y": 42, "size": 12}],
       "steps": [{"at": 0, "op": "enter", "ids": ["a"]}, ...]}
    ]}
    t0/t1 = （緊·導播）timeline 秒。素材檔在 episode assets/broll/<slug>.*、
    貼紙在 assets/stickers/*.png（irasutoya s800，透明背景）。

    Editorial Master 若在已知局部區間留錯機位，可加入 video-only camera correction；
    role 可為 host/guest/wide，來源必須是 episode-local Video/ 內對應機位，不重算
    數十 GB raw camera 的 hash，且不改 Master audio：
      {"t0": 0.0, "t1": 20.933, "kind": "camera-correction",
       "slug": "opening-host-camera-1", "subject_role": "host",
       "source_path": "Video/1_CAMERA 1.mp4", "src_in": 1260.700,
       "note": "主持人開場提問"}

機制與教訓：
- 影像素材 AppendToTimeline 後設 ZoomX/Y 填滿 1080×1920（fit 語意同 director：
  Resolve 先 fit 再 zoom，fill 倍率 = max(canvas/fit_w, canvas/fit_h)）
- photo 的 Ken Burns 走 Fusion Transform 線性 Size 關鍵影格（punch zoom 同機制；
  ⚠️ Center 是位置不是支點，勿踩）
- 貼紙/概念卡圖片以 data URI 進 hyperframes variables——episode 素材不進
  repo composition assets；vars.json sidecar 兼作 render 紀錄
- 軌道契約：track 1 主鏡、track 2 開場第二機 + B-roll、track 3 punch 卡、
  track 4 貼紙/概念卡。與 titles 相同的冪等清場（名稱前綴比對）
- 執行順序：director → broll → titles（director 重建會洗掉上層軌）

用法：
    python scripts/run_short_broll.py <episode> --id punch-S1 [--stills <dir>]
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import os
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
    build_authoritative_broll_receipt,
    receipt_path,
    write_broll_receipt,
)
from agents.brook.script_video.identity_placement import (  # noqa: E402
    IdentityPlacementError,
    verify_identity_placement,
)
from shared.highlight_materialization import (  # noqa: E402
    HighlightSource,
    verify_materialization_receipt,
)

logger = logging.getLogger("short_broll")

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPS = {
    "sticker_pair": REPO_ROOT / "video" / "compositions" / "sticker_pair",
    "icon_choreography": REPO_ROOT / "video" / "compositions" / "sticker_pair",
    "concept_card": REPO_ROOT / "video" / "compositions" / "concept_card",
    # 章節籤（修修 2026-08-04 grill：長片證據驅動語彙）——長片專屬，
    # 只有 *_wide.html；短片誤用會在 _card_hash 讀檔時 fail loud
    "chapter_label": REPO_ROOT / "video" / "compositions" / "chapter_label",
    # 滿版章節轉場（Brook script_video 既有 composition，原生 1920×1080——
    # 檔名沒有 _wide，走 _comp_file 的 fallback）
    "transition_title": REPO_ROOT / "video" / "compositions" / "transition_title",
}


def _comp_file(comp: str, suffix: str) -> str:
    """composition 檔名解析：優先 {comp}{suffix}.html；不存在時退回 {comp}.html
    （原生 16:9 的 Brook composition 如 transition_title 沒有 _wide 變體）。"""
    if (COMPS[comp] / "compositions" / f"{comp}{suffix}.html").exists():
        return f"{comp}{suffix}.html"
    return f"{comp}.html"


CARDS_DIR = "highlights/tighten/cards"
BROLL_TRACK = 2
CARD_TRACK = 4
# 品牌 badge loop（修修 2026-08-04：左下角 logo 小動畫，全片循環）——
# 預合成好的 16:9 alpha 短 loop（ffmpeg scale+pad 定位，不靠 Resolve
# transform 的座標換算），逐 loop 鋪滿 timeline
BADGE_TRACK = 5
CANVAS_W, CANVAS_H = 1080, 1920
# 格式參數（修修 2026-08-03 長片線）。短片欄 = 既有已驗收值。
# 長片畫布 16:9，貼紙/概念卡走 *_wide composition。
FORMAT_BROLL = {
    "short": {"canvas": (1080, 1920), "comp_suffix": ""},
    "long": {"canvas": (1920, 1080), "comp_suffix": "_wide"},
}
# composition data-duration 上限（進場+待機+退場都要收在裡面）
COMP_MAX_SEC = {
    "sticker_pair": 8.0,
    "icon_choreography": 8.0,
    "concept_card": 6.0,
    "chapter_label": 8.0,
    "transition_title": 4.0,
}  # _wide 版 data-duration 4s、show_sec 控退場
# 滿版紙紋底（修修 2026-08-04 B2 定版）：transition_title 的 paper 系是透明
# 字卡，疊 beige paper texture motion bg 預合成成滿版——滿版蓋掉畫面，字
# 才不壓臉（HTML 內嵌 <video> 在 hyperframes 渲染器不可靠，texture 走 ffmpeg）。
# 源：E:\data\subtle-animated-beige-paper-texture-background-loo-…mov（修修抓的
# Envato 素材，預縮 1080p 落 episode assets）。scrim style 自帶半透明底、不合成。
PAPER_TEXTURE = "paper-texture.mp4"
KENBURNS_SCALE = 1.06  # photo 慢推幅度（波旬語彙：照片不能死著）


def _data_uri(path: Path) -> str:
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _validate_icon_legibility(index: int, item: dict) -> None:
    """A semantic icon scene needs one readable subject, not literal clutter."""
    primary_id = str(item.get("primary_icon_id", "")).strip()
    if not primary_id:
        return  # legacy choreography remains compatible until explicitly migrated
    icons = item.get("icons") or []
    if len(icons) > 3:
        raise SystemExit(f"item {index} 有主體的 icon_motion 最多 3 個 icons")
    by_id = {str(icon.get("id", "")).strip(): icon for icon in icons}
    if primary_id not in by_id:
        raise SystemExit(f"item {index} primary_icon_id={primary_id!r} 不在 icons")
    primary_size = float(by_id[primary_id].get("size", 0))
    if primary_size < 18:
        raise SystemExit(f"item {index} 主 icon 至少 18% 畫面寬，目前 {primary_size:g}%")


def _card_hash(comp: str, variables: dict, suffix: str = "") -> str:
    """comp = composition 目錄鍵；suffix = 畫幅變體（長片 "_wide"，同目錄不同檔）。"""
    comp_digest = hashlib.md5(
        (COMPS[comp] / "compositions" / _comp_file(comp, suffix)).read_bytes()
    ).hexdigest()[:8]
    payload = json.dumps(variables, ensure_ascii=False, sort_keys=True) + suffix + comp_digest
    return hashlib.md5(payload.encode("utf-8")).hexdigest()[:10]


def _render_card(comp: str, variables: dict, out_path: Path, suffix: str = "") -> None:
    """npx hyperframes render → ProRes 4444 alpha（Windows 走 --variables-file）。"""
    vars_file = out_path.with_suffix(".vars.json")
    vars_file.write_text(json.dumps(variables, ensure_ascii=False, indent=1), encoding="utf-8")
    cmd = (
        f"npx --yes hyperframes@0.7.72 render . -c compositions/{_comp_file(comp, suffix)} "
        f'-o "{out_path}" --format mov -q standard --quiet --no-browser-gpu '
        f'--variables-file "{vars_file}"'
    )
    logger.info("render %s: %s", comp, out_path.name)
    for attempt in (1, 2):
        proc = subprocess.run(
            cmd, shell=True, cwd=str(COMPS[comp]), capture_output=True, text=True, encoding="utf-8"
        )
        if proc.returncode == 0 and out_path.exists():
            return
        # 連續多次 render 後偶發空 stderr 失敗（2026-08-04 兩輪實測皆在第 5 次
        # 連續 render 掛），非內容問題——冷卻後重試一次
        logger.warning("hyperframes render 失敗（第 %d 次），重試…", attempt)
        time.sleep(5)
    raise SystemExit(f"hyperframes render 失敗×2: {(proc.stderr or '')[-400:]}")


def _composite_texture(
    card_mov: Path, tex: Path, out_path: Path, show_sec: float, card_sec: float
) -> None:
    """透明字卡疊滿版紙紋底 → prores4444。texture alpha fade 進退場透出實拍。

    峰值 alpha 0.92（修修七輪：「白色的背景稍微變成半透明」）——滿版期間
    隱約透出實拍，與名牌/hero 的半透明紙白卡同一套材質語彙。"""
    fade_out = max(0.4, show_sec - 0.32)
    fc = (
        "[0:v]fps=30,format=yuva420p,colorchannelmixer=aa=0.92,"
        f"fade=t=in:st=0:d=0.32:alpha=1,fade=t=out:st={fade_out:.2f}:d=0.32:alpha=1[tex];"
        "[tex][1:v]overlay=0:0,format=yuva444p10le"
    )
    proc = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-stream_loop",
            "-1",
            "-i",
            str(tex),
            "-i",
            str(card_mov),
            "-filter_complex",
            fc,
            "-t",
            f"{card_sec:.2f}",
            "-c:v",
            "prores_ks",
            "-profile:v",
            "4444",
            "-pix_fmt",
            "yuva444p10le",
            str(out_path),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not out_path.exists():
        raise SystemExit(f"紙紋底合成失敗: {(proc.stderr or '')[-300:]}")


def _probe_meta(path: Path) -> tuple[float, float]:
    """ffprobe 取 (SAR, 源 fps)。兩個都是十七輪血案：

    - SAR：meditation.mov 1080×1920 但 SAR 109:120 → 顯示寬僅 981px，
      只看像素尺寸算出 zoom=1.0 → 左右各留一條縫（Resolve 依 SAR 顯示）
    - fps：AppendToTimeline 的 startFrame/endFrame 是**源幀**——素材 fps
      24–60 不一，用 timeline 30fps 換算會讓長度縮水/膨脹（exam-students
      50fps → 3.8s 變 2.3s，盲審抓到）
    """
    sar, fps = 1.0, 0.0
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=sample_aspect_ratio,r_frame_rate",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        parts = out.split(",")
        # 逐欄位獨立 parse——SAR 常見 "N/A"（方形像素），一起 parse 會把
        # fps 也拖下水回退 30fps（十八輪：teen-phone 60fps 又縮回一半）
        if parts:
            try:
                n, d = parts[0].split(":")
                sar = int(n) / int(d) if int(d) else 1.0
            except ValueError:
                sar = 1.0
        if len(parts) >= 2:
            try:
                fn, fd = parts[1].split("/")
                fps = int(fn) / int(fd) if int(fd) else 0.0
            except ValueError:
                fps = 0.0
    except (OSError, subprocess.TimeoutExpired):
        pass
    return sar, fps


def _probe_dur(path: Path) -> float:
    """ffprobe 取影片長度（秒）——badge loop 鋪軌用；失敗回 4.0（badge 慣例長度）。"""
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        return float(out)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return 4.0


def _fill_zoom(res: str, sar: float = 1.0, canvas: tuple[int, int] | None = None) -> float:
    """素材解析度 "WxH"（+SAR）→ 填滿畫布的 Zoom 倍率（Resolve 先 fit 再 zoom）。

    ⚠️ 長片（16:9 畫布）餵直式素材時倍率會衝到 ~3.16——那不是壞掉，是
    「只看得到直式素材中間那條橫帶」。短片線下載的 stock 全是 Vertical
    取向，拿來鋪長片必須逐支看 `--stills` 確認裁完主體還在。
    """
    cw, ch = canvas or (CANVAS_W, CANVAS_H)
    try:
        w, h = (int(x) for x in res.split("x"))
    except (ValueError, AttributeError):
        return 1.0
    w = w * (sar if sar > 0 else 1.0)  # 顯示寬度
    fit = min(cw / w, ch / h)
    return max(cw / (w * fit), ch / (h * fit))


def _should_fill_media(*, orchestrated: bool, kind: str) -> bool:
    """Legacy footage fills the frame; agent-approved Long footage keeps its full composition."""

    return not orchestrated and kind in {"video", "photo"}


def _needs_transition_texture(job: dict) -> bool:
    """Paper transition renders carry alpha and need their opaque paper motion background."""

    return job.get("comp") == "transition_title" and str(
        job.get("vars", {}).get("style", "")
    ).startswith("paper")


def _guest_namecard_job(
    episode_dir: Path,
    cid: str,
    fmt: str,
    item: dict,
    index: int,
) -> dict:
    """Turn a sealed guest-namecard recipe event into an existing renderer job."""

    if fmt != "long":
        raise SystemExit("guest-namecard 目前只支援 long highlight 16:9 composition")
    try:
        t0, t1 = float(item["t0"]), float(item["t1"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SystemExit(f"item {index} guest-namecard timestamps 不合法") from exc
    span = round(t1 - t0, 2)
    comp = "chapter_label"
    if span < 0.8 or span > COMP_MAX_SEC[comp] - 0.3:
        raise SystemExit(
            f"item {index} guest-namecard {span}s 不在 [0.8, {COMP_MAX_SEC[comp] - 0.3}]"
        )
    try:
        placement = verify_identity_placement(
            episode_dir,
            cut_id=cid,
            guest_namecard_start=t0,
            guest_namecard_end=t1,
        )
    except IdentityPlacementError as exc:
        raise SystemExit(f"item {index} guest-namecard placement 驗證失敗：{exc}") from exc
    if item.get("identity_placement") != placement.identity():
        raise SystemExit(f"item {index} guest-namecard identity lineage 已過期")
    name = item.get("name")
    title = item.get("title")
    style = item.get("style", "paper")
    if not isinstance(name, str) or not name.strip() or name != name.strip():
        raise SystemExit(f"item {index} guest-namecard 缺少或未 trim name")
    if not isinstance(title, str) or not title.strip() or title != title.strip():
        raise SystemExit(f"item {index} guest-namecard 缺少或未 trim title")
    if style not in {"paper", "ink", "orange"}:
        raise SystemExit(f"item {index} guest-namecard style 不合法")
    return {
        "comp": comp,
        "vars": {
            "show_sec": span,
            "label": name,
            "sub": title,
            "align": "left",
            "style": style,
        },
        "t0": t0,
        "span": span,
        "i": index,
        "kind": "guest-namecard",
    }


def _ken_burns(item, span_sec: float, fps: float, zoom_hi: float) -> bool:
    """photo 慢推：Fusion Transform 線性 Size 1.0→zoom_hi（Pivot 預設畫面中心）。"""
    comp = item.GetFusionCompByIndex(1) if item.GetFusionCompCount() > 0 else item.AddFusionComp()
    if comp is None:
        return False
    frames = round(span_sec * fps, 2)
    lua = f"""
local ok, err = pcall(function()
  local mi = comp:FindToolByID("MediaIn")
  local mo = comp:FindToolByID("MediaOut")
  local xf = comp:AddTool("Transform", -32768, -32768)
  xf:SetAttrs({{TOOLS_Name = "KenBurns"}})
  xf.Input = mi.Output
  mo.Input = xf.Output
  xf.Size = comp:BezierSpline()
  xf:SetInput("Size", 1.0, 0)
  xf:SetInput("Size", {zoom_hi}, {frames})
end)
"""
    comp.Execute(lua)
    return True


def _verify_director_materialization(
    episode_dir: Path,
    cid: str,
    candidate: dict,
    timeline,
    master,
    fps: float,
) -> dict:
    """Bind the live director Timeline to the current candidate and Master."""

    try:
        return verify_materialization_receipt(
            episode_dir,
            cid,
            source=HighlightSource(
                srt_path=master.srt_path,
                media_path=master.media_path,
                lineage=master.identity(),
            ),
            timeline=timeline,
            expected_timeline_name=timeline.GetName(),
            expected_format=str(candidate["format"]),
            expected_source_range={
                "start_sec": float(candidate["t_start"]),
                "end_sec": float(candidate["t_end"]),
                "start_frame": int(float(candidate["t_start"]) * fps),
                "end_frame": int(float(candidate["t_end"]) * fps),
            },
        )
    except EditorialMasterContractError as exc:
        raise SystemExit(f"B-roll materialization receipt 驗證失敗：{exc}") from exc


def validate_plan(episode_dir: Path, cid: str) -> dict:
    """Read-only Stock Video preflight for agent-authored B-roll plans."""

    master = _open_editorial_master(episode_dir)
    candidate, _winner = _load_winner(episode_dir, cid, master.identity())
    path = episode_dir / TIGHTEN_DIR / f"{cid}_broll.json"
    if not path.is_file():
        raise SystemExit(f"{path} 不存在——agent 先從 tight SRT 規劃素材點")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        items = payload["items"]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise SystemExit(f"{path} 不是合法 B-roll plan") from exc
    try:
        receipt = build_authoritative_broll_receipt(
            episode_dir,
            cid,
            str(candidate["format"]),
            items,
            master.identity(),
            editorial_master=master,
        )
    except BrollContractError as exc:
        raise SystemExit(f"Stock Video production gate 失敗：{exc}") from exc
    return {
        "status": "plan-valid",
        "cut_id": cid,
        "format": candidate["format"],
        "stock_video_count": receipt["stock_video_count"],
        "stock_videos": receipt["stock_videos"],
        "content_hash": receipt["content_hash"],
    }


def emit_audited_recipe(
    episode_dir: Path,
    cid: str,
    materializations: list[dict] | tuple[dict, ...],
    *,
    output_dir: Path | None = None,
) -> Path:
    """Deterministically project accepted DP rows into the B-roll renderer schema."""

    from agents.brook.script_video.highlight_visual_pipeline import (
        HighlightVisualContractError,
        validate_materialization_projection,
    )

    structural: list[dict] = []
    current = episode_dir / TIGHTEN_DIR / f"{cid}_broll.json"
    if current.is_file():
        try:
            old_items = json.loads(current.read_text(encoding="utf-8"))["items"]
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise BrollContractError(
                f"既有 B-roll recipe 無法保留 structural rows：{current}"
            ) from exc
        if not isinstance(old_items, list):
            raise BrollContractError("既有 B-roll recipe items 必須是 array")
        structural = [
            dict(item)
            for item in old_items
            if isinstance(item, dict)
            and (
                str(item.get("kind") or "").strip().lower()
                in {"camera-correction", "guest-namecard", "badge"}
                or (
                    str(item.get("kind") or "").strip().lower() == "concept"
                    and str(item.get("slug") or "").strip().lower() == "guest-namecard"
                )
            )
        ]
    content: list[dict] = []
    for index, raw in enumerate(materializations):
        try:
            projection = validate_materialization_projection(
                raw, label=f"materializations[{index}]"
            )
        except HighlightVisualContractError as exc:
            raise BrollContractError(f"DP materialization schema 不合法：{exc}") from exc
        target = projection["target_lane"]
        implementation = projection["implementation_kind"]
        if target == "title_track3":
            continue
        if target == "broll_track2":
            kind = "video" if implementation == "stock_video" else "photo"
        elif target == "content_card_track4":
            kind = "sticker" if implementation == "sticker_pair" else "concept"
        else:
            raise BrollContractError(f"DP materialization target_lane 不合法：{target}")
        row = {
            "kind": kind,
            "slug": projection["materialization_id"],
            "t0": projection["t0"],
            "t1": projection["t1"],
            "src_in": projection["source_range"]["start_sec"],
            "source_range": projection["source_range"],
            "media_path": projection["media"]["path"],
            "on_screen_text": projection["on_screen_text"],
            "provenance": projection["provenance"],
            "render_spec": projection["render_spec"],
            "visual_materialization": projection,
        }
        if target == "content_card_track4":
            row["comp"] = implementation
            row["vars"] = projection["render_spec"]["render_params"]
        content.append(row)
    destination_dir = output_dir or (episode_dir / TIGHTEN_DIR)
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{cid}_broll.json"
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps({"items": [*structural, *content]}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, destination)
    return destination


def _camera_correction_job(episode_dir: Path, item: dict, *, index: int) -> dict:
    """Validate one video-only A-roll correction without hashing a raw camera file."""

    role = str(item.get("subject_role") or "")
    if role not in {"host", "guest", "wide"}:
        raise SystemExit(f"item {index} camera-correction subject_role 必須是 host/guest/wide")
    raw_source = item.get("source_path")
    if not isinstance(raw_source, str) or not raw_source.strip():
        raise SystemExit(f"item {index} camera-correction source_path 不合法")
    video_root = (episode_dir / "Video").resolve()
    source = (episode_dir / raw_source).resolve()
    try:
        source.relative_to(video_root)
    except ValueError as exc:
        raise SystemExit(
            f"item {index} camera-correction source_path 必須是 episode-local Video 檔案"
        ) from exc
    if not source.is_file():
        raise SystemExit(f"item {index} camera-correction source 不存在：{raw_source}")
    from run_short_director import _load_cfg

    cfg = _load_cfg(episode_dir, "long")
    expected_name = (
        cfg.get("cams", {}).get("0")
        if role == "host"
        else cfg.get("cams", {}).get("1")
        if role == "guest"
        else cfg.get("wide_cam")
    )
    if not isinstance(expected_name, str) or source.name.casefold() != expected_name.casefold():
        raise SystemExit(f"item {index} camera-correction {role} 必須使用 {expected_name}")
    try:
        t0, t1, src_in = float(item["t0"]), float(item["t1"]), float(item["src_in"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SystemExit(f"item {index} camera-correction timing 不合法") from exc
    if t0 < 0 or t1 <= t0:
        raise SystemExit(f"item {index} camera-correction timeline range 不合法")
    if src_in < 0:
        raise SystemExit(f"item {index} camera-correction src_in 不合法")
    return {
        "path": source,
        "t0": t0,
        "span": t1 - t0,
        "kind": "camera-correction",
        "i": index,
        "src_in": src_in,
        "subject_role": role,
    }


def _validate_camera_correction_ranges(items: list[dict], *, label: str) -> None:
    """Reject ambiguous camera layers while allowing deliberate adjacent cuts."""

    previous_end = 0.0
    for index, item in enumerate(sorted(items, key=lambda row: float(row.get("t0", -1)))):
        try:
            t0, t1 = float(item["t0"]), float(item["t1"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SystemExit(f"{label} camera-correction {index} timing 不合法") from exc
        if t0 < previous_end - 1e-6:
            raise SystemExit(f"{label} camera-correction {index} 與前一段重疊")
        previous_end = t1


def _media_append_spec(job: dict, clip, *, fps: float, tl_start: int) -> dict:
    """Build the V2 overlay-track video append spec for footage or camera correction."""

    source_fps = job.get("src_fps") or fps
    start_frame = int(job["src_in"] * source_fps)
    end_frame = (
        start_frame + int(round(job["span"] * fps))
        if job.get("kind") == "photo"
        else int((job["src_in"] + job["span"]) * source_fps)
    )
    return {
        "mediaPoolItem": clip,
        "mediaType": 1,
        "trackIndex": BROLL_TRACK,
        "recordFrame": tl_start + int(job["t0"] * fps),
        "startFrame": start_frame,
        "endFrame": end_frame,
    }


def _configure_photo_hold(clip, *, span: float, fps: float) -> int:
    """Set and verify the Resolve still-image duration used by the timeline hold."""

    frames = int(round(span * fps))
    if frames <= 0:
        raise SystemExit("photo hold duration 必須至少一格")
    if clip.SetClipProperty("Frames", str(frames)) is not True:
        raise SystemExit("Resolve 不支援設定 photo hold Frames")
    actual = clip.GetClipProperty("Frames")
    try:
        actual_frames = int(float(str(actual)))
    except (TypeError, ValueError) as exc:
        raise SystemExit("Resolve 無法回讀 photo hold Frames") from exc
    if actual_frames != frames:
        raise SystemExit(
            f"Resolve photo hold Frames 未生效：expected={frames}, actual={actual_frames}"
        )
    return frames


def _load_camera_correction_jobs(episode_dir: Path, cid: str) -> list[dict]:
    """Read only structural camera rows; do not require the content-visual DP receipt."""

    plan_path = episode_dir / TIGHTEN_DIR / f"{cid}_broll.json"
    if not plan_path.is_file():
        raise SystemExit(f"{plan_path} 不存在")
    try:
        document = json.loads(plan_path.read_text(encoding="utf-8"))
        items = document["items"]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise SystemExit(f"camera-correction recipe 無法讀取：{plan_path}") from exc
    if not isinstance(items, list):
        raise SystemExit(f"camera-correction recipe items 必須是 array：{plan_path}")
    rows = [
        item for item in items if isinstance(item, dict) and item.get("kind") == "camera-correction"
    ]
    if not rows:
        raise SystemExit(f"{plan_path} 沒有 camera-correction")
    _validate_camera_correction_ranges(rows, label=cid)
    jobs: list[dict] = []
    for index, item in enumerate(rows):
        job = _camera_correction_job(episode_dir, item, index=index)
        job["sar"], job["src_fps"] = _probe_meta(job["path"])
        jobs.append(job)
    return jobs


def _camera_correction_master_baseline(timeline, master_media: Path) -> tuple[tuple, ...]:
    """Snapshot track-1 Master video/audio without inspecting upper overlay tracks."""

    expected = os.path.normcase(str(master_media.resolve()))
    snapshot: list[tuple] = []
    for track_type in ("video", "audio"):
        items = list(timeline.GetItemListInTrack(track_type, 1) or [])
        if not items:
            raise SystemExit(f"camera-correction: {track_type} track 1 是空的")
        for index, item in enumerate(items, 1):
            try:
                media = item.GetMediaPoolItem()
                actual = os.path.normcase(
                    str(Path(media.GetClipProperty("File Path") or "").resolve())
                )
                start, end = int(item.GetStart()), int(item.GetEnd())
            except (AttributeError, OSError, TypeError, ValueError) as exc:
                raise SystemExit(
                    f"camera-correction: {track_type} track 1 item {index} 無法驗證"
                ) from exc
            if actual != expected:
                raise SystemExit(
                    f"camera-correction: {track_type} track 1 item {index} 不是 Editorial Master"
                )
            snapshot.append((track_type, index, start, end, actual))
    return tuple(snapshot)


def apply_camera_corrections(episode_dir: Path, cid: str) -> dict:
    """Apply only video-track camera overrides to the existing canonical timeline.

    This path intentionally bypasses the content-visual Director/DP receipt because
    camera correction is a structural edit.  It still verifies the winner,
    Editorial Master and existing director materialization before mutating Resolve.
    """

    from build_resolve_project import connect_resolve

    master = _open_editorial_master(episode_dir)
    candidate, winner = _load_winner(episode_dir, cid, master.identity())
    if candidate.get("format", "short") != "long":
        raise SystemExit("camera-corrections-only 只允許 Long Highlight")
    jobs = _load_camera_correction_jobs(episode_dir, cid)

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
    label = f"{FORMAT_LABEL[candidate['format']]}{winner['rank']} - {candidate['title']}（緊·導播）"
    timeline = next(
        (
            project.GetTimelineByIndex(index)
            for index in range(1, project.GetTimelineCount() + 1)
            if project.GetTimelineByIndex(index)
            and project.GetTimelineByIndex(index).GetName() == label
        ),
        None,
    )
    if timeline is None:
        raise SystemExit(f"「{label}」不存在——先跑 run_short_director")
    project.SetCurrentTimeline(timeline)
    master_baseline = _camera_correction_master_baseline(timeline, master.media_path)

    tl_start = timeline.GetStartFrame()
    while timeline.GetTrackCount("video") < BROLL_TRACK:
        timeline.AddTrack("video")

    # Idempotency is deliberately local: remove only an existing raw-camera
    # overlay whose timeline range intersects one of the requested corrections.
    source_paths = {str(job["path"].resolve()).casefold() for job in jobs}
    requested_ranges = [
        (
            tl_start + int(job["t0"] * fps),
            tl_start + int((job["t0"] + job["span"]) * fps),
        )
        for job in jobs
    ]

    def stale_correction(item) -> bool:
        try:
            media = item.GetMediaPoolItem()
            path = str(media.GetClipProperty("File Path") or "").casefold() if media else ""
            item_range = (int(item.GetStart()), int(item.GetEnd()))
        except (AttributeError, TypeError, ValueError):
            return False
        return path in source_paths and any(
            item_range[0] < end and item_range[1] > start for start, end in requested_ranges
        )

    stale = [
        item
        for item in (timeline.GetItemListInTrack("video", BROLL_TRACK) or [])
        if stale_correction(item)
    ]
    if stale:
        timeline.DeleteClips(stale)

    occupied = [
        (int(item.GetStart()), int(item.GetEnd()))
        for item in (timeline.GetItemListInTrack("video", BROLL_TRACK) or [])
    ]
    for job, requested in zip(jobs, requested_ranges):
        if any(requested[0] < end and requested[1] > start for start, end in occupied):
            raise SystemExit(
                f"camera-correction {job['t0']:.3f}–{job['t0'] + job['span']:.3f}s "
                f"與 track {BROLL_TRACK} 既有畫面重疊"
            )

    camera_bin = next(
        (folder for folder in root.GetSubFolderList() if folder.GetName() == "Cams"), None
    ) or mp.AddSubFolder(root, "Cams")
    mp.SetCurrentFolder(camera_bin)
    made: list[dict] = []
    for job in jobs:
        clips = mp.ImportMedia([str(job["path"])]) or []
        if not clips:
            raise SystemExit(f"camera-correction 匯入失敗：{job['path']}")
        clip = clips[0]
        result = mp.AppendToTimeline([_media_append_spec(job, clip, fps=fps, tl_start=tl_start)])
        if not result or (isinstance(result, list) and result[0] is None):
            raise SystemExit(f"camera-correction 疊軌失敗 @{job['t0']:.3f}s")
        item = (timeline.GetItemListInTrack("video", BROLL_TRACK) or [])[-1]
        zoom = _fill_zoom(
            clip.GetClipProperty("Resolution"),
            job["sar"],
            tuple(FORMAT_BROLL["long"]["canvas"]),
        )
        if zoom > 1.001:
            item.SetProperty("ZoomX", zoom)
            item.SetProperty("ZoomY", zoom)
        made.append(
            {
                "role": job["subject_role"],
                "source": str(job["path"]),
                "t0": job["t0"],
                "t1": job["t0"] + job["span"],
                "src_in": job["src_in"],
            }
        )
    mp.SetCurrentFolder(root)
    if _camera_correction_master_baseline(timeline, master.media_path) != master_baseline:
        raise SystemExit("camera-correction 意外改動 Editorial Master track 1 baseline")
    if not pm.SaveProject():
        raise SystemExit("Resolve SaveProject 失敗")
    return {
        "status": "camera-corrections-applied",
        "timeline": label,
        "items": made,
        "audio_source": "editorial_master_unchanged",
    }


def apply(
    episode_dir: Path,
    cid: str,
    stills_dir: Path | None = None,
    *,
    orchestrator_timeline_name: str | None = None,
    orchestrator_timeline_uid: str | None = None,
    recipe_path: Path | None = None,
) -> dict:
    from build_resolve_project import connect_resolve

    orchestrated = orchestrator_timeline_name is not None or orchestrator_timeline_uid is not None
    if orchestrated:
        if not orchestrator_timeline_name or not orchestrator_timeline_uid:
            raise SystemExit("new orchestrator apply requires exact Timeline name and UID")
        master = c = w = broll_receipt = None
        fmt = "long"
    else:
        master = _open_editorial_master(episode_dir)
        c, w = _load_winner(episode_dir, cid, master.identity())
        fmt = c.get("format", "short")
    fcfg = FORMAT_BROLL[fmt]
    canvas = tuple(fcfg["canvas"])
    suffix = fcfg["comp_suffix"]
    broll_path = (
        Path(recipe_path)
        if recipe_path is not None
        else episode_dir / TIGHTEN_DIR / f"{cid}_broll.json"
    )
    if not broll_path.exists():
        raise SystemExit(f"{broll_path} 不存在——agent 先從 tight SRT 規劃素材點")
    items = json.loads(broll_path.read_text(encoding="utf-8"))["items"]
    if not orchestrated:
        try:
            broll_receipt = build_authoritative_broll_receipt(
                episode_dir,
                cid,
                str(fmt),
                items,
                master.identity(),
                editorial_master=master,
            )
        except BrollContractError as exc:
            raise SystemExit(f"Stock Video production gate 失敗：{exc}") from exc
    structural_kinds = {"camera-correction", "guest-namecard", "badge"}

    def _preserved_structural_kind(item: dict) -> str | None:
        kind = str(item.get("kind") or "")
        if kind in structural_kinds:
            return kind
        if kind == "concept" and str(item.get("slug") or "") == "guest-namecard":
            return "guest-namecard"
        return None

    structural_items = [
        (item, kind) for item in items if (kind := _preserved_structural_kind(item)) is not None
    ]
    render_items = sorted(
        (item for item in items if not orchestrated or _preserved_structural_kind(item) is None),
        key=lambda x: x["t0"],
    )
    assets_dir = episode_dir / "assets" / "broll"
    stickers_dir = episode_dir / "assets" / "stickers"
    cards_dir = episode_dir / CARDS_DIR
    cards_dir.mkdir(parents=True, exist_ok=True)

    # 1) 準備 jobs：媒體素材找檔、卡片素材 render（hash cache）
    media_jobs, card_jobs, badge_jobs = [], [], []
    camera_corrections = [
        item for item in render_items if str(item.get("kind") or "") == "camera-correction"
    ]
    _validate_camera_correction_ranges(camera_corrections, label=cid)
    for i, it in enumerate(render_items):
        t0, t1 = float(it["t0"]), float(it["t1"])
        span = round(t1 - t0, 2)
        kind = it["kind"]
        if span < 0.8:
            raise SystemExit(f"item {i}（{it.get('slug')}）只有 {span}s——太短，B-roll 至少 0.8s")
        if kind in ("video", "photo"):
            projection = it["visual_materialization"]
            selected = (episode_dir / projection["media"]["path"]).resolve()
            # Source trim is part of the audited projection; recipe-side drift
            # was rejected before job preparation.
            source_start = float(projection["source_range"]["start_sec"])
            sar, src_fps = _probe_meta(selected)
            media_jobs.append(
                {
                    "path": selected,
                    "t0": t0,
                    "span": span,
                    "kind": kind,
                    "i": i,
                    # src_in（秒）：素材源內起點偏移——素材開頭讀不懂/是廢畫面時跳過
                    # （首輪盲審：空錢包前 1s 是黑色皮件側面）
                    "src_in": source_start,
                    "sar": sar,
                    # photo 的「fps」無意義（jpg 會回報 25）——歸零走 timeline
                    # fps（搭配 clip Frames 設定）；probe 失敗同樣落回
                    "src_fps": 0.0 if kind == "photo" else src_fps,
                    "timeline_name": f"{cid}_broll_{projection['materialization_id']}",
                }
            )
        elif kind == "camera-correction":
            job = _camera_correction_job(episode_dir, it, index=i)
            job["sar"], job["src_fps"] = _probe_meta(job["path"])
            media_jobs.append(job)
        elif kind == "sticker":
            projection = it["visual_materialization"]
            card_jobs.append(
                {
                    "comp": "sticker_pair",
                    "vars": it["vars"],
                    "mov": (episode_dir / projection["media"]["path"]).resolve(),
                    "t0": t0,
                    "span": span,
                    "i": i,
                    "timeline_name": f"{cid}_broll_{projection['materialization_id']}",
                }
            )
        elif kind == "icon_motion":
            if fmt != "short":
                raise SystemExit(f"item {i} icon_motion 目前只支援 short 畫幅")
            comp = "icon_choreography"
            if span > COMP_MAX_SEC[comp] - 0.3:
                raise SystemExit(f"item {i} icon 動畫 {span}s 超過上限 {COMP_MAX_SEC[comp] - 0.3}s")
            icons = it.get("icons", [])
            if not 1 <= len(icons) <= 5:
                raise SystemExit(f"item {i} icon_motion 必須有 1–5 個 icons")
            _validate_icon_legibility(i, it)
            icon_ids = [str(icon.get("id", "")).strip() for icon in icons]
            if any(not icon_id for icon_id in icon_ids) or len(set(icon_ids)) != len(icon_ids):
                raise SystemExit(f"item {i} icons 的 id 必須非空且唯一")
            icon_vars = []
            for icon in icons:
                p = stickers_dir / str(icon["file"])
                if not p.exists():
                    raise SystemExit(f"assets/stickers/{icon['file']} 不存在")
                icon_vars.append(
                    {
                        "id": str(icon["id"]),
                        "src": _data_uri(p),
                        "x": float(icon.get("x", 50)),
                        "y": float(icon.get("y", 42)),
                        "size": float(icon.get("size", 12)),
                        "rotation": float(icon.get("rotation", 0)),
                    }
                )
            allowed_ops = {"enter", "fade", "move_to", "emphasis", "exit"}
            steps = it.get("steps", [])
            previous_at = -1.0
            for step in steps:
                at = float(step.get("at", -1))
                if at < previous_at or at < 0 or at > span:
                    raise SystemExit(f"item {i} steps.at 必須遞增且落在 0–{span}s")
                previous_at = at
                if step.get("op") not in allowed_ops:
                    raise SystemExit(f"item {i} step op={step.get('op')} 不合法")
                targets = step.get("ids", [step.get("id")])
                targets = [target for target in targets if target is not None]
                if any(str(target) not in icon_ids for target in targets):
                    raise SystemExit(f"item {i} step 指向不存在的 icon id")
            variables = {
                "show_sec": span,
                "icons_json": json.dumps(icon_vars, ensure_ascii=False),
                "steps_json": json.dumps(steps, ensure_ascii=False),
            }
            card_jobs.append({"comp": comp, "vars": variables, "t0": t0, "span": span, "i": i})
        elif kind == "concept":
            legacy_namecard = str(it.get("slug") or "").strip().lower() == "guest-namecard"
            if legacy_namecard:
                covering = [
                    correction
                    for correction in camera_corrections
                    if str(correction.get("subject_role") or "") == "guest"
                    and float(correction.get("t0", -1)) <= t0
                    and float(correction.get("t1", -1)) > t0
                ]
                if len(covering) != 1:
                    raise SystemExit(
                        f"item {i} legacy guest-namecard 必須由唯一 guest camera-correction "
                        "覆蓋起始畫面；新節目請改用 kind=guest-namecard quorum receipt"
                    )
            comp = it.get("comp", "concept_card")
            if comp not in COMPS:
                raise SystemExit(f"item {i} comp={comp} 不存在")
            if span > COMP_MAX_SEC[comp] - 0.3:
                raise SystemExit(f"item {i} 概念卡 {span}s 超過上限 {COMP_MAX_SEC[comp] - 0.3}s")
            if not legacy_namecard:
                projection = it["visual_materialization"]
                card_jobs.append(
                    {
                        "comp": comp,
                        "vars": it["vars"],
                        "mov": (episode_dir / projection["media"]["path"]).resolve(),
                        "t0": t0,
                        "span": span,
                        "i": i,
                        "timeline_name": f"{cid}_broll_{projection['materialization_id']}",
                    }
                )
                continue
            variables = {"show_sec": span}
            for k, v in it.get("vars", {}).items():
                if k.endswith("_icon"):
                    p = stickers_dir / v
                    if not p.exists():
                        raise SystemExit(f"assets/stickers/{v} 不存在")
                    variables[k.replace("_icon", "_src")] = _data_uri(p)
                else:
                    variables[k] = v
            card_jobs.append({"comp": comp, "vars": variables, "t0": t0, "span": span, "i": i})
        elif kind == "guest-namecard":
            card_jobs.append(_guest_namecard_job(episode_dir, cid, fmt, it, i))
        elif kind == "badge":
            hits = sorted(assets_dir.glob(f"{it['slug']}.*"))
            if not hits:
                raise SystemExit(f"assets/broll/{it['slug']}.* 不存在——先預合成 badge")
            _sar, src_fps = _probe_meta(hits[0])
            badge_jobs.append(
                {"path": hits[0], "t0": t0, "t1": t1, "src_fps": src_fps or 30.0, "i": i}
            )
        else:
            raise SystemExit(
                f"item {i} kind={kind} 不合法（video/photo/camera-correction/"
                "sticker/icon_motion/concept/guest-namecard/badge）"
            )

    for job in card_jobs:
        # Content-bearing HyperFrames clips were already rendered, selected and
        # visually audited by DP/Director.  Only structural namecards reach the
        # legacy renderer below.
        if "mov" in job:
            mov = Path(job["mov"])
        else:
            h = _card_hash(job["comp"], job["vars"], suffix)
            mov = cards_dir / f"{cid}_broll_{job['i']}_{h}.mov"
            if not mov.exists():
                # 編號無關的 cache 檢索：hash 相同=內容相同，插入新 item 造成的
                # 編號位移不重渲（2026-08-04：插 3 支 stock 讓 5 張章節籤全部
                # cache miss 白渲 10 分鐘）
                same_hash = sorted(cards_dir.glob(f"{cid}_broll_*_{h}.mov"))
                if same_hash:
                    logger.info("cache hit（編號位移）: %s → %s", same_hash[0].name, mov.name)
                    mov = same_hash[0]
                else:
                    _render_card(job["comp"], job["vars"], mov, suffix)
            else:
                logger.info("cache hit: %s", mov.name)
        # B2 定版：paper 系滿版轉場卡疊紙紋 motion bg（scrim 自帶底不合成）
        if _needs_transition_texture(job):
            tex = assets_dir / PAPER_TEXTURE
            if not tex.exists():
                raise SystemExit(f"assets/broll/{PAPER_TEXTURE} 不存在——先落紙紋底（見 SKILL.md）")
            stable_name = (
                str(job.get("timeline_name") or f"{cid}_broll_{job['i']}")
                .replace("/", "_")
                .replace("\\", "_")
            )
            tex_mov = cards_dir / f"{stable_name}_tex.mov"
            source_mtime = max(mov.stat().st_mtime_ns, tex.stat().st_mtime_ns)
            if not tex_mov.exists() or tex_mov.stat().st_mtime_ns < source_mtime:
                logger.info("紙紋底合成: %s", tex_mov.name)
                _composite_texture(
                    mov,
                    tex,
                    tex_mov,
                    float(job["vars"]["show_sec"]),
                    COMP_MAX_SEC[job["comp"]],
                )
            mov = tex_mov
        job["mov"] = mov

    # Re-open both trust roots before any Resolve access. CURRENT may switch
    # while jobs are prepared; an older audited generation must never apply.
    if not orchestrated:
        master = _open_editorial_master(episode_dir)
        c, w = _load_winner(episode_dir, cid, master.identity())
        try:
            fresh_broll_receipt = build_authoritative_broll_receipt(
                episode_dir,
                cid,
                str(c["format"]),
                items,
                master.identity(),
                editorial_master=master,
            )
        except BrollContractError as exc:
            raise SystemExit(f"Stock Video production gate 失敗：{exc}") from exc
        if fresh_broll_receipt != broll_receipt:
            raise SystemExit("Stock Video plan／素材在準備期間發生變更，未修改 Resolve")

    # 2) Resolve：匯入 + 疊軌
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
    director_label = (
        orchestrator_timeline_name
        if orchestrated
        else f"{FORMAT_LABEL[c['format']]}{w['rank']} - {c['title']}（緊·導播）"
    )
    director = None
    for i in range(1, project.GetTimelineCount() + 1):
        t = project.GetTimelineByIndex(i)
        if t and t.GetName() == director_label:
            director = t
            break
    if director is None:
        raise SystemExit(f"「{director_label}」不存在——先跑 run_short_director")
    if orchestrated:
        timeline_uid = None
        for method_name in ("GetUniqueId", "GetUniqueID"):
            method = getattr(director, method_name, None)
            value = method() if callable(method) else None
            if isinstance(value, str) and value.strip():
                timeline_uid = value.strip()
                break
        if timeline_uid != orchestrator_timeline_uid:
            raise SystemExit("new orchestrator target Timeline UID changed before B-roll apply")
    else:
        _verify_director_materialization(episode_dir, cid, c, director, master, fps)
    selected = project.SetCurrentTimeline(director)
    if orchestrated and selected is False:
        raise SystemExit("Resolve refused to select orchestrator target Timeline")
    if orchestrated:
        get_current = getattr(project, "GetCurrentTimeline", None)
        current = get_current() if callable(get_current) else director
        current_uid = None
        for method_name in ("GetUniqueId", "GetUniqueID"):
            method = getattr(current, method_name, None)
            value = method() if callable(method) else None
            if isinstance(value, str) and value.strip():
                current_uid = value.strip()
                break
        if current_uid != orchestrator_timeline_uid:
            raise SystemExit("Resolve current Timeline differs from orchestrator target UID")

    # 冪等清場（timeline items）：**媒體路徑歸屬判定**——本 timeline 上凡是
    # 媒體檔在 episode assets/broll/ 底下的 item 都是本 script 放的（開場分割
    # 是 COMBO 機位源，路徑不同），一律清掉再重疊。slug 改名不會再留孤兒
    # （十七輪：sports-car 換 slug 後舊 item 沒被 name 比對清到）。
    # 卡片 item 用 <cid>_broll_ 名稱前綴（每 cid 專屬，不跨短片）。
    assets_prefix = str(assets_dir.resolve()).lower()
    correction_paths = {
        str(job["path"].resolve()).casefold()
        for job in media_jobs
        if job["kind"] == "camera-correction"
    }

    protected_tracks = {
        "camera-correction": BROLL_TRACK,
        "guest-namecard": CARD_TRACK,
        "badge": BADGE_TRACK,
    }
    protected_ranges = [
        (
            kind,
            protected_tracks[kind],
            int(float(item["t0"]) * fps),
            int(float(item["t1"]) * fps),
        )
        for item, kind in structural_items
    ]
    protect_tl_start = director.GetStartFrame()

    def _ours(it, track_index: int) -> bool:
        if orchestrated:
            item_start = it.GetStart() - protect_tl_start
            item_end = it.GetEnd() - protect_tl_start
            if any(
                track_index == protected_track
                and (
                    (
                        kind == "badge"
                        and item_start >= protected_start - 2
                        and item_end <= protected_end + 2
                    )
                    or (
                        kind != "badge"
                        and abs(item_start - protected_start) <= 2
                        and abs(item_end - protected_end) <= 2
                    )
                )
                for kind, protected_track, protected_start, protected_end in protected_ranges
            ):
                return False
        if (it.GetName() or "").startswith(f"{cid}_broll_"):
            return True
        try:
            mpi = it.GetMediaPoolItem()
            fp = (mpi.GetClipProperty("File Path") or "") if mpi else ""
        except (AttributeError, TypeError):
            return False
        return fp.lower().startswith(assets_prefix) or fp.casefold() in correction_paths

    for ti in range(1, director.GetTrackCount("video") + 1):
        stale = [it for it in (director.GetItemListInTrack("video", ti) or []) if _ours(it, ti)]
        if stale:
            director.DeleteClips(stale)
    known = {j["path"].stem for j in media_jobs} | {f"{cid}_broll_"}
    broll_bin = next(
        (f for f in root.GetSubFolderList() if f.GetName() == "BRoll"), None
    ) or mp.AddSubFolder(root, "BRoll")
    stale_clips = (
        []
        if orchestrated
        else [
            cl
            for cl in (broll_bin.GetClipList() or [])
            if any((cl.GetName() or "").startswith(k) for k in known)
        ]
    )
    if stale_clips:
        mp.DeleteClips(stale_clips)

    mp.SetCurrentFolder(broll_bin)
    while director.GetTrackCount("video") < CARD_TRACK:
        director.AddTrack("video")
    made = []
    tl_start = director.GetStartFrame()

    # 重疊防呆：track 2 開場分割 item（0–opener_sec）還在——B-roll 撞上去
    # AppendToTimeline 會靜默落到別處（S3 期刊照血案 2026-07-27）
    occupied = [
        (it.GetStart() - tl_start, it.GetEnd() - tl_start)
        for it in (director.GetItemListInTrack("video", BROLL_TRACK) or [])
    ]
    for job in media_jobs:
        f0, f1 = int(job["t0"] * fps), int((job["t0"] + job["span"]) * fps)
        for s, e in occupied:
            if f0 < e and f1 > s:
                raise SystemExit(
                    f"item {job['i']}（{job['path'].stem}）{job['t0']}s 與 track "
                    f"{BROLL_TRACK} 既有 item（{s / fps:.1f}–{e / fps:.1f}s，多半是"
                    "開場分割）重疊——改 t0 避開"
                )

    for job in media_jobs:
        clips = mp.ImportMedia([str(job["path"])]) or []
        if not clips:
            raise SystemExit(f"匯入失敗: {job['path']}")
        clip = clips[0]
        if job.get("timeline_name"):
            clip.SetClipProperty("Clip Name", job["timeline_name"])
        if job["kind"] == "photo":
            # 靜照的 endFrame 會被忽略（走專案預設靜照時長 5s，實測 journal
            # 照 3.2s 變 5.0s）——先把 clip 的 Frames 設成目標長度
            _configure_photo_hold(clip, span=job["span"], fps=fps)
        ok = mp.AppendToTimeline([_media_append_spec(job, clip, fps=fps, tl_start=tl_start)])
        if not ok or (isinstance(ok, list) and ok[0] is None):  # [None]=失敗（2026-08-04）
            raise SystemExit(f"疊軌失敗 @{job['t0']}（track {BROLL_TRACK} 可能被佔）")
        item = (director.GetItemListInTrack("video", BROLL_TRACK) or [])[-1]
        zoom = _fill_zoom(clip.GetClipProperty("Resolution"), job["sar"], canvas)
        should_fill = _should_fill_media(orchestrated=orchestrated, kind=job["kind"])
        if should_fill and job["kind"] == "photo":
            # 靜照先放大到 fill，Ken Burns 再往上推
            item.SetProperty("ZoomX", zoom)
            item.SetProperty("ZoomY", zoom)
            if not _ken_burns(item, job["span"], fps, KENBURNS_SCALE):
                logger.warning("Ken Burns 失敗 @%.1fs——照片維持靜態", job["t0"])
        elif should_fill and zoom > 1.001:
            item.SetProperty("ZoomX", zoom)
            item.SetProperty("ZoomY", zoom)
        made.append(
            {"slug": job["path"].stem, "kind": job["kind"], "at": job["t0"], "sec": job["span"]}
        )

    for job in card_jobs:
        clips = mp.ImportMedia([str(job["mov"])]) or []
        if not clips:
            raise SystemExit(f"匯入失敗: {job['mov']}")
        if job.get("timeline_name"):
            clips[0].SetClipProperty("Clip Name", job["timeline_name"])
        ok = mp.AppendToTimeline(
            [
                {
                    "mediaPoolItem": clips[0],
                    "mediaType": 1,
                    "trackIndex": CARD_TRACK,
                    "recordFrame": tl_start + int(job["t0"] * fps),
                    "startFrame": 0,
                    "endFrame": int(job["span"] * fps) + 2,
                }
            ]
        )
        if not ok or (isinstance(ok, list) and ok[0] is None):  # [None]=失敗（2026-08-04）
            raise SystemExit(f"疊軌失敗 @{job['t0']}（track {CARD_TRACK}）")
        made.append(
            {
                "slug": job["mov"].stem,
                "kind": job.get("kind", job["comp"]),
                "at": job["t0"],
                "sec": job["span"],
            }
        )

    for job in badge_jobs:
        while director.GetTrackCount("video") < BADGE_TRACK:
            director.AddTrack("video")
        clips = mp.ImportMedia([str(job["path"])]) or []
        if not clips:
            raise SystemExit(f"匯入失敗: {job['path']}")
        # 逐 loop 鋪滿 [t0, min(t1, timeline 尾)]——loop 檔長 = 源長
        loop_frames_src = int(round(_probe_dur(job["path"]) * job["src_fps"]))
        end_frame = min(int(job["t1"] * fps), director.GetEndFrame() - tl_start + 1)
        pos = int(job["t0"] * fps)
        n_loops = 0
        while pos < end_frame:
            remain_tl = end_frame - pos
            take_src = min(loop_frames_src, int(remain_tl * job["src_fps"] / fps))
            if take_src <= 0:
                break
            ok = mp.AppendToTimeline(
                [
                    {
                        "mediaPoolItem": clips[0],
                        "mediaType": 1,
                        "trackIndex": BADGE_TRACK,
                        "recordFrame": tl_start + pos,
                        "startFrame": 0,
                        "endFrame": take_src,
                    }
                ]
            )
            if not ok or (isinstance(ok, list) and ok[0] is None):  # [None]=失敗（2026-08-04）
                raise SystemExit(f"badge 疊軌失敗 @frame {pos}")
            pos += int(take_src * fps / job["src_fps"])
            n_loops += 1
        made.append(
            {
                "slug": job["path"].stem,
                "kind": "badge",
                "at": job["t0"],
                "sec": round((end_frame - int(job["t0"] * fps)) / fps, 1),
                "loops": n_loops,
            }
        )

    mp.SetCurrentFolder(root)
    pm.SaveProject()
    committed_broll_receipt = None
    if not orchestrated:
        try:
            post_apply_broll_receipt = build_authoritative_broll_receipt(
                episode_dir,
                cid,
                str(c["format"]),
                items,
                master.identity(),
                editorial_master=master,
            )
        except BrollContractError as exc:
            raise SystemExit(f"Stock Video materialization receipt 寫入失敗：{exc}") from exc
        if post_apply_broll_receipt != broll_receipt:
            raise SystemExit(
                "Stock Video plan／素材在疊軌期間發生變更；未發布 materialization receipt"
            )
        committed_broll_receipt = write_broll_receipt(
            episode_dir,
            cid,
            str(c["format"]),
            items,
            master.identity(),
            editorial_master=master,
        )

    stills = []
    if stills_dir is not None:
        stills_dir.mkdir(parents=True, exist_ok=True)
        rjobs = []
        for m in made:
            fr = tl_start + int((m["at"] + m["sec"] / 2) * fps)
            project.SetRenderSettings(
                {
                    "MarkIn": fr,
                    "MarkOut": fr,
                    "TargetDir": str(stills_dir),
                    "CustomName": f"broll_{cid}_{m['slug'][:24]}",
                }
            )
            jid = project.AddRenderJob()
            if jid:
                rjobs.append((jid, f"broll_{cid}_{m['slug'][:24]}"))
        project.StartRendering([j for j, _ in rjobs], isInteractiveMode=False)
        for _ in range(180):
            if not project.IsRenderingInProgress():
                break
            time.sleep(1)
        for jid, name in rjobs:
            project.DeleteRenderJob(jid)
            stills.append(name)

    result = {
        "status": "brolled",
        "timeline": director_label,
        "items": made,
        "stills": stills,
        "stock_video_count": (
            committed_broll_receipt["stock_video_count"]
            if committed_broll_receipt is not None
            else sum(item.get("kind") == "video" for item in items)
        ),
    }
    if committed_broll_receipt is not None:
        result["stock_video_receipt"] = str(receipt_path(episode_dir, cid))
    return result


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="短片 B-roll / 貼紙 / 概念卡（波旬式素材層）")
    parser.add_argument("episode", help="episode 資料夾")
    parser.add_argument("--id", required=True, help="winner id（如 punch-S1）")
    parser.add_argument("--stills", help="物化後渲樣張到此資料夾")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="驗 current Director/DP/Audit、exact recipes/media，不連 Resolve／不寫 receipt",
    )
    parser.add_argument(
        "--camera-corrections-only",
        action="store_true",
        help="只套 *_broll.json 的局部 host/guest/wide video override；不重跑 DP/B-roll",
    )
    args = parser.parse_args(argv)
    if args.validate_only and args.camera_corrections_only:
        parser.error("--validate-only 與 --camera-corrections-only 不可同時使用")
    if args.camera_corrections_only:
        result = apply_camera_corrections(Path(args.episode), args.id)
    elif args.validate_only:
        result = validate_plan(Path(args.episode), args.id)
    else:
        result = apply(Path(args.episode), args.id, Path(args.stills) if args.stills else None)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
