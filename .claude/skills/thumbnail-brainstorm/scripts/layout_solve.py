"""layout_solve — 封面人物幾何的確定性求解與驗收（零目測）。

2026-08-04 事故根因：目測讀數 ±5% 誤差 + 確認偏誤 + bottom 錨定負偏移方向
搞反，把一版數學上正確的排版「修」壞了。本工具把人從量測迴路拿掉：

- 地標（頭頂/眼睛/下巴的 cutout 像素 row）一次性高倍精測後寫進
  `cutouts_manifest.json` 的 `landmarks_px`
- solve：給目標（頭高 px、眼線 px）→ 反解 height_pct / y_pct
- verify：給 spec → 純數學預測螢幕上的頭頂/眼線/下巴位置，直接判 PASS/FAIL
  （眼線差 ≤10px、頭高差 ≤12px）——render 前就知道對不對

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
HEAD_TOL_PX = 12


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
        head_d = abs(preds["host"]["head_h"] - preds["guest"]["head_h"])
        ok_eye, ok_head = eye_d <= EYE_TOL_PX, head_d <= HEAD_TOL_PX
        print(f"眼線差 {eye_d:.1f}px（≤{EYE_TOL_PX}）: {'PASS' if ok_eye else 'FAIL'}")
        print(f"頭高差 {head_d:.1f}px（≤{HEAD_TOL_PX}）: {'PASS' if ok_head else 'FAIL'}")
        out = 0 if (ok_eye and ok_head) else 1
    return out


def solve_duo(
    manifest: dict,
    host: str,
    guest: str,
    canvas_h: float = 720,
    canvas_w: float = 1280,
    guest_face_boost: float = 1.05,
    clip_frac: float = 0.08,
) -> dict:
    """TF 式雙臉版式（修修 2026-08-04 謝伯讓集定案）的通用求解。

    規則（跨集不變；每集只要 landmarks 換新）：
    1. guest 基準 scale = 頭頂到圖底剛好填滿畫布（headroom 0 + 下緣貼底的耦合解）
    2. host 臉高（眼-下巴）= guest 基準臉高（等大用臉不用頭——蓬髮吃頭高額度）
    3. guest 再 × face_boost（感知校準：正面臉+眼鏡看起來小一號，修修 A/B 選 +5%）
    4. 兩人眼線鎖同一水平（以 guest 基準解的眼位為準）
    5. 外側各切頭寬 clip_frac（頭 92% 可見，TF 規格）；頭界用 alpha bbox 不含目測
    """
    lm_h, ch_h = _landmarks(manifest, host)
    lm_g, ch_g = _landmarks(manifest, guest)
    for lm, name in ((lm_h, host), (lm_g, guest)):
        if "head_cols" not in lm or "cutout_w" not in lm:
            raise SystemExit(f"{name} 缺 head_cols/cutout_w——先量 alpha bbox 補進 manifest")

    s_g0 = canvas_h / (ch_g - lm_g["head_top"])  # 規則 1
    eye_ref = (lm_g["eye"] - lm_g["head_top"]) * s_g0  # 規則 4 的鎖定眼位
    s_h = (lm_g["chin"] - lm_g["eye"]) * s_g0 / (lm_h["chin"] - lm_h["eye"])  # 規則 2
    s_g = s_g0 * guest_face_boost  # 規則 3

    out = {}
    roles = (("host", host, lm_h, ch_h, s_h), ("guest", guest, lm_g, ch_g, s_g))
    for role, name, lm, ch, s in roles:
        displayed_h = ch * s
        screen_top = eye_ref - lm["eye"] * s
        y_pct = (canvas_h - screen_top - displayed_h) / canvas_h * 100
        head_w = (lm["head_cols"][1] - lm["head_cols"][0]) * s
        clip = clip_frac * head_w
        if role == "host":  # 左緣錨定
            x_pct = (-clip - lm["head_cols"][0] * s) / canvas_w * 100
        else:  # 右緣錨定
            shift = clip + (lm["cutout_w"] - lm["head_cols"][1]) * s
            x_pct = -shift / canvas_w * 100
        out[role] = {
            f"{role}_height_pct": round(displayed_h / canvas_h * 100, 1),
            f"{role}_x_pct": round(x_pct, 1),
            f"{role}_y_pct": round(y_pct, 1),
        }
        out[f"_{role}_predicted"] = _predict(lm, ch, canvas_h, displayed_h / canvas_h * 100, y_pct)
    out["_eye_lock_px"] = round(eye_ref, 1)
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
    d.add_argument("--guest-face-boost", type=float, default=1.05)
    d.add_argument("--clip-frac", type=float, default=0.08)
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
                ),
                ensure_ascii=False,
                indent=1,
            )
        )
        return 0
    return verify(manifest, json.loads(Path(a.spec).read_text(encoding="utf-8")), a.canvas_h)


if __name__ == "__main__":
    sys.exit(main())
