"""自由艦隊封面的字型解析。

封面要在無頭的 Linux VPS 上用 Pillow 畫，沒有瀏覽器可以幫忙 fallback 字型，
所以三套字型都要明確找到檔案：

- Latin：repo 內附的 Plus Jakarta Sans ExtraBold（`assets/`，OFL 授權）。
- CJK Black（大標）／CJK Bold（eyebrow 的中文字、日期膠囊）：依序看
  env `FLEET_COVER_CJK_BLACK_FONT` / `FLEET_COVER_CJK_BOLD_FONT`，再看 Ubuntu
  `fonts-noto-cjk(-extra)` 安裝的 `/usr/share/fonts/opentype/noto/NotoSansCJK-*.ttc`。

`.ttc` 是多個字面（JP / KR / SC / TC / HK）打包在一起的集合檔，要挑 family 名稱
含 "TC"（繁體中文）的那一個 index；找到後快取，不必每次重掃。

全部找不到就丟 `CoverFontError`，訊息直接寫出該設哪個 env、該裝哪個 apt 套件。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
LATIN_FONT_PATH = ASSETS_DIR / "PlusJakartaSans-ExtraBold.ttf"

ENV_CJK_BLACK = "FLEET_COVER_CJK_BLACK_FONT"
ENV_CJK_BOLD = "FLEET_COVER_CJK_BOLD_FONT"

# Ubuntu: fonts-noto-cjk 提供 Bold、fonts-noto-cjk-extra 提供 Black
DEFAULT_CJK_BLACK_PATHS = ["/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc"]
DEFAULT_CJK_BOLD_PATHS = ["/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"]

# 一個 .ttc 最多掃幾個字面（Noto CJK 一包 5–10 個）
_MAX_TTC_FACES = 32


class CoverFontError(RuntimeError):
    """找不到封面需要的 CJK 字型。"""


@dataclass(frozen=True)
class FontRef:
    path: str
    index: int = 0


@lru_cache(maxsize=16)
def _ttc_tc_index(path: str) -> int:
    """在 .ttc 裡找 family 名稱含 "TC" 的字面 index；找不到退回 0。"""
    for i in range(_MAX_TTC_FACES):
        try:
            family, _style = ImageFont.truetype(path, 12, index=i).getname()
        except OSError:
            break
        if "TC" in (family or ""):
            return i
    return 0


def _ref_for(path: str) -> FontRef:
    if path.lower().endswith(".ttc"):
        return FontRef(path, _ttc_tc_index(path))
    return FontRef(path, 0)


def _resolve(env_name: str, defaults: list[str], label: str) -> FontRef:
    env_path = os.environ.get(env_name, "").strip()
    if env_path:
        if not Path(env_path).is_file():
            raise CoverFontError(f"{env_name} 指向的{label}字型檔不存在：{env_path}")
        return _ref_for(env_path)
    for candidate in defaults:
        if Path(candidate).is_file():
            return _ref_for(candidate)
    raise CoverFontError(
        f"找不到 CJK {label} 字型。請設定 env {env_name} 指向字型檔，"
        f"或在 VPS 安裝 apt 套件 fonts-noto-cjk-extra（會提供 {', '.join(defaults)}）。"
    )


def cjk_black() -> FontRef:
    """大標用的 CJK Black 字型。"""
    return _resolve(ENV_CJK_BLACK, DEFAULT_CJK_BLACK_PATHS, "Black")


def cjk_bold() -> FontRef:
    """eyebrow 中文字與日期膠囊用的 CJK Bold 字型。"""
    return _resolve(ENV_CJK_BOLD, DEFAULT_CJK_BOLD_PATHS, "Bold")


def latin() -> FontRef:
    """repo 內附的 Plus Jakarta Sans ExtraBold。"""
    return FontRef(str(LATIN_FONT_PATH), 0)


@lru_cache(maxsize=128)
def load(ref: FontRef, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(ref.path, size, index=ref.index)
