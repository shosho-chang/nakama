"""候選收件匣 + 事件記錄 — ADR-048 Phase 1（D-B）.

每日回顧（``agents/robin/daily_review.py``）原把候選存成單槽快照
``KB/.centaur/daily_review_latest.json``，每次 run 整個覆寫——昨天提的候選若沒處理，
今天一覆寫就消失（修修回報的「點子消失」）。本層把候選**持久化**進 ``state.db``，
跨每日 run **結轉**（carry-forward）：未開卡的候選一直留在收件匣，直到使用者
開卡 / 略過 / 之後再說。

兩張表：

- ``candidate_inbox`` — 每條候選一列，status ∈ {``open``, ``carded``}。``first_seen`` /
  ``last_seen`` 記首次 / 最近一次被提出，``carded_path`` 記開卡後的永久卡路徑。
  這是 carry-forward 的權威：:func:`list_open` 回所有 ``status='open'``，每日回顧
  據此把昨天沒處理的候選帶到今天。

- ``candidate_events`` — append-only 事件流（``proposed`` / ``card`` / ``skip`` /
  ``defer`` / ``expire``），是 ADR-048 後續學習（Phase 3 行為模式 → P-1 排序）的
  **ground truth**。

紅線 / 權威分界（避免雙寫漂移）：

- 這是系統記帳（哪些候選還沒處理 + 使用者怎麼處置），**不是永久卡、不碰
  ``KB/Permanent/``**。
- skip / defer 的「過濾權威」仍是 vault-side ``daily_review_state.json``（ADR-041
  慣例、Syncthing 同步）。本層對 skip / defer 只**額外記事件**供學習，不搶 filter 權，
  也**不**改 ``inbox.status``。``inbox.status`` 只在「開卡」時 ``open`` → ``carded``
  —— 單一 home，無雙寫漂移。完整把 skip / defer 收進 DB 留作 Phase 1b。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from shared.log import get_logger
from shared.schemas.daily_review import CandidateCard, SourceRef
from shared.state import _get_conn

logger = get_logger("nakama.candidate_inbox")

_SCHEMA_INITIALIZED = False

# 候選狀態。skip / defer 不在此（見模組 docstring：過濾權威留 JSON）。
STATUS_OPEN = "open"
STATUS_CARDED = "carded"

# 事件類型（ground truth 學習用）。``open`` = drawer 打開未開卡（高意圖訊號，
# 待有對應 endpoint hook 再寫）；其餘四類在每日回顧 / 開卡 / skip / defer 落點寫入。
EVENT_TYPES = frozenset({"proposed", "card", "skip", "defer", "expire", "open"})

# 每日回顧顯示上限：超過此數的未處理候選**留在收件匣不丟**，僅在 bundle warnings
# 警示（避免一面牆淹沒人，也別讓 5am edge 重算無上限）。清掉一些後其餘自動浮現。
MAX_INBOX_DISPLAY = 30


def _ensure_schema() -> None:
    global _SCHEMA_INITIALIZED
    if _SCHEMA_INITIALIZED:
        return
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS candidate_inbox (
            candidate_id    TEXT PRIMARY KEY,
            suggested_title TEXT NOT NULL,
            why             TEXT NOT NULL DEFAULT '',
            source_refs     TEXT NOT NULL DEFAULT '[]',
            strong_signal   INTEGER NOT NULL DEFAULT 0,
            proposed_priority INTEGER NOT NULL DEFAULT 0,
            status          TEXT NOT NULL DEFAULT 'open',
            first_seen      TEXT NOT NULL,
            last_seen       TEXT NOT NULL,
            carded_path     TEXT,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_candidate_inbox_status
            ON candidate_inbox(status, strong_signal DESC, first_seen DESC);

        CREATE TABLE IF NOT EXISTS candidate_events (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id  TEXT NOT NULL,
            event_type    TEXT NOT NULL,
            reason        TEXT,
            metadata      TEXT,
            occurred_at   TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_candidate_events_cid
            ON candidate_events(candidate_id, occurred_at);
        CREATE INDEX IF NOT EXISTS idx_candidate_events_type
            ON candidate_events(event_type, occurred_at DESC);
    """)
    conn.commit()
    _SCHEMA_INITIALIZED = True


# ---------------------------------------------------------------------------
# Events（append-only ground truth）
# ---------------------------------------------------------------------------


def log_event(
    candidate_id: str,
    event_type: str,
    *,
    reason: str | None = None,
    metadata: dict | None = None,
    occurred_at: str | None = None,
) -> int:
    """記一筆候選事件（append-only）。``occurred_at`` 預設 now（UTC ISO）。回新 row id。

    ``event_type`` 不在 :data:`EVENT_TYPES` 仍照寫（append-only log 不擋未知型別，
    只記 warning），避免呼叫端新增型別時靜默失敗。``metadata`` 以 JSON TEXT 存。
    """
    if event_type not in EVENT_TYPES:
        logger.warning("unknown candidate event_type=%r (logged anyway)", event_type)
    _ensure_schema()
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    meta_json = json.dumps(metadata, ensure_ascii=False) if metadata else None
    cur = conn.execute(
        """INSERT INTO candidate_events (candidate_id, event_type, reason, metadata, occurred_at)
           VALUES (?, ?, ?, ?, ?)""",
        (candidate_id, event_type, reason, meta_json, occurred_at or now),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_events(
    *,
    candidate_id: str | None = None,
    event_type: str | None = None,
    since: str | None = None,
    limit: int = 500,
) -> list[dict]:
    """讀事件流（Phase 3 學習的入口）。可選 filter；``since`` 是 ISO 下界（含）。

    回 dict list（``metadata`` 已 parse 回 dict），newest first。
    """
    _ensure_schema()
    conn = _get_conn()
    conds: list[str] = []
    params: list = []
    if candidate_id is not None:
        conds.append("candidate_id = ?")
        params.append(candidate_id)
    if event_type is not None:
        conds.append("event_type = ?")
        params.append(event_type)
    if since is not None:
        conds.append("occurred_at >= ?")
        params.append(since)
    where = f"WHERE {' AND '.join(conds)}" if conds else ""
    rows = conn.execute(
        f"""SELECT id, candidate_id, event_type, reason, metadata, occurred_at
            FROM candidate_events
            {where}
            ORDER BY occurred_at DESC, id DESC
            LIMIT ?""",
        [*params, limit],
    ).fetchall()
    out: list[dict] = []
    for r in rows:
        meta = None
        if r["metadata"]:
            try:
                meta = json.loads(r["metadata"])
            except (ValueError, TypeError):
                meta = None
        out.append(
            {
                "id": r["id"],
                "candidate_id": r["candidate_id"],
                "event_type": r["event_type"],
                "reason": r["reason"],
                "metadata": meta,
                "occurred_at": r["occurred_at"],
            }
        )
    return out


# ---------------------------------------------------------------------------
# Inbox（持久化 + carry-forward）
# ---------------------------------------------------------------------------


def _serialize_refs(refs: list[SourceRef]) -> str:
    return json.dumps([r.model_dump() for r in refs], ensure_ascii=False)


def _deserialize_refs(raw: str | None) -> list[SourceRef]:
    try:
        data = json.loads(raw or "[]")
    except (ValueError, TypeError):
        return []
    out: list[SourceRef] = []
    for d in data:
        if isinstance(d, dict):
            try:
                out.append(SourceRef(**d))
            except (TypeError, ValueError):
                continue
    return out


def upsert_candidate(card: CandidateCard, *, today: str) -> bool:
    """把一條 P-1 候選寫進收件匣。

    - 新候選 → INSERT（``status='open'``、``first_seen=last_seen=today``）+ 記
      ``proposed`` 事件，回 ``True``。
    - 已存在且仍 ``open`` → 只刷新 ``last_seen`` 與 title/why/refs/strong（取最新
      P-1 措辭），回 ``False``。
    - 已存在但已 ``carded`` → 只觸 ``last_seen``（保留 terminal 狀態，不復活），回 ``False``。

    ``today`` 是台北日曆日 ISO（``YYYY-MM-DD``，與每日回顧 ``review_date`` 同基準）。
    """
    _ensure_schema()
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    refs_json = _serialize_refs(card.source_refs)
    strong = 1 if card.strong_signal else 0

    row = conn.execute(
        "SELECT status FROM candidate_inbox WHERE candidate_id = ?",
        (card.candidate_id,),
    ).fetchone()

    if row is None:
        conn.execute(
            """INSERT INTO candidate_inbox
                 (candidate_id, suggested_title, why, source_refs, strong_signal,
                  proposed_priority, status, first_seen, last_seen, carded_path,
                  created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?, NULL, ?, ?)""",
            (
                card.candidate_id,
                card.suggested_title,
                card.why,
                refs_json,
                strong,
                card.priority,
                today,
                today,
                now,
                now,
            ),
        )
        conn.commit()
        log_event(card.candidate_id, "proposed", metadata={"title": card.suggested_title})
        return True

    if row["status"] == STATUS_OPEN:
        conn.execute(
            """UPDATE candidate_inbox
               SET suggested_title = ?, why = ?, source_refs = ?, strong_signal = ?,
                   proposed_priority = ?, last_seen = ?, updated_at = ?
               WHERE candidate_id = ?""",
            (
                card.suggested_title,
                card.why,
                refs_json,
                strong,
                card.priority,
                today,
                now,
                card.candidate_id,
            ),
        )
    else:  # carded：只觸 last_seen，不動 terminal 狀態
        conn.execute(
            "UPDATE candidate_inbox SET last_seen = ?, updated_at = ? WHERE candidate_id = ?",
            (today, now, card.candidate_id),
        )
    conn.commit()
    return False


def list_open() -> list[CandidateCard]:
    """收件匣所有 ``status='open'`` 候選 → :class:`CandidateCard`（無 edges；edges 由
    每日回顧重算）。

    排序：強訊號置頂，其餘新 → 舊（``first_seen DESC``）——剛讀的最相關擺前面、強訊號
    永遠置頂；``priority`` 依此序重編（0 = 置頂）。skip / defer 的過濾由呼叫端
    （每日回顧）用 state JSON 套用，本函式不涉。
    """
    _ensure_schema()
    conn = _get_conn()
    rows = conn.execute(
        """SELECT candidate_id, suggested_title, why, source_refs, strong_signal
           FROM candidate_inbox
           WHERE status = 'open'
           ORDER BY strong_signal DESC, first_seen DESC, proposed_priority ASC, candidate_id"""
    ).fetchall()
    out: list[CandidateCard] = []
    for i, r in enumerate(rows):
        out.append(
            CandidateCard(
                candidate_id=r["candidate_id"],
                suggested_title=r["suggested_title"],
                why=r["why"] or "",
                source_refs=_deserialize_refs(r["source_refs"]),
                edges=[],
                priority=i,
                strong_signal=bool(r["strong_signal"]),
            )
        )
    return out


def mark_carded(candidate_id: str, *, carded_path: str = "") -> bool:
    """開卡：候選 ``open`` → ``carded``（:func:`list_open` 不再回它），記 ``card`` 事件
    + 永久卡路徑。

    回 ``True`` 若收件匣確有此候選被更新；fleeting / 手建卡無對應候選（rowcount 0）→
    ``False``（仍記 card 事件，append-only ground truth 不漏）。
    """
    _ensure_schema()
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """UPDATE candidate_inbox
           SET status = 'carded', carded_path = ?, updated_at = ?
           WHERE candidate_id = ?""",
        (carded_path or None, now, candidate_id),
    )
    conn.commit()
    log_event(
        candidate_id,
        "card",
        metadata={"carded_path": carded_path} if carded_path else None,
    )
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Inspectors（測試 / 維運用）
# ---------------------------------------------------------------------------


def get_candidate(candidate_id: str) -> dict | None:
    """讀單列收件匣 raw 狀態（含 ``status`` / ``carded_path`` / ``first_seen`` 等）。無 → None。"""
    _ensure_schema()
    conn = _get_conn()
    r = conn.execute(
        "SELECT * FROM candidate_inbox WHERE candidate_id = ?",
        (candidate_id,),
    ).fetchone()
    return dict(r) if r is not None else None


def count_open() -> int:
    """目前 ``status='open'`` 的候選數（含尚未被 state JSON skip / defer 濾掉者）。"""
    _ensure_schema()
    conn = _get_conn()
    r = conn.execute("SELECT COUNT(*) AS n FROM candidate_inbox WHERE status = 'open'").fetchone()
    return int(r["n"]) if r is not None else 0


def carded_among(candidate_ids: list[str]) -> set[str]:
    """回傳這批 candidate_id 中狀態為 ``carded`` 的子集。

    UI 用：快照凍結在開卡之前，同日 reload 要即時濾掉剛開卡的候選（不必等隔天 cron
    重算）。空輸入 / 空字串自動略過。
    """
    ids = [c for c in candidate_ids if c]
    if not ids:
        return set()
    _ensure_schema()
    conn = _get_conn()
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT candidate_id FROM candidate_inbox "
        f"WHERE status = 'carded' AND candidate_id IN ({placeholders})",
        ids,
    ).fetchall()
    return {r["candidate_id"] for r in rows}


def _reset_for_tests(conn: sqlite3.Connection | None = None) -> None:  # pragma: no cover
    """測試輔助：清空兩張表（isolated_db fixture 已換 DB，通常不需；保留供顯式清理）。"""
    _ensure_schema()
    conn = conn or _get_conn()
    conn.execute("DELETE FROM candidate_inbox")
    conn.execute("DELETE FROM candidate_events")
    conn.commit()
