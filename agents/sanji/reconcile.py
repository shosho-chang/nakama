"""每日對帳（05:00）——architecture 上必要的排程：
streak 斷檔是「事件的缺席」，只有排程掃描能偵測；它同時是 Sanji 停機的補課 safety net。

七件事：
  1. 收藏掃描：vendor 對 bookmark 不發 hook（原始碼證實）→ 增量掃 reactions 補入帳
  1b. 讚掃描／留言掃描：與 hook 路徑同冪等鍵——第一次跑＝歷史認列（2026-08-25
      修修裁決：內容品質訊號完整認列；歷史上也只存在這些訊號），之後＝hook 漏接安全網
  2. fail-open：滯留 >48h 的待判定案自動放行（``auto_approved_by_timeout``，留痕）
  3. 斷流偵測：events cursor 自上次對帳未動 → 告警（靜默失效的主防線）
  4. 投影抽核：昨日活躍者 balances vs 帳本重算，落差告警（idempotency 破洞偵測）
  5. 等級回沖：全表依「當前曲線」重算等級帶——曲線校準後不必寫一次性 backfill，
     隔夜自動收斂（門檻只降不升，所以回沖永遠不會讓人掉級）

告警走 shared.alerts（Slack，Franky 既有管道）。
"""

from __future__ import annotations

from datetime import date, timedelta

from agents.sanji import rules
from agents.sanji.loop import LevelStamper, award_checkin, level_fields
from agents.sanji.settings import SanjiConfig
from agents.sanji.store import Store
from agents.sanji.wp_client import GamAPIError, WPClient
from shared.alerts import alert
from shared.log import get_logger

logger = get_logger("nakama.sanji.reconcile")


def run(cfg: SanjiConfig, client: WPClient, store: Store) -> dict:
    """跑完整對帳，回傳摘要 dict（log/測試用）。單項失敗不中斷其他項。"""
    summary: dict[str, object] = {}

    for name, fn in (
        ("bookmarks", _sweep_bookmarks),
        ("likes", _sweep_likes),
        ("comments", _sweep_comments),
        ("fail_open", _sweep_fail_open),
        ("flow", _check_event_flow),
        ("balances", _audit_balances),
        ("levels", _restamp_levels),
    ):
        try:
            summary[name] = fn(cfg, client, store)
        except Exception as exc:  # noqa: BLE001 — 對帳的單項失敗要告警但不中斷
            logger.error(f"[reconcile] {name} failed: {exc}")
            alert(
                "error", "gam", f"Sanji 對帳項目 {name} 失敗：{exc}", dedupe_key=f"gam-rec-{name}"
            )
            summary[name] = f"error: {exc}"

    logger.info(f"[reconcile] done: {summary}")
    return summary


# ── 1. 收藏掃描 ──────────────────────────────────────────────────
def _sweep_bookmarks(cfg: SanjiConfig, client: WPClient, store: Store) -> dict:
    cursor = store.get_cursor("reactions_bookmark")
    granted = 0
    scanned = 0
    owner_cache: dict[int, int] = {}

    while True:
        page = client.reactions(cursor, types="bookmark", limit=200)
        rows = page.get("reactions", [])
        if not rows:
            break

        grants: list[dict] = []
        for row in rows:
            scanned += 1
            feed_id = int(row.get("object_id", 0))
            if str(row.get("object_type", "")) != "feed" or not feed_id:
                continue
            if feed_id not in owner_cache:
                try:
                    owner_cache[feed_id] = int(client.feed(feed_id).get("user_id", 0))
                except GamAPIError:
                    owner_cache[feed_id] = 0  # 貼文已刪——收藏不入帳
            g = rules.grant_for_bookmark(row, owner_cache[feed_id], sanji_user_id=cfg.sanji_user_id)
            if g and g["source"] in cfg.scored_sources:
                grants.append(g)

        if grants:
            stamper = LevelStamper(client)
            for i in range(0, len(grants), 100):
                client.grants([stamper.stamp(g) for g in grants[i : i + 100]])
            granted += len(grants)

        cursor = int(page.get("max_id", cursor))
        store.set_cursor("reactions_bookmark", cursor)
        if len(rows) < 200:
            break

    return {"scanned": scanned, "granted": granted}


# ── 2. fail-open（漏斗⑦） ────────────────────────────────────────
def _sweep_likes(cfg: SanjiConfig, client: WPClient, store: Store) -> dict:
    """讚的增量掃描（cursor 獨立於 hook 事件流；鍵同格式故兩路互為冪等）。"""
    cursor = store.get_cursor("reactions_like")
    granted = 0
    scanned = 0
    owner_cache: dict[int, int] = {}

    while True:
        page = client.reactions(cursor, types="like", limit=200)
        rows = page.get("reactions", [])
        if not rows:
            break

        grants: list[dict] = []
        for row in rows:
            scanned += 1
            feed_id = int(row.get("object_id", 0))
            if str(row.get("object_type", "")) != "feed" or not feed_id:
                continue
            if feed_id not in owner_cache:
                try:
                    owner_cache[feed_id] = int(client.feed(feed_id).get("user_id", 0))
                except GamAPIError:
                    owner_cache[feed_id] = 0  # 貼文已刪——不入帳
            g = rules.grant_for_like_row(row, owner_cache[feed_id], sanji_user_id=cfg.sanji_user_id)
            if g and g["source"] in cfg.scored_sources:
                grants.append(g)

        if grants:
            # plugin 批次上限 100/批——切塊（2026-08-25 首跑教訓：讚一頁近 200 筆直接被防呆擋下）
            stamper = LevelStamper(client)
            created = 0
            for i in range(0, len(grants), 100):
                results = client.grants([stamper.stamp(g) for g in grants[i : i + 100]])
                created += sum(
                    1 for r in results.get("results", []) if r.get("status") == "created"
                )
            granted += created

        cursor = max(int(r.get("id", 0)) for r in rows)
        store.set_cursor("reactions_like", cursor)
        if len(rows) < 200:
            break

    if granted:
        logger.info(f"[reconcile] like sweep granted {granted}/{scanned}")
    return {"scanned": scanned, "granted": granted}


def _sweep_comments(cfg: SanjiConfig, client: WPClient, store: Store) -> dict:
    """留言的增量掃描（owner 已由 plugin join 好，不需逐文查作者）。"""
    cursor = store.get_cursor("comments")
    granted = 0
    scanned = 0

    while True:
        page = client.comments_list(cursor, limit=200)
        rows = page.get("comments", [])
        if not rows:
            break

        grants: list[dict] = []
        for row in rows:
            scanned += 1
            g = rules.grant_for_comment_row(row, sanji_user_id=cfg.sanji_user_id)
            if g and g["source"] in cfg.scored_sources:
                grants.append(g)

        if grants:
            # plugin 批次上限 100/批——切塊（2026-08-25 首跑教訓：讚一頁近 200 筆直接被防呆擋下）
            stamper = LevelStamper(client)
            created = 0
            for i in range(0, len(grants), 100):
                results = client.grants([stamper.stamp(g) for g in grants[i : i + 100]])
                created += sum(
                    1 for r in results.get("results", []) if r.get("status") == "created"
                )
            granted += created

        cursor = max(int(r.get("id", 0)) for r in rows)
        store.set_cursor("comments", cursor)
        if len(rows) < 200:
            break

    if granted:
        logger.info(f"[reconcile] comment sweep granted {granted}/{scanned}")
    return {"scanned": scanned, "granted": granted}


def _sweep_fail_open(cfg: SanjiConfig, client: WPClient, store: Store) -> dict:
    stale = store.pending_older_than_hours(cfg.fail_open_hours)
    for row in stale:
        award_checkin(
            client,
            store,
            cfg,
            user_id=int(row["user_id"]),
            feed_id=int(row["feed_id"]),
            day=str(row["day"]),
            season=str(row["season"]),
            ref_event_id=int(row["event_id"]),
        )
        store.decide(int(row["event_id"]), "auto_approved_by_timeout", note="fail-open 48h")
        logger.info(f"[reconcile] fail-open release event={row['event_id']}")

    if stale:
        alert(
            "warn",
            "gam",
            f"Sanji fail-open 放行 {len(stale)} 件滯留判定（>48h）——判定漏斗有積壓",
        )
    return {"released": len(stale)}


# ── 3. 斷流偵測 ──────────────────────────────────────────────────
def _check_event_flow(cfg: SanjiConfig, client: WPClient, store: Store) -> dict:
    current = store.get_cursor("events")
    last_seen = store.get_cursor("flow_snapshot")
    store.set_cursor("flow_snapshot", current)

    if last_seen and current == last_seen:
        # 24h 零新事件：捕捉層斷線 / plugin 停用 / 社群真的全靜——都值得有人看一眼
        alert(
            "error",
            "gam",
            "Sanji 斷流警報：距上次對帳 events cursor 未前進（24h 零事件）。"
            "檢查 plugin 是否停用、hook 是否失效。",
            dedupe_key="gam-flow-stall",
        )
        return {"stalled": True, "cursor": current}
    return {"stalled": False, "cursor": current}


# ── 4. 投影抽核 ──────────────────────────────────────────────────
def _audit_balances(cfg: SanjiConfig, client: WPClient, store: Store) -> dict:
    """昨日打卡者的 balances vs 帳本重算。
    ⚠️ 覆蓋範圍是「昨日活躍者」不是全體——全量審計等資料量成形後排 Phase 2。
    """
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    users = store.users_checked_in_on(yesterday)
    mismatches = 0

    for uid in users:
        before = client.balance(uid)
        after = client.balance(uid, rebuild=True)  # rebuild = 由帳本重算並覆寫投影
        if int(before.get("xp_total", 0)) != int(after.get("xp_total", 0)) or int(
            before.get("berry_balance", 0)
        ) != int(after.get("berry_balance", 0)):
            mismatches += 1
            alert(
                "error",
                "gam",
                f"Sanji 投影落差 user={uid}: "
                f"xp {before.get('xp_total')}→{after.get('xp_total')} "
                f"berry {before.get('berry_balance')}→{after.get('berry_balance')}"
                "（已重算修復，查 idempotency）",
                dedupe_key=f"gam-balance-{uid}",
            )

    return {"audited": len(users), "mismatches": mismatches}


# ── 5. 等級回沖 ──────────────────────────────────────────────────
def _restamp_levels(cfg: SanjiConfig, client: WPClient, store: Store) -> dict:
    """全表把 xp_total 重新換算成等級帶，只回沖有變的人。

    等級是 xp_total 的純函式，所以這件事冪等、可重跑。
    存在的理由：曲線重新校準後（例如 2026-08-24 的 v2），既有成員的投影還停在
    舊等級——不做這個就得等他下一筆入帳才更新，profile 上會顯示過期稱號。
    """
    after_uid = 0
    scanned = 0
    changed: list[dict] = []

    while True:
        page = client.balances(after_uid, limit=200)
        items = page.get("items") or []
        if not items:
            break
        for row in items:
            uid = int(row.get("user_id", 0))
            after_uid = max(after_uid, uid)
            scanned += 1
            want = level_fields(int(row.get("xp_total", 0)))
            same = (
                int(row.get("level", 0)) == want["level_after"]
                and str(row.get("level_label", "")) == want["level_label"]
                and int(row.get("level_min_xp", -1)) == want["level_min_xp"]
                and int(row.get("next_level_xp", -1)) == want["next_level_xp"]
                and str(row.get("next_level_label", "")) == want["next_level_label"]
            )
            if not same:
                changed.append({"user_id": uid, **want})
        if len(items) < 200:
            break

    updated = 0
    for i in range(0, len(changed), 500):
        updated += int(client.restamp_levels(changed[i : i + 500]).get("updated", 0))

    if updated:
        logger.info(f"[reconcile] restamped {updated}/{scanned} levels")
    return {"scanned": scanned, "restamped": updated}
