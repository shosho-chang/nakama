#!/usr/bin/env python3
"""N1 字塊遮蔽 QA（設計系統定案量法 + 修修 2026-08-05 人名硬規則）。

兩道 gate，缺一 FAIL：
1. 標題大字：左右遮蔽平衡 |左−右| ≤ 600px²（美感容忍帶——house style 允許
   首尾字輕靠人物後方）。
2. **credit／人名帶：零遮蔽**（≤5px² 抗鋸齒容忍）。修修 2026-08-05：
   「重要資訊像是人名，千萬不能被擋住」——實際事故：安吉集 N1 的「吉」
   右下角被 guest cutout 上的麥克風海綿咬掉，總遮蔽 409px² 過了平衡 gate
   卻正好咬在人名上。平衡是美感量，人名是資訊量，兩者不同 gate。

用法：
    python occlusion_check.py <full.png> <textonly.png>
textonly = 同 spec 但兩張 cutout 換 1×1 透明片 render 的版本。
"""

import sys

import numpy as np
from PIL import Image

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BALANCE_TOL = 600  # 標題平衡（px²）
CREDIT_TOL = 5  # 人名帶硬遮擋（px²，抗鋸齒餘量）
HARD_DIFF = 150  # 人名帶「硬遮擋」判準：物件實蓋（麥海綿 ≈ diff 550）。
#                  90–150 是 glow 半透明染色——字形完整，另報資訊不 FAIL
#                  （安吉集實測：glow 把「吉」尾筆染暖 13%，diff 91–105）
LOGO_Y = 500  # y 以下視為 logo／角標，排除


def main() -> int:
    full = np.array(Image.open(sys.argv[1]).convert("RGB")).astype(int)
    text = np.array(Image.open(sys.argv[2]).convert("RGB")).astype(int)
    r, g, b = text[..., 0], text[..., 1], text[..., 2]
    white = (r > 200) & (g > 200) & (b > 200)
    orange = (r > 200) & (g > 80) & (g < 160) & (b < 90)
    mask = white | orange
    mask[LOGO_Y:, :] = False
    if not mask.any():
        raise SystemExit("text-only 圖抓不到字塊——mask 條件或 render 有問題")

    # 依連續 row 分群：最下面一群 = credit／人名帶，其餘 = 標題大字
    rows = np.nonzero(mask.any(axis=1))[0]
    groups, start = [], rows[0]
    for a, bb in zip(rows, rows[1:]):
        if bb - a > 4:
            groups.append((start, a))
            start = bb
    groups.append((start, rows[-1]))
    credit_band = groups[-1] if len(groups) > 1 else None

    diff = (np.abs(full - text).sum(axis=2) > 90) & mask
    title_mask = mask.copy()
    if credit_band:
        title_mask[credit_band[0] : credit_band[1] + 1, :] = False
    t_ys, t_xs = np.nonzero(title_mask)
    mid = (t_xs.min() + t_xs.max()) / 2
    t_diff = diff & title_mask
    left = int(t_diff[:, : int(mid)].sum())
    right = int(t_diff[:, int(mid) :].sum())
    ok_bal = abs(left - right) <= BALANCE_TOL
    print(
        f"標題帶 rows {groups[0][0]}–{groups[-2][1] if credit_band else groups[-1][1]} "
        f"左遮 {left}px² 右遮 {right}px² |差| {abs(left - right)}"
        f"（≤{BALANCE_TOL}）{'PASS' if ok_bal else 'FAIL'}"
    )
    ok_credit = True
    if credit_band:
        c_mask = mask.copy()
        c_mask[: credit_band[0], :] = False
        d = np.abs(full - text).sum(axis=2)
        c_hard = int(((d > HARD_DIFF) & c_mask).sum())
        c_tint = int(((d > 90) & (d <= HARD_DIFF) & c_mask).sum())
        ok_credit = c_hard <= CREDIT_TOL
        print(
            f"credit／人名帶 rows {credit_band[0]}–{credit_band[1]} "
            f"硬遮擋 {c_hard}px²（≤{CREDIT_TOL}，人名零遮蔽硬規則）"
            f"{'PASS' if ok_credit else 'FAIL'}"
            f"｜glow 染色 {c_tint}px²（資訊，字形不受影響）"
        )
    else:
        print("（未偵測到 credit 帶——單一字群）")
    print("字塊 QA: " + ("PASS" if ok_bal and ok_credit else "FAIL"))
    return 0 if ok_bal and ok_credit else 1


if __name__ == "__main__":
    sys.exit(main())
