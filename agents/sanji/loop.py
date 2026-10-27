"""Sanji 主輪詢迴圈——分鐘級回饋的心臟。輪詢本身零 LLM；只有文字打卡判定會呼叫模型。

流程（每輪）：
  1. cursor 增量拉事件（``GET /events``）
  2. 確定性事件（presence/lesson/course/like）→ rules → 批次入帳
  3. ``checkin_submitted`` → 判定漏斗 → 通過則入帳＋公開留言回覆
  4. 處理完整批才推進 cursor（at-least-once；plugin 端 idempotency 保證重放安全）

故障模式：
  - ``GamDisabled``（止血開關）→ 安靜長睡，不告警（那是人為關閉）
  - 其他例外 → log ＋ 短睡重試；cursor 未推進，事件不丟失
  - 連續失敗 ``_ALERT_AFTER_FAILURES`` 輪 → Franky DM（dedupe 一天一則），恢復時再 DM 一次

2026-09-06 教訓：Cloudflare 擋下每一輪請求三週，loop 只每分鐘記一行一模一樣的
ERROR（journal 7,800+ 行），自己從不告警；唯一的訊號是隔天 05:00 對帳的告警。
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from agents.sanji import judge, rules, templates
from agents.sanji.settings import SanjiConfig
from agents.sanji.store import Store
from agents.sanji.wp_client import GamAPIError, GamDisabled, WPClient
from shared.alerts import alert, resolve
from shared.log import get_logger

logger = get_logger("nakama.sanji.loop")

_DISABLED_SLEEP = 600  # 止血開關關閉時的輪詢間隔
_ERROR_SLEEP = 60
_ALERT_AFTER_FAILURES = 5  # ~5 分鐘；濾掉 WP 重啟、網路瞬斷
_ALERT_RETRY_SECONDS = 3600  # 故障持續中每小時再送一次；實際 DM 由 dedupe 壓成一天一則
_ALERT_DEDUPE_MINUTES = 24 * 60
_LOG_EVERY_FAILURES = 60  # 同一個錯誤連續出現時，每 60 輪（~1h）才再記一行 ERROR
_LOOP_DOWN_KEY = "gam-loop-down"
_TAIPEI = ZoneInfo("Asia/Taipei")


def level_fields(xp_total: int) -> dict:
    """等級帶四欄——投影要畫進度條，但 plugin 仍拿不到整張曲線。"""
    level, floor, nxt = rules.level_band(xp_total)
    return {
        "level_after": level,
        "level_label": rules.level_label(level),
        "level_min_xp": floor,
        "next_level_xp": nxt,
        # 滿級 → 空字串（UI 據此切「已達最高階」）
        "next_level_label": rules.level_label(level + 1) if nxt else "",
    }


class LevelStamper:
    """替每筆 grant 算等級帶（等級曲線只存在 nakama；plugin 不知道門檻）。

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
        grant.update(level_fields(self._xp[uid]))
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

    # 打卡狀態照記（streak 連續性不能有洞），入帳與公開回覆才受計分名單管制。
    store.record_checkin_day(user_id, day, season, feed_id)
    streak = store.current_streak(user_id, day)

    # ⚠️ 這道閘門與 grant_for_event 的那道是同一個名單。2026-09-02 之前打卡
    # 走的是 SanjiLoop.cycle 的 if/else 前半段，繞過了名單——挑戰一上線就會
    # 無視 scored_sources 直接入帳。閘門收在這裡，因為 loop 與 reconcile 都
    # 經過本函式，補在呼叫端會漏掉其中一條。
    if "checkin_day" not in cfg.scored_sources:
        logger.info(f"[loop] checkin 不在計分名單，只記狀態不入帳 user={user_id} day={day}")
        return

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
        streak=streak,
        bonus_xp=bonus["xp"] if bonus else 0,
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
        # 故障追蹤（in-memory；跨重啟的 DM 去重靠 alert_state 表）
        self._fail_streak = 0
        self._down_since: datetime | None = None
        self._last_error = ""
        self._last_alert_at: datetime | None = None
        # 啟動後第一次成功也要試著 resolve：可能是上一個 process 告警過、重啟後直接就好了
        self._resolve_pending = True

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

    # ── health ───────────────────────────────────────────────────
    def on_cycle_error(self, exc: Exception, *, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        msg = str(exc)
        self._fail_streak += 1
        if self._fail_streak == 1:
            self._down_since = now
        if (
            self._fail_streak == 1
            or msg != self._last_error
            or self._fail_streak % _LOG_EVERY_FAILURES == 0
        ):
            logger.error(f"[loop] cycle error（連續第 {self._fail_streak} 輪）: {msg}")
        self._last_error = msg

        if self._fail_streak < _ALERT_AFTER_FAILURES:
            return
        if (
            self._last_alert_at is not None
            and (now - self._last_alert_at).total_seconds() < _ALERT_RETRY_SECONDS
        ):
            return
        self._last_alert_at = now
        try:
            alert(
                "error",
                "gam",
                f"Sanji 主迴圈連續 {self._fail_streak} 輪失敗（{self._since_label()} 起），"
                f"入帳與打卡回覆都停了；事件留在 plugin 不會丟，恢復後自動補處理。"
                f"最後錯誤：{msg[:300]}",
                dedupe_key=_LOOP_DOWN_KEY,
                dedupe_minutes=_ALERT_DEDUPE_MINUTES,
            )
        except Exception as alert_exc:  # noqa: BLE001 — 告警失敗不能拖垮迴圈
            logger.error(f"[loop] alert failed: {alert_exc}")

    def on_cycle_ok(self) -> None:
        if not (self._fail_streak or self._resolve_pending):
            return
        if self._fail_streak:
            logger.info(
                f"[loop] 恢復：{self._since_label()} 起連續 {self._fail_streak} 輪失敗後成功"
            )
        since = f"（{self._since_label()} 起中斷）" if self._down_since else ""
        try:
            resolve(
                "gam",
                f"Sanji 主迴圈已恢復{since}；積壓事件照 cursor 補處理中。",
                dedupe_key=_LOOP_DOWN_KEY,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[loop] resolve failed: {exc}")
        self._fail_streak = 0
        self._down_since = None
        self._last_error = ""
        self._last_alert_at = None
        self._resolve_pending = False

    def _since_label(self) -> str:
        if self._down_since is None:
            return "?"
        return self._down_since.astimezone(_TAIPEI).strftime("%m-%d %H:%M")

    # ── forever ──────────────────────────────────────────────────
    def run_forever(self) -> None:
        logger.info(f"[loop] start（poll={self.cfg.poll_seconds}s, theme={self.theme}）")
        while True:
            try:
                processed = self.cycle()
                self.on_cycle_ok()
                # 滿頁＝可能還有積壓，立刻再拉；否則按節奏睡
                time.sleep(0 if processed >= 200 else self.cfg.poll_seconds)
            except GamDisabled:
                logger.info(f"[loop] gam_enabled=0，{_DISABLED_SLEEP}s 後再看")
                time.sleep(_DISABLED_SLEEP)
            except KeyboardInterrupt:
                logger.info("[loop] interrupted, bye")
                return
            except Exception as exc:  # noqa: BLE001 — 服務迴圈不許死
                self.on_cycle_error(exc)
                time.sleep(_ERROR_SLEEP)
