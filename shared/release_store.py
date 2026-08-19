"""Release store — deep module over state.db `releases` / `release_targets`
（video-publishing-plan Q3、ADR-055 slice 1）。

Public API（其餘 `_*` 私有；caller 看不到 SQL 與 row 形狀）：

    register_release(episode, cut_id, format, ...)   # UPSERT，回 release_id
    ensure_target(release_id, platform)              # 冪等建 target，回 target_id
    get_release(episode, cut_id)                     # 一筆 release + 其 targets
    list_releases(episode=None)                      # 清單（審核頁/CLI 用）
    update_target(target_id, **fields)               # 狀態機轉移 + 文案回填

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

from datetime import datetime, timezone
from typing import Any, Optional

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
)
_TYPED_TARGET_FIELDS = {
    "thumbnail_status": {"missing", "processing", "set", "failed", "skipped", "unknown"},
    "video_processing_status": {"missing", "processing", "processed", "failed", "unknown"},
    "caption_status": {"missing", "processing", "serving", "failed", "unknown"},
    "platform_privacy_status": {"private", "unlisted", "public"},
}


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
