# 轉面圖修正 v2：第二面鞋朝右；側面頭沿用原圖側臉，只把「頭頂弧線 → 額頭 → 臉」接成一條平滑線
# 用法：python fix2.py <src.png> <out.png>
import sys, math, numpy as np
from PIL import Image, ImageDraw

A = np.array(Image.open(sys.argv[1]).convert("RGB")).copy()
HH, WW = A.shape[:2]
blk = np.array(Image.fromarray(A).convert("L")) < 128
S, SW = 4, 13

def runs(row, x0, x1):
    xs = np.where(row[x0:x1])[0] + x0
    out, s = [], None
    for x in xs:
        if s is None: s = p = x; continue
        if x != p + 1: out.append((int(s), int(p))); s = x
        p = x
    if s is not None: out.append((int(s), int(p)))
    return out

# ---------- 1. 第二面的腳：鏡射成朝右 ----------
LA, LB, Y0, Y1 = 455, 517, 836, 876
tpl = A[Y0:Y1 + 1, 468:546][:, ::-1].copy()
tg = np.array(Image.fromarray(tpl).convert("L")) < 128
fill = np.zeros_like(tg)
for r in range(tg.shape[0]):
    xs = np.where(tg[r])[0]
    if len(xs): fill[r, xs[0]:xs[-1] + 1] = True
A[Y0:Y1 + 1, 390:552] = 255
for lx in (LA, LB):
    A[Y0:Y1 + 1, lx - 8:lx + 9] = A[Y0 - 4, lx - 8:lx + 9]
for lx in (LB, LA):
    reg = A[Y0:Y1 + 1, lx - 28:lx - 28 + tpl.shape[1]]
    reg[fill] = tpl[fill]

# ---------- 2. 側面額頭接縫 ----------
# 只補有凸角的 196–243 列：三次 Hermite 曲線，兩端「位置＋斜率」都對齊原線，接點不轉折
G = np.array(Image.fromarray(A).convert("L")).astype(float)
def center(y):  # 原線中線（次像素：第一段黑的左右緣灰階內插取中點）
    row = G[int(y), 640:760]; i = int(np.argmax(row < 128))
    xl = 640 + i - 1 + (row[i - 1] - 128) / (row[i - 1] - row[i])
    j = i + int(np.argmax(row[i:] >= 128))
    xr = 640 + j - 1 + (128 - row[j - 1]) / (row[j] - row[j - 1])
    return (xl + xr) / 2
H0, H1 = 182, 262
x0, x1 = center(H0), center(H1)
m0 = (center(H0 + 2) - center(H0 - 2)) / 4      # 斜率 dx/dy
m1 = (center(H1 + 3) - center(H1 - 3)) / 6
def fx(y):
    if y <= H0: return center(y)
    if y >= H1: return center(y)
    t = (y - H0) / (H1 - H0); L = H1 - H0
    return ((2*t**3 - 3*t**2 + 1) * x0 + (t**3 - 2*t**2 + t) * L * m0
            + (-2*t**3 + 3*t**2) * x1 + (t**3 - t**2) * L * m1)
E0, E1 = H0 - 2, H1 + 2
for y in range(E0, E1 + 1):            # 擦掉舊外框：每列第一段黑（含反鋸齒邊）
    a0, a1 = runs(blk[y], 660, 760)[0]
    A[y, a0 - 2:min(a1 + 3, 706)] = 255
for y in range(218, 247):              # 鋸齒第一段（外框 → 谷底）整段擦掉重畫
    A[y, 680:706 if y < 241 else 704] = 255
# 4x 超取樣畫：新外框段＋鋸齒第一段（外框 → 第一個谷底 (706,244)）
ink = Image.new("L", (WW * S, HH * S), 0); d = ImageDraw.Draw(ink)
r = SW * S / 2
def dot(p): d.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=255)
def stroke(pts):
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        n = max(1, int(math.hypot(x1 - x0, y1 - y0) / 2))
        for t in np.linspace(0, 1, n + 1): dot((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
P = lambda x, y: (x * S, y * S)
stroke([P(fx(y), y) for y in np.arange(E0 - 3, E1 + 3.5, 0.5)])
hy = 226
stroke([P(fx(hy), hy), P(706.5, 243.5)])
# ---------- 3. 側面眉眼：眉毛接到鼻樑（臉的輪廓線），眼睛獨立成半月 ----------
# 原圖眉毛＝第 264–275 列橫線，經左邊直筆連到下方半月眼；改成眉毛往左接到輪廓、拿掉直筆
for y in range(262, 312):
    A[y, 689:736] = 255
BROW_DEG = float(sys.argv[3]) if len(sys.argv) > 3 else 5.0   # 眉毛往後上揚的角度
bl = (center(270), 269.75); br = (732.0, 269.75 - (732.0 - center(270)) * math.tan(math.radians(BROW_DEG)))
ux, uy = br[0] - bl[0], br[1] - bl[1]; n = math.hypot(ux, uy); nx, ny = -uy / n * 5.75, ux / n * 5.75   # 半粗 5.75（原圖 11.5）
d.polygon([P(bl[0] + nx, bl[1] + ny), P(br[0] + nx, br[1] + ny), P(br[0] - nx, br[1] - ny), P(bl[0] - nx, bl[1] - ny)], fill=255)
ecx, ebot, er = 709, 309.5, 18         # 半月眼：圓心、平底、半徑（同原圖 691–727、底 309）
d.pieslice([P(ecx - er, ebot - er), P(ecx + er, ebot + er)], 180, 360, fill=255)
# ---------- 4. 第二面領口黑塊：原圖下巴與領口之間被填黑，擦掉、把下巴弧和左領口弧接回來 ----------
# 下巴（頭外框）中線：左段 x444–466、右段 x520–564（量自原圖每欄黑段中點）
chinL = [(444, 399.0), (448, 401.0), (452, 403.5), (456, 407.0), (460, 411.5), (464, 415.5)]
chinR = [(520, 442.0), (536, 438.0), (540, 435.5), (544, 433.0), (548, 430.5), (552, 426.5), (556, 421.5), (560, 416.0)]
cx_, cy_ = zip(*(chinL + chinR)); cc = np.polyfit(cx_, cy_, 4); chin = lambda x: float(np.polyval(cc, x))
# 左領口弧中線：x424–464，往右延伸到碰到下巴
arcL = [(424, 456.5), (428, 452.0), (432, 448.0), (436, 444.5), (440, 442.0), (444, 439.5), (448, 437.5), (452, 435.5), (456, 434.0), (460, 433.0), (464, 432.0)]
ax_, ay_ = zip(*arcL); ca = np.polyfit(ax_, ay_, 2); arc = lambda x: float(np.polyval(ca, x))
# 原圖左領口弧在 x≈470 就到頂往下彎、碰不到下巴（黑塊就是填在這個缺口裡）。
# 改成像正面一樣往上接進下巴：從 x=452 起，用 Hermite 曲線接到下巴上的 (478, chin(478))
hx0, hx1 = 452.0, 478.0
hy0, hy1 = arc(hx0), chin(hx1)
hm0 = (arc(hx0 + 1) - arc(hx0 - 1)) / 2; hm1 = -0.75          # 末端以約 37° 往上插進下巴
def arc2(x):
    t = (x - hx0) / (hx1 - hx0); L = hx1 - hx0
    return ((2*t**3 - 3*t**2 + 1) * hy0 + (t**3 - 2*t**2 + t) * L * hm0
            + (-2*t**3 + 3*t**2) * hy1 + (t**3 - t**2) * L * hm1)
xm = hx1
# 擦掉黑塊：下巴中線以下、x 462–527、y ≤ 460 的範圍（右領口弧從 x≥528 開始，不動）
for x in range(452, 528):
    ytop = int(chin(x) + SW / 2 + 1)
    A[ytop:461, x] = 255
for x in range(462, 528):          # 下巴線本身也擦掉重畫（原本跟黑塊黏在一起）
    A[int(chin(x) - 9):int(chin(x) + 9), x] = 255
stroke([P(x, chin(x)) for x in np.arange(458, 532, 0.5)])
# 左領口弧：x424 以後整段擦掉，用擬合弧（到 452）＋Hermite（到下巴）一筆重畫，避免新舊接縫
for x in range(430, 452):
    A[int(arc(x) - 10):int(arc(x) + 11), x] = 255
stroke([P(x, arc(x) if x < hx0 else arc2(x)) for x in np.arange(428, xm + 0.5, 0.5)])
# 右領口弧：第 446–478 列整段擦掉，用原弧中線擬合 x(y)，從第 482 列畫到下巴
rpts = [(452, 534.0), (456, 537.5), (460, 541.0), (464, 543.5), (468, 546.5), (472, 548.5), (476, 550.5), (480, 552.5), (484, 554.5)]
ry_, rx_ = zip(*rpts); cr = np.polyfit(ry_, rx_, 2); rarc = lambda y: float(np.polyval(cr, y))
for y in range(444, 480):
    c = rarc(y); A[y, int(c - 11):int(c + 12)] = 255
ytop = next(y for y in np.arange(452, 430, -0.25) if y <= chin(rarc(y)) + 1)
stroke([P(rarc(y), y) for y in np.arange(483, ytop - 0.5, -0.5)])
print("collar join", round(hx1, 1), round(hy1, 1))
ink = np.array(ink.resize((WW, HH), Image.BOX)).astype(np.float32) / 255
Af = A.astype(np.float32) * (1 - ink[..., None])
Af = Af.clip(0, 255).astype(np.uint8)
Af[(Af > 232).all(-1)] = 255
Image.fromarray(Af).save(sys.argv[2])
print("hermite", round(x0,1), round(m0,2), round(x1,1), round(m1,2), "brow", [round(v,1) for v in br])
