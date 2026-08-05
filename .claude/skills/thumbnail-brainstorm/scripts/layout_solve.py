"""layout_solve — 封面人物幾何的確定性求解（零目測）。

2026-08-04 事故：目測讀數 ±5% + 確認偏誤把數學正確的排版「修」壞 → 地標
必須程式量。2026-08-05 事故：地標改「每張照片各量各解」後，姿勢噪音直接
變成成品噪音（跨張人物尺寸漂 16–22%、眼線漂 63px）→ scale 必須鎖定。

分工（缺一不可）：
- 地標：`face_measure.py cutouts --write`（mediapipe + alpha 規則）是唯一
  合法來源——agent 目視量地標已兩次釀禍，禁止。
- solve-duo：基準對解一次 scale/眼線後鎖定；表情版只解 y/x。
- verify：spec 參數 vs solver 重算的一致性——**只證明參數自洽**。
- ⚠️ 交付 gate 是 `face_measure.py render`（量 render 出的 PNG 上觀眾
  實際看到的臉）。verify PASS ≠ 看起來對——2026-08-05 三張 verify 全 PASS
  的成品，render 實測 host 臉跨張縮水 16%。

幾何模型（thumbnail_reaction / thumbnail_full 的 CSS 契約）：
    displayed_h = canvas_h * height_pct/100
    scale       = displayed_h / cutout_h
    bottom_off  = canvas_h * y_pct/100        # 負值 = 元素下緣沉到畫布外
    screen_top  = canvas_h - bottom_off - displayed_h
    screen_y(row) = screen_top + row * scale

用法：
    python layout_solve.py solve --manifest <m.json> --cutout host_v2_serious.png \
        --canvas-h 720 --target-head-px 400 --target-eye-px 288
    python layout_solve.py verify --manifest <m.json> --spec <spec.json> --canvas-h 720
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

EYE_TOL_PX = 10  # 修修 2026-07-29 驗收標準（720p）
HEAD_TOL_PX = 12  # 舊判準，僅供參考輸出
FACE_BOOST_TARGET = 1.05  # TF-duo 定案：guest 臉（眼-下巴）= host × 1.05
FACE_RATIO_TOL = 0.03


def _landmarks(manifest: dict, cutout: str) -> tuple[dict, float]:
    entry = (manifest.get("validated") or {}).get(cutout)
    if entry is None:
        raise SystemExit(f"{cutout} 不在 manifest validated 清單——中間迭代檔禁止排版")
    lm = entry.get("landmarks_px")
    if not lm:
        raise SystemExit(f"{cutout} 缺 landmarks_px（head_top/eye/chin/cutout_h）——先高倍精測補上")
    return lm, float(lm["cutout_h"])


def solve(manifest: dict, cutout: str, canvas_h: float, head_px: float, eye_px: float) -> dict:
    lm, ch = _landmarks(manifest, cutout)
    head_h = lm["chin"] - lm["head_top"]
    scale = head_px / head_h
    displayed_h = ch * scale
    height_pct = displayed_h / canvas_h * 100
    screen_top = eye_px - lm["eye"] * scale
    bottom_off = canvas_h - screen_top - displayed_h
    y_pct = bottom_off / canvas_h * 100
    return {
        "cutout": cutout,
        "height_pct": round(height_pct, 1),
        "y_pct": round(y_pct, 1),
        "predicted": _predict(lm, ch, canvas_h, height_pct, y_pct),
    }


def _predict(lm: dict, ch: float, canvas_h: float, height_pct: float, y_pct: float) -> dict:
    displayed_h = canvas_h * height_pct / 100
    scale = displayed_h / ch
    screen_top = canvas_h - canvas_h * y_pct / 100 - displayed_h
    p = {k: round(screen_top + lm[k] * scale, 1) for k in ("head_top", "eye", "chin")}
    p["head_h"] = round((lm["chin"] - lm["head_top"]) * scale, 1)
    return p


def verify(manifest: dict, spec: dict, canvas_h: float) -> int:
    v = spec["variables"]
    # 表情繼承版 spec（帶 _solve 中繼資料）：最強判準 = spec 參數必須與 solver
    # 重算完全一致（表情臉高比對 expr 對無意義——張嘴會撐長臉高，見規則 7）
    refs = spec.get("_solve")
    if refs:
        expected = solve_duo(
            manifest,
            refs["host"],
            refs["guest"],
            canvas_h,
            float(refs.get("canvas_w", 1280)),
            guest_face_boost=float(refs.get("guest_face_boost", 1.0)),
            clip_frac=float(refs.get("clip_frac", 0.08)),
            outward_shift_pct=float(refs.get("outward_shift_pct", 5.0)),
            host_expr=refs.get("host_expr"),
            guest_expr=refs.get("guest_expr"),
        )
        ok = True
        for role in ("host", "guest"):
            for k, want in expected[role].items():
                got = float(v.get(k, float("nan")))
                if abs(got - want) > 0.05:
                    print(f"{k}: spec {got} ≠ solver {want} FAIL")
                    ok = False
            p = expected[f"_{role}_predicted"]
            lock = expected["_eye_lock_px"]
            print(f"{role}: 頭頂 {p['head_top']}px 眼 {p['eye']}px（eye_lock {lock}）")
        print("solver 重算一致性: " + ("PASS" if ok else "FAIL"))
        return 0 if ok else 1
    out, preds = 0, {}
    for role in ("host", "guest"):
        url = (spec.get("images") or {}).get(f"{role}_cutout_data_url", "")
        if not url:
            continue
        name = Path(url).name
        lm, ch = _landmarks(manifest, name)
        h_pct = float(v[f"{role}_height_pct"])
        preds[role] = _predict(lm, ch, canvas_h, h_pct, float(v.get(f"{role}_y_pct", 0)))
        print(
            f"{role} ({name}): 頭頂 {preds[role]['head_top']}px  眼 {preds[role]['eye']}px  "
            f"下巴 {preds[role]['chin']}px  頭高 {preds[role]['head_h']}px"
        )
    if len(preds) == 2:
        eye_d = abs(preds["host"]["eye"] - preds["guest"]["eye"])
        # 等大判準 = 臉高（眼-下巴）比值落在 face_boost ± 容差——頭高含髮量
        # 不可比（TF-duo 定案規則 2+3；頭高差僅供參考印出）
        face_h = preds["host"]["chin"] - preds["host"]["eye"]
        face_g = preds["guest"]["chin"] - preds["guest"]["eye"]
        ratio = face_g / face_h
        ok_eye = eye_d <= EYE_TOL_PX
        ok_face = abs(ratio - FACE_BOOST_TARGET) <= FACE_RATIO_TOL
        head_d = abs(preds["host"]["head_h"] - preds["guest"]["head_h"])
        print(f"眼線差 {eye_d:.1f}px（≤{EYE_TOL_PX}）: {'PASS' if ok_eye else 'FAIL'}")
        print(
            f"臉高比 guest/host {ratio:.3f}（目標 {FACE_BOOST_TARGET}±{FACE_RATIO_TOL}）: "
            f"{'PASS' if ok_face else 'FAIL'}（頭高差 {head_d:.1f}px 含髮量僅供參考）"
        )
        out = 0 if (ok_eye and ok_face) else 1
    return out


def solve_duo(
    manifest: dict,
    host: str,
    guest: str,
    canvas_h: float = 720,
    canvas_w: float = 1280,
    guest_face_boost: float = 1.0,
    clip_frac: float = 0.08,
    outward_shift_pct: float = 5.0,
    host_expr: str | None = None,
    guest_expr: str | None = None,
) -> dict:
    """TF 式雙臉版式 v3（2026-08-05 安吉集三輪事故後定版）。

    ⛔ 歷史教訓（三輪都是「每張照片重新量、重新解」惹的禍）：
    - v1 臉高等大 + 繼承（謝伯讓集 OK 純屬兩人臉/頭比例剛好接近）
    - v2 臉高等大（安吉集：head_top 量到耳機頭帶 → 頭差 14%）
    - v3' 頭高等大 + 每表情重解（頭高被姿勢/髮頂噪音污染 ±8% → 跨張
      guest 頭 384→445px、host 臉 190→221px、眼線漂 63px——修修三連抱怨）
    每個單張標量（臉高/頭高/IOD）都會被姿勢真實改變 ±5–10%，靠任何
    「per-photo 等大規則」都是把量測噪音放大成成品噪音。

    v3 契約（物理：同人同機位同裁切框 → 同 pixel scale；IOD 佐證）：
    1. **量尺只解一次**（基準對 = 兩人正面 serious 定稿）然後鎖定：
       - s_g = canvas_h / (guest 頭頂→圖底) × guest_face_boost   [TF 框架]
       - s_h = s_g0 × geomean(臉高比, 頭高比)——臉（眼-下巴）與頭（crown-下巴）
         **同時**逼近等大；好地標下兩指標差 <2%，幾何平均把兩者都壓進 ±1%。
         差 >6% 代表地標可疑或髮型極端，警告並要求人工拍板。
    2. **眼線 = 全集一個常數** eye_lock（由基準解出）。
    3. **表情版鎖 scale，只解 y/x**：y 把該格 eye 釘上 eye_lock；若因此
       bottom_off > 0（人底下露背景縫）→ clamp 0 並回報眼線偏移量。
       x 照 5/6 出血規則用該格 head_cols。表情版裁切框尺寸必須與基準相同
       （不同 = 不可鎖，fail loud）。
    4. guest_face_boost 是**感知校準**，per-pairing 一次由修修拍板後鎖定
       （謝伯讓集 1.05 = 眼鏡正面臉補償；安吉集 1.0）。預設 1.0。
    5. 外側各切頭寬 clip_frac + 外移 outward_shift_pct（TF 規格，不變）。

    ⚠️ 本函式輸出只是「參數正確」。**交付 gate 是 face_measure.py render**
    ——對 render 出的 PNG 直接量兩張臉。參數自洽 ≠ 看起來對。
    """
    lm_h_ref, ch_h_ref = _landmarks(manifest, host)
    lm_g_ref, ch_g_ref = _landmarks(manifest, guest)
    for lm, name in ((lm_h_ref, host), (lm_g_ref, guest)):
        if "head_cols" not in lm or "cutout_w" not in lm:
            raise SystemExit(f"{name} 缺 head_cols/cutout_w——先跑 face_measure.py cutouts --write")

    actual = {"host": host_expr or host, "guest": guest_expr or guest}
    lms = {"host": (lm_h_ref, ch_h_ref), "guest": (lm_g_ref, ch_g_ref)}
    for role, ref_name in (("host", host), ("guest", guest)):
        if actual[role] != ref_name:
            lm_a, ch_a = _landmarks(manifest, actual[role])
            ref_lm, ref_ch = lms[role]
            if (ch_a, lm_a.get("cutout_w")) != (ref_ch, ref_lm.get("cutout_w")):
                raise SystemExit(
                    f"{actual[role]} 尺寸 {lm_a.get('cutout_w')}x{ch_a} ≠ 基準 "
                    f"{ref_lm.get('cutout_w')}x{ref_ch}——裁切框不同，scale 不可鎖定"
                )
            lms[role] = (lm_a, ch_a)

    # ── 規則 1：量尺從基準對解一次，表情版不得重解 ──
    s_g0 = canvas_h / (ch_g_ref - lm_g_ref["head_top"])
    face_ratio = (lm_g_ref["chin"] - lm_g_ref["eye"]) / (lm_h_ref["chin"] - lm_h_ref["eye"])
    head_ratio = (lm_g_ref["chin"] - lm_g_ref["head_top"]) / (
        lm_h_ref["chin"] - lm_h_ref["head_top"]
    )
    warnings = []
    if abs(face_ratio / head_ratio - 1) > 0.06:
        warnings.append(
            f"基準對臉高比 {face_ratio:.3f} 與頭高比 {head_ratio:.3f} 差 >6%——"
            "地標可疑或髮型極端，幾何平均解需人工確認"
        )
    s_h = s_g0 * (face_ratio * head_ratio) ** 0.5
    s_g = s_g0 * guest_face_boost
    # ── 規則 2：眼線常數由基準解出（表情版共用，跨包共用）──
    eye_lock = (lm_g_ref["eye"] - lm_g_ref["head_top"]) * s_g0

    out = {}
    roles = (
        ("host", actual["host"], *lms["host"], s_h),
        ("guest", actual["guest"], *lms["guest"], s_g),
    )
    for role, name, lm, ch, s in roles:
        displayed_h = ch * s
        screen_top = eye_lock - lm["eye"] * s
        bottom_off = canvas_h - screen_top - displayed_h
        if bottom_off > 0:  # 規則 3 clamp：人底下不許露背景縫
            warnings.append(
                f"{name} 依眼線鎖需上抬 {bottom_off:.1f}px 會露底縫——clamp 貼底，"
                f"眼線下移 {bottom_off:.1f}px（該格前傾/低頭較多，可換格）"
            )
            bottom_off = 0.0
        y_pct = bottom_off / canvas_h * 100
        head_w = (lm["head_cols"][1] - lm["head_cols"][0]) * s
        clip = clip_frac * head_w
        if role == "host":  # 左緣錨定
            x_pct = (-clip - lm["head_cols"][0] * s) / canvas_w * 100 - outward_shift_pct
        else:  # 右緣錨定
            shift = clip + (lm["cutout_w"] - lm["head_cols"][1]) * s
            x_pct = -shift / canvas_w * 100 - outward_shift_pct
        out[role] = {
            f"{role}_height_pct": round(displayed_h / canvas_h * 100, 1),
            f"{role}_x_pct": round(x_pct, 1),
            f"{role}_y_pct": round(y_pct, 1),
        }
        out[f"_{role}_predicted"] = _predict(lm, ch, canvas_h, displayed_h / canvas_h * 100, y_pct)
    out["_eye_lock_px"] = round(eye_lock, 1)
    out["_scale_lock"] = {
        "host": round(s_h, 6),
        "guest": round(s_g, 6),
        "face_ratio_ref": round(face_ratio, 4),
        "head_ratio_ref": round(head_ratio, 4),
    }
    if warnings:
        out["_warnings"] = warnings
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("solve")
    s.add_argument("--manifest", required=True)
    s.add_argument("--cutout", required=True)
    s.add_argument("--canvas-h", type=float, default=720)
    s.add_argument("--target-head-px", type=float, required=True)
    s.add_argument("--target-eye-px", type=float, required=True)
    d = sub.add_parser("solve-duo")
    d.add_argument("--manifest", required=True)
    d.add_argument("--host", required=True)
    d.add_argument("--guest", required=True)
    d.add_argument("--canvas-h", type=float, default=720)
    d.add_argument("--canvas-w", type=float, default=1280)
    d.add_argument("--guest-face-boost", type=float, default=1.0,
                   help="感知校準，per-pairing 修修拍板後鎖定（謝伯讓 1.05；安吉 1.0）")
    d.add_argument("--clip-frac", type=float, default=0.08)
    d.add_argument("--outward-shift-pct", type=float, default=5.0)
    d.add_argument("--host-expr", default=None, help="表情版 cutout（繼承 --host 的 scale）")
    d.add_argument("--guest-expr", default=None, help="表情版 cutout（繼承 --guest 的 scale）")
    v = sub.add_parser("verify")
    v.add_argument("--manifest", required=True)
    v.add_argument("--spec", required=True)
    v.add_argument("--canvas-h", type=float, default=720)
    a = ap.parse_args()
    manifest = json.loads(Path(a.manifest).read_text(encoding="utf-8"))
    if a.cmd == "solve":
        print(
            json.dumps(
                solve(manifest, a.cutout, a.canvas_h, a.target_head_px, a.target_eye_px),
                ensure_ascii=False,
                indent=1,
            )
        )
        return 0
    if a.cmd == "solve-duo":
        print(
            json.dumps(
                solve_duo(
                    manifest,
                    a.host,
                    a.guest,
                    a.canvas_h,
                    a.canvas_w,
                    a.guest_face_boost,
                    a.clip_frac,
                    a.outward_shift_pct,
                    a.host_expr,
                    a.guest_expr,
                ),
                ensure_ascii=False,
                indent=1,
            )
        )
        return 0
    return verify(manifest, json.loads(Path(a.spec).read_text(encoding="utf-8")), a.canvas_h)


if __name__ == "__main__":
    sys.exit(main())
