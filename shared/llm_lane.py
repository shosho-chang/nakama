"""ADR-070 D5：訂閱額度用完的狀態機（issue #1321，S2a 後端）。

**唯一真相在 VPS 的 ``state.db``**，只有一列全域狀態（``llm_lane_state``，
``id=1``），``interactive`` / ``batch`` 兩類呼叫各自一份欄位。每次轉換都用
``version`` 欄位做 compare-and-swap（樂觀鎖，衝突就重讀重試，見 :func:`_transition`）。

狀態機（每一類各自，來源：ADR-070 §D5）::

    subscription ──(額度用完)──▶ exhausted
    exhausted ──[interactive] 當日上限還有剩，自動──▶ openrouter_auto
    exhausted ──[batch] 修修在 Bridge 核准，附上限──▶ openrouter_approved
    openrouter_auto / openrouter_approved ──(上限用完)──▶ exhausted（再 DM）
    exhausted / openrouter_* ──(resets_at 到了且探針成功 / 修修按「切回訂閱」)──▶ subscription

只擋用完的那一類 model：``rate_limit_type`` 帶 family 後綴時（如
``seven_day_opus``）只擋該 family；``five_hour`` / ``seven_day`` 這種全域額度
全部擋下（``blocked_family=None``）。``billing_error`` 沒有 ``rate_limit_type``，
同樣全域擋下。

**本模組只給 VPS-side 呼叫端寫入**（gateway / cron / bridge runtime group）；
桌機（``shared.llm_context.get_runtime_group() == "desktop"``）不自己決定，
改用 :func:`get_dispatch_state` 透過 Bridge 的 ``GET /api/llm-lane`` 讀快取
（60 秒），讀不到就沿用最後一次讀到的值；再讀不到才退回「當作訂閱可用」的
保守預設，因為桌機沒有本地的權威狀態可用。
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from shared.log import get_logger

logger = get_logger("nakama.llm_lane")

CALL_CLASSES: frozenset[str] = frozenset({"interactive", "batch"})
STATUSES: frozenset[str] = frozenset(
    {"subscription", "exhausted", "openrouter_auto", "openrouter_approved"}
)

INTERACTIVE_DAILY_CAP_USD = 5.0
BATCH_DEFAULT_APPROVAL_CAP_USD = 20.0

TAIPEI = ZoneInfo("Asia/Taipei")

_MODEL_FAMILIES: tuple[str, ...] = ("opus", "sonnet", "haiku", "fable")

_MAX_CAS_RETRIES = 5

DEFAULT_API_BASE = "http://127.0.0.1:8000"
_REMOTE_CACHE_TTL_S = 60.0


class LaneCasConflict(RuntimeError):
    """CAS 更新連續衝突超過重試上限（極端併發下才會發生，正常操作不會踩到）。"""


def today_taipei() -> str:
    """台北時區的今天日期（interactive 每日上限以此歸零）。"""
    return datetime.now(tz=TAIPEI).date().isoformat()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def model_family(model: str | None) -> str | None:
    """從 Claude 別名或 id 判斷 family（``opus``/``sonnet``/``haiku``/``fable``）。

    辨識不出（``None``、非 Claude model）回 ``None``。
    """
    if not model:
        return None
    lowered = model.lower()
    for fam in _MODEL_FAMILIES:
        if fam in lowered:
            return fam
    return None


def family_from_rate_limit_type(rate_limit_type: str | None) -> str | None:
    """``rate_limit_type`` 帶 family 後綴（如 ``seven_day_opus``）時回傳 family；
    ``five_hour`` / ``seven_day`` 這種全域額度回 ``None``（代表擋全部）。
    """
    if not rate_limit_type:
        return None
    for fam in _MODEL_FAMILIES:
        if rate_limit_type.endswith(f"_{fam}"):
            return fam
    return None


def _check_call_class(call_class: str) -> None:
    if call_class not in CALL_CLASSES:
        raise ValueError(f"call_class 必須是 {sorted(CALL_CLASSES)} 之一，收到 {call_class!r}")


# ── 狀態的資料形狀 ───────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class ClassLaneState:
    status: str
    blocked_family: str | None
    rate_limit_type: str | None
    resets_at: int | None
    spend_usd: float
    spend_period: str | None
    cap_usd: float | None
    switched_at: str | None

    def blocks(self, family: str | None) -> bool:
        """這個 family 的呼叫現在還能不能走訂閱（L1）。"""
        if self.status == "subscription":
            return False
        if self.blocked_family is None:
            return True
        return family is None or family == self.blocked_family


@dataclasses.dataclass(frozen=True)
class LlmLaneState:
    version: int
    updated_at: str
    interactive: ClassLaneState
    batch: ClassLaneState

    def for_class(self, call_class: str) -> ClassLaneState:
        _check_call_class(call_class)
        return self.interactive if call_class == "interactive" else self.batch


def _default_state() -> LlmLaneState:
    """全部走訂閱的保守預設值（不碰 DB）：桌機讀不到 VPS 狀態、也沒有快取時使用。"""
    empty = ClassLaneState(
        status="subscription",
        blocked_family=None,
        rate_limit_type=None,
        resets_at=None,
        spend_usd=0.0,
        spend_period=None,
        cap_usd=None,
        switched_at=None,
    )
    return LlmLaneState(version=0, updated_at=_now_iso(), interactive=empty, batch=empty)


# ── DB IO（VPS-side：直接讀寫本機 state.db）─────────────────────────────


def _lane_conn() -> sqlite3.Connection:
    """狀態表專用的短命連線（autocommit 模式，CAS 交易由呼叫端明確 BEGIN IMMEDIATE）。

    比照 ``shared.state._lease_conn``：不用共用的 ``state._conn``（跨 thread
    共用、隱式交易），CAS 的 read-modify-write 需要自己控制交易邊界。
    """
    from shared import state  # noqa: PLC0415 — lazy：讓測試的 isolated_db fixture 生效

    state._get_conn()  # 確保 schema（llm_lane_state 由 _init_tables 建）已存在
    conn = sqlite3.connect(str(state.get_db_path()), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _row_to_state(row: sqlite3.Row) -> LlmLaneState:
    interactive = ClassLaneState(
        status=row["interactive_status"],
        blocked_family=row["interactive_blocked_family"],
        rate_limit_type=row["interactive_rate_limit_type"],
        resets_at=row["interactive_resets_at"],
        spend_usd=row["interactive_spend_usd"],
        spend_period=row["interactive_spend_day"],
        cap_usd=INTERACTIVE_DAILY_CAP_USD,
        switched_at=row["interactive_switched_at"],
    )
    batch = ClassLaneState(
        status=row["batch_status"],
        blocked_family=row["batch_blocked_family"],
        rate_limit_type=row["batch_rate_limit_type"],
        resets_at=row["batch_resets_at"],
        spend_usd=row["batch_spend_usd"],
        spend_period=None,
        cap_usd=row["batch_cap_usd"],
        switched_at=row["batch_switched_at"],
    )
    return LlmLaneState(
        version=row["version"], updated_at=row["updated_at"], interactive=interactive, batch=batch
    )


def _read_row(conn: sqlite3.Connection) -> sqlite3.Row:
    conn.execute(
        "INSERT OR IGNORE INTO llm_lane_state (id, version, updated_at) VALUES (1, 0, ?)",
        (_now_iso(),),
    )
    row = conn.execute("SELECT * FROM llm_lane_state WHERE id = 1").fetchone()
    return row


def get_state() -> LlmLaneState:
    """本機 ``state.db`` 的權威 lane 狀態（VPS-side：gateway / cron / bridge 都呼叫這個）。"""
    conn = _lane_conn()
    try:
        return _row_to_state(_read_row(conn))
    finally:
        conn.close()


def _cas_update(expected_version: int, updates: dict[str, Any]) -> bool:
    """單次 CAS 嘗試：``version`` 不符就不改任何東西，回 ``False`` 讓呼叫端重讀重試。"""
    conn = _lane_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            assignments = ", ".join(f"{k} = ?" for k in updates)
            params = [*updates.values(), _now_iso(), expected_version]
            cur = conn.execute(
                f"UPDATE llm_lane_state SET {assignments}, version = version + 1, "
                f"updated_at = ? WHERE id = 1 AND version = ?",
                params,
            )
            conn.execute("COMMIT")
            return cur.rowcount == 1
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def _transition(mutate_fn: Any) -> LlmLaneState:
    """讀現狀 → ``mutate_fn(state) -> dict | None`` 算出要改的欄位 → CAS 寫入。

    ``mutate_fn`` 回 ``None`` 代表不用改（no-op，直接回目前狀態）。CAS 因為
    ``version`` 被別的 writer 搶先動過而失敗時，重讀最新狀態、重跑
    ``mutate_fn``（保持 read-modify-write 的正確性），最多試 :data:`_MAX_CAS_RETRIES`
    次；還是衝突就 :class:`LaneCasConflict`（正常操作幾乎不會踩到，只有極端併發）。
    """
    state_ = get_state()
    for _ in range(_MAX_CAS_RETRIES):
        updates = mutate_fn(state_)
        if updates is None:
            return state_
        if _cas_update(state_.version, updates):
            return get_state()
        state_ = get_state()
    raise LaneCasConflict(f"llm_lane CAS 連續 {_MAX_CAS_RETRIES} 次衝突，放棄")


def _fire_alert(severity: str, category: str, message: str, *, dedupe_key: str) -> None:
    from shared.alerts import alert  # noqa: PLC0415

    alert(severity, category, message, dedupe_key=dedupe_key)


# ── 狀態轉換 ────────────────────────────────────────────────────────────


def record_exhausted(
    call_class: str,
    *,
    model: str | None,
    rate_limit_type: str | None,
    resets_at: int | None,
) -> LlmLaneState:
    """訂閱額度用完（``RateLimitEvent.rate_limit_info.status=="rejected"`` 或
    ``AssistantMessage.error`` 是 ``rate_limit`` / ``billing_error``）。

    ``interactive`` 當日上限還有剩 → 直接轉 ``openrouter_auto``（自動改道）；
    上限已經用完 → 停在 ``exhausted``。``batch`` 一律停在 ``exhausted``，
    等重置或修修在 Bridge 核准（:func:`approve_batch`）。
    """
    _check_call_class(call_class)
    family = family_from_rate_limit_type(rate_limit_type)
    day = today_taipei()
    prefix = call_class

    def _mutate(s: LlmLaneState) -> dict[str, Any]:
        cls = s.for_class(call_class)
        updates: dict[str, Any] = {
            f"{prefix}_status": "exhausted",
            f"{prefix}_blocked_family": family,
            f"{prefix}_rate_limit_type": rate_limit_type,
            f"{prefix}_resets_at": resets_at,
        }
        if call_class == "interactive":
            spend = cls.spend_usd if cls.spend_period == day else 0.0
            updates[f"{prefix}_spend_usd"] = spend
            updates[f"{prefix}_spend_day"] = day
            if spend < INTERACTIVE_DAILY_CAP_USD:
                updates[f"{prefix}_status"] = "openrouter_auto"
        return updates

    new_state = _transition(_mutate)
    cls = new_state.for_class(call_class)
    if cls.status == "exhausted":
        logger.warning(
            "llm_lane %s exhausted (model=%s rate_limit_type=%s resets_at=%s)",
            call_class,
            model,
            rate_limit_type,
            resets_at,
        )
        _fire_alert(
            "error",
            "llm_lane",
            f"{call_class} 訂閱額度用完（rate_limit_type={rate_limit_type}、"
            f"resets_at={resets_at}）；OpenRouter 上限也已用完或需要核准",
            dedupe_key=f"llm_lane_exhausted_{call_class}_{family or 'all'}",
        )
    else:
        logger.warning(
            "llm_lane %s auto-switched to openrouter (model=%s rate_limit_type=%s)",
            call_class,
            model,
            rate_limit_type,
        )
        _fire_alert(
            "error",
            "llm_lane",
            f"{call_class} 訂閱額度用完，自動轉 OpenRouter"
            f"（每日上限 US${INTERACTIVE_DAILY_CAP_USD:g}）",
            dedupe_key=f"llm_lane_auto_switch_{call_class}",
        )
    return new_state


def record_openrouter_spend(call_class: str, *, cost_usd: float) -> LlmLaneState:
    """累加 OpenRouter 模式下的實際花費；超過上限就轉回 ``exhausted``（再 DM）。

    ``interactive`` 按台北日累計（換日重新從 0 開始）；``batch`` 按每次核准累計
    （下一次 :func:`approve_batch` 才重新歸零）。
    """
    _check_call_class(call_class)
    day = today_taipei()
    prefix = call_class

    def _mutate(s: LlmLaneState) -> dict[str, Any]:
        cls = s.for_class(call_class)
        if call_class == "interactive":
            base = cls.spend_usd if cls.spend_period == day else 0.0
            spend = base + cost_usd
            cap = INTERACTIVE_DAILY_CAP_USD
            updates: dict[str, Any] = {f"{prefix}_spend_usd": spend, f"{prefix}_spend_day": day}
        else:
            spend = cls.spend_usd + cost_usd
            cap = cls.cap_usd if cls.cap_usd is not None else BATCH_DEFAULT_APPROVAL_CAP_USD
            updates = {f"{prefix}_spend_usd": spend}
        if spend >= cap and cls.status in ("openrouter_auto", "openrouter_approved"):
            updates[f"{prefix}_status"] = "exhausted"
        return updates

    new_state = _transition(_mutate)
    cls = new_state.for_class(call_class)
    if cls.status == "exhausted":
        logger.warning(
            "llm_lane %s openrouter cap exhausted (spend=%.2f)", call_class, cls.spend_usd
        )
        _fire_alert(
            "error",
            "llm_lane",
            f"{call_class} OpenRouter 上限用完（已花 US${cls.spend_usd:.2f}），轉回 exhausted",
            dedupe_key=f"llm_lane_cap_exhausted_{call_class}",
        )
    return new_state


def switch_to_subscription(call_class: str) -> LlmLaneState:
    """切回訂閱（探針成功，或修修在 Bridge / CLI 手動按「等重置」）。"""
    _check_call_class(call_class)
    prefix = call_class

    def _mutate(_s: LlmLaneState) -> dict[str, Any]:
        return {
            f"{prefix}_status": "subscription",
            f"{prefix}_blocked_family": None,
            f"{prefix}_rate_limit_type": None,
            f"{prefix}_resets_at": None,
            f"{prefix}_switched_at": _now_iso(),
        }

    new_state = _transition(_mutate)
    logger.info("llm_lane %s switched back to subscription", call_class)
    _fire_alert(
        "error",
        "llm_lane",
        f"{call_class} 已切回訂閱",
        dedupe_key=f"llm_lane_switch_back_{call_class}",
    )
    return new_state


def approve_batch(*, cap_usd: float = BATCH_DEFAULT_APPROVAL_CAP_USD) -> LlmLaneState:
    """修修在 Bridge / CLI 核准 batch 先走 OpenRouter，附上限（預設 US$20，可改）。

    不發通知：這是修修自己按下去的操作，不需要再 DM 通知他自己。
    """
    if isinstance(cap_usd, bool) or not isinstance(cap_usd, (int, float)) or cap_usd <= 0:
        raise ValueError(f"cap_usd 必須是正數，收到 {cap_usd!r}")

    def _mutate(_s: LlmLaneState) -> dict[str, Any]:
        return {
            "batch_status": "openrouter_approved",
            "batch_cap_usd": float(cap_usd),
            "batch_spend_usd": 0.0,
        }

    new_state = _transition(_mutate)
    logger.info("llm_lane batch approved for openrouter, cap=%.2f", cap_usd)
    return new_state


def set_state(
    call_class: str,
    *,
    status: str,
    blocked_family: str | None = None,
    rate_limit_type: str | None = None,
    resets_at: int | None = None,
    cap_usd: float | None = None,
) -> LlmLaneState:
    """手動覆寫（CLI ``set`` / 回滾用）：直接把某一類設成任意合法狀態。"""
    _check_call_class(call_class)
    if status not in STATUSES:
        raise ValueError(f"status 必須是 {sorted(STATUSES)} 之一，收到 {status!r}")
    prefix = call_class

    def _mutate(_s: LlmLaneState) -> dict[str, Any]:
        updates: dict[str, Any] = {
            f"{prefix}_status": status,
            f"{prefix}_blocked_family": blocked_family,
            f"{prefix}_rate_limit_type": rate_limit_type,
            f"{prefix}_resets_at": resets_at,
        }
        if call_class == "batch" and cap_usd is not None:
            updates["batch_cap_usd"] = float(cap_usd)
        if status == "subscription":
            updates[f"{prefix}_switched_at"] = _now_iso()
        return updates

    new_state = _transition(_mutate)
    logger.info("llm_lane %s manually set to %s", call_class, status)
    return new_state


# ── 桌機讀 VPS 狀態（60 秒快取，讀不到沿用最後一次讀到的值）────────────────


_remote_cache: dict[str, Any] = {"state": None, "fetched_at": 0.0}


def _http_get(url: str, headers: dict[str, str]) -> dict[str, Any]:
    import httpx  # noqa: PLC0415

    resp = httpx.get(url, headers=headers, timeout=10.0)
    resp.raise_for_status()
    return resp.json()


def _cls_from_json(d: dict[str, Any]) -> ClassLaneState:
    return ClassLaneState(
        status=d["status"],
        blocked_family=d.get("blocked_family"),
        rate_limit_type=d.get("rate_limit_type"),
        resets_at=d.get("resets_at"),
        spend_usd=float(d.get("spend_usd", 0.0)),
        spend_period=d.get("spend_period"),
        cap_usd=d.get("cap_usd"),
        switched_at=d.get("switched_at"),
    )


def _state_from_json(data: dict[str, Any]) -> LlmLaneState:
    return LlmLaneState(
        version=int(data["version"]),
        updated_at=data["updated_at"],
        interactive=_cls_from_json(data["interactive"]),
        batch=_cls_from_json(data["batch"]),
    )


def _fetch_remote_state() -> LlmLaneState:
    api_base = os.environ.get("NAKAMA_API_BASE", DEFAULT_API_BASE).rstrip("/")
    api_key = os.environ.get("WEB_SECRET")
    headers = {"X-Robin-Key": api_key} if api_key else {}
    data = _http_get(f"{api_base}/api/llm-lane", headers)
    return _state_from_json(data)


def get_dispatch_state() -> LlmLaneState:
    """``run_text`` dispatch 用的讀取入口：VPS-side 直接讀本機 DB；桌機改讀
    Bridge 的快取（60 秒），讀不到沿用上一次讀到的值，完全沒有快取時保守當作
    「訂閱可用」（:func:`_default_state`），不誤把桌機自己的本機 DB 當成權威來源。
    """
    from shared.llm_context import get_runtime_group  # noqa: PLC0415

    if get_runtime_group() != "desktop":
        return get_state()

    now = time.monotonic()
    cache_age = now - _remote_cache["fetched_at"]
    if _remote_cache["state"] is not None and cache_age < _REMOTE_CACHE_TTL_S:
        return _remote_cache["state"]
    try:
        fetched = _fetch_remote_state()
    except Exception as exc:  # noqa: BLE001 — 讀不到時沿用快取或保守預設，不能讓呼叫端炸
        if _remote_cache["state"] is not None:
            logger.warning("llm_lane remote fetch failed, using last cached state: %s", exc)
            return _remote_cache["state"]
        logger.warning("llm_lane remote fetch failed, no cache yet, assuming subscription: %s", exc)
        return _default_state()
    _remote_cache["state"] = fetched
    _remote_cache["fetched_at"] = now
    return fetched


# ── CLI ─────────────────────────────────────────────────────────────────


def _print_state(s: LlmLaneState) -> None:
    print(json.dumps(dataclasses.asdict(s), ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="llm_lane", description="ADR-070 D5 lane 狀態檢視 / 手動覆寫（VPS-side）"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("show", help="印出目前的 lane 狀態（JSON）")

    p_set = sub.add_parser("set", help="手動覆寫某一類的狀態")
    p_set.add_argument("--call-class", required=True, choices=sorted(CALL_CLASSES))
    p_set.add_argument("--status", required=True, choices=sorted(STATUSES))
    p_set.add_argument("--blocked-family", default=None, choices=(*_MODEL_FAMILIES, None))
    p_set.add_argument("--rate-limit-type", default=None)
    p_set.add_argument("--resets-at", type=int, default=None)
    p_set.add_argument("--cap", type=float, default=None, dest="cap_usd", help="batch 專用")

    p_approve = sub.add_parser(
        "approve-batch", help="核准 batch 先走 OpenRouter，附上限（預設 US$20）"
    )
    p_approve.add_argument(
        "--cap", type=float, default=BATCH_DEFAULT_APPROVAL_CAP_USD, dest="cap_usd"
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "show":
        _print_state(get_state())
        return 0
    if args.cmd == "set":
        s = set_state(
            args.call_class,
            status=args.status,
            blocked_family=args.blocked_family,
            rate_limit_type=args.rate_limit_type,
            resets_at=args.resets_at,
            cap_usd=args.cap_usd,
        )
        _print_state(s)
        return 0
    if args.cmd == "approve-batch":
        s = approve_batch(cap_usd=args.cap_usd)
        _print_state(s)
        return 0
    logging.getLogger(__name__).error("unknown command: %s", args.cmd)
    return 2


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "BATCH_DEFAULT_APPROVAL_CAP_USD",
    "CALL_CLASSES",
    "INTERACTIVE_DAILY_CAP_USD",
    "STATUSES",
    "ClassLaneState",
    "LaneCasConflict",
    "LlmLaneState",
    "approve_batch",
    "family_from_rate_limit_type",
    "get_dispatch_state",
    "get_state",
    "model_family",
    "record_exhausted",
    "record_openrouter_spend",
    "set_state",
    "switch_to_subscription",
    "today_taipei",
]
