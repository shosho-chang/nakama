"""publish_timeline — 成品 render 要用哪一條 Resolve timeline，由 Release 說了算。

發布線原本用 `winners.json` 的 rank + title 湊出 timeline 顯示名
（`長3 - 當天堂變成圈養，人還剩什麼（緊·導播）`），完全不認得 ADR-066 的
Finished Cut Release。20260805 林之晨的實測：

    cut                        Release preview     湊出來的 timeline
    value-L01                     592.90s            592.97s   ← 對得上
    value-L02                     563.71s            329.53s   ← 差 234 秒
    long3-fresh-20260828-r4       492.31s            260.00s   ← 差 232 秒

舊的短版 timeline 都還留在專案裡，所以 render **不會報錯**——它會把 260 秒的
舊剪輯冒充成 492 秒的成品，掛上已核准的標題與縮圖登錄進 DB。安靜地發錯內容
比失敗更糟，因為沒有人會知道。

這裡把對應關係變成 episode 內一份顯式紀錄（`highlights/publish-timelines.v1.json`），
並且**每次 render 前都拿實際 timeline 長度跟 Release preview 對一次**。ADR-066
的 run 可由 resolve transaction 機器推導；migrated Release 沒有 transaction，
只能由人記一次——記下來的東西可以被稽核，猜出來的不行。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

MAP_RELPATH = "highlights/publish-timelines.v1.json"
SCHEMA = "nakama.publish_timelines.v1"

# Release preview 是轉出來的 mp4，容器長度跟 timeline 的 frame 數本來就會差一兩
# 個 frame（實測 592.900 vs 592.967）。這道護欄要抓的是差**兩百多秒**的錯片，
# 所以留 2 秒寬容仍有兩位數的安全倍率，不會為了 rounding 誤停產線。
DURATION_TOLERANCE_SEC = 2.0


class PublishTimelineError(RuntimeError):
    """對應關係缺漏或對不上——一律停，不回退到會出錯片的舊猜法。"""


@dataclass(frozen=True)
class PublishTimelineTarget:
    """一支 cut 的 render 目標，附上它該有的長度供 render 前複驗。"""

    cut_id: str
    timeline: str
    release_id: str
    release_cut_id: str
    expected_duration_sec: float


def load_timeline_map(episode_dir: Path) -> dict | None:
    """讀 episode 的 timeline 對應表；沒有這個檔就回 None（舊集數沿用舊行為）。"""
    path = Path(episode_dir) / MAP_RELPATH
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != SCHEMA:
        raise PublishTimelineError(f"{path} 的 schema 不是 {SCHEMA}——不採信來路不明的對應表")
    return payload


def resolve_target(timeline_map: dict, cut_id: str) -> PublishTimelineTarget:
    """對應表 → 這支 cut 的 render 目標。缺項是錯誤，不是「就用舊規則」。"""
    entry = (timeline_map.get("cuts") or {}).get(cut_id)
    if entry is None:
        known = ", ".join(sorted((timeline_map.get("cuts") or {}))) or "（空）"
        raise PublishTimelineError(
            f"{cut_id} 不在 {MAP_RELPATH} 裡——這支還沒登記要用哪條 timeline。\n"
            f"  已登記的有：{known}\n"
            f"  補上它，不要讓 publish_prep 回頭用 winners.json 的名字猜（會出錯片）。"
        )
    missing = [k for k in ("timeline", "release_id", "expected_duration_sec") if not entry.get(k)]
    if missing:
        raise PublishTimelineError(f"{cut_id} 的對應表少了欄位 {missing}")
    return PublishTimelineTarget(
        cut_id=cut_id,
        timeline=str(entry["timeline"]),
        release_id=str(entry["release_id"]),
        release_cut_id=str(entry.get("release_cut_id") or cut_id),
        expected_duration_sec=float(entry["expected_duration_sec"]),
    )


def verify_duration(target: PublishTimelineTarget, actual_duration_sec: float) -> None:
    """render 前最後一道：實際 timeline 長度必須等於 Release 的 preview 長度。"""
    delta = abs(actual_duration_sec - target.expected_duration_sec)
    if delta > DURATION_TOLERANCE_SEC:
        raise PublishTimelineError(
            f"{target.cut_id}: timeline「{target.timeline}」長度 {actual_duration_sec:.3f}s，"
            f"但 Release {target.release_id} 的成品長度是 "
            f"{target.expected_duration_sec:.3f}s（差 {delta:.3f}s）。\n"
            f"  → 這條 timeline 不是這個 Release 的內容。專案裡通常還留著同名的舊剪輯；\n"
            f"     先確認 {MAP_RELPATH} 指到正確的那條，不要就這樣 render 出去。"
        )


def canonical_timeline_from_transactions(
    transactions_dir: Path, transaction_receipt_id: str
) -> str | None:
    """ADR-066 run：由 Release 的 transaction receipt 反查 canonical timeline 名。

    migrated Release 沒有 transaction，回 None——那種只能靠對應表裡人記的值。
    """
    directory = Path(transactions_dir)
    if not directory.is_dir():
        return None
    for path in sorted(directory.glob("resolve-*.json")):
        payload = json.loads(path.read_text(encoding="utf-8")).get("payload") or {}
        if payload.get("transaction_receipt_id") != transaction_receipt_id:
            continue
        if payload.get("status") != "committed":
            continue
        canonical = payload.get("canonical") or {}
        name = canonical.get("name")
        return str(name) if name else None
    return None
