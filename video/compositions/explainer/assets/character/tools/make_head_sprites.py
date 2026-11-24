# 從定稿圖切出頭部素材（透明底、臉內填白），給 figure.js 用
#   python make_head_sprites.py <character 資料夾> <輸出資料夾>
# 讀 turnaround_v1.png（五面）與 expressions_v1.png（3×3 表情），輸出 head_*.png ＋ heads.json
# heads.json 每筆：w,h（圖寬高）、top（頭頂 y）、chinX/chinY（下巴最低點）、height（頭頂到下巴）
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

src_dir, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
out_dir.mkdir(parents=True, exist_ok=True)


def extract(rgb, box, name, extra_ink=True, cut_extra=1, fixed_cut=None, keep_below=None):
    x0, y0, x1, y1 = box
    crop = np.pad(
        rgb[y0:y1, x0:x1], ((40, 40), (40, 40), (0, 0)), constant_values=255
    )  # 四邊墊白，避免頭貼邊
    g = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    ink = (g < 140).astype(np.uint8)
    # 正面臉的外框有缺口（logo 原設計），先閉合再找封閉區
    ink = cv2.morphologyEx(
        ink, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31))
    )
    n, lab, stats, cent = cv2.connectedComponentsWithStats(1 - ink, connectivity=4)
    H, W = g.shape
    border = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]])))
    # 頭的實心範圍＝外框以內全部（不是背景的地方）
    border.discard(0)  # 標籤 0 是墨線本身，不是背景
    sil = (~np.isin(lab, list(border))).astype(np.uint8)
    # 只留最大的一塊（頭＋脖子＋上身），去掉汗滴之類的小塊
    m, lab2, st2, _ = cv2.connectedComponentsWithStats(sil, connectivity=8)
    sil = (lab2 == 1 + int(np.argmax(st2[1:, cv2.CC_STAT_AREA]))).astype(np.uint8)
    # 找下巴：脖子是下巴、兩條脖子線、領口圍出的小白區；它的上緣＝下巴外框最低點
    wid = sil.sum(1).astype(float)
    ymax = int(np.argmax(wid))
    face = [i for i in range(1, n) if i not in border and stats[i, cv2.CC_STAT_AREA] > 3000]
    fbot = max(stats[i, cv2.CC_STAT_TOP] + stats[i, cv2.CC_STAT_HEIGHT] for i in face)
    necks = [
        i
        for i in range(1, n)
        if i not in border
        and stats[i, cv2.CC_STAT_TOP] >= fbot - 4
        and stats[i, cv2.CC_STAT_WIDTH] < 0.5 * wid[ymax]
        and stats[i, cv2.CC_STAT_AREA] > 150
    ]
    if fixed_cut is not None:
        yn = fixed_cut + 40  # +40：前面墊了白邊
    elif necks:
        yn = min(stats[i, cv2.CC_STAT_TOP] for i in necks)
    else:
        low = wid[ymax:]
        nz = low[low > 0]
        wmin = nz.min() if len(nz) else 0
        yn = ymax + next((i for i, w in enumerate(low) if 0 < w <= wmin * 1.35 + 4), len(low) - 1)
    cut = yn + cut_extra
    keep = None
    if keep_below is not None:  # 思考那格：手一路留到手腕
        kx0, kx1, ky1 = keep_below
        keep = sil.copy()
        keep[:cut] = 0
        keep[ky1 + 40 :] = 0
        keep[:, : kx0 + 40] = 0
        keep[:, kx1 + 40 :] = 0
    sil[cut:] = 0
    if fixed_cut is not None:
        # 下巴附近只留下巴本身的寬度（領口弧線從兩側接上來的那段切掉）
        ref = np.where(sil[cut - 14] > 0)[0]
        if len(ref):
            band = np.zeros_like(sil)
            band[cut - 12 : cut] = 1
            band[:, ref.min() : ref.max() + 1] = 0
            sil[band > 0] = 0
    else:
        near_face = cv2.dilate(
            np.isin(lab, face).astype(np.uint8),
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (35, 35)),
        )
        band = np.zeros_like(sil)
        band[max(0, yn - 40) : cut] = 1
        sil[(band > 0) & (near_face == 0)] = 0
    if keep is not None:
        sil = np.maximum(sil, keep)
    chin_in = cut
    dark = 1 - g.astype(np.float32) / 255
    alpha = np.where(sil > 0, 1.0, 0.0)
    if extra_ink:
        # 頭外面、下巴以上的墨線（汗滴等）也帶上
        near = cv2.dilate(sil, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (161, 161)))
        ext = (near > 0) & (sil == 0)
        ext[chin_in + 4 :] = False
        alpha = np.where(ext, np.clip(dark * 1.4, 0, 1), alpha)
    rgbo = np.where(sil[..., None] > 0, crop, 0).astype(np.uint8)  # 剪影外只會有墨線（黑）
    rgba = np.dstack([rgbo, (alpha * 255).astype(np.uint8)])
    ys, xs = np.where(alpha > 0.1)
    bx0, bx1, by0, by1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    rgba = rgba[by0:by1, bx0:bx1]
    # 頭頂、下巴最低點（以外框輪廓計）
    sy, sx = np.where(sil[by0:by1, bx0:bx1] > 0)
    top = int(sy.min())
    chinY = int(sy.max())
    chinX = float(sx[sy >= chinY - 2].mean())
    Image.fromarray(rgba).save(out_dir / f"head_{name}.png", optimize=True)
    return {
        "src": f"head_{name}.png",
        "w": int(rgba.shape[1]),
        "h": int(rgba.shape[0]),
        "top": top,
        "chinX": round(chinX, 1),
        "chinY": chinY,
        "height": chinY - top,
    }


meta = {}
ta = np.array(Image.open(src_dir / "turnaround_v1.png").convert("RGB"))
# 五面各自的頭部範圍（turnaround_v1.png，1536×1024）
for name, box in {
    "front": (40, 120, 330, 470),
    "q3": (340, 120, 620, 470),
    "side": (610, 120, 900, 470),
    "q3back": (920, 120, 1190, 470),
    "back": (1200, 120, 1500, 470),
}.items():
    meta[name] = extract(ta, box, name, extra_ink=False)
ex = np.array(Image.open(src_dir / "expressions_v1.png").convert("RGB"))
names = [
    "default",
    "laugh",
    "surprised",
    "think",
    "confused",
    "tired",
    "angry",
    "nervous",
    "determined",
]
for i, name in enumerate(names):
    c, r = i % 3, i // 3
    kb = (230, 335, 336) if name == "think" else None  # 思考：x 230–380、到格內 y 336 的手留著
    meta["x_" + name] = extract(
        ex,
        (c * 512, r * 341, (c + 1) * 512, (r + 1) * 341),
        "x_" + name,
        fixed_cut=281,
        keep_below=kb,
    )
json.dump(meta, open(out_dir / "heads.json", "w"), indent=1)
for k, v in meta.items():
    print(k, v)
