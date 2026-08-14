#!/usr/bin/env python3
"""render_request.py — 吃 gate 上組好的封面配方，render 一次（修修 2026-08-14 裁決）。

    python render_request.py --episode-slug 20260721-zhengguowei \
        --packaging-dir "G:/Footages/20260721 鄭國威/packaging" --cut-id full

修修原話：「先把標題、大字跟 cutout 選定之後，你再去做 render，這樣 render 一次
就好了。」gate（VPS）只寫 `approval.json` 的 `render_request`；出圖在桌機端，
就是本 script：

1. 讀 render_request（哪條標題／哪兩張臉／大字＋橘框詞）
2. 幾何走 solver：scale 由**基準 cutout 解一次後鎖定**（同角色同裁切框 → 同
   pixel scale，v2.5 教訓 19），本張只解 y（眼線對齊）與 x（各自 head_cols）
3. render → `occlusion_check` 兩輪線性內插收斂字塊左右遮蔽平衡
4. `face_measure render` 交付 gate（量成品像素，不是量參數）
5. 回填 `render_request.rendered_png`，並把該 rank 的 package 縮圖換成這張

不做的事：不改標題文字（那是 gate 的 `/title`）、不自己挑臉（那是修修的事）。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

_SKILL_DIR = Path(__file__).resolve().parent
_REPO = _SKILL_DIR.parents[3]
sys.path.insert(0, str(_REPO))

from shared.config import get_vault_path  # noqa: E402

CANVAS_H, CANVAS_W = 720.0, 1280.0
TARGET_HEAD_PX = 468.0  # N1 house style：頭高 65% 畫布
HOST_HEAD_X, GUEST_HEAD_X = 192.0, 1075.0  # 15% / 84%（設計系統驗收帶）
BALANCE_TOL = 600
_DIFF_RE = re.compile(r"左遮 (\d+)px² 右遮 (\d+)px²")


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(_REPO),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )


def _landmarks(manifest: dict, name: str) -> dict:
    entry = (manifest.get("validated") or {}).get(name)
    if entry is None or not entry.get("landmarks_px"):
        raise SystemExit(
            f"{name} 不在 cutouts_manifest.json 的 validated 清單（或缺 landmarks_px）——"
            "先跑 face_measure.py cutouts --write"
        )
    return entry["landmarks_px"]


def _solve(lm: dict, height_pct: float, eye_target: float, head_x: float, role: str) -> dict:
    ch = float(lm["cutout_h"])
    displayed = CANVAS_H * height_pct / 100
    scale = displayed / ch
    screen_top = eye_target - lm["eye"] * scale
    y_pct = (CANVAS_H - screen_top - displayed) / CANVAS_H * 100
    if role == "guest" and y_pct > 0:  # 正值會把人抬起來 → 底部露背景（v15 血淚）
        y_pct = 0.0
        screen_top = CANVAS_H - displayed
    mid = (lm["head_cols"][0] + lm["head_cols"][1]) / 2
    if role == "host":
        x_pct = (head_x - mid * scale) / CANVAS_W * 100
    else:
        right_edge = head_x + (float(lm["cutout_w"]) - mid) * scale
        x_pct = (CANVAS_W - right_edge) / CANVAS_W * 100
    return {
        "height_pct": round(height_pct, 2),
        "y_pct": round(y_pct, 2),
        "x_pct": round(x_pct, 2),
        "eye": round(screen_top + lm["eye"] * scale, 1),
        "crown": round(screen_top + lm["head_top"] * scale, 1),
        "scale": scale,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--episode-slug", required=True)
    ap.add_argument("--packaging-dir", type=Path, required=True)
    ap.add_argument("--cut-id", required=True)
    ap.add_argument("--host-baseline", default="host_v1_serious.png")
    ap.add_argument("--guest-baseline", default="guest_v1_serious.png")
    ap.add_argument("--credit", default="", help="來賓 credit（頭銜＋姓名）；空 = 沿用上一張 spec")
    ap.add_argument(
        "--eye-target",
        type=float,
        default=307.0,
        help="眼線落在畫布的 y（px）。**調小 = 兩人一起往上、headroom 變少**（修修 2026-08-14）",
    )
    ap.add_argument(
        "--guest-face-boost",
        type=float,
        default=1.0,
        help="來賓臉相對主持人的感知放大（指標等大 ≠ 感知等大；per-pairing 由修修拍板一次）",
    )
    ap.add_argument("--out-suffix", default="", help="輸出檔名後綴（做比較板時用）")
    args = ap.parse_args()

    # 每集拍板一次的感知常數（修修在比較板上點的那一檔）。CLI 明給就以 CLI 為準。
    geo_path = args.packaging_dir / "geometry.json"
    if geo_path.is_file():
        geo = json.loads(geo_path.read_text(encoding="utf-8"))
        given = set(sys.argv)
        if "--eye-target" not in given:
            args.eye_target = float(geo.get("eye_target", args.eye_target))
        if "--guest-face-boost" not in given:
            args.guest_face_boost = float(geo.get("guest_face_boost", args.guest_face_boost))
        print(
            f"[geometry.json] eye_target={args.eye_target} guest_face_boost={args.guest_face_boost}"
        )

    vault = get_vault_path()
    ep_vault = vault / "Attachments" / "packaging" / args.episode_slug
    cut_dir = vault / "Attachments" / "cutouts" / "podcast" / args.episode_slug
    manifest = json.loads((cut_dir / "cutouts_manifest.json").read_text(encoding="utf-8"))
    approval = json.loads((ep_vault / "approval.json").read_text(encoding="utf-8"))

    entry = next((a for a in approval["approvals"] if a["cut_id"] == args.cut_id), None)
    if entry is None or not entry.get("render_request"):
        raise SystemExit(f"{args.cut_id} 沒有 render_request——請修修先在 gate 上組配方")
    req = entry["render_request"]

    host_name = Path(req["host_cutout"]).name
    guest_name = Path(req["guest_cutout"]).name

    # scale 鎖定：基準 cutout 解一次（同角色同裁切框 = 同 scale，v2.5 教訓 19）
    hb, gb = _landmarks(manifest, args.host_baseline), _landmarks(manifest, args.guest_baseline)
    host_h = TARGET_HEAD_PX / (hb["chin"] - hb["head_top"]) * float(hb["cutout_h"]) / CANVAS_H * 100
    guest_h = (
        TARGET_HEAD_PX
        * args.guest_face_boost
        / (gb["chin"] - gb["head_top"])
        * float(gb["cutout_h"])
        / CANVAS_H
        * 100
    )

    hlm, glm = _landmarks(manifest, host_name), _landmarks(manifest, guest_name)
    guest = _solve(glm, guest_h, args.eye_target, GUEST_HEAD_X, "guest")
    host = _solve(hlm, host_h, guest["eye"], HOST_HEAD_X, "host")  # 眼線對齊來賓
    print(
        f"host {host_name}: h={host['height_pct']} y={host['y_pct']} "
        f"x={host['x_pct']} eye={host['eye']}"
    )
    print(
        f"guest {guest_name}: h={guest['height_pct']} y={guest['y_pct']} "
        f"x={guest['x_pct']} eye={guest['eye']}"
    )
    print(f"眼線差 {abs(host['eye'] - guest['eye']):.1f}px（門檻 10）")
    print(
        f"headroom：host crown {host['crown']:.0f}px（{host['crown'] / CANVAS_H:.1%}）"
        f" guest crown {guest['crown']:.0f}px（{guest['crown'] / CANVAS_H:.1%}）"
        f" · guest_face_boost={args.guest_face_boost}"
    )

    credit = args.credit
    if not credit:
        prev = sorted(args.packaging_dir.glob("spec_pkg*.json"))
        if prev:
            credit = json.loads(prev[0].read_text(encoding="utf-8"))["variables"].get(
                "guest_credit", ""
            )

    def build(center: float, textonly: bool) -> Path:
        blank = args.packaging_dir / "_transparent1px.png"
        spec = {
            "variables": {
                "title_lines": req["big_text"],
                "highlight_text": req.get("highlight_text", ""),
                "ep_label": "",
                "guest_name": "",
                "guest_credit": credit,
                "host_height_pct": host["height_pct"],
                "host_x_pct": host["x_pct"],
                "host_y_pct": host["y_pct"],
                "guest_height_pct": guest["height_pct"],
                "guest_x_pct": guest["x_pct"],
                "guest_y_pct": guest["y_pct"],
                "title_max_width": 580,
                "logo_height_px": 92,
                "text_center_pct": center,
                "person_glow_color": "#F37425",
                "inner_edge_fade_pct": 9,
                "palette": {"accent": "#F37425"},
            },
            "images": {
                "host_cutout_data_url": str(blank if textonly else cut_dir / host_name),
                "guest_cutout_data_url": str(blank if textonly else cut_dir / guest_name),
                "bg_image_data_url": r"E:/data/podcast thumbnail background.png",
                "logo_data_url": r"E:/data/podcast thumbnail props/channel_logo_face_white.png",
            },
        }
        suffix = "_textonly" if textonly else ""
        path = args.packaging_dir / f"spec_req_{args.cut_id}{suffix}.json"
        path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    out = args.packaging_dir / f"pkg-{args.cut_id}-{req['title_rank']}{args.out_suffix}.png"
    tout = args.packaging_dir / f"_textonly-req-{args.cut_id}.png"
    render = str(_SKILL_DIR / "render_still.py")
    occ = str(_SKILL_DIR / "occlusion_check.py")

    center, history = 50.0, []
    for attempt in range(3):
        for textonly, dest in ((False, out), (True, tout)):
            r = _run(
                [
                    sys.executable,
                    render,
                    "--composition",
                    "thumbnail_full",
                    "--spec",
                    str(build(center, textonly)),
                    "--out",
                    str(dest),
                ]
            )
            if r.returncode != 0:
                raise SystemExit(f"render 失敗：{r.stderr[-500:]}")
        c = _run([sys.executable, occ, str(out), str(tout)])
        m = _DIFF_RE.search(c.stdout or "")
        left, right = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
        print(f"  center={center}: 左遮 {left} 右遮 {right} 差 {abs(left - right)}")
        if c.returncode == 0:
            break
        history.append((center, left - right))
        if len(history) == 1:
            center = round(center + (1.5 if left > right else -1.5), 2)
        else:
            (c0, d0), (c1, d1) = history[-2], history[-1]
            center = round(c1 - d1 * (c1 - c0) / (d1 - d0), 2) if d1 != d0 else center
    else:
        print("⚠ 遮蔽平衡沒收斂——人工調 text_center 再重跑", file=sys.stderr)

    qa = _run(
        [
            sys.executable,
            str(_SKILL_DIR / "face_measure.py"),
            "render",
            "--canvas-h",
            "720",
            "--png",
            str(out),
        ]
    )
    print(qa.stdout.strip().splitlines()[-1] if qa.stdout else "(face_measure 無輸出)")

    if args.out_suffix:  # 比較板：只出圖，不回填（還沒定案）
        print(f"→ {out}（比較板，未回填）")
        return 0

    # 回填：vault 複製一份 + render_request.rendered_png + 該 rank 的 package 縮圖
    dst = ep_vault / out.name
    dst.write_bytes(out.read_bytes())
    req["rendered_png"] = f"Attachments/packaging/{args.episode_slug}/{out.name}"
    (ep_vault / "approval.json").write_text(
        json.dumps(approval, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for path in (args.packaging_dir / "packages.json", ep_vault / "packages.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        for cut in data["cuts"]:
            if cut["cut_id"] != args.cut_id:
                continue
            for p in cut["packages"]:
                if p["title_rank"] == req["title_rank"]:
                    p["thumbnail_png"] = req["rendered_png"]
                    p["host_cutout"] = req["host_cutout"]
                    p["guest_cutout"] = req["guest_cutout"]
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"→ {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
