#!/usr/bin/env python3
"""face_measure — 封面人臉量測的唯一合法來源（mediapipe，零目測、零 agent 讀圖）。

2026-08-05 安吉集三輪封面事故的終局工具。前三輪的共同根因：地標靠
「agent 目視 + alpha 啟發式」逐張量，noise ±5–10%，任何下游規則（臉高等大、
頭高等大）都把量測噪音直接放大成成品尺寸噪音。本工具兩端夾死：

- `cutouts`：對 manifest 裡每顆 cutout 跑 FaceLandmarker（iris 中點 = eye、
  landmark 152 = chin）+ alpha 頭頂規則（寬度達峰值 35% 首列）+ head_cols。
  `--write` 回寫 manifest。跨表情 IOD 離散度順帶輸出——同機位同裁切框下
  IOD 應近乎常數，偏差大 = 那格人有前傾/後仰，排版要另眼看待。
- `render`：對 render 出的成品 PNG 直接偵測左右兩張臉，量「觀眾實際看到的」
  臉高/眼線/位置，跨多張比一致性，PASS/FAIL 附 exit code。
  **這是交付 gate：solver 的 verify PASS 只代表參數自洽，不代表看起來對。**

用法：
    python face_measure.py cutouts --manifest <cutouts_manifest.json> [--write]
    python face_measure.py render --canvas-h 720 --host-ratio-target 1.0 \
        --png SL4.png --png SL3.png --png SL7.png
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MODEL_PATH = Path(os.environ.get("FACE_LANDMARKER_MODEL", r"E:\data\models\face_landmarker.task"))

# FaceLandmarker 478 點模型的固定索引（含虹膜）
IRIS_L, IRIS_R = 468, 473  # 左/右虹膜中心
CHIN = 152  # 下巴最低點（menton）

# alpha 頭頂規則（與 2026-08-05 manifest 重測一致）：排除耳機頭帶/稀疏髮絲
CROWN_PEAK_ZONE = 0.60  # 峰值取前 60% rows
CROWN_FRAC = 0.35  # 寬度達峰值 35% 的首列
ALPHA_ON = 20

# render QA 門檻（720p）。臉高會被姿勢/張嘴真實改變（大笑 eye-chin +9% 是
# 物理不是 bug），所以跨張一致性主尺 = IOD（瞳距，姿勢下最穩），臉高當寬鬆
# backstop 只抓 solver 級災難（2026-08-05 事故是 16–22%）。眼線在 crown 錨定
# （solver v3.1）下隨頭部姿勢漂是**合法**（TF 原版實測 ~10% canvas），跨張
# 漂移只做資訊輸出；硬檢查 = 每張實測眼位 vs solver 預測（帶 --spec 時）。
RENDER_IOD_SPREAD_TOL = 0.08  # 同角色跨張 IOD 離散 ≤8%
RENDER_FACE_SPREAD_TOL = 0.12  # 同角色跨張臉高離散 backstop ≤12%
RENDER_EYE_PRED_TOL = 10.0  # 每張：實測眼位 vs solver 預測 ≤10px
RENDER_RATIO_TOL = 0.12  # 包內 guest/host 臉高比 sanity ±0.12（精確值印出供人判）
SIDE_ZONE = 0.30  # 左右 30% 畫寬內的臉才算 host/guest（中央 prop 照的臉忽略）


def _landmarker(num_faces: int):
    if not MODEL_PATH.is_file():
        raise SystemExit(
            f"缺模型 {MODEL_PATH}——下載：curl -L -o \"{MODEL_PATH}\" "
            "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
            "face_landmarker/float16/latest/face_landmarker.task"
        )
    import mediapipe as mp
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision

    opts = vision.FaceLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=str(MODEL_PATH)),
        num_faces=num_faces,
        min_face_detection_confidence=0.4,
    )
    return mp, vision.FaceLandmarker.create_from_options(opts)


def _detect(mp, lmk, rgb: np.ndarray) -> list:
    img = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
    return lmk.detect(img).face_landmarks


def _face_metrics(lms, w: int, h: int) -> dict:
    """單張臉的量測：eye = 兩虹膜中點 row、chin = lm152、iod = 虹膜距。"""
    ex = (lms[IRIS_L].x + lms[IRIS_R].x) / 2 * w
    ey = (lms[IRIS_L].y + lms[IRIS_R].y) / 2 * h
    cy = lms[CHIN].y * h
    iod = float(np.hypot((lms[IRIS_L].x - lms[IRIS_R].x) * w, (lms[IRIS_L].y - lms[IRIS_R].y) * h))
    return {"eye_x": round(ex, 1), "eye": round(ey, 1), "chin": round(cy, 1),
            "face_h": round(cy - ey, 1), "iod": round(iod, 1)}


def _alpha_crown(alpha: np.ndarray) -> tuple[int, tuple[int, int]]:
    """頭頂 row（寬度達前 60% 峰值 35% 的首列）+ 該標準下的頭部左右界。"""
    on = alpha > ALPHA_ON
    widths = on.sum(axis=1)
    zone = widths[: int(len(widths) * CROWN_PEAK_ZONE)]
    peak = int(zone.max())
    if peak == 0:
        raise SystemExit("alpha 全空——不是有效 cutout")
    thr = peak * CROWN_FRAC
    crown = int(np.argmax(widths >= thr))
    return crown, peak


def _head_cols(alpha: np.ndarray, crown: int, chin: int) -> tuple[int, int]:
    band = alpha[crown : max(chin, crown + 1)] > ALPHA_ON
    cols = np.where(band.any(axis=0))[0]
    return int(cols[0]), int(cols[-1])


def cmd_cutouts(args) -> int:
    manifest_path = Path(args.manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest.get("validated") or {}
    mp, lmk = _landmarker(num_faces=1)
    rows, by_role = [], {}
    for name in sorted(entries):
        p = manifest_path.parent / name
        if not p.is_file():
            raise SystemExit(f"{name} 在 manifest 但檔案不存在：{p}")
        im = Image.open(p)
        if im.mode != "RGBA":
            raise SystemExit(f"{name} 不是 RGBA（{im.mode}）——去背壞掉，重 finalize")
        arr = np.array(im)
        alpha = arr[..., 3]
        # 透明區合成中灰再偵測（黑底會弄髒臉緣偵測）
        rgb = arr[..., :3].astype(np.float32)
        a = (alpha.astype(np.float32) / 255.0)[..., None]
        rgb = (rgb * a + 128.0 * (1 - a)).astype(np.uint8)
        faces = _detect(mp, lmk, rgb)
        if len(faces) != 1:
            raise SystemExit(f"{name} 偵測到 {len(faces)} 張臉（預期 1）——cutout 有問題")
        h, w = alpha.shape
        m = _face_metrics(faces[0], w, h)
        crown, _ = _alpha_crown(alpha)
        c0, c1 = _head_cols(alpha, crown, int(m["chin"]))
        old = entries[name].get("landmarks_px") or {}
        new = {
            "head_top": crown,
            "eye": round(m["eye"], 1),
            "chin": round(m["chin"], 1),
            "cutout_h": h,
            "cutout_w": w,
            "head_cols": [c0, c1],
            "iod": m["iod"],
            "eye_x": m["eye_x"],
            "_measured": f"{args.date} face_measure.py（mediapipe iris/chin + alpha crown {CROWN_FRAC:.0%}）",
            "_head_top_method": "alpha 寬度達前60%峰值 35% 的首列（程式量測，非目測）",
        }
        deltas = {
            k: round(float(new[k]) - float(old[k]), 1)
            for k in ("head_top", "eye", "chin")
            if k in old
        }
        rows.append((name, new, deltas))
        by_role.setdefault(name.split("_")[0], []).append((name, m["iod"]))
        if args.write:
            entries[name]["landmarks_px"] = new
    for name, new, deltas in rows:
        print(
            f"{name}: crown {new['head_top']} eye {new['eye']} chin {new['chin']} "
            f"iod {new['iod']} head_cols {new['head_cols']}  Δ舊值 {deltas or '—'}"
        )
    for role, pairs in by_role.items():
        iods = np.array([i for _, i in pairs])
        spread = float(iods.max() / iods.min() - 1)
        flag = "" if spread <= 0.04 else "  ⚠️ >4%：有格子明顯前傾/後仰，該格慎用"
        print(f"{role}: IOD 離散 {spread:.1%}（{iods.min():.1f}–{iods.max():.1f}px）{flag}")
    if args.write:
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"已回寫 {manifest_path}")
    return 0


def _expected_from_specs(args) -> list[dict] | None:
    """--spec（與 --png 一一對應）→ 每張的 solver 預測眼位（host/guest）。"""
    specs = args.spec or []
    if not specs:
        return None
    if len(specs) != len(args.png):
        raise SystemExit("--spec 數量需與 --png 一一對應")
    if not args.manifest:
        raise SystemExit("--spec 需要 --manifest（重算 solver 預測）")
    sys.path.insert(0, str(Path(__file__).parent))
    from layout_solve import solve_duo

    mani = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    out = []
    for sp in specs:
        refs = (json.loads(Path(sp).read_text(encoding="utf-8")).get("_solve")) or {}
        if not refs:
            raise SystemExit(f"{sp} 缺 _solve 中繼資料——無從重算預測")
        exp = solve_duo(
            mani,
            refs["host"],
            refs["guest"],
            args.canvas_h,
            float(refs.get("canvas_w", 1280)),
            guest_face_boost=float(refs.get("guest_face_boost", 1.0)),
            clip_frac=float(refs.get("clip_frac", 0.08)),
            outward_shift_pct=float(refs.get("outward_shift_pct", 5.0)),
            host_expr=refs.get("host_expr"),
            guest_expr=refs.get("guest_expr"),
            face_target_frac=float(refs.get("face_target_frac", 0.347)),
        )
        out.append({r: float(exp[f"_{r}_predicted"]["eye"]) for r in ("host", "guest")})
    return out


def cmd_render(args) -> int:
    expected = _expected_from_specs(args)
    mp, lmk = _landmarker(num_faces=6)
    per_png = {}
    ok = True
    for i, png in enumerate(args.png):
        arr = np.array(Image.open(png).convert("RGB"))
        h, w = arr.shape[:2]
        faces = _detect(mp, lmk, arr)
        sides: dict[str, dict] = {}
        for f in faces:
            m = _face_metrics(f, w, h)
            cx = m["eye_x"] / w
            role = "host" if cx < SIDE_ZONE else ("guest" if cx > 1 - SIDE_ZONE else None)
            if role and (role not in sides or m["face_h"] > sides[role]["face_h"]):
                sides[role] = m
        missing = {"host", "guest"} - set(sides)
        if missing:
            raise SystemExit(f"{Path(png).name}: 偵測不到 {missing}（左右 {SIDE_ZONE:.0%} 區內無臉）")
        per_png[Path(png).name] = sides
        r = sides["guest"]["face_h"] / sides["host"]["face_h"]
        print(
            f"{Path(png).name}: host 臉高 {sides['host']['face_h']} eye@{sides['host']['eye']} | "
            f"guest 臉高 {sides['guest']['face_h']} eye@{sides['guest']['eye']} | guest/host {r:.3f}"
        )
        if expected:
            for role in ("host", "guest"):
                d = abs(sides[role]["eye"] - expected[i][role])
                ok_p = d <= RENDER_EYE_PRED_TOL
                ok = ok and ok_p
                print(
                    f"  {role} 眼位 vs solver 預測 {expected[i][role]:.1f}：差 {d:.1f}px"
                    f"（≤{RENDER_EYE_PRED_TOL:.0f}）{'PASS' if ok_p else 'FAIL'}"
                )
    for role in ("host", "guest"):
        fh = np.array([v[role]["face_h"] for v in per_png.values()])
        iod = np.array([v[role]["iod"] for v in per_png.values()])
        ey = np.array([v[role]["eye"] for v in per_png.values()])
        f_spread = float(fh.max() / fh.min() - 1)
        i_spread = float(iod.max() / iod.min() - 1)
        ok_i = i_spread <= RENDER_IOD_SPREAD_TOL
        ok_f = f_spread <= RENDER_FACE_SPREAD_TOL
        ok = ok and ok_i and ok_f
        print(
            f"{role}: 跨張 IOD 離散 {i_spread:.1%}（≤{RENDER_IOD_SPREAD_TOL:.0%}）"
            f"{'PASS' if ok_i else 'FAIL'} | 臉高離散 {f_spread:.1%}"
            f"（≤{RENDER_FACE_SPREAD_TOL:.0%}）{'PASS' if ok_f else 'FAIL'} | "
            f"眼線跨張漂 {float(ey.max() - ey.min()):.1f}px（資訊：crown 錨定下隨姿勢漂為合法）"
        )
    for name, sides in per_png.items():
        r = sides["guest"]["face_h"] / sides["host"]["face_h"]
        ok_r = abs(r - args.host_ratio_target) <= RENDER_RATIO_TOL
        ok = ok and ok_r
        print(
            f"{name}: guest/host {r:.3f}（目標 {args.host_ratio_target}±{RENDER_RATIO_TOL}）"
            f"{'PASS' if ok_r else 'FAIL'}"
        )
    print("渲染 QA: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("cutouts")
    c.add_argument("--manifest", required=True)
    c.add_argument("--write", action="store_true")
    c.add_argument("--date", default="2026-08-05")
    r = sub.add_parser("render")
    r.add_argument("--png", action="append", required=True)
    r.add_argument("--spec", action="append", help="與 --png 一一對應；給了就多驗「實測眼位 vs solver 預測」")
    r.add_argument("--manifest", help="--spec 重算預測用")
    r.add_argument("--canvas-h", type=float, default=720)
    r.add_argument("--host-ratio-target", type=float, default=1.0,
                   help="guest/host 臉高比目標（= solver 的 guest_face_boost 感知校準值）")
    args = ap.parse_args()
    return cmd_cutouts(args) if args.cmd == "cutouts" else cmd_render(args)


if __name__ == "__main__":
    sys.exit(main())
