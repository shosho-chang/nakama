"""Gamification 規則引擎——分數表、等級曲線、冪等鍵。全部純函式，零 I/O。

這裡是整個系統唯一的「規則」所在地（plugin 是笨層，帳目金額由這裡算好經 REST 送過去）。
設計裁決見 agents/sanji/CONTEXT.md；營運方案見 docs/plans/fleet-gamification-master-plan.md。

鐵則：
- 每筆授予帶 ``RULE_VERSION``——規則改版只影響未來，永不回溯重算。
- 冪等鍵格式一旦上線即凍結（改格式 = 同一事實重複入帳）。
- Sanji 自己（服務帳號）排除在點數經濟之外；測試帳號照常入帳、只在呈現層過濾。
"""

from __future__ import annotations

from datetime import date, datetime

# 規則版本——任何分數表 / 曲線 / 鍵格式變動都要 bump（格式：YYYY.MM.DD-vN）
RULE_VERSION = "2026.08.23-v1"

# ── 分數表（XP 一律 10 的倍數；貝里 = XP ÷ 10，恆為整數）──────────────
XP_TABLE: dict[str, int] = {
    "presence_day": 10,  # 每日在場（PTT 式一天一次；portal ticker 訊號）
    "checkin_day": 10,  # 挑戰打卡一天（Sanji 判定通過後）
    "streak_7": 30,  # 連續 7 天獎（當場入袋，斷了重新數、可再得）
    "full_attendance": 200,  # 全勤獎（賽季結算時發）
    "like_received": 30,  # 貼文被讚（他人驗證）
    "bookmark_received": 150,  # 貼文被收藏（最強品質訊號，讚的 5 倍）
    "lesson_completed": 50,  # 完成單課
    "course_completed": 300,  # 完成整門課
    "quiz_passed": 50,  # 通過測驗
}

# 挑戰榜只計這些來源（榜單 = SUM(xp) WHERE season=本季 AND source IN 挑戰類）
CHALLENGE_SOURCES = frozenset({"checkin_day", "streak_7", "full_attendance"})

# ── 等級曲線（15 階，指數；門檻 = 生涯里程 XP）────────────────────────
LEVEL_THRESHOLDS: list[tuple[int, int]] = [
    (1, 0),
    (2, 100),
    (3, 300),
    (4, 1_000),
    (5, 2_500),
    (6, 5_000),
    (7, 9_000),
    (8, 15_000),
    (9, 24_000),
    (10, 35_000),
    (11, 50_000),
    (12, 70_000),
    (13, 100_000),
    (14, 140_000),
    (15, 200_000),
]


def berry_of(xp: int) -> int:
    """貝里 = XP ÷ 10（分數表保證整除；沖正的負值同樣適用）。"""
    return xp // 10 if xp >= 0 else -((-xp) // 10)


def level_for(xp_total: int) -> int:
    """生涯里程 → 等級。"""
    level = 1
    for n, need in LEVEL_THRESHOLDS:
        if xp_total >= need:
            level = n
    return level


def level_label(level: int) -> str:
    """等級稱號。命名歸修修（艦隊世界觀），定稿前用 Lv.N 佔位。"""
    return f"Lv.{level}"


def season_of(d: date) -> str:
    """賽季標籤：'2026Q4'。梯次 = 1/1、4/1、7/1、10/1。"""
    return f"{d.year}Q{(d.month - 1) // 3 + 1}"


def _event_date(ev: dict) -> str:
    """事件的站台日（YYYY-MM-DD）。created_at 由 plugin 以站台時區寫入。"""
    raw = str(ev.get("created_at", ""))[:10]
    # 驗證格式，壞資料直接炸出來（寧可停下也不入錯帳）
    datetime.strptime(raw, "%Y-%m-%d")
    return raw


def _grant(
    user_id: int,
    source: str,
    idem: str,
    *,
    season: str = "",
    ref_event_id: int = 0,
    reason: str = "",
    xp_override: int | None = None,
) -> dict:
    xp = XP_TABLE[source] if xp_override is None else xp_override
    return {
        "user_id": user_id,
        "xp": xp,
        "berry": berry_of(xp),
        "source": source,
        "season": season,
        "ref_event_id": ref_event_id,
        "reason": reason,
        "idempotency_key": idem,
        "rule_version": RULE_VERSION,
    }


def grant_for_event(ev: dict, *, sanji_user_id: int) -> dict | None:
    """確定性事件 → 授予（不經 LLM 的那些）。回傳 None = 此事件不自動入帳。

    - ``checkin_submitted`` 永遠回 None——打卡走判定漏斗，不在這裡。
    - ``quiz_submitted`` 暫回 None——vendor 的 quizResult 形狀未實地驗證，
      UAT 拿到真實 meta 前不啟用計分（誠實優於猜測）。
    - Sanji 自己的事件一律 None（機器人排除在經濟之外）。
    """
    user_id = int(ev.get("user_id", 0))
    if not user_id or user_id == sanji_user_id:
        return None

    etype = str(ev.get("event_type", ""))
    eid = int(ev.get("id", 0))
    meta = ev.get("meta") or {}

    if etype == "presence_day":
        day = _event_date(ev)
        return _grant(user_id, "presence_day", f"presence:{user_id}:{day}", ref_event_id=eid)

    if etype == "lesson_completed":
        lesson_id = int(ev.get("object_id", 0))
        if not lesson_id:
            return None
        return _grant(
            user_id, "lesson_completed", f"lesson:{user_id}:{lesson_id}", ref_event_id=eid
        )

    if etype == "course_completed":
        course_id = int(ev.get("object_id", 0))
        if not course_id:
            return None
        return _grant(
            user_id, "course_completed", f"course:{user_id}:{course_id}", ref_event_id=eid
        )

    if etype == "reaction_added":
        # 只有 like 計分；自讚不計。受益人 = 貼文作者（事件的 user_id）。
        if str(meta.get("type", "")) != "like":
            return None
        actor = int(meta.get("actor_id", 0))
        if actor and actor == user_id:
            return None
        dedupe = str(ev.get("dedupe_key") or "")
        if not dedupe.startswith("react:"):
            return None  # 沒有 react row id 就無法保證冪等——寧可不入帳，對帳補
        return _grant(user_id, "like_received", f"like:{dedupe}", ref_event_id=eid)

    return None


def grant_for_bookmark(row: dict, feed_owner_id: int, *, sanji_user_id: int) -> dict | None:
    """收藏授予（來自每日 reactions 增量掃描，不是事件流——vendor 對 bookmark 不發 hook）。

    ``row`` = plugin ``GET /reactions`` 的一列（fcom_post_reactions）。
    受益人 = 貼文作者；自藏不計。
    """
    if not feed_owner_id or feed_owner_id == sanji_user_id:
        return None
    actor = int(row.get("user_id", 0))
    if actor and actor == feed_owner_id:
        return None
    react_id = int(row.get("id", 0))
    if not react_id:
        return None
    return _grant(feed_owner_id, "bookmark_received", f"bookmark:react:{react_id}")


def grant_for_checkin(
    user_id: int, feed_id: int, day: str, season: str, *, ref_event_id: int = 0
) -> dict:
    """打卡通過判定後的當日基礎分。一人一天一分（冪等鍵含日期，同日多篇只計一次）。"""
    return _grant(
        user_id,
        "checkin_day",
        f"checkin:{user_id}:{day}",
        season=season,
        ref_event_id=ref_event_id,
        reason=f"feed:{feed_id}",
    )


def streak_bonus_if_due(user_id: int, day: str, season: str, run_length: int) -> dict | None:
    """連續獎：每滿 7 天當場入袋。斷了重新數、之後再滿 7 天可再得——
    所以冪等鍵掛「達成日」而不是 run 長度（同季斷後重建到 7 不會被舊鍵擋掉）。
    """
    if run_length <= 0 or run_length % 7 != 0:
        return None
    return _grant(
        user_id,
        "streak_7",
        f"streak7:{user_id}:{day}",
        season=season,
        reason=f"run:{run_length}",
    )


def full_attendance_grant(user_id: int, season: str) -> dict:
    """全勤獎（賽季結算時、由對帳流程確認 62/62 後發）。一季一次。"""
    return _grant(user_id, "full_attendance", f"fullatt:{user_id}:{season}", season=season)


def reversal(original: dict, *, reverses_grant_id: int, reason: str, idem_suffix: str = "") -> dict:
    """沖正：對既有 grant 開負值事件（永不 UPDATE/DELETE 歷史）。

    ``original`` 需含 user_id / xp / berry / source / season。
    """
    xp = -int(original["xp"])
    idem = f"reversal:{reverses_grant_id}"
    if idem_suffix:
        idem += f":{idem_suffix}"
    return {
        "user_id": int(original["user_id"]),
        "xp": xp,
        "berry": berry_of(xp),
        "source": "reversal",
        "season": str(original.get("season", "")),
        "ref_event_id": 0,
        "reverses_grant_id": reverses_grant_id,
        "reason": reason,
        "idempotency_key": idem,
        "rule_version": RULE_VERSION,
    }
