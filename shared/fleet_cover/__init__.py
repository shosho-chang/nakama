"""自由艦隊（Freedom Fleet）社群活動封面產生器。

- `render_cover(spec)`：純 Pillow 畫 1600×800 JPEG（VPS 無頭環境可跑，不需要瀏覽器）。
- `generate_illustration(topic)`：選用；呼叫 OpenAI 生一張無文字、透明背景的手繪風插圖，
  當封面主圖（`main_kind="illustration"`）。

字型解析見 `fonts.py`（CJK 字型靠 env 或 apt `fonts-noto-cjk-extra`）。
"""

from shared.fleet_cover.fonts import CoverFontError
from shared.fleet_cover.illustration import IllustrationError, generate_illustration
from shared.fleet_cover.render import CoverSpec, render_cover

__all__ = [
    "CoverFontError",
    "CoverSpec",
    "IllustrationError",
    "generate_illustration",
    "render_cover",
]
