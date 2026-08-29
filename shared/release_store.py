"""Release store — deep module over state.db `releases` / `release_targets`
（video-publishing-plan Q3、ADR-055 slice 1）。

Public API（其餘 `_*` 私有；caller 看不到 SQL 與 row 形狀）：

    register_release(episode, cut_id, format, ...)   # UPSERT，回 release_id
    ensure_target(release_id, platform)              # 冪等建 target，回 target_id
    get_release(episode, cut_id)                     # 一筆 release + 其 targets
    list_releases(episode=None)                      # 清單（審核頁/CLI 用）
    update_target(target_id, **fields)               # 狀態機轉移 + 文案回填
    confirm_target_outcome(...)                      # uploaded + video_id CAS 確認結果

隱藏的設計約束：

1. `releases` UNIQUE (episode, cut_id)：重跑 publish_prep = 更新檔案資訊
   （re-render 後 file_bytes / rendered_at 變），**不**重複建列。
2. `ensure_target` 對既有 target 是 no-op——re-render 不得清掉修修已填的
   文案或已排的 publish_at（上傳中/已上傳的 target 更不能動）。
3. `update_target` 白名單欄位——status/video_id 等執行欄位與 title/description
   等文案欄位都走這裡，但 platform/release_id 不可改（改了就是另一個 target）。
4. 時間全存 UTC ISO8601（與 state.py 其他表一致）。

Schema canonical copy 在 `shared/state.py::_init_tables` + `migrations/018_releases.sql`
+ `migrations/019_youtube_reconciliation.sql`。
Tests：`tests/shared/test_release_store.py`。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional
from urllib.parse import urlsplit

from shared.state import _get_conn

_TARGET_FIELDS = frozenset(
    {
        "status",
        "title",
        "description",
        "thumbnail_path",
        "publish_at",
        "video_id",
        "url",
        "error",
        "upload_session_uri",
        "thumbnail_status",
        "caption_id",
        "video_processing_status",
        "platform_privacy_status",
        "platform_publish_at",
        "caption_status",
        "reconciliation_error",
        "last_reconciled_at",
        "adapter",
        "idempotency_key",
        "checkpoint_json",
        "ineligibility_reason",
    }
)

VALID_STATUS = (
    "draft",
    "approved",
    "uploading",
    "uploaded",
    "published",
    "failed",
    "needs_restart",
    "ineligible",
)
_TYPED_TARGET_FIELDS = {
    "thumbnail_status": {"missing", "processing", "set", "failed", "skipped", "unknown"},
    "video_processing_status": {"missing", "processing", "processed", "failed", "unknown"},
    "caption_status": {"missing", "processing", "serving", "failed", "unknown"},
    "platform_privacy_status": {"private", "unlisted", "public"},
}
_CAMPAIGN_ANCHOR_EDITABLE_STATUSES = frozenset({"draft", "approved", "failed"})
TARGET_CLAIM_STALE_AFTER = timedelta(minutes=15)
_CLAIM_LOCK = threading.Lock()


@dataclass(frozen=True, slots=True)
class ReleaseCampaignAnchor:
    """Observable shared-anchor state for one Release."""

    state: Literal["none", "shared", "divergent"]
    anchor_at: datetime | None
    target_anchors: tuple[tuple[str, str | None], ...]

    @property
    def expected_token(self) -> str:
        """Opaque compare-and-set token for the exact observed target-anchor group."""

        payload = {
            "state": self.state,
            "targets": [
                [platform, _canonical_anchor_token_value(raw_anchor)]
                for platform, raw_anchor in self.target_anchors
            ],
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return "release-anchor-v1:" + hashlib.sha256(encoded).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def register_release(
    episode: str,
    cut_id: str,
    format: str,
    file_path: str,
    *,
    work_title: str = "",
    file_bytes: int = 0,
    duration_sec: float = 0.0,
) -> int:
    """登錄（或更新）一支已匯出的 Cut。回 release_id。"""
    if format not in ("long", "short"):
        raise ValueError(f"format 必須是 long/short，收到 {format!r}")
    conn = _get_conn()
    now = _now()
    with conn:
        conn.execute(
            """
            INSERT INTO releases
                (episode, cut_id, format, work_title, file_path, file_bytes,
                 duration_sec, rendered_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (episode, cut_id) DO UPDATE SET
                format = excluded.format,
                work_title = excluded.work_title,
                file_path = excluded.file_path,
                file_bytes = excluded.file_bytes,
                duration_sec = excluded.duration_sec,
                rendered_at = excluded.rendered_at
            """,
            (episode, cut_id, format, work_title, file_path, file_bytes, duration_sec, now, now),
        )
    row = conn.execute(
        "SELECT id FROM releases WHERE episode = ? AND cut_id = ?", (episode, cut_id)
    ).fetchone()
    return int(row["id"])


def ensure_target(release_id: int, platform: str = "youtube") -> int:
    """冪等建立 release target（草稿）。既有 target 完全不動（文案/狀態保留）。"""
    conn = _get_conn()
    with conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO release_targets (release_id, platform, updated_at)
            VALUES (?, ?, ?)
            """,
            (release_id, platform, _now()),
        )
    row = conn.execute(
        "SELECT id FROM release_targets WHERE release_id = ? AND platform = ?",
        (release_id, platform),
    ).fetchone()
    return int(row["id"])


def get_release(episode: str, cut_id: str) -> Optional[dict[str, Any]]:
    """一筆 release + 其全部 targets；不存在回 None。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM releases WHERE episode = ? AND cut_id = ?", (episode, cut_id)
    ).fetchone()
    if row is None:
        return None
    rel = dict(row)
    rel["targets"] = [
        dict(t)
        for t in conn.execute(
            "SELECT * FROM release_targets WHERE release_id = ? ORDER BY platform", (row["id"],)
        ).fetchall()
    ]
    return rel


def list_releases(episode: Optional[str] = None) -> list[dict[str, Any]]:
    """release 清單（含每支 targets 的 platform:status 摘要）。"""
    conn = _get_conn()
    if episode:
        rows = conn.execute(
            "SELECT * FROM releases WHERE episode = ? ORDER BY cut_id", (episode,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM releases ORDER BY episode, cut_id").fetchall()
    out = []
    for r in rows:
        rel = dict(r)
        rel["target_status"] = {
            t["platform"]: t["status"]
            for t in conn.execute(
                "SELECT platform, status FROM release_targets WHERE release_id = ?", (r["id"],)
            ).fetchall()
        }
        out.append(rel)
    return out


def _parse_aware_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _canonical_anchor_token_value(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = _parse_aware_utc(value)
    return "utc:" + parsed.isoformat() if parsed is not None else "raw:" + value


def _campaign_anchor_snapshot(targets: list[dict[str, Any]]) -> ReleaseCampaignAnchor:
    target_anchors = tuple(
        (str(target["platform"]), target.get("publish_at")) for target in targets
    )
    raw_values = [target.get("publish_at") for target in targets]
    if raw_values and all(value is None for value in raw_values):
        return ReleaseCampaignAnchor("none", None, target_anchors)
    parsed = [_parse_aware_utc(value) for value in raw_values]
    if parsed and all(value is not None and value == parsed[0] for value in parsed):
        return ReleaseCampaignAnchor("shared", parsed[0], target_anchors)
    return ReleaseCampaignAnchor("divergent", None, target_anchors)


def get_release_campaign_anchor(episode: str, cut_id: str) -> ReleaseCampaignAnchor:
    """Read one Release's shared Campaign Anchor without selecting divergent values."""

    release = get_release(episode, cut_id)
    if release is None:
        raise ValueError(f"release 不存在: {episode}/{cut_id}")
    targets = release["targets"]
    if not targets:
        raise ValueError(f"release 沒有 targets: {episode}/{cut_id}")
    return _campaign_anchor_snapshot(targets)


def set_release_campaign_anchor(
    episode: str,
    cut_id: str,
    campaign_anchor_at: datetime | None,
    *,
    expected_anchor_token: str,
) -> ReleaseCampaignAnchor:
    """Compare-and-set one shared Campaign Anchor for every Release Target."""

    if campaign_anchor_at is not None and (
        campaign_anchor_at.tzinfo is None or campaign_anchor_at.utcoffset() is None
    ):
        raise ValueError("campaign anchor must be timezone-aware")
    normalized = (
        campaign_anchor_at.astimezone(timezone.utc).isoformat()
        if campaign_anchor_at is not None
        else None
    )
    conn = _get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        release = conn.execute(
            "SELECT id FROM releases WHERE episode = ? AND cut_id = ?", (episode, cut_id)
        ).fetchone()
        if release is None:
            raise ValueError(f"release 不存在: {episode}/{cut_id}")
        targets = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM release_targets WHERE release_id = ? ORDER BY platform",
                (release["id"],),
            ).fetchall()
        ]
        if not targets:
            raise ValueError(f"release 沒有 targets: {episode}/{cut_id}")
        current = _campaign_anchor_snapshot(targets)
        if current.expected_token != expected_anchor_token:
            raise ValueError("stale Campaign Anchor; reload before scheduling")
        blocked = [
            target["platform"]
            for target in targets
            if target["status"] not in _CAMPAIGN_ANCHOR_EDITABLE_STATUSES
        ]
        if blocked:
            raise ValueError("campaign anchor 已鎖定: " + ", ".join(blocked))
        cursor = conn.execute(
            """
            UPDATE release_targets
            SET publish_at = ?, updated_at = ?
            WHERE release_id = ? AND status IN (?, ?, ?)
            """,
            (
                normalized,
                _now(),
                release["id"],
                *sorted(_CAMPAIGN_ANCHOR_EDITABLE_STATUSES),
            ),
        )
        if cursor.rowcount != len(targets):
            raise ValueError("campaign anchor 更新期間 Release Targets 已變更")
        updated_targets = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM release_targets WHERE release_id = ? ORDER BY platform",
                (release["id"],),
            ).fetchall()
        ]
        result = _campaign_anchor_snapshot(updated_targets)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise


def update_target(target_id: int, **fields: Any) -> None:
    """更新 target 欄位（白名單）。status 值域檢查；不存在的 target fail loud。"""
    bad = set(fields) - _TARGET_FIELDS
    if bad:
        raise ValueError(f"不可更新的欄位: {sorted(bad)}")
    if "status" in fields and fields["status"] not in VALID_STATUS:
        raise ValueError(f"status 必須是 {VALID_STATUS}，收到 {fields['status']!r}")
    for field, allowed in _TYPED_TARGET_FIELDS.items():
        value = fields.get(field)
        if value is not None and value not in allowed:
            raise ValueError(f"{field} 必須是 {sorted(allowed)} 或 None，收到 {value!r}")
    if not fields:
        return
    conn = _get_conn()
    sets = ", ".join(f"{k} = ?" for k in fields)
    with conn:
        cur = conn.execute(
            f"UPDATE release_targets SET {sets}, updated_at = ? WHERE id = ?",
            (*fields.values(), _now(), target_id),
        )
    if cur.rowcount == 0:
        raise ValueError(f"release_target id={target_id} 不存在")


def confirm_target_outcome(
    target_id: int,
    *,
    expected_video_id: str,
    expected_updated_at: str,
    status: Literal["published", "failed"],
    url: str | None = None,
    error: str | None = None,
) -> bool:
    """Confirm one native outcome while the scanned target identity still wins."""

    video_id = expected_video_id.strip()
    if not video_id:
        raise ValueError("expected_video_id must be non-empty")
    if status not in {"published", "failed"}:
        raise ValueError("outcome status must be published or failed")
    observed_at = expected_updated_at.strip()
    if not observed_at:
        raise ValueError("expected_updated_at must be non-empty")
    if url is not None:
        if "\\" in url or any(ord(character) < 32 or ord(character) == 127 for character in url):
            raise ValueError("outcome URL contains unsafe characters")
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("outcome URL must be absolute HTTP(S)")
    # ``shared.state`` deliberately exposes one process-global connection for
    # legacy callers.  A connection transaction is not thread-owned, however:
    # concurrent ``with conn`` blocks can commit each other's transaction.  The
    # reconciler CAS therefore gets a short-lived connection so SQLite, rather
    # than a Python context-manager race, serialises competing writers.
    state_conn = _get_conn()
    database_row = state_conn.execute("PRAGMA database_list").fetchone()
    database_path = str(database_row[2]) if database_row is not None else ""
    if not database_path or database_path == ":memory:":
        raise RuntimeError("outcome CAS requires a file-backed state database")
    conn = sqlite3.connect(database_path, timeout=30.0)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        if str(journal_mode).lower() != "wal":
            raise RuntimeError("outcome CAS requires the canonical WAL state database")
        cursor = conn.execute(
            """
            UPDATE release_targets
            SET status = ?, url = COALESCE(?, url), error = ?, updated_at = ?
            WHERE id = ? AND status = 'uploaded' AND video_id = ? AND updated_at = ?
              AND NOT EXISTS (
                SELECT 1
                FROM release_targets AS sibling
                WHERE sibling.id != release_targets.id
                  AND sibling.platform = release_targets.platform
                  AND sibling.video_id = release_targets.video_id
              )
            """,
            (status, url, error, _now(), target_id, video_id, observed_at),
        )
        conn.commit()
        return cursor.rowcount == 1
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def claim_target(
    target_id: int,
    *,
    now: datetime | None = None,
    stale_after: timedelta = TARGET_CLAIM_STALE_AFTER,
    expected_publish_at: str | None = None,
) -> dict[str, Any] | None:
    """Atomically claim one approved or stale-uploading Release Target.

    ``updated_at`` is the lease heartbeat.  Checkpoint writes refresh it via
    :func:`update_target`, so an in-flight adapter is reclaimable only after
    ``TARGET_CLAIM_STALE_AFTER`` without adding a second lease model.  When
    supplied, ``expected_publish_at`` adds an exact compare-and-set guard for
    callers whose eligibility depends on the scheduled instant.
    """

    claimed_at = now or datetime.now(timezone.utc)
    if claimed_at.tzinfo is None or claimed_at.utcoffset() is None:
        raise ValueError("claim now must be timezone-aware")
    if stale_after <= timedelta(0):
        raise ValueError("stale_after must be positive")
    claimed_at = claimed_at.astimezone(timezone.utc)
    stale_cutoff = claimed_at - stale_after
    publish_at_guard = " AND publish_at = ?" if expected_publish_at is not None else ""
    params: list[Any] = [claimed_at.isoformat(), target_id, stale_cutoff.isoformat()]
    if expected_publish_at is not None:
        params.append(expected_publish_at)
    conn = _get_conn()
    with _CLAIM_LOCK:
        row = conn.execute(
            f"""
            UPDATE release_targets
            SET status = 'uploading', error = NULL, updated_at = ?
            WHERE id = ?
              AND (
                status = 'approved'
                OR (status = 'uploading' AND updated_at <= ?)
              )
              {publish_at_guard}
            RETURNING *
            """,
            params,
        ).fetchone()
        conn.commit()
    return dict(row) if row is not None else None
