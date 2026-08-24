"""Sanji 主輪詢迴圈——分鐘級回饋的心臟。輪詢本身零 LLM；只有文字打卡判定會呼叫模型。

流程（每輪）：
  1. cursor 增量拉事件（``GET /events``）
  2. 確定性事件（presence/lesson/course/like）→ rules → 批次入帳
  3. ``checkin_submitted`` → 判定漏斗 → 通過則入帳＋公開留言回覆
  4. 處理完整批才推進 cursor（at-least-once；plugin 端 idempotency 保證重放安全）

故障模式：
  - ``GamDisabled``（止血開關）→ 安靜長睡，不告警（那是人為關閉）
  - 其他例外 → log ＋ 短睡重試；cursor 未推進，事件不丟失
"""

from __future__ import annotations

import time
from datetime import date, timedelta

from agents.sanji import judge, rules, templates
from agents.sanji.settings import SanjiConfig
from agents.sanji.store import Store
from agents.sanji.wp_client import GamAPIError, GamDisabled, WPClient
from shared.log import get_logger

logger = get_logger("nakama.sanji.loop")

_DISABLED_SLEEP = 600  # 止血開關關閉時的輪詢間隔
_ERROR_SLEEP = 60


class LevelStamper:
    """替每筆 grant 算 level_after（等級曲線只存在 nakama；plugin 不知道門檻）。

    以 WP balances 為基準、批內累加——同一輪多筆授予會拿到遞增後的正確等級。
    """

    def __init__(self, client: WPClient):
        self._client = client
        self._xp: dict[int, int] = {}

    def stamp(self, grant: dict) -> dict:
        uid = int(grant["user_id"])
        if uid not in self._xp:
            self._xp[uid] = int(self._client.balance(uid).get("xp_total", 0))
        self._xp[uid] += int(grant["xp"])
        level = rules.level_for(self._xp[uid])
        grant["level_after"] = level
        grant["level_label"] = rules.level_label(level)
        return grant


def award_checkin(
    client: WPClient,
    store: Store,
    cfg: SanjiConfig,
    *,
    user_id: int,
    feed_id: int,
    day: str,
    season: str,
    ref_event_id: int,
    reply: bool = True,
) -> None:
    """打卡通過後的完整動作：投影→算 streak→入帳→公開回覆。

    loop（即時判定）與 reconcile（fail-open 放行）共用——兩條路徑一字不差。
    """
    # 回歸偵測：昨天沒打、但過去有打過（首次打卡的新人不該收到「歡迎回來」）
    prev_day = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    returned_after_gap = (
        not store.has_checkin(user_id, prev_day)
    ) and store.has_any_checkin_before(user_id, day)

    store.record_checkin_day(user_id, day, season, feed_id)
    streak = store.current_streak(user_id, day)

    grants = [rules.grant_for_checkin(user_id, feed_id, day, season, ref_event_id=ref_event_id)]
    bonus = rules.streak_bonus_if_due(user_id, day, season, streak)
    if bonus:
        grants.append(bonus)

    stamper = LevelStamper(client)
    results = client.grants([stamper.stamp(g) for g in grants])

    # 同日第二篇：checkin grant 會是 duplicate——不重複回覆（回覆也要冪等）
    statuses = {r["idempotency_key"]: r["status"] for r in results.get("results", [])}
    checkin_status = statuses.get(grants[0]["idempotency_key"], "invalid")
    if checkin_status != "created":
        logger.info(f"[loop] checkin duplicate user={user_id} day={day}（同日已計，跳過回覆）")
        return

    if not reply:
        return

    text = templates.render_checkin_reply(
        user_id=user_id,
        day=day,
        xp=grants[0]["xp"],
        berry=grants[0]["berry"],
        streak=streak,
        bonus_xp=bonus["xp"] if bonus else 0,
        bonus_berry=bonus["berry"] if bonus else 0,
        returned_after_gap=returned_after_gap,
    )
    try:
        client.comment(feed_id, text)
    except GamAPIError as exc:
        # 留言失敗不回滾入帳（帳是真相，回覆是回饋）；留 log 供對帳補查。
        logger.warning(f"[loop] reply failed feed={feed_id}: {exc}")


class SanjiLoop:
    def __init__(self, cfg: SanjiConfig, client: WPClient, store: Store, *, theme: str = ""):
        self.cfg = cfg
        self.client = client
        self.store = store
        self.theme = theme or "身心健康練習"

    # ── cycle ────────────────────────────────────────────────────
    def cycle(self) -> int:
        """跑一輪。回傳處理的事件數（0 = 沒新事件）。"""
        after = self.store.get_cursor("events")
        page = self.client.events(after, limit=200)
        events = page.get("events", [])
        if not events:
            return 0

        deterministic: list[dict] = []
        for ev in events:
            etype = str(ev.get("event_type", ""))
            if etype == "checkin_submitted":
                self._handle_checkin(ev)
            else:
                g = rules.grant_for_event(ev, sanji_user_id=self.cfg.sanji_user_id)
                if g and g["source"] in self.cfg.scored_sources:
                    deterministic.append(g)

        if deterministic:
            stamper = LevelStamper(self.client)
            for i in range(0, len(deterministic), 100):
                self.client.grants([stamper.stamp(g) for g in deterministic[i : i + 100]])

        self.store.set_cursor("events", int(page.get("max_id", after)))
        logger.info(
            f"[loop] cycle: {len(events)} events, {len(deterministic)} deterministic grants"
        )
        return len(events)

    def _handle_checkin(self, ev: dict) -> None:
        event_id = int(ev.get("id", 0))
        feed_id = int(ev.get("object_id", 0))
        user_id = int(ev.get("user_id", 0))
        day = str(ev.get("created_at", ""))[:10]
        if not (event_id and feed_id and user_id and day):
            logger.warning(f"[loop] malformed checkin event: {ev.get('id')}")
            return
        if user_id == self.cfg.sanji_user_id:
            return  # 機器人不參與經濟

        season = rules.season_of(date.fromisoformat(day))
        is_new = self.store.enqueue_judgment(event_id, feed_id, user_id, day, season)
        if not is_new:
            row = [r for r in self.store.pending() if r["event_id"] == event_id]
            if not row:
                return  # 已判過（重放）——冪等跳過

        try:
            feed = self.client.feed(feed_id)
        except GamAPIError as exc:
            logger.warning(f"[loop] feed fetch failed {feed_id}: {exc}（留在佇列，fail-open 兜底）")
            return

        decision = judge.judge_feed(feed, self.theme)
        logger.info(
            f"[loop] judge feed={feed_id} user={user_id} → {decision.action} ({decision.note})"
        )

        if decision.action in {"approve", "provisional"}:
            award_checkin(
                self.client,
                self.store,
                self.cfg,
                user_id=user_id,
                feed_id=feed_id,
                day=day,
                season=season,
                ref_event_id=event_id,
            )
            note = decision.note if decision.action == "approve" else f"PROVISIONAL {decision.note}"
            self.store.decide(event_id, "approved", note=note)
        elif decision.action == "reject":
            # 紅線：不公開退件。標記後留給營運週報（Phase 2 走 DM 補件）。
            self.store.decide(event_id, "rejected", note=decision.note)
        else:  # queue —— 留 pending，reconcile 的 48h fail-open 兜底
            pass

    # ── forever ──────────────────────────────────────────────────
    def run_forever(self) -> None:
        logger.info(f"[loop] start（poll={self.cfg.poll_seconds}s, theme={self.theme}）")
        while True:
            try:
                processed = self.cycle()
                # 滿頁＝可能還有積壓，立刻再拉；否則按節奏睡
                time.sleep(0 if processed >= 200 else self.cfg.poll_seconds)
            except GamDisabled:
                logger.info(f"[loop] gam_enabled=0，{_DISABLED_SLEEP}s 後再看")
                time.sleep(_DISABLED_SLEEP)
            except KeyboardInterrupt:
                logger.info("[loop] interrupted, bye")
                return
            except Exception as exc:  # noqa: BLE001 — 服務迴圈不許死
                logger.error(f"[loop] cycle error: {exc}")
                time.sleep(_ERROR_SLEEP)
