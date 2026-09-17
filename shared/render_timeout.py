"""封面 render 的逾時判讀——watcher 與 Bridge 共用同一份判準。

**為什麼要共用**：600 秒的逾時不是 watcher 那一層丟的。watcher 的
`subprocess.run(..., timeout=1800)` 包的是 `render_request.py`，而**那支自己**用
`timeout=600` 跑 `render_still.py`，且沒有任何 try/except 包住它。所以 child 是帶著
traceback 以 exit code 1 結束的，watcher 的 `subprocess.run` **正常回傳**——
`TimeoutExpired` 一次都不會在 watcher 這一層被攔到。

2026-09-17 蘇予昕 punch-L02 就是這樣：連續五次 600 秒逾時，watcher 每次都以為那是
一般失敗。只看 `isinstance(exc, TimeoutExpired)` 的話，連號永遠是 0，畫面永遠說
「再按一次就會過」。

判準放在這裡、兩端都用它，是為了不要再出現「watcher 認得的逾時」與「Bridge 認得的
逾時」是兩套的情況。
"""

from __future__ import annotations

#: child 逾時在 stderr 留下的簽名。`TimeoutExpired` 來自 traceback 的例外名，
#: `timed out after` 來自它的訊息本體——兩者各自都足以辨識。
_SIGNATURES = ("TimeoutExpired", "timed out after")


def looks_like_render_timeout(text: str | None) -> bool:
    """這段輸出看起來是 render 逾時嗎？"""
    if not text:
        return False
    return any(signature in text for signature in _SIGNATURES)
