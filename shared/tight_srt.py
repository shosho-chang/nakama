"""精華段最新一版 tight SRT 的單一來源。

**preview（審核頁）／CC（captions.insert）／短片燒字幕三邊必須指到同一個檔**——
以前三處各自寫一份 glob，任何一邊改了版本挑選規則，修修在審核頁看到的字幕就
不再是實際會上架的那份。2026-08-12 修修要求審核頁預設顯示字幕時收斂成本模組。

版本號是零填充的（`SL3_tight_r001.srt` … `r011`），所以字典序＝版本序。
"""

from __future__ import annotations

import re
from pathlib import Path

# SRT 時間碼用逗號分毫秒，WebVTT 用點——差別只有這個
_SRT_MS = re.compile(r"(\d\d:\d\d:\d\d),(\d\d\d)")


def latest_tight_srt(episode_dir: Path, cut_id: str) -> Path | None:
    """該段最新版 tight SRT；沒有就回 None（呼叫端自己決定是警告還是致命）。"""
    srts = sorted((episode_dir / "highlights" / "srt").glob(f"{cut_id}_tight_r*.srt"))
    return srts[-1] if srts else None


def srt_to_vtt(srt_text: str) -> str:
    """SRT → WebVTT。瀏覽器的 `<track>` 只吃 VTT，不吃 SRT。

    只換時間碼分隔符與補檔頭；序號、換行、文字一律原樣保留——審核頁看到的
    斷行必須跟上架的 CC 一模一樣，這裡不做任何「順手美化」。
    """
    body = srt_text.lstrip("\ufeff").replace("\r\n", "\n").strip()
    return "WEBVTT\n\n" + _SRT_MS.sub(r"\1.\2", body) + "\n"
