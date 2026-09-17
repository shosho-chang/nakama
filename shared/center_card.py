"""中央卡的尺寸契約——裁到卡片比例，再縮到出圖尺寸。

N2 的中央卡是 678×455（1.4901），CSS `object-fit: cover`：素材比例不合就從短邊硬裁，
所以**先裁到卡片比例**再縮，進畫面的才是挑的那一塊。輸出 2× = 1356×910。

這份契約原本只住在 `install_center_asset.py`（agent 自己配封面那條路）。gate 那條路
的 `fetch_licensed_center.py` 只驗尺寸、直接 `shutil.copy2`——於是 Envato 的授權原檔
原封不動進了合成。2026-09-17 蘇予昕 punch-L02：6000×4000、20MB 的 JPEG 進 Chrome，
`render_still.py` 每次都撐到 600 秒逾時，修修在 gate 上等了四十分鐘才看到失敗。

兩條路要用同一份裁切，所以搬到這裡；`MIN_LONG_EDGE` 也一併放進來——「長邊太短」
判的是「這還是浮水印預覽，不是授權原檔」，兩條路的判準本來就該一致。
"""

from __future__ import annotations

from typing import Literal

from PIL import Image

#: 出圖尺寸（卡片 678×455 的 2 倍）。
CARD_W, CARD_H = 1356, 910
#: 卡片長寬比。
TARGET = CARD_W / CARD_H
#: 授權原檔的長邊下限。低於這個數就還是候選池的浮水印預覽——候選預覽是 600px
#: 級的浮水印圖，授權原檔動輒 6000px；門檻取封面畫布寬，低於它就不可能是原檔。
MIN_LONG_EDGE = 1280

Anchor = Literal["center", "left", "right", "top"]


def crop_box(width: int, height: int, *, anchor: Anchor = "center") -> tuple[int, int, int, int]:
    """把 `width×height` 置中（或靠 `anchor`）裁成卡片比例的框。

    主體不在正中間時用 `anchor` 把裁切窗挪過去——橫向素材挪左右，縱向素材挪上下。
    """
    if width / height > TARGET:  # 太寬 → 裁左右
        new_w = round(height * TARGET)
        if anchor == "left":
            x0 = 0
        elif anchor == "right":
            x0 = width - new_w
        else:
            x0 = (width - new_w) // 2
        return (x0, 0, x0 + new_w, height)
    # 太高 → 裁上下
    new_h = round(width / TARGET)
    y0 = 0 if anchor == "top" else (height - new_h) // 2
    return (0, y0, width, y0 + new_h)


def crop_to_card(
    image: Image.Image, *, anchor: Anchor = "center"
) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """回傳（裁到卡片比例並縮到 `CARD_W×CARD_H` 的複本, 實際用的裁切框）。原圖不動。

    框一起回傳，是為了讓呼叫端印出來的框保證就是真的用的那個——分開算兩次，
    日後在中間插一道變形，印出來的就會是謊話。
    """
    box = crop_box(image.width, image.height, anchor=anchor)
    return image.crop(box).resize((CARD_W, CARD_H), Image.LANCZOS), box
