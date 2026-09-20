"""`--force` 覆寫：修修下的命令直接做，門讓路、紀錄照留。

這個模組是整套 Finished Cut Production 唯一的覆寫開關。它**只**由 CLI
（`scripts/run_finished_cut_production.py --force`）打開；watcher 那些無人看管的
路徑不碰它，所以它們的行為一個字都不會變。

三件事構成這個模組的契約：

* `let_pass(reason_code, message)` 是每一道讓路的門唯一的問法。沒開覆寫時它回
  `False`，呼叫端照原樣 raise——也就是說**不帶 `--force` 的行為與以前逐位元組相同**。
* 讓路的門會留下紀錄。`⚠ OVERRIDDEN:` 印到 stderr，同一道門（同 reason_code 同訊息）
  只印一次並累計次數；整份帳走 `entries()` 交給 CLI 寫進收據。
* 覆寫狀態放在 `ContextVar`，不是模組全域旗標：同一個行程裡開了又關會乾淨地還原，
  測試之間不會互相污染。

門本身的「讓路 / 保留」判準不在這裡，在各個門的呼叫點：物理上做不下去的事
（檔案不在、Resolve 連不上、媒體解不開、磁碟寫不進去）照樣 raise，因為讓那種事
往下走只會換成更難懂的爆法。
"""

from __future__ import annotations

import sys
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import IO

__all__ = [
    "ForceOverride",
    "OverriddenGate",
    "activate",
    "active",
    "deactivate",
    "entries",
    "is_active",
    "let_pass",
]


@dataclass(frozen=True, slots=True)
class OverriddenGate:
    """一道讓路的門，連同它被撞到幾次。"""

    reason_code: str
    message: str
    count: int


class ForceOverride:
    """一次 CLI 執行期間的覆寫帳本。"""

    def __init__(self, *, stream: IO[str] | None = None) -> None:
        self._stream = stream
        self._order: list[tuple[str, str]] = []
        self._counts: dict[tuple[str, str], int] = {}

    def let_pass(self, reason_code: str, message: str) -> bool:
        """記下這道門並回 `True`——呼叫端據此不 raise、往下走。"""

        key = (reason_code, message)
        seen = self._counts.get(key)
        if seen is None:
            self._order.append(key)
            self._counts[key] = 1
            self._write(f"⚠ OVERRIDDEN: {reason_code} {message}\n")
        else:
            self._counts[key] = seen + 1
        return True

    def entries(self) -> tuple[OverriddenGate, ...]:
        """依第一次撞到的順序列出所有讓路的門。"""

        return tuple(
            OverriddenGate(reason_code=code, message=message, count=self._counts[(code, message)])
            for code, message in self._order
        )

    def _write(self, line: str) -> None:
        stream = self._stream if self._stream is not None else sys.stderr
        try:
            stream.write(line)
            stream.flush()
        except (OSError, ValueError):
            # 印不出來不能反過來變成第二道門。帳本本身才是稽核來源。
            pass


_ACTIVE: ContextVar[ForceOverride | None] = ContextVar(
    "finished_cut_force_override",
    default=None,
)


def activate(*, stream: IO[str] | None = None) -> tuple[ForceOverride, Token]:
    """打開覆寫。回傳帳本與還原用的 token。"""

    override = ForceOverride(stream=stream)
    return override, _ACTIVE.set(override)


def deactivate(token: Token) -> None:
    """關掉覆寫，回到 `activate` 之前的狀態。"""

    _ACTIVE.reset(token)


def active() -> ForceOverride | None:
    return _ACTIVE.get()


def is_active() -> bool:
    return _ACTIVE.get() is not None


def let_pass(reason_code: str, message: str) -> bool:
    """這道門可以讓路嗎？沒開覆寫一律 `False`。"""

    override = _ACTIVE.get()
    if override is None:
        return False
    return override.let_pass(reason_code, message)


def entries() -> tuple[OverriddenGate, ...]:
    override = _ACTIVE.get()
    if override is None:
        return ()
    return override.entries()
