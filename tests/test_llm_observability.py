"""ADR-026 三 col observability schema 測試。

驗證 ``shared.llm_observability.record_call`` 收到 auth_requested /
auth_actual / fallback_reason 後正確 propagate 到 ``state.api_calls``。

DB 隔離：依賴 ``tests/conftest.py`` 的 ``isolated_db`` autouse fixture
（每個 test 走 tmp_path / "test.db"）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from shared import llm_context, llm_observability, state


def _query_latest(db: Path) -> dict:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT auth_requested, auth_actual, fallback_reason, model, agent "
        "FROM api_calls ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return dict(row) if row else {}


def test_record_call_propagates_auth_cols(isolated_db: Path) -> None:
    llm_context.set_current_agent("robin")
    llm_observability.record_call(
        model="claude-sonnet-4-6",
        input_tokens=100,
        output_tokens=50,
        auth_requested="subscription_preferred",
        auth_actual="api",
        fallback_reason="NO_OAUTH_TOKEN",
    )
    state._get_conn().commit()

    row = _query_latest(isolated_db)
    assert row["agent"] == "robin"
    assert row["auth_requested"] == "subscription_preferred"
    assert row["auth_actual"] == "api"
    assert row["fallback_reason"] == "NO_OAUTH_TOKEN"


def test_record_call_auth_cols_default_null(isolated_db: Path) -> None:
    """既有 caller 沒升級到 auth-aware dispatch 時，三 col 應為 NULL。"""
    llm_context.set_current_agent("brook")
    llm_observability.record_call(
        model="claude-opus-4-7",
        input_tokens=200,
        output_tokens=80,
    )
    state._get_conn().commit()

    row = _query_latest(isolated_db)
    assert row["auth_requested"] is None
    assert row["auth_actual"] is None
    assert row["fallback_reason"] is None


def test_record_call_no_fallback(isolated_db: Path) -> None:
    """沒 fallback 時 fallback_reason 為 None 但 requested/actual 一致。"""
    llm_context.set_current_agent("nami")
    llm_observability.record_call(
        model="claude-sonnet-4-6",
        input_tokens=10,
        output_tokens=5,
        auth_requested="subscription_required",
        auth_actual="subscription",
        fallback_reason=None,
    )
    state._get_conn().commit()

    row = _query_latest(isolated_db)
    assert row["auth_requested"] == "subscription_required"
    assert row["auth_actual"] == "subscription"
    assert row["fallback_reason"] is None


def test_pre_migration_db_gets_auth_cols(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """既有 DB（沒 auth_* col）開啟時，_init_tables 的 ALTER 應補上三 col。

    這個 test 自己管 DB（不能用 isolated_db autouse，因為要 pre-create schema）。
    """
    db = tmp_path / "premigration.db"
    pre = sqlite3.connect(str(db))
    pre.executescript(
        """
        CREATE TABLE api_calls (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            agent         TEXT NOT NULL,
            run_id        INTEGER,
            model         TEXT NOT NULL,
            input_tokens  INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL,
            called_at     TEXT NOT NULL
        );
        """
    )
    pre.commit()
    pre.close()

    monkeypatch.setattr(state, "get_db_path", lambda: db)
    if state._conn is not None:
        try:
            state._conn.close()
        except sqlite3.Error:
            pass
    monkeypatch.setattr(state, "_conn", None)

    state._get_conn()

    after = sqlite3.connect(str(db))
    cols = {r[1] for r in after.execute("PRAGMA table_info(api_calls)").fetchall()}
    after.close()
    assert {"auth_requested", "auth_actual", "fallback_reason"} <= cols
