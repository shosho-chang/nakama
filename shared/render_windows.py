"""渲染期間把 chrome-headless-shell 的 console 視窗擋掉。

## 量過的事實（2026-09-09）

一張字卡渲染會新增 **9 個可見的 console 視窗**，全部屬於 `chrome-headless-shell`
自己生的 renderer／gpu／utility 子行程。試過而且量過沒有用的：

| 手段 | 施加位置 | 結果 |
|---|---|---|
| `CREATE_NO_WINDOW` + `SW_HIDE` | python 層 / node 層 | 新增 9 |
| `CREATE_NEW_CONSOLE` + `SW_HIDE` | python 層 / node 層 | 新增 9 |
| 只有 `SW_HIDE`（繼承父 console） | python 層 / node 層 | 新增 9 |
| 完全不加 | python 層 / node 層 | 新增 9 |
| `CreateProcessW` + `lpDesktop` 丟到隱藏 desktop | node 層 | 新增 9（desktop 有建起來，視窗仍在使用者 desktop） |

creationflags 只作用在「我們直接建立的那一個行程」，管不到孫行程；而 console 視窗
是 `conhost.exe` 由 csrss 在**互動 desktop** 上開的，不跟建立者的 desktop 走——所以
desktop 隔離對 console 視窗無效（這點是實測出來的，不是推的）。hyperframes 是 hash
驗證的 pinned runtime（`PinnedHyperFramesRuntime.verify` 比對整棵 node_modules
的 tree identity），不能改它傳給 puppeteer 的參數。

## 所以只剩「視窗一出現就藏掉」

`SetWinEventHook(EVENT_OBJECT_SHOW)` 是事件驅動的，視窗一顯示就回呼，延遲 ~1ms，
不是輪詢的 30ms。回呼裡比對標題，是渲染視窗就 `ShowWindow(SW_HIDE)`。另外掛一個
低頻 sweep 當保險，接住 hook 漏掉的（例如 hook 註冊前就已經開的）。

只藏標題含 `chrome-headless-shell` / `hyperframes` 的視窗——不會動到使用者的東西。
行程本身照跑，算圖照完成，只是不佔畫面、不搶焦點。
"""

from __future__ import annotations

import ctypes
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from ctypes import wintypes

__all__ = ["hide_render_windows"]

#: 只認這兩個標記，避免誤藏使用者自己的視窗。
_RENDER_WINDOW_MARKERS = ("chrome-headless-shell", "hyperframes")

_EVENT_OBJECT_SHOW = 0x8002
_EVENT_OBJECT_CREATE = 0x8000
_WINEVENT_OUTOFCONTEXT = 0x0000
_WINEVENT_SKIPOWNPROCESS = 0x0002
_OBJID_WINDOW = 0
_SW_HIDE = 0
#: 訊息幫浦的輪詢間隔。事件是排隊送的，這個值就是視窗留在畫面上的上限。
_PUMP_INTERVAL_SEC = 0.001
_WM_QUIT = 0x0012


_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True) if os.name == "nt" else None
#: pid -> 是不是渲染行程。console 視窗一個接一個開，同一個 pid 不重查。
_process_cache: dict[int, bool] = {}
#: 診斷用：這個行程總共藏掉幾個視窗。
hidden_total = 0


def _process_image(pid: int) -> str:
    """回傳 pid 的執行檔完整路徑；查不到回空字串。"""
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not _kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return buffer.value
    finally:
        _kernel32.CloseHandle(handle)


def _is_render_window(user32: ctypes.WinDLL, hwnd: int) -> bool:
    """認得出這是不是我們產線開的 console 視窗。

    兩個線索都要看，而且**否定結果不能快取**：console 視窗的宿主行程是
    `conhost.exe`，image path 永遠比不中；標題才是那條 chrome 的完整路徑，但標題是
    視窗建立後才被設上去的，太早問會拿到空字串。第一版把「這個 pid 不是渲染視窗」
    快取起來，於是每個 conhost 只被判斷一次、判成 False 就再也不會被藏——實測視窗
    在畫面上停 1.5～3 秒就是這個 bug。
    """
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    key = int(pid.value)
    if key == 0:
        return False
    if _process_cache.get(key):
        return True
    length = user32.GetWindowTextLengthW(hwnd)
    if length > 0:
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value.replace("\\", "/").lower()
        if any(marker in title for marker in _RENDER_WINDOW_MARKERS):
            _process_cache[key] = True
            return True
    image = _process_image(key).replace("\\", "/").lower()
    if any(marker in image for marker in _RENDER_WINDOW_MARKERS):
        _process_cache[key] = True
        return True
    return False


_GWL_EXSTYLE = -20
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_APPWINDOW = 0x00040000
_SWP_NOSIZE = 0x0001
_SWP_NOZORDER = 0x0004
_SWP_NOACTIVATE = 0x0010
_OFFSCREEN_XY = -32000
_WS_EX_LAYERED = 0x00080000
_LWA_ALPHA = 0x00000002


class _RECT(ctypes.Structure):
    _fields_ = (
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    )


def _on_screen(user32: ctypes.WinDLL, hwnd: int) -> bool:
    """視窗是不是還在畫面上（可見且沒有被移到畫面外）。"""
    if not user32.IsWindowVisible(hwnd):
        return False
    rect = _RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return False
    return rect.left > _OFFSCREEN_XY // 2


def _banish(user32: ctypes.WinDLL, hwnd: int) -> None:
    """把視窗弄到使用者看不到的地方。

    只靠 `ShowWindow(SW_HIDE)` 不夠——實測 console 視窗會被 conhost 重新顯示，
    停留時間中位數還有 2.3 秒。所以三件事一起做：
      1. 從 Alt-Tab／工作列移除（WS_EX_TOOLWINDOW，拿掉 WS_EX_APPWINDOW）
      2. 移到 (-32000, -32000)——就算被重新顯示也還在畫面外
      3. 再 SW_HIDE 一次
    """
    try:
        style = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        user32.SetWindowLongW(
            hwnd, _GWL_EXSTYLE, (style | _WS_EX_TOOLWINDOW) & ~_WS_EX_APPWINDOW
        )
        user32.SetWindowPos(
            hwnd, None, _OFFSCREEN_XY, _OFFSCREEN_XY, 0, 0,
            _SWP_NOSIZE | _SWP_NOZORDER | _SWP_NOACTIVATE,
        )
        # 再保險：整個視窗設成全透明。就算 conhost 又把它秀出來，alpha=0 的視窗
        # 在畫面上仍然什麼都看不到。
        user32.SetWindowLongW(
            hwnd, _GWL_EXSTYLE,
            user32.GetWindowLongW(hwnd, _GWL_EXSTYLE) | _WS_EX_LAYERED,
        )
        user32.SetLayeredWindowAttributes(hwnd, 0, 0, _LWA_ALPHA)
        user32.ShowWindow(hwnd, _SW_HIDE)
    except OSError:  # pragma: no cover
        pass


@contextmanager
def hide_render_windows(sweep_sec: float = 0.01) -> Iterator[None]:
    """在 with 區塊期間，渲染 console 視窗一出現就藏起來。

    非 Windows 平台是 no-op。任何 Win32 呼叫失敗都靜默退化成「不擋」——擋視窗
    再重要也不該讓渲染本身失敗。
    """
    if os.name != "nt":
        yield
        return

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    ready = threading.Event()
    stop = threading.Event()

    win_event_proc = ctypes.WINFUNCTYPE(
        None,
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.HWND,
        wintypes.LONG,
        wintypes.LONG,
        wintypes.DWORD,
        wintypes.DWORD,
    )
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _on_event(_hook, _event, hwnd, id_object, _id_child, _thread, _time) -> None:
        if id_object != _OBJID_WINDOW or not hwnd:
            return
        try:
            if _is_render_window(user32, hwnd):
                global hidden_total
                hidden_total += 1
                _banish(user32, hwnd)
        except OSError:  # pragma: no cover - 視窗可能在比對到一半就消失
            pass

    def _sweep_once() -> None:
        def callback(hwnd, _lparam):  # noqa: ANN001
            if _on_screen(user32, hwnd) and _is_render_window(user32, hwnd):
                global hidden_total
                hidden_total += 1
                _banish(user32, hwnd)
            return True

        user32.EnumWindows(enum_proc(callback), 0)

    def _pump() -> None:
        callback = win_event_proc(_on_event)
        hook_show = user32.SetWinEventHook(
            _EVENT_OBJECT_SHOW,
            _EVENT_OBJECT_SHOW,
            None,
            callback,
            0,
            0,
            _WINEVENT_OUTOFCONTEXT | _WINEVENT_SKIPOWNPROCESS,
        )
        hook_create = user32.SetWinEventHook(
            _EVENT_OBJECT_CREATE,
            _EVENT_OBJECT_CREATE,
            None,
            callback,
            0,
            0,
            _WINEVENT_OUTOFCONTEXT | _WINEVENT_SKIPOWNPROCESS,
        )
        ready.set()
        message = wintypes.MSG()
        last_sweep = 0.0
        while not stop.is_set():
            # PeekMessage 而不是 GetMessage：GetMessage 會 block 到有訊息為止，
            # stop 設起來也醒不過來。迴圈要跑得夠密——WINEVENT_OUTOFCONTEXT 的事件是
            # 排進本執行緒的訊息佇列，這裡睡多久，視窗就會在畫面上留多久。
            while user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
            now = time.monotonic()
            if now - last_sweep >= sweep_sec:
                _sweep_once()
                last_sweep = now
            stop.wait(_PUMP_INTERVAL_SEC)
        for hook in (hook_show, hook_create):
            if hook:
                user32.UnhookWinEvent(hook)

    worker = threading.Thread(target=_pump, name="hide-render-windows", daemon=True)
    worker.start()
    ready.wait(timeout=2.0)
    try:
        yield
    finally:
        stop.set()
        worker.join(timeout=2.0)
