# 「思考」表情：ChatGPT 畫的手比身體的手套大一倍，拿掉手、把被手擋住的下巴接回來；手改由 figure.js 畫
#   python fix_think_head.py <heads 資料夾>
# 數字量自 head_x_think.png：左側外框最後乾淨的一點 (66,220) 斜率 dx/dy≈0.72；右側 (172,224) 斜率≈-0.45
import sys, json, numpy as np
from pathlib import Path
from PIL import Image, ImageDraw

d = Path(sys.argv[1])
im = Image.open(d / "head_x_think.png").convert("RGBA")
a = np.array(im)
H, W = a.shape[:2]
Y0 = 222
a[Y0:, :, 3] = 0                                   # 下巴以下（手、手臂）全拿掉
a[210:Y0, 76:112, :3] = 255                        # 拇指尖露在下巴線上面的那一小段，刷成臉的白
L, R = np.array([66.0, 220.0]), np.array([172.0, 224.0])
tL = np.array([0.72, 1.0]); tL /= np.linalg.norm(tL)
tR = np.array([0.45, -1.0]); tR /= np.linalg.norm(tR)
K = 120.0
def herm(t):
    h00, h10, h01, h11 = 2*t**3 - 3*t**2 + 1, t**3 - 2*t**2 + t, -2*t**3 + 3*t**2, t**3 - t**2
    return h00 * L + h10 * K * tL + h01 * R + h11 * K * tR
pts = [herm(t) for t in np.linspace(0, 1, 80)]
S = 4
newH = int(max(p[1] for p in pts) + 10)
canvas = np.zeros((max(H, newH), W, 4), np.uint8); canvas[:H] = a
big = Image.fromarray(canvas).resize((W * S, canvas.shape[0] * S), Image.NEAREST)
fill = Image.new("L", big.size, 0); ink = Image.new("L", big.size, 0)
df, di = ImageDraw.Draw(fill), ImageDraw.Draw(ink)
poly = [(p[0] * S, p[1] * S) for p in pts]
df.polygon([(L[0] * S, (Y0 - 2) * S)] + poly + [(R[0] * S, (Y0 - 2) * S)], fill=255)
r = 6.2 * S
for p in pts:
    di.ellipse([p[0] * S - r, p[1] * S - r, p[0] * S + r, p[1] * S + r], fill=255)
big = np.array(big).astype(np.float32)
f = np.array(fill)[..., None] / 255.0; k = np.array(ink)[..., None] / 255.0
big[..., :3] = big[..., :3] * (1 - f) + 255 * f
big[..., 3:] = np.maximum(big[..., 3:], 255 * f)
big[..., :3] = big[..., :3] * (1 - k)
big[..., 3:] = np.maximum(big[..., 3:], 255 * k)
out = Image.fromarray(big.clip(0, 255).astype(np.uint8)).resize((W, canvas.shape[0]), Image.LANCZOS)
out.save(d / "head_x_think.png", optimize=True)
meta = json.load(open(d / "heads.json"))
chin = max(p[1] for p in pts) + 6
meta["x_think"].update({"h": out.size[1], "chinY": int(round(chin)), "height": int(round(chin)),
                        "chinX": round(float(pts[int(np.argmax([p[1] for p in pts]))][0]), 1)})
json.dump(meta, open(d / "heads.json", "w"), indent=1)
print(meta["x_think"])
