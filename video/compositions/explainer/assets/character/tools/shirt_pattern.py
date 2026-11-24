# 把品牌 Motif A/B（shards-band-gray.png 的橘色碎片，原圖裁切、不重畫）印到五面 T 恤上，
# 做幾種擺法比較
# 用法：python shirt_pattern.py <turnaround.png> <shards.png> <outdir>
import sys

import cv2
import numpy as np
from PIL import Image

src = np.array(Image.open(sys.argv[1]).convert("RGB"))
pat = np.array(Image.open(sys.argv[2]).convert("RGB")).astype(int)
out = sys.argv[3]
H, W = src.shape[:2]
ORANGE = np.array([0xF5, 0x73, 0x31])

# 碎片遮罩：shards-band-gray 裡接近橘色的像素
pm = (np.abs(pat - ORANGE).sum(-1) < 120).astype(np.uint8)
SCALE = float(sys.argv[4]) if len(sys.argv) > 4 else 0.24  # 碎片縮到適合 T 恤的大小
pm = cv2.resize(pm, None, fx=SCALE, fy=SCALE, interpolation=cv2.INTER_AREA)
pm = np.tile(pm, (3, 2))  # 拼接，讓整件 T 恤都蓋得到
PH, PW = pm.shape

# 先把現有的橘色胸標刷白，才能 flood fill 出整塊 T 恤
img = src.copy()
isor = (np.abs(img.astype(int) - ORANGE).sum(-1) < 160) & (img[..., 0] > 180)
isor = cv2.dilate(isor.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
img[isor] = 255

SEEDS = {1: (195, 560), 2: (490, 560), 3: (727, 580), 4: (1050, 560), 5: (1350, 560)}


def torso_mask(seed):
    g = (np.array(Image.fromarray(img).convert("L")) > 200).astype(np.uint8)
    ff = np.zeros((H + 2, W + 2), np.uint8)
    work = g.copy() * 255
    cv2.floodFill(work, ff, seed, 128, loDiff=0, upDiff=0, flags=4)
    m = work == 128
    return m


masks = {k: torso_mask(v) for k, v in SEEDS.items()}
for k, m in masks.items():
    ys, xs = np.where(m)
    print(k, "torso bbox", xs.min(), xs.max(), ys.min(), ys.max(), "area", m.sum())


def apply(region_fn, crop_off, name):
    res = img.copy()
    for k, m in masks.items():
        ys, xs = np.where(m)
        x0, y0, x1, y1 = xs.min(), ys.min(), xs.max(), ys.max()
        reg = region_fn(k, m, x0, y0, x1, y1)
        # 碎片圖貼齊軀幹左上角；側面／斜面再位移一點，像布料繞到側邊
        ox, oy = crop_off(k)
        tile = np.zeros((H, W), bool)
        hh, ww = min(PH - oy, H - y0), min(PW - ox, W - x0)
        tile[y0 : y0 + hh, x0 : x0 + ww] = pm[oy : oy + hh, ox : ox + ww] > 0
        sel = reg & tile
        # 只塗原本是白色的地方，黑線永遠在上面
        white = res.astype(int).sum(-1) > 600
        res[sel & white] = ORANGE
    Image.fromarray(res).save(f"{out}/{name}.png")


def offs(k):
    return {1: (20, 30), 2: (40, 30), 3: (70, 30), 4: (20, 30), 5: (20, 30)}[k]


# B 胸前橫帶：軀幹上方 18%–42% 高度的一條帶子
def band(k, m, x0, y0, x1, y1):
    r = np.zeros_like(m)
    a, b = y0 + int((y1 - y0) * 0.18), y0 + int((y1 - y0) * 0.42)
    r[a:b] = True
    return m & r


# C 下擺碎片：軀幹下方 45% 起
def hem(k, m, x0, y0, x1, y1):
    r = np.zeros_like(m)
    r[y0 + int((y1 - y0) * 0.55) :] = True
    return m & r


# D 整件印花
def full(k, m, x0, y0, x1, y1):
    return m


apply(band, offs, "opt_B_band")
apply(hem, lambda k: (offs(k)[0], offs(k)[1] + 25), "opt_C_hem")
apply(full, offs, "opt_D_full")
