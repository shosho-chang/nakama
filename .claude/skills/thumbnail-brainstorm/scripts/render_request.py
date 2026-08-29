#!/usr/bin/env python3
"""render_request.py — 吃 gate 上組好的封面配方，render 一次（修修 2026-08-14 裁決）。

    python render_request.py --episode-slug 20260721-zhengguowei \
        --packaging-dir "G:/Footages/20260721 鄭國威/packaging" --cut-id full \
        --package-rank 3

修修原話：「先把標題、大字跟 cutout 選定之後，你再去做 render，這樣 render 一次
就好了。」gate（VPS）只寫 `approval.json` 的 `render_request`；出圖在桌機端，
就是本 script：

1. 讀所選 package 的 render_recipe（哪條標題／哪兩張臉／大字＋橘框詞）
2. 幾何走 solver：scale 由**基準 cutout 解一次後鎖定**（同角色同裁切框 → 同
   pixel scale，v2.5 教訓 19），本張只解 y（眼線對齊）與 x（各自 head_cols）
3. render → `occlusion_check` 兩輪線性內插收斂字塊左右遮蔽平衡
4. `face_measure render` 交付 gate（量成品像素，不是量參數）
5. 只回填所選 package 的 `render_recipe.rendered_png` 與縮圖

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
sys.path.insert(0, str(_SKILL_DIR))

from composition_receipt import build_receipt_plan  # noqa: E402

from shared.config import get_vault_path  # noqa: E402

CANVAS_H, CANVAS_W = 720.0, 1280.0
TARGET_HEAD_PX = 468.0  # N1 house style：頭高 65% 畫布
HOST_HEAD_X, GUEST_HEAD_X = 192.0, 1075.0  # 15% / 84%（設計系統驗收帶）
BALANCE_TOL = 600
_DIFF_RE = re.compile(r"左遮 (\d+)px² 右遮 (\d+)px²")

ACCENT_RGB = (243, 116, 37)  # #F37425
HL_GAP = 14  # 橘框四邊要留的視覺等距（px）
HL_TOL = 2  # 收斂門檻：四邊都在 ±2px 內就算平均
HL_CSS_PAD = (14, 14, 5, 14)  # composition CSS 的保底值（上,右,下,左）
# 橘框搜尋範圍：避開左下 EP 標籤與右下人名標籤（也是 accent 底色）
_HL_WINDOW = (300, 1000, 40, 600)  # x0, x1, y0, y1


def _measure_highlight(png: Path) -> tuple[int, int, int, int] | None:
    """量成品 PNG 裡橘框四邊的**實際視覺留白**（上,右,下,左）。

    瀏覽器端量不準（measureText 的 actualBoundingBox 與離屏 canvas 掃描都跟 DOM
    排版對不上，實測左右仍差 13–14px），所以量測放在這裡：對真正輸出的像素做。
    """
    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        return None

    a = np.asarray(Image.open(png).convert("RGB")).astype(int)
    x0, x1, y0, y1 = _HL_WINDOW
    win = a[y0:y1, x0:x1]
    box = np.abs(win - np.array(ACCENT_RGB)).sum(axis=2) < 40
    ys, xs = np.nonzero(box)
    if len(ys) == 0:
        return None
    by0, by1, bx0, bx1 = ys.min(), ys.max(), xs.min(), xs.max()
    sub = win[by0 : by1 + 1, bx0 : bx1 + 1]
    ink = (sub[:, :, 0] > 225) & (sub[:, :, 1] > 225) & (sub[:, :, 2] > 225)
    iy, ix = np.nonzero(ink)
    if len(iy) == 0:
        return None
    return (
        int(iy.min()),
        int(bx1 - bx0 - ix.max()),
        int(by1 - by0 - iy.max()),
        int(ix.min()),
    )


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


def _book_cover_layer(req: dict, vault: Path) -> tuple[dict, dict]:
    """Return the lossless N1 author/book layer carried by a package recipe."""
    if not req.get("book_cover"):
        return {}, {}
    return (
        {
            "book_cover_opacity": float(req.get("book_cover_opacity", 0.42)),
            "book_cover_brightness": float(req.get("book_cover_brightness", 0.38)),
            "book_cover_height_pct": float(req.get("book_cover_height_pct", 100)),
        },
        {"book_cover_data_url": str(vault / req["book_cover"])},
    )


CENTER_CANDIDATE_MARKER = "/center-candidates/"


def _refuse_watermarked_preview(center_visual_asset: str) -> None:
    """gate 上挑的是**浮水印預覽**，不是授權檔——直接 render 會把浮水印燒進封面。

    修修在 board 上挑候選圖時，存進配方的是 center-candidates/ 底下那份預覽
    （2026-08-29 新增的候選池）。桌機端要先照該候選的 Elements 品項網址取得正式
    授權檔、放進這一集的 packaging 目錄、把配方指過去，才可以 render。
    """
    if CENTER_CANDIDATE_MARKER in center_visual_asset.replace("\\", "/"):
        raise SystemExit(
            "\n".join(
                (
                    f"中央圖還指著候選池的浮水印預覽（{center_visual_asset}）——先下正式授權檔。",
                    "  1. 看 center-candidates/<cut>.json 裡該筆的 source（Elements 品項網址）",
                    "  2. 依 brook-director SKILL 的下載程序取正式檔，存進這一集 packaging 目錄",
                    "  3. 把 render_recipe.center_visual_asset 指過去，再跑一次",
                    "  直接 render 會把浮水印燒進封面。",
                )
            )
        )


def _build_reaction_spec(
    req: dict,
    *,
    vault: Path,
    cut_dir: Path,
    host_name: str,
    guest_name: str,
    host: dict,
    guest: dict,
) -> dict:
    """Build the lossless N2 spec carried by one package recipe."""
    center = req.get("center_geometry") or {}
    _refuse_watermarked_preview(str(req["center_visual_asset"]))
    return {
        "composition": "thumbnail_reaction",
        "variables": {
            "prop_position": "center",
            "prop_width_pct": float(center["width_pct"]),
            "prop_height_px": float(center["height_px"]),
            "prop_center_x_pct": float(center["x_pct"]),
            "prop_center_y_pct": float(center["y_pct"]),
            "frame_style": "skew",
            "caption": "",
            "host_height_pct": host["height_pct"],
            "host_x_pct": host["x_pct"],
            "host_y_pct": host["y_pct"],
            "guest_height_pct": guest["height_pct"],
            "guest_x_pct": guest["x_pct"],
            "guest_y_pct": guest["y_pct"],
            "person_glow_color": "#F37425",
            "inner_edge_fade_pct": 0,
            "logo_height_px": 92,
            "logo_position": "bottom-left",
            "palette": {"accent": "#F37425"},
        },
        "images": {
            "prop_image_data_url": str(vault / req["center_visual_asset"]),
            "host_cutout_data_url": str(cut_dir / host_name),
            "guest_cutout_data_url": str(cut_dir / guest_name),
            "bg_image_data_url": r"E:/data/podcast thumbnail background.png",
            "logo_data_url": r"E:/data/podcast thumbnail props/channel_logo_face_white.png",
        },
    }


def _update_selected_package(data: dict, cut_id: str, package_rank: int, req: dict) -> None:
    """Update exactly one package; fail loud instead of drifting another rank."""
    for cut in data.get("cuts", []):
        if cut.get("cut_id") != cut_id:
            continue
        for package in cut.get("packages", []):
            if package.get("title_rank") == package_rank:
                package["thumbnail_png"] = req["rendered_png"]
                package["host_cutout"] = req["host_cutout"]
                package["guest_cutout"] = req["guest_cutout"]
                package["render_recipe"] = req
                return
        raise SystemExit(f"{cut_id} 找不到 package rank {package_rank}")
    raise SystemExit(f"找不到 cut {cut_id}")


def _write_selected_package(paths: list[Path], cut_id: str, package_rank: int, req: dict) -> None:
    """Apply the same selected-package update to working and vault contracts."""
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        _update_selected_package(data, cut_id, package_rank, req)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _matches_legacy_approval_request(entry: dict | None, req: dict) -> bool:
    """Only mirror a real legacy request; JSON ``null`` is not a request mapping."""
    legacy = entry.get("render_request") if entry else None
    return isinstance(legacy, dict) and legacy.get("requested_at") == req.get("requested_at")


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


def _render_reaction_request(
    *,
    args: argparse.Namespace,
    vault: Path,
    ep_vault: Path,
    cut_dir: Path,
    req: dict,
    package_rank: int,
    host_name: str,
    guest_name: str,
    host: dict,
    guest: dict,
    entry: dict | None,
    approval: dict,
    approval_path: Path,
) -> int:
    """Render and persist one N2 recipe without falling through the N1 text path."""
    work_dir = args.packaging_dir / "_work"
    work_dir.mkdir(exist_ok=True)
    spec = _build_reaction_spec(
        req,
        vault=vault,
        cut_dir=cut_dir,
        host_name=host_name,
        guest_name=guest_name,
        host=host,
        guest=guest,
    )
    spec_path = work_dir / f"spec_req_{args.cut_id}_reaction.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    out = args.packaging_dir / f"pkg-{args.cut_id}-{package_rank}{args.out_suffix}.png"
    result = _run(
        [
            sys.executable,
            str(_SKILL_DIR / "render_still.py"),
            "--composition",
            "thumbnail_reaction",
            "--spec",
            str(spec_path),
            "--out",
            str(out),
        ]
    )
    if result.returncode != 0:
        raise SystemExit(f"render 失敗：{result.stderr[-500:]}")

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
    if args.out_suffix:
        print(f"→ {out}（比較板，未回填）")
        return 0

    receipt_spec = {
        "title_rank": package_rank,
        "thumbnail": str(out),
        "render_spec": str(spec_path),
        "host_cutout": req["host_cutout"],
        "guest_cutout": req["guest_cutout"],
        # 來歷跟著配方走（gate 在挑圖時寫進去）。少了它 build_receipt_plan 會擋下來
        # ——2026-08-29 我把 center_provenance 設成必填卻只接了 attach 那條路，
        # 於是每一次 gate 觸發的重新 render 都死在收據那一步。
        "center_provenance": req.get("center_provenance"),
    }
    plan = build_receipt_plan(
        spec=receipt_spec,
        episode=json.loads((ep_vault / "packages.json").read_text(encoding="utf-8"))["episode"],
        cut_id=args.cut_id,
        episode_slug=args.episode_slug,
        vault_root=vault,
    )
    ep_vault.mkdir(parents=True, exist_ok=True)
    (ep_vault / out.name).write_bytes(out.read_bytes())
    (ep_vault / plan.center_name).write_bytes(plan.center_source.read_bytes())
    (ep_vault / plan.sidecar_name).write_bytes(plan.sidecar_source.read_bytes())
    vault_receipts = ep_vault / "composition_receipts"
    vault_receipts.mkdir(exist_ok=True)
    (vault_receipts / plan.receipt_name).write_text(
        json.dumps(plan.payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    working_receipts = args.packaging_dir / "composition_receipts"
    working_receipts.mkdir(exist_ok=True)
    (working_receipts / plan.receipt_name).write_text(
        json.dumps(plan.payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    req["rendered_png"] = plan.payload["thumbnail_png"]
    req["geometry"] = {
        "host_height_pct": round(float(host["height_pct"]), 2),
        "host_x_pct": round(float(host["x_pct"]), 2),
        "host_y_pct": round(float(host["y_pct"]), 2),
        "guest_height_pct": round(float(guest["height_pct"]), 2),
        "guest_x_pct": round(float(guest["x_pct"]), 2),
        "guest_y_pct": round(float(guest["y_pct"]), 2),
    }
    if _matches_legacy_approval_request(entry, req):
        entry["render_request"] = req
        approval_path.write_text(
            json.dumps(approval, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    _write_selected_package(
        [args.packaging_dir / "packages.json", ep_vault / "packages.json"],
        args.cut_id,
        package_rank,
        req,
    )

    final_dir = args.packaging_dir.parent / "final"
    final_dir.mkdir(exist_ok=True)
    (final_dir / f"cover-{args.cut_id}.png").write_bytes(out.read_bytes())
    packages = json.loads((ep_vault / "packages.json").read_text(encoding="utf-8"))
    title_text = next(
        (
            title["text"]
            for cut in packages["cuts"]
            if cut["cut_id"] == args.cut_id
            for title in cut["titles"]
            if title["rank"] == req["title_rank"]
        ),
        "",
    )
    (final_dir / f"title-{args.cut_id}.txt").write_text(title_text + "\n", encoding="utf-8")
    print(f"→ {ep_vault / out.name}")
    print(f"→ {final_dir / f'cover-{args.cut_id}.png'}（定稿夾）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--episode-slug", required=True)
    ap.add_argument("--packaging-dir", type=Path, required=True)
    ap.add_argument("--cut-id", required=True)
    ap.add_argument("--package-rank", type=int, choices=(1, 2, 3))
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
    approval_path = ep_vault / "approval.json"
    approval = (
        json.loads(approval_path.read_text(encoding="utf-8"))
        if approval_path.is_file()
        else {"approvals": []}
    )
    entry = next((a for a in approval["approvals"] if a["cut_id"] == args.cut_id), None)
    vault_packages = json.loads((ep_vault / "packages.json").read_text(encoding="utf-8"))
    if args.package_rank is not None:
        raw_cut = next((c for c in vault_packages["cuts"] if c["cut_id"] == args.cut_id), None)
        raw_package = next(
            (
                package
                for package in (raw_cut or {}).get("packages", [])
                if package["title_rank"] == args.package_rank
            ),
            None,
        )
        if raw_package is None or not raw_package.get("render_recipe"):
            raise SystemExit(
                f"{args.cut_id} package {args.package_rank} 沒有 render_recipe——"
                "請修修先在 gate 選定 package 再存配方"
            )
        req = raw_package["render_recipe"]
    else:
        # Transitional fallback for approval files written before per-package recipes.
        if entry is None or not entry.get("render_request"):
            raise SystemExit(f"{args.cut_id} 沒有 render_request——請修修先在 gate 上組配方")
        req = entry["render_request"]
    package_rank = args.package_rank or int(req["title_rank"])

    host_name = Path(req["host_cutout"]).name
    guest_name = Path(req["guest_cutout"]).name

    given_geo = req.get("geometry") if req.get("geometry_manual") else None
    if given_geo:
        # 修修在 gate 上拖過了 → 照他的來，不再解算（2026-08-15：「cutout 的位置跟
        # 大小都沒有很確定」——solver 自洽不等於好看，眼睛是他的）
        host = {k: given_geo[f"host_{k}"] for k in ("height_pct", "x_pct", "y_pct")}
        guest = {k: given_geo[f"guest_{k}"] for k in ("height_pct", "x_pct", "y_pct")}
        print(f"[geometry] 用 gate 上調好的位置：host {host} guest {guest}")
    else:
        # scale 鎖定：基準 cutout 解一次（同角色同裁切框 = 同 scale）。Manual
        # recipes deliberately skip landmarks so records-only full-body cutouts remain usable.
        hb = _landmarks(manifest, args.host_baseline)
        gb = _landmarks(manifest, args.guest_baseline)
        host_h = (
            TARGET_HEAD_PX / (hb["chin"] - hb["head_top"]) * float(hb["cutout_h"]) / CANVAS_H * 100
        )
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

    if req.get("composition", "thumbnail_full") == "thumbnail_reaction":
        return _render_reaction_request(
            args=args,
            vault=vault,
            ep_vault=ep_vault,
            cut_dir=cut_dir,
            req=req,
            package_rank=package_rank,
            host_name=host_name,
            guest_name=guest_name,
            host=host,
            guest=guest,
            entry=entry,
            approval=approval,
            approval_path=approval_path,
        )

    # credit 來源優先序：CLI → 配方（gate 上可改）→ 舊 spec 撈一次當遷移。
    # 舊做法只有第三項，而且 glob 只看 packaging 根目錄——2026-08-15 把中間產物
    # 搬進 _work/ 之後就撈不到，整行抬頭從封面消失（修修回報）。
    credit = args.credit or (req.get("guest_credit") or "").strip()
    if not credit:
        for d in (args.packaging_dir, args.packaging_dir / "_work"):
            prev = sorted(d.glob("spec_pkg*.json"))
            if prev:
                credit = json.loads(prev[0].read_text(encoding="utf-8"))["variables"].get(
                    "guest_credit", ""
                )
                break

    hl_pad: list[int] = []

    # 中間產物（spec、textonly 對照圖、佔位圖）收進 _work/，packaging 根目錄只留
    # 定稿與資料檔（修修 2026-08-15：「package 那個資料夾裡面太亂了」）
    work_dir = args.packaging_dir / "_work"
    work_dir.mkdir(exist_ok=True)
    blank = work_dir / "_transparent1px.png"
    if not blank.is_file():
        # 以前這張是靠人手放進去的，新集數的 packaging 目錄沒有就 render 失敗
        from PIL import Image

        Image.new("RGBA", (1, 1), (0, 0, 0, 0)).save(blank)

    def build(center: float, textonly: bool) -> Path:
        book_variables, book_images = _book_cover_layer(req, vault)
        spec = {
            "variables": {
                "title_lines": req["big_text"],
                "highlight_text": req.get("highlight_text", ""),
                "highlight_pad": hl_pad,
                "ep_label": "",
                "guest_name": "",
                "guest_credit": credit,
                "host_height_pct": host["height_pct"],
                "host_x_pct": host["x_pct"],
                "host_y_pct": host["y_pct"],
                "guest_height_pct": guest["height_pct"],
                "guest_x_pct": guest["x_pct"],
                "guest_y_pct": guest["y_pct"],
                "title_max_width": int(req.get("title_max_width") or 580),
                "logo_height_px": 92,
                "text_center_pct": center,
                "person_glow_color": "#F37425",
                "inner_edge_fade_pct": 9,
                "palette": {"accent": "#F37425"},
                **book_variables,
            },
            "images": {
                "host_cutout_data_url": str(blank if textonly else cut_dir / host_name),
                "guest_cutout_data_url": str(blank if textonly else cut_dir / guest_name),
                "bg_image_data_url": r"E:/data/podcast thumbnail background.png",
                "logo_data_url": r"E:/data/podcast thumbnail props/channel_logo_face_white.png",
                **book_images,
            },
        }
        suffix = "_textonly" if textonly else ""
        path = work_dir / f"spec_req_{args.cut_id}{suffix}.json"
        path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    out = args.packaging_dir / f"pkg-{args.cut_id}-{package_rank}{args.out_suffix}.png"
    tout = work_dir / f"_textonly-req-{args.cut_id}.png"
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

    # 橘框四邊等距收斂（修修 2026-08-15：「間隔沒有平均」）。
    # 每個中文字的 ink 在 em 框裡偏移都不同，固定 padding 必然歪；量成品回推兩輪。
    if req.get("highlight_text"):
        # 量→補→出圖，最後一輪只量（確認收斂才不印警告）。要多輪是因為 text-indent
        # 拉字身時 shrink-to-fit 會跟著縮，單輪只吃掉約六成誤差 → 幾何收斂。
        for _ in range(5):
            gaps = _measure_highlight(out)
            if gaps is None:
                print("  橘框留白：量不到（跳過收斂）")
                break
            worst = max(abs(g - HL_GAP) for g in gaps)
            base = hl_pad or list(HL_CSS_PAD)
            print(
                f"  橘框留白 上{gaps[0]} 右{gaps[1]} 下{gaps[2]} 左{gaps[3]}"
                f"（目標 {HL_GAP}±{HL_TOL}）"
            )
            if worst <= HL_TOL:
                break
            # 左邊允許負值（composition 會轉成 text-indent）；上/右/下沒有負 padding
            hl_pad = [base[i] + (HL_GAP - gaps[i]) for i in range(4)]
            hl_pad[:3] = [max(0, v) for v in hl_pad[:3]]
            r = _run(
                [
                    sys.executable,
                    render,
                    "--composition",
                    "thumbnail_full",
                    "--spec",
                    str(build(center, False)),
                    "--out",
                    str(out),
                ]
            )
            if r.returncode != 0:
                raise SystemExit(f"render 失敗：{r.stderr[-500:]}")
        else:
            print("⚠ 橘框留白兩輪仍未收斂——字型可能有異常側距", file=sys.stderr)

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

    # 回填：vault 複製一份 + 所選 package recipe；其他兩個 package 不動。
    dst = ep_vault / out.name
    dst.write_bytes(out.read_bytes())
    req["rendered_png"] = f"Attachments/packaging/{args.episode_slug}/{out.name}"
    req["guest_credit"] = credit  # 從舊 spec 撈到的也回填，gate 才顯示得出來
    # 實際用的位置寫回 gate：solver 解完的數字要變成拖曳介面的起點，
    # 不然修修一打開排版預覽看到的是 composition 預設值（差了 30% 以上）
    req["geometry"] = {
        "host_height_pct": round(float(host["height_pct"]), 2),
        "host_x_pct": round(float(host["x_pct"]), 2),
        "host_y_pct": round(float(host["y_pct"]), 2),
        "guest_height_pct": round(float(guest["height_pct"]), 2),
        "guest_x_pct": round(float(guest["x_pct"]), 2),
        "guest_y_pct": round(float(guest["y_pct"]), 2),
    }
    if _matches_legacy_approval_request(entry, req):
        entry["render_request"] = req  # legacy watcher/UI compatibility only
        approval_path.write_text(
            json.dumps(approval, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    _write_selected_package(
        [args.packaging_dir / "packages.json", ep_vault / "packages.json"],
        args.cut_id,
        package_rank,
        req,
    )

    # 定稿夾（修修 2026-08-15）：上架只需要兩樣東西——封面跟標題。檔名固定、
    # 每次 render 覆蓋，所以這個資料夾永遠只有「現在這一版」，不會越積越多。
    final_dir = args.packaging_dir.parent / "final"
    final_dir.mkdir(exist_ok=True)
    (final_dir / f"cover-{args.cut_id}.png").write_bytes(out.read_bytes())
    title_text = next(
        (
            t["text"]
            for cut in json.loads((ep_vault / "packages.json").read_text(encoding="utf-8"))["cuts"]
            if cut["cut_id"] == args.cut_id
            for t in cut["titles"]
            if t["rank"] == req["title_rank"]
        ),
        "",
    )
    (final_dir / f"title-{args.cut_id}.txt").write_text(title_text + "\n", encoding="utf-8")

    print(f"→ {dst}")
    print(f"→ {final_dir / f'cover-{args.cut_id}.png'}（定稿夾）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
