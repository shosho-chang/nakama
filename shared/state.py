"""SQLite 狀態管理：追蹤已處理檔案、agent 執行紀錄、記憶搜尋。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from shared.config import get_db_path

_conn: Optional[sqlite3.Connection] = None


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        db_path = get_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(db_path), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        _conn.execute("PRAGMA busy_timeout=5000")
        _conn.execute("PRAGMA foreign_keys=ON")
        _init_tables(_conn)
    return _conn


def _init_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS files_processed (
            file_path   TEXT PRIMARY KEY,
            file_hash   TEXT NOT NULL,
            agent       TEXT NOT NULL,
            processed_at TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'done'
        );

        CREATE TABLE IF NOT EXISTS agent_runs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            agent       TEXT NOT NULL,
            started_at  TEXT NOT NULL,
            finished_at TEXT,
            status      TEXT NOT NULL DEFAULT 'running',
            summary     TEXT,
            input_tokens  INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS api_calls (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            agent               TEXT NOT NULL,
            run_id              INTEGER,
            model               TEXT NOT NULL,
            input_tokens        INTEGER NOT NULL,
            output_tokens       INTEGER NOT NULL,
            cache_read_tokens   INTEGER NOT NULL DEFAULT 0,
            cache_write_tokens  INTEGER NOT NULL DEFAULT 0,
            latency_ms          INTEGER NOT NULL DEFAULT 0,
            called_at           TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_api_calls_agent_time
            ON api_calls(agent, called_at);
        CREATE INDEX IF NOT EXISTS idx_api_calls_time
            ON api_calls(called_at);

        CREATE TABLE IF NOT EXISTS scout_seen (
            source      TEXT NOT NULL,
            item_id     TEXT NOT NULL,
            url         TEXT,
            first_seen  TEXT NOT NULL,
            PRIMARY KEY (source, item_id)
        );

        CREATE TABLE IF NOT EXISTS community_alerts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id     TEXT,
            alert_type  TEXT NOT NULL,
            severity    TEXT NOT NULL DEFAULT 'info',
            created_at  TEXT NOT NULL,
            resolved    INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS files_read (
            file_path   TEXT NOT NULL PRIMARY KEY,
            read_at     TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS agent_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source      TEXT NOT NULL,
            event_type  TEXT NOT NULL,
            payload     TEXT NOT NULL DEFAULT '{}',
            created_at  TEXT NOT NULL,
            consumed_by TEXT,
            consumed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS event_consumptions (
            event_id    INTEGER NOT NULL,
            consumer    TEXT NOT NULL,
            consumed_at TEXT NOT NULL,
            PRIMARY KEY (event_id, consumer),
            FOREIGN KEY (event_id) REFERENCES agent_events(id)
        );

        -- ADR-002 Tier 3: 記憶搜尋層
        CREATE TABLE IF NOT EXISTS memories (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            agent       TEXT NOT NULL,
            type        TEXT NOT NULL,
            title       TEXT NOT NULL,
            content     TEXT NOT NULL,
            tags        TEXT NOT NULL DEFAULT '[]',
            confidence  TEXT NOT NULL DEFAULT 'medium',
            source      TEXT,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL,
            expires_at  TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_memories_agent ON memories(agent);
        CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(type);

        -- ADR-006: HITL approval queue（Brook / Usopp / Chopper / Sanji 共用）
        -- Status FSM SoT 在 shared/approval_queue.ALLOWED_TRANSITIONS；
        -- CHECK 列表必與 ALL_STATUSES 相等，由 FSM 測試斷言
        CREATE TABLE IF NOT EXISTS approval_queue (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at              TEXT NOT NULL
                                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now')),
            updated_at              TEXT NOT NULL
                                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now')),

            source_agent            TEXT NOT NULL,
            target_platform         TEXT NOT NULL,
            target_site             TEXT,
            action_type             TEXT NOT NULL,
            priority                INTEGER NOT NULL DEFAULT 50 CHECK (priority BETWEEN 0 AND 100),

            payload_version         INTEGER NOT NULL,
            payload                 TEXT NOT NULL,
            title_snippet           TEXT NOT NULL,
            diff_target_id          TEXT,

            status                  TEXT NOT NULL
                                    CHECK (status IN ('pending','in_review','approved','rejected',
                                                      'claimed','published','failed','archived')),

            reviewer                TEXT,
            review_note             TEXT,
            reviewed_at             TEXT,

            -- ADR-005b §10 compliance gate
            reviewer_compliance_ack INTEGER NOT NULL DEFAULT 0,

            worker_id               TEXT,
            claimed_at              TEXT,
            published_at            TEXT,
            execution_result        TEXT,
            retry_count             INTEGER NOT NULL DEFAULT 0,
            error_log               TEXT,

            operation_id            TEXT NOT NULL,
            cost_usd_compose        REAL
        );

        CREATE INDEX IF NOT EXISTS idx_queue_status_priority
            ON approval_queue(status, priority DESC, created_at ASC);
        CREATE INDEX IF NOT EXISTS idx_queue_agent_status
            ON approval_queue(source_agent, status);
        CREATE INDEX IF NOT EXISTS idx_queue_operation
            ON approval_queue(operation_id);

        -- ADR-005b §1 / §2 / §10 Usopp publish_jobs state machine
        -- (canonical DDL: migrations/002_publish_jobs.sql)
        CREATE TABLE IF NOT EXISTS publish_jobs (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            draft_id            TEXT    NOT NULL UNIQUE,
            approval_queue_id   INTEGER NOT NULL,
            operation_id        TEXT    NOT NULL,
            state               TEXT    NOT NULL
                                CHECK (state IN ('claimed', 'media_ready', 'post_draft',
                                                 'seo_ready', 'validated', 'published',
                                                 'cache_purged', 'done', 'failed')),
            state_updated_at    TEXT    NOT NULL,
            featured_media_id   INTEGER,
            post_id             INTEGER,
            permalink           TEXT,
            seo_status          TEXT
                                CHECK (seo_status IS NULL OR
                                       seo_status IN ('written', 'fallback_meta', 'skipped')),
            cache_purged        INTEGER NOT NULL DEFAULT 0,
            compliance_flags    TEXT,
            claimed_at          TEXT    NOT NULL,
            completed_at        TEXT,
            failure_reason      TEXT,
            retry_count         INTEGER NOT NULL DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_publish_jobs_state
            ON publish_jobs(state, state_updated_at);
        CREATE INDEX IF NOT EXISTS idx_publish_jobs_approval_queue
            ON publish_jobs(approval_queue_id);
        CREATE INDEX IF NOT EXISTS idx_publish_jobs_operation
            ON publish_jobs(operation_id);

        -- ADR-007 §4 Franky monitoring（canonical DDL: migrations/003_franky_tables.sql）
        -- Slice 1 scope: alert_state + health_probe_state.
        -- cron_runs / vps_metrics / r2_backup_checks deferred to Slice 2/3.
        CREATE TABLE IF NOT EXISTS alert_state (
            dedup_key       TEXT PRIMARY KEY,
            rule_id         TEXT NOT NULL,
            last_fired_at   TEXT NOT NULL,
            suppress_until  TEXT NOT NULL,
            state           TEXT NOT NULL CHECK (state IN ('firing', 'resolved')),
            last_message    TEXT NOT NULL,
            fire_count      INTEGER NOT NULL DEFAULT 1
        );

        CREATE INDEX IF NOT EXISTS idx_alert_state_suppress
            ON alert_state(suppress_until);
        CREATE INDEX IF NOT EXISTS idx_alert_state_rule
            ON alert_state(rule_id, last_fired_at DESC);

        CREATE TABLE IF NOT EXISTS health_probe_state (
            target              TEXT PRIMARY KEY,
            consecutive_fails   INTEGER NOT NULL DEFAULT 0,
            last_check_at       TEXT NOT NULL,
            last_status         TEXT NOT NULL CHECK (last_status IN ('ok', 'fail')),
            last_error          TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_health_probe_status
            ON health_probe_state(last_status, last_check_at DESC);

        -- ADR-007 §5 R2 backup verification history.
        -- Canonical DDL lives in migrations/004_r2_backup_checks.sql.
        CREATE TABLE IF NOT EXISTS r2_backup_checks (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            checked_at          TEXT NOT NULL,
            latest_object_key   TEXT,
            latest_object_size  INTEGER,
            latest_object_mtime TEXT,
            status              TEXT NOT NULL
                                CHECK (status IN ('ok', 'stale', 'missing', 'too_small')),
            detail              TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_r2_backup_time
            ON r2_backup_checks(checked_at DESC);

        -- ADR-008 §2 — Phase 2a-min GSC rows store.
        -- Canonical DDL: migrations/005_gsc_rows.sql.
        -- Owned by `shared/gsc_rows_store.py`; written by `agents/franky` GSC daily cron.
        CREATE TABLE IF NOT EXISTS gsc_rows (
            site         TEXT NOT NULL,
            date         TEXT NOT NULL,
            query        TEXT NOT NULL,
            page         TEXT NOT NULL,
            country      TEXT NOT NULL,
            device       TEXT NOT NULL
                         CHECK (device IN ('desktop', 'mobile', 'tablet')),
            clicks       INTEGER NOT NULL,
            impressions  INTEGER NOT NULL,
            ctr          REAL    NOT NULL,
            position     REAL    NOT NULL,
            fetched_at   TEXT    NOT NULL,
            PRIMARY KEY (site, date, query, page, country, device)
        );

        CREATE INDEX IF NOT EXISTS idx_gsc_site_date
            ON gsc_rows(site, date DESC);

        CREATE INDEX IF NOT EXISTS idx_gsc_query
            ON gsc_rows(query);

        -- PRD #226 §"Audit result schema" — SEO 中控台 v1 audit_results table.
        -- Canonical DDL: migrations/006_audit_results.sql.
        -- Owned by `shared/audit_results_store.py`; written by
        -- `agents/brook/audit_runner` after each subprocess audit run.
        CREATE TABLE IF NOT EXISTS audit_results (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            target_site       TEXT,
            wp_post_id        INTEGER,
            url               TEXT NOT NULL,
            focus_keyword     TEXT NOT NULL DEFAULT '',
            audited_at        TEXT NOT NULL,
            overall_grade     TEXT NOT NULL
                              CHECK (overall_grade IN ('A','B+','B','C+','C','D','F')),
            pass_count        INTEGER NOT NULL DEFAULT 0,
            warn_count        INTEGER NOT NULL DEFAULT 0,
            fail_count        INTEGER NOT NULL DEFAULT 0,
            skip_count        INTEGER NOT NULL DEFAULT 0,
            suggestions_json  TEXT NOT NULL DEFAULT '[]',
            raw_markdown      TEXT NOT NULL DEFAULT '',
            review_status     TEXT NOT NULL DEFAULT 'fresh'
                              CHECK (review_status IN
                                     ('fresh','in_review','exported','archived')),
            approval_queue_id INTEGER
        );

        CREATE INDEX IF NOT EXISTS idx_audit_results_post_audited_at
            ON audit_results(target_site, wp_post_id, audited_at DESC);

        CREATE INDEX IF NOT EXISTS idx_audit_results_url
            ON audit_results(url, audited_at DESC);

        CREATE INDEX IF NOT EXISTS idx_audit_results_review_status
            ON audit_results(review_status, audited_at DESC);

        -- PRD #255 §"Schema" — keyword_research_runs table for Slice 2 (#258 / A′).
        -- Canonical DDL: migrations/007_keyword_research_runs.sql.
        -- Owned by `shared/keyword_research_history_store.py`; written by
        -- `thousand_sunny/routers/bridge_zoro.py` after a successful research run.
        CREATE TABLE IF NOT EXISTS keyword_research_runs (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            topic        TEXT NOT NULL,
            en_topic     TEXT,
            content_type TEXT NOT NULL CHECK (content_type IN ('blog', 'youtube')),
            report_md    TEXT NOT NULL,
            created_at   TEXT NOT NULL,
            triggered_by TEXT NOT NULL CHECK (triggered_by IN ('web', 'lifeos'))
        );

        CREATE INDEX IF NOT EXISTS idx_keyword_research_created_at
            ON keyword_research_runs(created_at DESC);

        CREATE INDEX IF NOT EXISTS idx_keyword_research_topic
            ON keyword_research_runs(topic, created_at DESC);

        -- Phase 3 observability: per-job heartbeat (last-success + consecutive failure
        -- counter). Owned by `shared/heartbeat.py`; consumed by `/bridge/health`.
        CREATE TABLE IF NOT EXISTS heartbeats (
            job_name             TEXT PRIMARY KEY,
            last_success_at      TEXT,
            last_run_at          TEXT NOT NULL,
            last_status          TEXT NOT NULL CHECK (last_status IN ('success', 'fail')),
            last_error           TEXT,
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            updated_at           TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_heartbeats_status
            ON heartbeats(last_status, last_run_at DESC);
    """)

    # Migration: api_calls 曾經沒有 cache token 欄位（Phase 4 前）。
    # ALTER TABLE ADD COLUMN 沒有 IF NOT EXISTS 語法，用 try/except 補。
    for col_ddl in (
        "ALTER TABLE api_calls ADD COLUMN cache_read_tokens INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE api_calls ADD COLUMN cache_write_tokens INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE api_calls ADD COLUMN latency_ms INTEGER NOT NULL DEFAULT 0",
    ):
        try:
            conn.execute(col_ddl)
        except sqlite3.OperationalError:
            pass  # 欄位已存在

    # FTS5 虛擬表需要獨立建立（不支援 IF NOT EXISTS 語法）
    try:
        conn.execute("""
            CREATE VIRTUAL TABLE memories_fts USING fts5(
                title, content, tags,
                content='memories',
                content_rowid='id'
            )
        """)
    except sqlite3.OperationalError:
        pass  # 已存在

    # FTS5 同步觸發器
    for trigger_sql in [
        """CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
            INSERT INTO memories_fts(rowid, title, content, tags)
            VALUES (new.id, new.title, new.content, new.tags);
        END""",
        """CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, title, content, tags)
            VALUES ('delete', old.id, old.title, old.content, old.tags);
        END""",
        """CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, title, content, tags)
            VALUES ('delete', old.id, old.title, old.content, old.tags);
            INSERT INTO memories_fts(rowid, title, content, tags)
            VALUES (new.id, new.title, new.content, new.tags);
        END""",
    ]:
        try:
            conn.execute(trigger_sql)
        except sqlite3.OperationalError:
            pass  # 已存在

    conn.commit()


def file_hash(path: Path) -> str:
    """計算檔案的 SHA-256 hash。"""
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def is_file_processed(path: Path, agent: str) -> bool:
    """檢查檔案是否已被指定 agent 處理過（且內容未變）。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT file_hash FROM files_processed WHERE file_path = ? AND agent = ?",
        (str(path), agent),
    ).fetchone()
    if row is None:
        return False
    return row["file_hash"] == file_hash(path)


def mark_file_processed(path: Path, agent: str) -> None:
    """標記檔案已處理。"""
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT OR REPLACE INTO files_processed
           (file_path, file_hash, agent, processed_at, status)
           VALUES (?, ?, ?, ?, 'done')""",
        (str(path), file_hash(path), agent, now),
    )
    conn.commit()


def is_file_read(path: Path) -> bool:
    """檢查檔案是否已被標記為已閱讀。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT 1 FROM files_read WHERE file_path = ?",
        (str(path),),
    ).fetchone()
    return row is not None


def mark_file_read(path: Path) -> None:
    """標記檔案為已閱讀。"""
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO files_read (file_path, read_at) VALUES (?, ?)",
        (str(path), now),
    )
    conn.commit()


def is_seen(source: str, item_id: str) -> bool:
    """檢查某 source 的 item（PMID、tweet id、url hash 等）是否已見過。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT 1 FROM scout_seen WHERE source = ? AND item_id = ?",
        (source, item_id),
    ).fetchone()
    return row is not None


def mark_seen(source: str, item_id: str, url: Optional[str] = None) -> None:
    """標記某 source 的 item 已見過（冪等：重複呼叫安全）。"""
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT OR IGNORE INTO scout_seen (source, item_id, url, first_seen)
           VALUES (?, ?, ?, ?)""",
        (source, item_id, url, now),
    )
    conn.commit()


def start_run(agent: str) -> int:
    """記錄 agent 開始執行，回傳 run_id。"""
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO agent_runs (agent, started_at, status) VALUES (?, ?, 'running')",
        (agent, now),
    )
    conn.commit()
    return cur.lastrowid


def finish_run(run_id: int, *, status: str = "done", summary: str = "") -> None:
    """記錄 agent 執行結束。"""
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE agent_runs SET finished_at = ?, status = ?, summary = ? WHERE id = ?",
        (now, status, summary, run_id),
    )
    conn.commit()


def record_api_call(
    agent: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    run_id: Optional[int] = None,
    *,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    latency_ms: int = 0,
) -> None:
    """記錄一次 LLM API 呼叫的 token 用量 + 延遲。

    ``input_tokens`` / ``output_tokens`` 對應 provider response.usage 的
    input_tokens / output_tokens（Anthropic：thinking tokens 已含在 output）。
    Cache tokens 來自 ``cache_read_input_tokens`` / ``cache_creation_input_tokens``。
    ``latency_ms`` 包含 retry 時間（end-to-end caller 視角的 wall-clock）；0 表示
    呼叫端未測量（既有 callers 升級前），p50/p95/p99 聚合會 filter 掉。
    """
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO api_calls
              (agent, run_id, model, input_tokens, output_tokens,
               cache_read_tokens, cache_write_tokens, latency_ms, called_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            agent,
            run_id,
            model,
            input_tokens,
            output_tokens,
            cache_read_tokens,
            cache_write_tokens,
            latency_ms,
            now,
        ),
    )
    # 同步累加到 agent_runs（只累計 input/output，cache 統計查 api_calls 即可）
    if run_id is not None:
        conn.execute(
            """UPDATE agent_runs
               SET input_tokens  = input_tokens  + ?,
                   output_tokens = output_tokens + ?
               WHERE id = ?""",
            (input_tokens, output_tokens, run_id),
        )
    conn.commit()


def get_cost_summary(agent: Optional[str] = None, days: int = 7) -> list[dict]:
    """回傳最近 N 天的 token 用量統計，依 agent 分組。

    Args:
        agent: 過濾特定 agent，None 表示全部
        days:  統計最近幾天

    Returns:
        列表，每筆含 agent, model, calls, input_tokens, output_tokens
    """
    conn = _get_conn()
    from datetime import timedelta

    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    select = """SELECT agent, model,
                       COUNT(*) as calls,
                       SUM(input_tokens) as input_tokens,
                       SUM(output_tokens) as output_tokens,
                       SUM(cache_read_tokens) as cache_read_tokens,
                       SUM(cache_write_tokens) as cache_write_tokens
                FROM api_calls"""
    if agent:
        rows = conn.execute(
            f"""{select}
                WHERE agent = ? AND called_at >= ?
                GROUP BY agent, model
                ORDER BY calls DESC""",
            (agent, since),
        ).fetchall()
    else:
        rows = conn.execute(
            f"""{select}
                WHERE called_at >= ?
                GROUP BY agent, model
                ORDER BY calls DESC""",
            (since,),
        ).fetchall()

    return [dict(row) for row in rows]


def get_cost_timeseries(
    *,
    agent: Optional[str] = None,
    days: int = 7,
    bucket: str = "day",
) -> list[dict]:
    """回傳時間序列的 token 用量，按 agent × model × bucket 聚合。

    Args:
        agent:  過濾特定 agent，None 表示全部
        days:   統計最近幾天
        bucket: "day" 或 "hour"

    Returns:
        列表，每筆含 bucket (ISO str)、agent、model、tokens 欄位
    """
    if bucket not in ("day", "hour"):
        raise ValueError(f"bucket must be 'day' or 'hour', got {bucket!r}")

    conn = _get_conn()
    from datetime import timedelta

    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    # SQLite substr 切 ISO8601：'2026-04-19T03:15:22...' → day 取前 10 碼、hour 取前 13 碼 + ':00'
    bucket_expr = (
        "substr(called_at, 1, 10)" if bucket == "day" else "substr(called_at, 1, 13) || ':00'"
    )

    params: list = [since]
    where = "called_at >= ?"
    if agent:
        where += " AND agent = ?"
        params.append(agent)

    rows = conn.execute(
        f"""SELECT {bucket_expr} as bucket,
                   agent, model,
                   COUNT(*) as calls,
                   SUM(input_tokens) as input_tokens,
                   SUM(output_tokens) as output_tokens,
                   SUM(cache_read_tokens) as cache_read_tokens,
                   SUM(cache_write_tokens) as cache_write_tokens
            FROM api_calls
            WHERE {where}
            GROUP BY bucket, agent, model
            ORDER BY bucket ASC, agent ASC""",
        params,
    ).fetchall()

    return [dict(row) for row in rows]


def get_latency_summary(agent: Optional[str] = None, days: int = 7) -> list[dict]:
    """回傳最近 N 天 LLM call 的延遲分布，依 agent × model 分組。

    p50/p95/p99 用 Python 算（SQLite 沒 PERCENTILE_CONT）。只看 ``latency_ms > 0``
    的 row（既有資料 default 0 表示未測量，會被略過避免拉低分位）。

    Args:
        agent: 過濾特定 agent，None 表示全部
        days:  統計最近幾天

    Returns:
        列表，每筆含 agent, model, calls, latency_p50_ms, latency_p95_ms,
        latency_p99_ms, latency_max_ms（若該 group 全 0 則不入榜）
    """
    conn = _get_conn()
    from datetime import timedelta

    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    params: list = [since]
    where = "called_at >= ? AND latency_ms > 0"
    if agent:
        where += " AND agent = ?"
        params.append(agent)

    rows = conn.execute(
        f"""SELECT agent, model, latency_ms
            FROM api_calls
            WHERE {where}
            ORDER BY agent, model""",
        params,
    ).fetchall()

    # 在 Python 端做 group + percentile（SQLite 缺 PERCENTILE_CONT）
    from collections import defaultdict

    buckets: dict[tuple[str, str], list[int]] = defaultdict(list)
    for row in rows:
        buckets[(row["agent"], row["model"])].append(row["latency_ms"])

    import math

    def _percentile(sorted_vals: list[int], p: float) -> int:
        if not sorted_vals:
            return 0
        # Nearest-rank: index = ceil(p * n) - 1（標準離散分位定義）
        n = len(sorted_vals)
        k = max(0, min(n - 1, math.ceil(p * n) - 1))
        return sorted_vals[k]

    out: list[dict] = []
    for (agent_name, model), vals in buckets.items():
        vals.sort()
        out.append(
            {
                "agent": agent_name,
                "model": model,
                "calls": len(vals),
                "latency_p50_ms": _percentile(vals, 0.50),
                "latency_p95_ms": _percentile(vals, 0.95),
                "latency_p99_ms": _percentile(vals, 0.99),
                "latency_max_ms": vals[-1],
            }
        )
    out.sort(key=lambda r: r["calls"], reverse=True)
    return out


# ---------------------------------------------------------------------------
# ADR-002 Tier 3: 記憶搜尋層
# ---------------------------------------------------------------------------


def remember(
    agent: str,
    type: str,
    title: str,
    content: str,
    tags: Optional[list[str]] = None,
    confidence: str = "medium",
    source: Optional[str] = None,
    ttl_days: Optional[int] = None,
) -> int:
    """記錄一筆新記憶到 Tier 3（SQLite + FTS5）。

    Args:
        agent:      記錄者（robin, franky, claude, ...）
        type:       記憶類型（semantic, episodic, procedural, user）
        title:      簡短標題
        content:    記憶內容
        tags:       標籤列表
        confidence: 信心度（high, medium, low）
        source:     來源（session_id, run_id, file_path, ...）
        ttl_days:   幾天後過期（None 表示永久）

    Returns:
        新記憶的 id
    """
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()
    expires_at = None
    if ttl_days is not None:
        from datetime import timedelta

        expires_at = (datetime.now(timezone.utc) + timedelta(days=ttl_days)).isoformat()

    cur = conn.execute(
        """INSERT INTO memories
           (agent, type, title, content, tags, confidence, source,
            created_at, updated_at, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            agent,
            type,
            title,
            content,
            json.dumps(tags or [], ensure_ascii=False),
            confidence,
            source,
            now,
            now,
            expires_at,
        ),
    )
    conn.commit()
    return cur.lastrowid


def search_memory(
    query: str,
    agent: Optional[str] = None,
    type: Optional[str] = None,
    limit: int = 5,
) -> list[dict]:
    """FTS5 全文搜尋記憶。

    Args:
        query:  搜尋關鍵字（支援 FTS5 語法：AND, OR, NOT, "phrase"）
        agent:  過濾特定 agent（None 表示全部）
        type:   過濾特定類型（None 表示全部）
        limit:  最多回傳幾筆

    Returns:
        列表，每筆含 id, agent, type, title, content, tags, confidence, created_at
        依相關度排序。
    """
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()

    # 建構 WHERE 子句
    conditions = ["m.id IN (SELECT rowid FROM memories_fts WHERE memories_fts MATCH ?)"]
    params: list = [query]

    # 排除過期記憶
    conditions.append("(m.expires_at IS NULL OR m.expires_at > ?)")
    params.append(now)

    if agent:
        conditions.append("m.agent = ?")
        params.append(agent)
    if type:
        conditions.append("m.type = ?")
        params.append(type)

    where = " AND ".join(conditions)
    params.append(limit)

    rows = conn.execute(
        f"""SELECT m.id, m.agent, m.type, m.title, m.content,
                   m.tags, m.confidence, m.source, m.created_at
            FROM memories m
            WHERE {where}
            ORDER BY m.created_at DESC
            LIMIT ?""",
        params,
    ).fetchall()

    results = []
    for row in rows:
        d = dict(row)
        d["tags"] = json.loads(d["tags"]) if d["tags"] else []
        results.append(d)
    return results


def list_memories(
    agent: Optional[str] = None,
    type: Optional[str] = None,
    limit: int = 20,
) -> list[dict]:
    """列出記憶（不需搜尋關鍵字）。

    Args:
        agent:  過濾特定 agent（None 表示全部）
        type:   過濾特定類型（None 表示全部）
        limit:  最多回傳幾筆

    Returns:
        列表，依建立時間倒序。
    """
    conn = _get_conn()
    now = datetime.now(timezone.utc).isoformat()

    conditions = ["(expires_at IS NULL OR expires_at > ?)"]
    params: list = [now]

    if agent:
        conditions.append("agent = ?")
        params.append(agent)
    if type:
        conditions.append("type = ?")
        params.append(type)

    where = " AND ".join(conditions)
    params.append(limit)

    rows = conn.execute(
        f"""SELECT id, agent, type, title, content, tags, confidence, source, created_at
            FROM memories
            WHERE {where}
            ORDER BY created_at DESC
            LIMIT ?""",
        params,
    ).fetchall()

    results = []
    for row in rows:
        d = dict(row)
        d["tags"] = json.loads(d["tags"]) if d["tags"] else []
        results.append(d)
    return results
