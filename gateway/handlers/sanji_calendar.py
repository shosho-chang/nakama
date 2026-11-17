"""Sanji 排程前查修修的 Google 行事曆、確認後寫進去（2026-11-16 修修要求，「跟 Nami 一樣」）。

共用 Nami 的 :mod:`shared.google_calendar`（主行事曆、``calendar.events`` scope）：

- :func:`check`：時段內有沒有重疊的「有時間」事件（整天事件不算），沒有就順便不給空檔建議。
- :func:`add`：確認建立後把活動／會議寫進行事曆。帶 idempotency key，重試不會建兩筆；
  不再查衝突（使用者已在確認卡看過衝突、選了照排）。

行事曆讀不到（token 失效、網路）**不擋**流程：確認卡註明「沒查到行事曆」，寫入失敗就在
成功訊息裡提醒手動加。Sanji 只看得到修修的行事曆，看不到曉宣的。
"""

from __future__ import annotations

from datetime import datetime

from shared.log import get_logger

logger = get_logger("nakama.gateway.sanji_calendar")

# 有衝突時，回這些字就照原時間排
FORCE_WORDS = {"照排", "照樣排", "還是排", "照原時間", "不管衝突"}


def _gc():
    from shared import google_calendar  # lazy：沒裝 google 套件的環境（測試）也能 import

    return google_calendar


def _hm(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%H:%M")
    except ValueError:
        return iso


def check(start: datetime, end: datetime) -> dict:
    """回 ``{"ok", "conflicts": [str], "free": [str], "error"}``（JSON-safe，可進 state）。"""
    out: dict = {"ok": False, "conflicts": [], "free": [], "error": ""}
    try:
        gc = _gc()
        hits = gc.find_conflicts(start.isoformat(), end.isoformat())
        out["conflicts"] = [f"{_hm(e.start)}–{_hm(e.end)}「{e.title}」" for e in hits[:3]]
        if hits:
            minutes = int((end - start).total_seconds() // 60)
            slots = gc.find_free_slots(start.date(), minutes, near=start.isoformat())
            out["free"] = [f"{_hm(s)}–{_hm(e)}" for s, e in slots]
        out["ok"] = True
    except Exception as e:  # 行事曆壞了不擋排程
        logger.warning(f"calendar check failed: {e}")
        out["error"] = str(e)[:150]
    return out


def card_lines(cal: dict | None, forced: bool) -> list[str]:
    """確認卡上的「行事曆」那幾行。"""
    if not cal:
        return []
    if not cal.get("ok"):
        err = cal.get("error") or "未知錯誤"
        return [f"行事曆：這次沒查到修修的 Google 行事曆（{err}），衝突請自己留意"]
    if not cal.get("conflicts"):
        return ["行事曆：修修這個時段沒有其他行程 ✅"]
    lines = [f"⚠️ 修修的行事曆這個時段已經有：{'、'.join(cal['conflicts'])}"]
    if cal.get("free"):
        lines.append(f"　當天附近的空檔：{'、'.join(cal['free'])}")
    if forced:
        lines.append("　（你說要照排，確認後會照原時間排）")
    else:
        lines.append(
            "　如果那就是這場活動本身（修修先記在行事曆上了），或不影響，回「照排」；"
            "要改時間就直接說"
        )
    return lines


def blocked(cal: dict | None, forced: bool) -> bool:
    """有衝突、使用者還沒說照排 → 不能直接確認。"""
    return bool(cal and cal.get("ok") and cal.get("conflicts") and not forced)


def add(
    *,
    title: str,
    start: datetime,
    end: datetime,
    description: str,
    key: str,
    attendees: list[str] | None = None,
) -> dict:
    """寫進行事曆。回 ``{"ok", "id", "link", "error"}``；失敗不丟例外（呼叫端只提醒）。

    ``attendees``（email）：加成與會者，Google 會寄邀請信給他們。
    """
    try:
        ev = _gc().create_event(
            title=title,
            start=start.isoformat(),
            end=end.isoformat(),
            description=description,
            check_conflict=False,
            idempotency_key=f"{key}@{start.date().isoformat()}",
            attendees=attendees or None,
        )
        return {"ok": True, "id": ev.id, "link": ev.html_link, "error": ""}
    except Exception as e:
        logger.warning(f"calendar add failed key={key}: {e}")
        return {"ok": False, "id": "", "link": "", "error": str(e)[:150]}
