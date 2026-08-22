"""short-broll：短片 B-roll / 貼紙 / 概念卡 — 對標鐘穎波旬集（修修 2026-07-27 通宵裁決）。

波旬範本的四種素材語彙（`docs` 見 SKILL Step 7.6）：
1. stock video 切出（比喻具象化：黑暗隧道剪影、山頂雲海）→ video track 2 全幅
2. stock photo（Ken Burns 慢推）→ video track 2 全幅
3. 雙貼紙（irasutoya 風插畫貼講者兩側，講故事時）→ hyperframes alpha → track 4
4. 概念圖解卡（兩插畫+雙向箭頭+橘塊標題）→ hyperframes alpha → track 4

輸入：highlights/tighten/<id>_broll.json
    {"items": [
      {"t0": 10.0, "t1": 13.5, "kind": "video", "slug": "doomscroll-dark", ...},
      {"t0": 0.8, "t1": 3.9, "kind": "photo", "slug": "science-journal", ...},
      {"t0": 2.2, "t1": 8.7, "kind": "sticker", "slug": "s1-x",
       "stickers": [{"file": "brain.png", "side": "left"}, ...]},
      {"t0": 40.1, "t1": 44.0, "kind": "concept", "slug": "causal", "comp": "concept_card",
       "vars": {"title": "相關 ≠ 因果", "left_icon": "smartphone.png", ...}}
    ]}
    t0/t1 = （緊·導播）timeline 秒。素材檔在 episode assets/broll/<slug>.*、
    貼紙在 assets/stickers/*.png（irasutoya s800，透明背景）。

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
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
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
    build_broll_receipt,
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
        raise SystemExit(
            f"item {index} guest-namecard placement 驗證失敗：{exc}"
        ) from exc
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
        receipt = build_broll_receipt(
            episode_dir,
            cid,
            str(candidate["format"]),
            items,
            master.identity(),
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


def apply(episode_dir: Path, cid: str, stills_dir: Path | None = None) -> dict:
    from build_resolve_project import connect_resolve

    master = _open_editorial_master(episode_dir)
    c, w = _load_winner(episode_dir, cid, master.identity())
    fmt = c.get("format", "short")
    fcfg = FORMAT_BROLL[fmt]
    canvas = tuple(fcfg["canvas"])
    suffix = fcfg["comp_suffix"]
    broll_path = episode_dir / TIGHTEN_DIR / f"{cid}_broll.json"
    if not broll_path.exists():
        raise SystemExit(f"{broll_path} 不存在——agent 先從 tight SRT 規劃素材點")
    items = json.loads(broll_path.read_text(encoding="utf-8"))["items"]
    try:
        broll_receipt = build_broll_receipt(
            episode_dir, cid, str(fmt), items, master.identity()
        )
    except BrollContractError as exc:
        raise SystemExit(f"Stock Video production gate 失敗：{exc}") from exc
    items.sort(key=lambda x: x["t0"])
    assets_dir = episode_dir / "assets" / "broll"
    stickers_dir = episode_dir / "assets" / "stickers"
    cards_dir = episode_dir / CARDS_DIR
    cards_dir.mkdir(parents=True, exist_ok=True)

    # 1) 準備 jobs：媒體素材找檔、卡片素材 render（hash cache）
    media_jobs, card_jobs, badge_jobs = [], [], []
    for i, it in enumerate(items):
        t0, t1 = float(it["t0"]), float(it["t1"])
        span = round(t1 - t0, 2)
        kind = it["kind"]
        if span < 0.8:
            raise SystemExit(f"item {i}（{it.get('slug')}）只有 {span}s——太短，B-roll 至少 0.8s")
        if kind in ("video", "photo"):
            hits = sorted(assets_dir.glob(f"{it['slug']}.*"))
            if not hits:
                raise SystemExit(f"assets/broll/{it['slug']}.* 不存在——先下載素材")
            sar, src_fps = _probe_meta(hits[0])
            media_jobs.append(
                {
                    "path": hits[0],
                    "t0": t0,
                    "span": span,
                    "kind": kind,
                    "i": i,
                    # src_in（秒）：素材源內起點偏移——素材開頭讀不懂/是廢畫面時跳過
                    # （首輪盲審：空錢包前 1s 是黑色皮件側面）
                    "src_in": float(it.get("src_in", 0.0)),
                    "sar": sar,
                    # photo 的「fps」無意義（jpg 會回報 25）——歸零走 timeline
                    # fps（搭配 clip Frames 設定）；probe 失敗同樣落回
                    "src_fps": 0.0 if kind == "photo" else src_fps,
                }
            )
        elif kind == "sticker":
            comp = "sticker_pair"
            if span > COMP_MAX_SEC[comp] - 0.3:
                raise SystemExit(f"item {i} 貼紙 {span}s 超過上限 {COMP_MAX_SEC[comp] - 0.3}s")
            variables: dict = {"show_sec": span}
            for st in it["stickers"]:
                p = stickers_dir / st["file"]
                if not p.exists():
                    raise SystemExit(f"assets/stickers/{st['file']} 不存在")
                variables[f"{st['side']}_src"] = _data_uri(p)
            for k in ("y_pct", "size_pct"):
                if k in it:
                    variables[k] = it[k]
            card_jobs.append({"comp": comp, "vars": variables, "t0": t0, "span": span, "i": i})
        elif kind == "concept":
            comp = it.get("comp", "concept_card")
            if comp not in COMPS:
                raise SystemExit(f"item {i} comp={comp} 不存在")
            if span > COMP_MAX_SEC[comp] - 0.3:
                raise SystemExit(f"item {i} 概念卡 {span}s 超過上限 {COMP_MAX_SEC[comp] - 0.3}s")
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
                f"item {i} kind={kind} 不合法"
                "（video/photo/sticker/concept/guest-namecard/badge）"
            )

    for job in card_jobs:
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
        style_val = str(job["vars"].get("style", ""))
        if job["comp"] == "transition_title" and style_val.startswith("paper"):
            tex = assets_dir / PAPER_TEXTURE
            if not tex.exists():
                raise SystemExit(f"assets/broll/{PAPER_TEXTURE} 不存在——先落紙紋底（見 SKILL.md）")
            tex_mov = mov.with_name(mov.stem + "_tex.mov")
            if not tex_mov.exists():
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

    # Card rendering above can take minutes. Re-open the trust root and winner
    # immediately before the first Resolve mutation instead of relying on the
    # earlier preflight identity remaining fresh.
    master = _open_editorial_master(episode_dir)
    c, w = _load_winner(episode_dir, cid, master.identity())
    try:
        fresh_broll_receipt = build_broll_receipt(
            episode_dir, cid, str(c["format"]), items, master.identity()
        )
    except BrollContractError as exc:
        raise SystemExit(f"Stock Video production gate 失敗：{exc}") from exc
    if fresh_broll_receipt != broll_receipt:
        raise SystemExit("Stock Video plan／素材在準備期間發生變更，未修改 Resolve")
    director_label = f"{FORMAT_LABEL[c['format']]}{w['rank']} - {c['title']}（緊·導播）"
    director = None
    for i in range(1, project.GetTimelineCount() + 1):
        t = project.GetTimelineByIndex(i)
        if t and t.GetName() == director_label:
            director = t
            break
    if director is None:
        raise SystemExit(f"「{director_label}」不存在——先跑 run_short_director")
    _verify_director_materialization(episode_dir, cid, c, director, master, fps)
    project.SetCurrentTimeline(director)

    # 冪等清場（timeline items）：**媒體路徑歸屬判定**——本 timeline 上凡是
    # 媒體檔在 episode assets/broll/ 底下的 item 都是本 script 放的（開場分割
    # 是 COMBO 機位源，路徑不同），一律清掉再重疊。slug 改名不會再留孤兒
    # （十七輪：sports-car 換 slug 後舊 item 沒被 name 比對清到）。
    # 卡片 item 用 <cid>_broll_ 名稱前綴（每 cid 專屬，不跨短片）。
    assets_prefix = str(assets_dir.resolve()).lower()

    def _ours(it) -> bool:
        if (it.GetName() or "").startswith(f"{cid}_broll_"):
            return True
        try:
            mpi = it.GetMediaPoolItem()
            fp = (mpi.GetClipProperty("File Path") or "") if mpi else ""
        except (AttributeError, TypeError):
            return False
        return fp.lower().startswith(assets_prefix)

    for ti in range(1, director.GetTrackCount("video") + 1):
        stale = [it for it in (director.GetItemListInTrack("video", ti) or []) if _ours(it)]
        if stale:
            director.DeleteClips(stale)
    known = {j["path"].stem for j in media_jobs} | {f"{cid}_broll_"}
    broll_bin = next(
        (f for f in root.GetSubFolderList() if f.GetName() == "BRoll"), None
    ) or mp.AddSubFolder(root, "BRoll")
    stale_clips = [
        cl
        for cl in (broll_bin.GetClipList() or [])
        if any((cl.GetName() or "").startswith(k) for k in known)
    ]
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
        if job["kind"] == "photo":
            # 靜照的 endFrame 會被忽略（走專案預設靜照時長 5s，實測 journal
            # 照 3.2s 變 5.0s）——先把 clip 的 Frames 設成目標長度
            clip.SetClipProperty("Frames", str(int(job["span"] * fps)))
        ok = mp.AppendToTimeline(
            [
                {
                    "mediaPoolItem": clip,
                    "mediaType": 1,
                    "trackIndex": BROLL_TRACK,
                    "recordFrame": tl_start + int(job["t0"] * fps),
                    # startFrame/endFrame 是「源幀」——用源 fps 換算（photo/probe
                    # 失敗 src_fps=0 → 落回 timeline fps）
                    "startFrame": int(job["src_in"] * (job["src_fps"] or fps)),
                    "endFrame": int((job["src_in"] + job["span"]) * (job["src_fps"] or fps)),
                }
            ]
        )
        if not ok or (isinstance(ok, list) and ok[0] is None):  # [None]=失敗（2026-08-04）
            raise SystemExit(f"疊軌失敗 @{job['t0']}（track {BROLL_TRACK} 可能被佔）")
        item = (director.GetItemListInTrack("video", BROLL_TRACK) or [])[-1]
        zoom = _fill_zoom(clip.GetClipProperty("Resolution"), job["sar"], canvas)
        if job["kind"] == "photo":
            # 靜照先放大到 fill，Ken Burns 再往上推
            item.SetProperty("ZoomX", zoom)
            item.SetProperty("ZoomY", zoom)
            if not _ken_burns(item, job["span"], fps, KENBURNS_SCALE):
                logger.warning("Ken Burns 失敗 @%.1fs——照片維持靜態", job["t0"])
        elif zoom > 1.001:
            item.SetProperty("ZoomX", zoom)
            item.SetProperty("ZoomY", zoom)
        made.append(
            {"slug": job["path"].stem, "kind": job["kind"], "at": job["t0"], "sec": job["span"]}
        )

    for job in card_jobs:
        clips = mp.ImportMedia([str(job["mov"])]) or []
        if not clips:
            raise SystemExit(f"匯入失敗: {job['mov']}")
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
    try:
        post_apply_broll_receipt = build_broll_receipt(
            episode_dir, cid, str(c["format"]), items, master.identity()
        )
    except BrollContractError as exc:
        raise SystemExit(f"Stock Video materialization receipt 寫入失敗：{exc}") from exc
    if post_apply_broll_receipt != broll_receipt:
        raise SystemExit("Stock Video plan／素材在疊軌期間發生變更；未發布 materialization receipt")
    committed_broll_receipt = write_broll_receipt(
        episode_dir, cid, str(c["format"]), items, master.identity()
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

    return {
        "status": "brolled",
        "timeline": director_label,
        "items": made,
        "stills": stills,
        "stock_video_count": committed_broll_receipt["stock_video_count"],
        "stock_video_receipt": str(receipt_path(episode_dir, cid)),
    }


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="短片 B-roll / 貼紙 / 概念卡（波旬式素材層）")
    parser.add_argument("episode", help="episode 資料夾")
    parser.add_argument("--id", required=True, help="winner id（如 punch-S1）")
    parser.add_argument("--stills", help="物化後渲樣張到此資料夾")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="只驗 Stock Video 數量、路徑與 hash，不連 Resolve／不寫 receipt",
    )
    args = parser.parse_args(argv)
    result = (
        validate_plan(Path(args.episode), args.id)
        if args.validate_only
        else apply(Path(args.episode), args.id, Path(args.stills) if args.stills else None)
    )
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
