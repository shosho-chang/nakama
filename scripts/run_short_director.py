"""short-director：短片雙機位導播 — 說話者切鏡 + 頭部 zoom punch。

修修 2026-07-26：短片畫面切分要更細緻。source 不再用機器導播混好的檔，
改用原始機位（CAM1=修修、CAM2=來賓），誰講話切誰、同人連續段落交替
zoom punch-in，全景機位不用。參考範本：E:\\data 鐘穎 ×3（外包剪輯師版）。

在 short-tighten 之上疊加：讀同一份 `highlights/tighten/<id>_cuts.json`
的保留段，配 mic 能量詞級說話者（shared/speaker_assign，同 speaker-split
那套），產生導播 shot list，建「短N - <標題>（緊·導播）」timeline：

- video：逐 shot 上軌，每 shot 設 ZoomX/Y + Pan 把 16:9 機位裁成 9:16
  頭部特寫；同說話者連續 shot 交替 base/punch 兩級 zoom（jump cut 節奏）
- audio/字幕：與 short-tighten 相同（normalized.wav 逐保留段對位、
  字幕塌縮重對時）
- opener：頭 4 秒上下分割雙人畫面（參考片開場語法——來賓上、修修下，
  幾秒內讓觀眾認識這是對談），`--no-opener` 關閉
- `--stills`：物化後抓樣張輸出 PNG（agent 校驗構圖用——Pan/Zoom/Crop 語意
  以實際 render 為準，不能只信計算）

用法：
    python scripts/run_short_director.py <episode> --id punch-S1
    python scripts/run_short_director.py <episode> --id punch-S1 --stills <dir>
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
from run_short_tighten import (  # noqa: E402
    TIGHTEN_DIR,
    _keep_segments,
    _load_winner,
    _retime_srt,
    import_srt_tidy,
)

logger = logging.getLogger("short_director")

# 機位配置（本集：CAM1=修修/speaker0、CAM2=來賓/speaker1；固定機位，
# 臉部座標校一次全集通用。換集若座標不同，寫 highlights/tighten/director.json 覆蓋）
DEFAULT_CFG = {
    "cams": {"0": "1_CAMERA 1.mp4", "1": "2_CAMERA 2.mp4"},
    "wide_cam": "3_CAMERA 3.mp4",  # 兩人全景（修修 2026-08-03 確認；CAM4-6 不用）
    "face_x": {"0": 880, "1": 1165},  # 1920 寬源片的臉部中心 x
    "zoom_base": 3.2,  # 16:9 → 9:16 滿高（3.16）+ 一點餘裕
    "punch_scale": 1.25,  # punch 放大倍率（七輪：1.15 不夠 dramatic）
    "min_shot": 1.0,  # 短於此的說話者 run 不切鏡（併入前一 shot）
    "opener_sec": 4.0,  # 開場上下分割秒數（不足 2s 的保留段不做）
    # 節奏（修修 2026-07-26 二輪：畫面變化太少——對齊鐘穎範本 ~3s/刀）
    "reaction_every": 9.0,  # 同人 run 每 ~9s 插一個聽者反應鏡頭
    "reaction_sec": 1.8,  # 反應鏡頭長度（點頭畫面，audio 不斷）
    "punch_ramp_sec": 0.25,  # speed-ramp zoom 秒數（十四輪：0.5 太慢、0.2 略衝，0.25 定版）
    "face_y": {"0": 330, "1": 330},  # 1080 高源片的眼線 y（punch zoom 鎖臉用）
    "reframe": True,  # 逐 shot 裁切重構圖（16:9 源 → 9:16）；長片關掉用滿幀
    "opener_style": "split",  # split=上下分割雙人 / wide=全景機位
    "reaction_style": "listener",  # listener=只切聽者 / alternate=聽者與全景交替
    "fine_subs": True,  # 字幕細切成呼吸單元
}

# 長片覆蓋（修修 2026-08-03 裁決：Step 7 走選項 B，從原始機位重導播）。
#
# 與短片的三個結構差異：
# 1. **不裁切**：16:9 源進 16:9 timeline，機位本身就是單人中景，滿幀直上。
#    短片那套 zoom 3.2 + Pan 鎖臉是為了把橫幅擠進直式，長片沒有這個問題。
# 2. **全景機位（CAM3）派上用場**：開場用全景建立「這是一場對談」，
#    反應鏡頭與聽者特寫交替——短片沒有全景可用，只能合成上下分割。
# 3. **字幕不細切**：長片依 Q4b 裁決不燒字幕、只上 CC
#    （docs/plans/2026-07-26-video-publishing-plan.md §Q4b），
#    CC 由 YouTube 自己排版，切成 8 字反而讓 CC 破碎難讀。
#
# 節奏放慢的依據同 tighten：剪輯文法 §1.1（A-roll p50 6.0s 已達標）
# 與建議 7（反 over-editing）。min_shot 1.5s、反應鏡頭每 12s 一次。
FORMAT_CFG = {
    "short": {},  # identity——短片行為與四支已驗收版本完全一致
    "long": {
        "zoom_base": 1.0,
        "reframe": False,
        "min_shot": 1.5,
        "opener_sec": 5.0,
        "opener_style": "wide",
        "reaction_every": 12.0,
        "reaction_sec": 2.0,
        "reaction_style": "alternate",
        "fine_subs": False,
    },
}
SRC_W = 1920
FIT = 1080 / 1920  # 16:9 源在 1080 寬 timeline 的 fit 縮放


def _load_cfg(episode_dir: Path, fmt: str = "short") -> dict:
    """格式預設 → episode 覆蓋。

    `director.json` 可以是平鋪（套用到所有格式，維持既有慣例）或依格式
    分層 `{"long": {...}, "short": {...}}`——換集校準時長短片的 face_x
    共用、但節奏參數可能要分開調。
    """
    override = episode_dir / TIGHTEN_DIR / "director.json"
    cfg = {**DEFAULT_CFG, **FORMAT_CFG.get(fmt, {})}
    if override.exists():
        raw = json.loads(override.read_text(encoding="utf-8"))
        per_fmt = {k: v for k, v in raw.items() if k in FORMAT_CFG}
        cfg.update({k: v for k, v in raw.items() if k not in FORMAT_CFG})
        cfg.update(per_fmt.get(fmt, {}))
    return cfg


def _pan(cfg: dict, spk: int, zoom: float) -> float:
    """把臉部中心水平移到畫面中央（timeline px；fit 後再乘 zoom）。"""
    return (SRC_W / 2 - cfg["face_x"][str(spk)]) * FIT * zoom


# Resolve 20.3 transform 語意（CROP-TEST timeline 實測，勿憑直覺改）：
# - Crop：zoom=1 時的螢幕 px（= fit 畫布 px），遮罩黏在影像上、隨 zoom 縮放
# - Pan：螢幕 px × PAN_SCALE（fit 寬/frame 寬；16:9 源進 1080 寬直式 = 1.0）
# - Tilt：螢幕 px × TILT_SCALE（fit 高/frame 高 = 607.5/1920），正值向上，
#   且效果**不**隨 zoom 放大
PAN_SCALE = 1.0
TILT_SCALE = (1080 * FIT) / 1920  # 0.3164


def _panel_props(cfg: dict, spk: int, top: bool) -> dict[str, float]:
    """上下分割 panel 的 transform+crop（1080×960 半屏，窗框對準臉部）。"""
    win_w, win_h = 810, 720  # 窗框源片尺寸，比例 = 半屏 1080×960
    fx = float(cfg["face_x"][str(spk)])
    x0 = min(max(fx - win_w / 2, 0.0), SRC_W - win_w)
    y0 = 90.0  # 眼線 y≈330 → 窗內 33% 高
    zoom = 1080 / (win_w * FIT)  # 窗框放大到滿寬 1080（高恰為 960）
    # 窗框中心相對影像中心的 fit-px 偏移；螢幕上隨 zoom 放大
    ox = ((x0 + win_w / 2) - SRC_W / 2) * FIT * zoom
    oy = ((y0 + win_h / 2) - 540) * FIT * zoom
    target_y = -480.0 if top else 480.0  # 相對 frame 中心（y 向下為正）
    return {
        "ZoomX": zoom,
        "ZoomY": zoom,
        "Pan": -ox / PAN_SCALE,
        "Tilt": -(target_y - oy) / TILT_SCALE,
        "CropLeft": x0 * FIT,
        "CropRight": (SRC_W - (x0 + win_w)) * FIT,
        "CropTop": y0 * FIT,
        "CropBottom": (1080 - (y0 + win_h)) * FIT,
    }


def _word_speakers(episode_dir: Path, t0: float, t1: float) -> list[tuple[float, float, int]]:
    """[t0,t1] 內詞級說話者 → (start, end, speaker) 詞列表。"""
    from shared.speaker_assign import assign_word_speakers, detect_mic_tracks, load_envelopes

    words = json.load(open(episode_dir / "subs" / "words.json", encoding="utf-8"))["words"]
    mics = detect_mic_tracks(episode_dir / "Audio")
    envs = load_envelopes(mics, reference=episode_dir / "normalized.wav")
    spk = assign_word_speakers(words, envs)
    out = []
    last = 0
    for w, s in zip(words, spk):
        if s is not None:
            last = s
        if t0 <= w["start"] < t1:
            out.append((w["start"], w["end"], s if s is not None else last))
    return out


def _snap(t: float, word_starts: list[float], lo: float, hi: float) -> float:
    """把切點吸附到最近的詞開頭（±0.6s 內；夾在 [lo, hi]）。"""
    cands = [w for w in word_starts if abs(w - t) <= 0.6 and lo <= w <= hi]
    return min(cands, key=lambda w: abs(w - t)) if cands else min(max(t, lo), hi)


def _expand_run(s: float, e: float, spk: int, word_starts: list[float], cfg: dict) -> list[dict]:
    """單一說話者 run → 反應鏡頭插入。

    - run 每 ~reaction_every 秒插一個 reaction_sec 的聽者反應鏡頭
      （鐘穎範本語法：獨白中切聽者點頭 1-2s 再切回，audio 不斷）
    - 修修 2026-07-26 五輪：**不再做 max_shot 機械切分/zoom 交替**——punch
      改成內容驅動的 speed-ramp zoom（見 _apply_punch_zooms），機械節奏的
      punch in/out 時間點「抓得很奇怪」
    """
    dur = e - s
    rsec = cfg["reaction_sec"]
    k = int(dur // cfg["reaction_every"])
    pieces: list[tuple[float, float, int, str]] = []
    if k > 0:
        chunk = (dur - k * rsec) / (k + 1)
        pos = s
        for _ in range(k):
            r0 = _snap(pos + chunk, word_starts, pos + cfg["min_shot"], e - rsec - cfg["min_shot"])
            pieces.append((pos, r0, spk, "talk"))
            pieces.append((r0, r0 + rsec, 1 - spk, "reaction"))
            pos = r0 + rsec
        pieces.append((pos, e, spk, "talk"))
    else:
        pieces.append((s, e, spk, "talk"))
    return [{"s": ps, "e": pe, "spk": pspk, "kind": kind} for ps, pe, pspk, kind in pieces]


def build_shots(
    segs: list[tuple[float, float]], words: list[tuple[float, float, int]], cfg: dict
) -> list[dict]:
    """保留段 × 詞級說話者 → 導播 shot list [(src_s, src_e, spk, zoom)]。

    - shot 邊界 = 保留段邊界 ∪ 段內說話者切換點（詞邊界）
    - 短於 min_shot 的說話者 run 不切鏡（併入前一 shot——0.5s 的附和
      切過去再切回來會閃屏）
    - 長 run 插聽者反應鏡頭（_expand_run）
    - zoom 全 base；punch 由 <id>_zoom.json 內容驅動（speed-ramp，五輪裁決）
    """
    shots: list[dict] = []
    for seg_s, seg_e in segs:
        in_seg = [w for w in words if w[1] > seg_s and w[0] < seg_e]
        runs: list[list[float | int]] = []  # [start, end, spk]
        for ws, we, s in in_seg:
            if runs and runs[-1][2] == s:
                runs[-1][1] = we
            else:
                runs.append([ws, we, s])
        # 短 run 併入前段（第一段短 run 併入後段）
        merged: list[list[float | int]] = []
        for r in runs:
            if merged and (r[1] - r[0]) < cfg["min_shot"]:
                merged[-1][1] = r[1]
            elif not merged and runs[1:] and (r[1] - r[0]) < cfg["min_shot"]:
                runs[1][0] = r[0]
            else:
                merged.append(r)
        # 吸收後相鄰同說話者 run 合併（附和被吸掉後前後是同一人，不留假切點）
        coalesced: list[list[float | int]] = []
        for r in merged:
            if coalesced and coalesced[-1][2] == r[2]:
                coalesced[-1][1] = r[1]
            else:
                coalesced.append(r)
        merged = coalesced
        if not merged:
            merged = [[seg_s, seg_e, shots[-1]["spk"] if shots else 1]]
        # 夾回保留段邊界；shot 邊界取 run 交界；長 run 展開（反應鏡頭）
        merged[0][0], merged[-1][1] = seg_s, seg_e
        word_starts = sorted(w[0] for w in in_seg)
        for i, r in enumerate(merged):
            if i + 1 < len(merged):
                r[1] = merged[i + 1][0]
            shots.extend(_expand_run(float(r[0]), float(r[1]), int(r[2]), word_starts, cfg))
    # zoom 全部 base——punch 改內容驅動 speed-ramp（_apply_punch_zooms）
    for sh in shots:
        sh["zoom"] = cfg["zoom_base"]
    # 反應鏡頭配鏡：短片只有兩個機位可用，一律切聽者特寫；長片有全景，
    # 改成「聽者特寫 / 全景」交替——定期讓觀眾看見兩人的相對關係，
    # 也避免 12 分鐘一路只在兩張臉之間乒乓。
    if cfg.get("reaction_style") == "alternate":
        n = 0
        for sh in shots:
            if sh.get("kind") == "reaction":
                if n % 2 == 1:
                    sh["cam"] = "wide"
                n += 1
    return shots


def _punch_keys(
    item_lo: float,
    item_hi: float,
    span_lo: float,
    span_hi: float,
    ramp: float,
    scale: float = 1.25,
) -> list[tuple[float, float]] | None:
    """單一 shot 與 punch 區間的交集 → comp 內 Size 關鍵影格 [(local_sec, factor)]。

    - 完整 ramp：span 起點 ramp-in、終點 ramp-out（各 ramp 秒）
    - span 跨 shot 邊界時：起點在前一 shot 內才 ramp-in，否則本 shot 直接
      punch 級起跳（跨刀 zoom 連續）；終點同理
    - style=cut 時呼叫端把 ramp 縮成 1 frame → 兩鍵貼齊 = 硬切直接放大
    """
    lo, hi = max(item_lo, span_lo), min(item_hi, span_hi)
    if hi - lo <= 0.05:
        return None
    keys: list[tuple[float, float]] = []
    if span_lo >= item_lo:  # ramp-in 在本 shot 內
        keys.append((span_lo - item_lo, 1.0))
        keys.append((min(span_lo + ramp, item_hi) - item_lo, scale))
    else:  # span 從前一 shot 延續進來——開頭就是 punch 級
        keys.append((0.0, scale))
    if span_hi <= item_hi:  # ramp-out 在本 shot 內
        keys.append((max(span_hi - ramp, lo) - item_lo, scale))
        keys.append((span_hi - item_lo, 1.0))
    else:
        keys.append((item_hi - item_lo, scale))
    # 去重疊（極短交集時 in/out 撞在一起）
    dedup: list[tuple[float, float]] = []
    for t, v in keys:
        if dedup and t <= dedup[-1][0] + 1e-6:
            continue
        dedup.append((t, v))
    return dedup if len(dedup) >= 2 else None


def _scurve_expand(keys: list[tuple[float, float]], samples: int = 7) -> list[tuple[float, float]]:
    """ramp 段展開成 easing 取樣（六/七輪：spline 預設內插等速不可信，直接取樣鎖形狀）。

    - 兩方向都 smootherstep 慢快慢（十二輪：放大直接放大就好，**不要過衝
      回彈**——曾試 easeOutBack，修修否決；速度 0.5s 維持）
    - 兩鍵間距 <0.1s（cut 硬切）不取樣
    """
    out = [keys[0]]
    for (t0, v0), (t1, v1) in zip(keys, keys[1:]):
        if v1 != v0 and t1 - t0 >= 0.1:
            for i in range(1, samples + 1):
                u = i / (samples + 1)
                s = 6 * u**5 - 15 * u**4 + 10 * u**3
                out.append((t0 + (t1 - t0) * u, v0 + (v1 - v0) * s))
        out.append((t1, v1))
    return out


def _apply_punch_zooms(appended: list[dict], punches: list[dict], fps: float, cfg: dict) -> int:
    """內容驅動 punch：講重點的區間 speed-ramp zoom-in，講完 ramp-out。

    對覆蓋 punch 區間的 talk shot item 加 Fusion comp（MediaIn→Transform→
    MediaOut）：

    - Size 關鍵影格 1.0→1.15，ramp 段 smootherstep 取樣（S 曲線）
    - **Transform Pivot 鎖在說話者臉部**（源片 normalized 座標，Fusion y
      軸向上）——繞畫面中心放大會讓臉在 ramp 過程飄移（六輪教訓）。
      ⚠️ Center 是影像「位置」不是縮放支點，設 Center 會把畫面搬走（實測）
    - 與 item 靜態 ZoomX 疊乘。reaction shot 不 punch。
    appended: [{item, tl_s, tl_e(timeline 秒), kind, spk}]
    """
    n = 0
    for p in punches:
        # style：ramp（speed-ramp 過衝回彈）或 cut（1 frame 硬切直接放大）——
        # 修修七輪：兩種交互使用。scale 可逐 punch 覆蓋
        style = p.get("style", "ramp")
        ramp_sec = cfg["punch_ramp_sec"] if style == "ramp" else 1.0 / fps
        scale = float(p.get("scale", cfg["punch_scale"]))
        for a in appended:
            if a["kind"] == "reaction":
                continue
            keys = _punch_keys(
                a["tl_s"], a["tl_e"], float(p["t0"]), float(p["t1"]), ramp_sec, scale
            )
            if not keys:
                continue
            keys = _scurve_expand(keys)
            item = a["item"]
            comp = (
                item.GetFusionCompByIndex(1)
                if item.GetFusionCompCount() > 0
                else item.AddFusionComp()
            )
            if comp is None:
                logger.warning(f"AddFusionComp 失敗 @{a['tl_s']:.1f}s——此 shot 跳過 punch")
                continue
            cx = float(cfg["face_x"][str(a["spk"])]) / 1920
            cy = 1.0 - float(cfg["face_y"][str(a["spk"])]) / 1080  # Fusion y 向上
            key_lua = "\n".join(
                f'  xf:SetInput("Size", {v:.5f}, {round(t * fps, 2)})' for t, v in keys
            )
            lua = f"""
local ok, err = pcall(function()
  local mi = comp:FindToolByID("MediaIn")
  local mo = comp:FindToolByID("MediaOut")
  local xf = comp:FindTool("PunchZoom")
  if xf == nil then
    xf = comp:AddTool("Transform", -32768, -32768)
    xf:SetAttrs({{TOOLS_Name = "PunchZoom"}})
    xf.Input = mi.Output
    mo.Input = xf.Output
    xf.Pivot = {{{cx:.4f}, {cy:.4f}}}
    xf.Size = comp:BezierSpline()
  end
{key_lua}
end)
"""
            comp.Execute(lua)
            n += 1
    return n


def direct(
    episode_dir: Path, cid: str, stills_dir: Path | None = None, opener: bool = True
) -> dict:
    from build_resolve_project import _template_path, _template_path_short, connect_resolve
    from run_short_tighten import FORMAT_TIGHTEN

    c, w = _load_winner(episode_dir, cid)
    fmt = c.get("format", "short")
    t0, t1 = float(c["t_start"]), float(c["t_end"])
    cfg = _load_cfg(episode_dir, fmt)
    cuts_path = episode_dir / TIGHTEN_DIR / f"{cid}_cuts.json"
    if not cuts_path.exists():
        raise SystemExit(f"{cuts_path} 不存在——先跑 run_short_tighten --detect + 複審")
    cuts = json.loads(cuts_path.read_text(encoding="utf-8"))["cuts"]
    if any(x.get("keep") is None for x in cuts):
        raise SystemExit("cuts.json 有未複審項（keep=null）——先複審")
    segs = _keep_segments(t0, t1, cuts, FORMAT_TIGHTEN[fmt]["min_keep_seg"])
    words = _word_speakers(episode_dir, t0, t1)
    shots = build_shots(segs, words, cfg)

    # opener：頭 N 秒改上下分割——從 shot list 裁掉該源片區間，改由雙 panel 補
    opener_span: tuple[float, float] | None = None
    if opener and segs:
        o_end = min(segs[0][0] + cfg["opener_sec"], segs[0][1])
        if o_end - segs[0][0] >= 2.0:
            opener_span = (segs[0][0], o_end)
            trimmed = []
            for sh in shots:
                if sh["e"] <= o_end:
                    continue
                if sh["s"] < o_end:
                    sh = {**sh, "s": o_end}
                trimmed.append(sh)
            shots = trimmed

    resolve = connect_resolve()
    pm = resolve.GetProjectManager()
    project_name = episode_dir.name
    project = pm.GetCurrentProject()
    if project is None or project.GetName() != project_name:
        project = pm.LoadProject(project_name)
    if project is None:
        raise SystemExit(f"project「{project_name}」不存在")
    fps = float(project.GetSetting("timelineFrameRate"))
    mp = project.GetMediaPool()
    root = mp.GetRootFolder()

    # 機位素材進 Cams bin（冪等：已在就重用）
    clips = {(x.GetName() or ""): x for x in (root.GetClipList() or [])}
    cams_bin = next((f for f in root.GetSubFolderList() if f.GetName() == "Cams"), None)
    if cams_bin:
        clips.update({(x.GetName() or ""): x for x in (cams_bin.GetClipList() or [])})
    cam_items: dict[int | str, object] = {}

    def _cam(fname: str):
        nonlocal cams_bin
        if fname not in clips:
            if cams_bin is None:
                cams_bin = mp.AddSubFolder(root, "Cams")
            mp.SetCurrentFolder(cams_bin)
            imported = mp.ImportMedia([str(episode_dir / "Video" / fname)]) or []
            mp.SetCurrentFolder(root)
            if not imported:
                raise SystemExit(f"機位匯入失敗: {fname}")
            clips[fname] = imported[0]
        return clips[fname]

    for spk, fname in cfg["cams"].items():
        cam_items[int(spk)] = _cam(fname)
    # 全景機位只在真的用得到時才匯入（短片不用，別白白拖一支 12GB 進 pool）
    needs_wide = cfg.get("opener_style") == "wide" or cfg.get("reaction_style") == "alternate"
    if needs_wide:
        cam_items["wide"] = _cam(cfg["wide_cam"])
    aud = clips.get("normalized.wav")

    label = f"{FORMAT_LABEL[c['format']]}{w['rank']} - {c['title']}（緊·導播）"
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
    template = _template_path_short() if fmt == "short" else _template_path()
    tl = None
    if template.exists():
        tl = mp.ImportTimelineFromFile(str(template), {})
        if tl:
            tl.SetName(label)
    else:
        logger.warning(f"字幕樣式模板不存在（{template}）——timeline 將是無樣式！")
    if tl is None:
        tl = mp.CreateEmptyTimeline(label)
    if tl is None:
        raise SystemExit(f"timeline 建立失敗: {label}")
    project.SetCurrentTimeline(tl)
    if fmt == "short":  # 長片維持 project 的 16:9 設定，不覆寫
        tl.SetSetting("useCustomSettings", "1")
        tl.SetSetting("timelineResolutionWidth", "1080")
        tl.SetSetting("timelineResolutionHeight", "1920")
    if tl.GetTrackCount("subtitle") == 0:
        tl.AddTrack("subtitle")

    def _set_props(item, props: dict[str, float]) -> None:
        for k, v in props.items():
            item.SetProperty(k, v)

    def _append_video(clip, f0: int, f1: int, extra: dict | None = None):
        spec = {"mediaPoolItem": clip, "mediaType": 1, "startFrame": f0, "endFrame": f1}
        if extra:
            spec.update(extra)
        items = mp.AppendToTimeline([spec])
        # ⚠️ AppendToTimeline 失敗會回傳 [None]——truthy！`if not items` 判不到
        # （2026-08-04 util-L4 事故：Resolve 忙碌時全片 append 靜默失敗，
        # script 照報 102 shots、timeline 是空的）
        if not items or (isinstance(items, list) and items[0] is None):
            raise SystemExit(f"{label}: 上軌失敗（AppendToTimeline 回 {items!r}）{f0}-{f1}")
        if isinstance(items, list):
            return items[0]
        track = (extra or {}).get("trackIndex", 1)
        return (tl.GetItemListInTrack("video", track) or [])[-1]

    tl_start = tl.GetStartFrame()
    split_opener = opener_span is not None and cfg.get("opener_style") == "split"
    # opener：短片走上下分割（下半 = 修修，先落 track1 開頭，上半後補 track2）；
    # 長片直接用全景機位一顆——16:9 本來就裝得下兩個人，不必合成
    if opener_span:
        f0, f1 = int(opener_span[0] * fps), int(opener_span[1] * fps)
        if split_opener:
            _set_props(_append_video(cam_items[0], f0, f1), _panel_props(cfg, 0, top=False))
        else:
            _append_video(cam_items["wide"], f0, f1)

    # video：逐 shot 上軌 + 逐 item 設 transform（fit 縮放後 ZoomX/Pan）
    appended: list[dict] = []
    tl_cursor = (
        (int(opener_span[1] * fps) - int(opener_span[0] * fps)) / fps if opener_span else 0.0
    )
    for sh in shots:
        f0, f1 = int(sh["s"] * fps), int(sh["e"] * fps)
        if f1 <= f0:
            continue
        src = cam_items["wide"] if sh.get("cam") == "wide" else cam_items[sh["spk"]]
        item = _append_video(src, f0, f1)
        # reframe=False（長片）：16:9 源進 16:9 timeline，滿幀直上不動 transform。
        # 全景 shot 任何格式都不重構圖——把兩人裁掉就失去用全景的意義。
        if cfg.get("reframe", True) and sh.get("cam") != "wide":
            _set_props(
                item,
                {"ZoomX": sh["zoom"], "ZoomY": sh["zoom"], "Pan": _pan(cfg, sh["spk"], sh["zoom"])},
            )
        appended.append(
            {
                "item": item,
                "tl_s": tl_cursor,
                "tl_e": tl_cursor + (f1 - f0) / fps,
                "kind": sh.get("kind", "talk"),
                "spk": sh["spk"],
            }
        )
        tl_cursor += (f1 - f0) / fps

    # 內容驅動 punch（<id>_zoom.json：agent 標「講重點」的 timeline 秒區間）
    zoom_path = episode_dir / TIGHTEN_DIR / f"{cid}_zoom.json"
    n_punch = 0
    if zoom_path.exists():
        punches = json.loads(zoom_path.read_text(encoding="utf-8"))["punches"]
        n_punch = _apply_punch_zooms(appended, punches, fps, cfg)

    # opener 上半 panel（來賓）：track2、recordFrame 對齊 timeline 開頭
    if split_opener:
        if tl.GetTrackCount("video") < 2:
            tl.AddTrack("video")
        f0, f1 = int(opener_span[0] * fps), int(opener_span[1] * fps)
        _set_props(
            _append_video(cam_items[1], f0, f1, {"trackIndex": 2, "recordFrame": tl_start}),
            _panel_props(cfg, 1, top=True),
        )

    # audio：與 tighten 相同——逐保留段 recordFrame 對位
    offset_frames = 0
    for seg_s, seg_e in segs:
        f0, f1 = int(seg_s * fps), int(seg_e * fps)
        if aud is not None:
            mp.AppendToTimeline(
                [
                    {
                        "mediaPoolItem": aud,
                        "mediaType": 2,
                        "trackIndex": 1,
                        "startFrame": f0,
                        "endFrame": f1,
                        "recordFrame": tl_start + offset_frames,
                    }
                ]
            )
        offset_frames += f1 - f0

    mp.SetCurrentFolder(root)
    seg_srt, n_cues = _retime_srt(episode_dir, cid, segs, cuts, fine=cfg["fine_subs"])
    srt_items = import_srt_tidy(mp, root, seg_srt)
    sub_ok = bool(mp.AppendToTimeline(srt_items)) if srt_items else False
    # 收尾驗證：實際上軌數 = shot 數（fail loud——報成功但 timeline 空的血案不再）
    placed = sum(
        len(tl.GetItemListInTrack("video", k) or [])
        for k in range(1, tl.GetTrackCount("video") + 1)
    )
    if placed < len(shots):
        raise SystemExit(
            f"{label}: 上軌驗證失敗——timeline 實有 {placed} items < shots {len(shots)}"
        )
    pm.SaveProject()

    stills = []
    if stills_dir is not None:
        opener_frames = int(opener_span[1] * fps) - int(opener_span[0] * fps) if opener_span else 0
        stills = _grab_stills(resolve, project, tl, shots, fps, stills_dir, opener_frames)

    n_switch = sum(1 for i in range(1, len(shots)) if shots[i]["spk"] != shots[i - 1]["spk"])
    return {
        "status": "directed",
        "format": fmt,
        "timeline": label,
        "shots": len(shots),
        "wide_shots": sum(1 for s in shots if s.get("cam") == "wide"),
        "cam_switches": n_switch,
        "punch_ramps": n_punch,
        "subtitles": sub_ok,
        "cues": n_cues,
        "stills": stills,
    }


def refresh_subs(episode_dir: Path, cid: str) -> dict:
    """（緊·導播）timeline **只換字幕軌，不動剪輯**。

    用途：修修在 Resolve 上手動微調過剪輯（最常見的是把段尾拉長讓句子講完）
    之後，字幕還停在原本的模型長度。重跑 director 會整條重建、洗掉手改，
    所以只換字幕。

    **timeline 比模型長 = 修修把尾段拉長了**——把 delta 加回最後一個保留段，
    重新對時，尾巴那幾句才有 CC。長片依 Q4b 不燒字幕只上 CC，缺這段等於
    上架的字幕檔少一句。
    """
    from build_resolve_project import connect_resolve
    from run_short_tighten import FORMAT_TIGHTEN

    c, w = _load_winner(episode_dir, cid)
    fmt = c.get("format", "short")
    cfg = _load_cfg(episode_dir, fmt)
    t0, t1 = float(c["t_start"]), float(c["t_end"])
    cuts = json.loads((episode_dir / TIGHTEN_DIR / f"{cid}_cuts.json").read_text(encoding="utf-8"))[
        "cuts"
    ]
    segs = _keep_segments(t0, t1, cuts, FORMAT_TIGHTEN[fmt]["min_keep_seg"])

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

    label = f"{FORMAT_LABEL[fmt]}{w['rank']} - {c['title']}（緊·導播）"
    tl = next(
        (
            t
            for i in range(1, project.GetTimelineCount() + 1)
            if (t := project.GetTimelineByIndex(i)) and t.GetName() == label
        ),
        None,
    )
    if tl is None:
        raise SystemExit(f"timeline「{label}」不存在——先跑一次 director")

    model_dur = sum(e - s for s, e in segs)
    actual_dur = (tl.GetEndFrame() - tl.GetStartFrame() + 1) / fps
    delta = actual_dur - model_dur
    if delta > 1.0 / fps:  # 一幀以上才算手改，浮點誤差不算
        segs = segs[:-1] + [(segs[-1][0], segs[-1][1] + delta)]
        logger.info(f"timeline 比模型長 {delta:.2f}s——尾段延長後重對時（修修手改）")
    elif delta < -1.0 / fps:
        logger.warning(f"timeline 比模型短 {-delta:.2f}s——尾端字幕會被截，請確認手改內容")

    project.SetCurrentTimeline(tl)
    for ti in range(1, tl.GetTrackCount("subtitle") + 1):
        items = tl.GetItemListInTrack("subtitle", ti) or []
        if items:
            tl.DeleteClips(items)
    seg_srt, n_cues = _retime_srt(episode_dir, cid, segs, cuts, fine=cfg["fine_subs"])
    srt_items = import_srt_tidy(mp, root, seg_srt)
    ok = bool(mp.AppendToTimeline(srt_items)) if srt_items else False
    pm.SaveProject()
    return {
        "status": "subs-refreshed" if ok else "append-failed",
        "timeline": label,
        "manual_tail_delta_sec": round(delta, 2),
        "srt": str(seg_srt),
        "cues": n_cues,
    }


def _grab_stills(
    resolve, project, tl, shots: list[dict], fps: float, out_dir: Path, opener_frames: int = 0
) -> list[str]:
    """render queue 渲單幀樣張驗構圖：每種（speaker, zoom）組合各一張。

    GrabStillFromCurrentFrame 在 20.3 scripting API 不可用，改走單幀
    render job（只刪自己加的 job，不動使用者排的）。
    """
    import time

    out_dir.mkdir(parents=True, exist_ok=True)
    seen: set[tuple[int, float]] = set()
    tl_start = tl.GetStartFrame()
    offset = opener_frames
    jobs: list[tuple[str, str]] = []
    if opener_frames:
        project.SetRenderSettings(
            {
                "MarkIn": tl_start + opener_frames // 2,
                "MarkOut": tl_start + opener_frames // 2,
                "TargetDir": str(out_dir),
                "CustomName": "opener",
            }
        )
        jid = project.AddRenderJob()
        if jid:
            jobs.append((jid, "opener"))
    for sh in shots:
        dur = int(sh["e"] * fps) - int(sh["s"] * fps)
        cam = sh.get("cam", "spk")
        # 長片的 zoom 全是 1.0，只用（spk, zoom）當 key 會只抽到兩張——
        # 把機位與 shot 類型也納入，talk / reaction / 全景各有樣張可驗
        key = (sh["spk"], sh["zoom"], cam, sh.get("kind", "talk"))
        if key not in seen and dur > int(fps):
            seen.add(key)
            frame = tl_start + offset + dur // 2
            prefix = f"{cam}{sh['spk']}_{sh.get('kind', 'talk')}_z{sh['zoom']:.2f}"
            project.SetRenderSettings(
                {
                    "MarkIn": frame,
                    "MarkOut": frame,
                    "TargetDir": str(out_dir),
                    "CustomName": prefix,
                }
            )
            jid = project.AddRenderJob()
            if jid:
                jobs.append((jid, prefix))
        offset += dur
    if not jobs:
        return []
    project.StartRendering([j for j, _ in jobs], isInteractiveMode=False)
    for _ in range(120):
        if not project.IsRenderingInProgress():
            break
        time.sleep(1)
    for jid, _ in jobs:
        project.DeleteRenderJob(jid)
    # 渲出來的單幀影片檔轉 PNG（容器由 project render 預設決定）
    import subprocess

    paths = []
    for _, prefix in jobs:
        vid = next(iter(out_dir.glob(f"{prefix}.*")), None)
        if vid and vid.suffix.lower() != ".png":
            png = out_dir / f"{prefix}.png"
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(vid)]
            subprocess.run(cmd + ["-frames:v", "1", str(png)], check=False)
            if png.exists():
                paths.append(str(png))
        elif vid:
            paths.append(str(vid))
    return paths


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="短片雙機位導播：說話者切鏡 + zoom punch")
    parser.add_argument("episode", help="episode 資料夾")
    parser.add_argument("--id", required=True, help="winner id（如 punch-S1）")
    parser.add_argument("--stills", help="物化後抓樣張到此資料夾（agent 校驗構圖）")
    parser.add_argument("--no-opener", action="store_true", help="不做開場上下分割")
    parser.add_argument(
        "--refresh-subs",
        action="store_true",
        help="只換（緊·導播）的字幕軌，不重建 timeline（修修手改剪輯後用）",
    )
    args = parser.parse_args(argv)
    if args.refresh_subs:
        print(json.dumps(refresh_subs(Path(args.episode), args.id), ensure_ascii=False, indent=1))
        return 0
    result = direct(
        Path(args.episode),
        args.id,
        Path(args.stills) if args.stills else None,
        opener=not args.no_opener,
    )
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
