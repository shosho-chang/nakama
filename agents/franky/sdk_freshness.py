"""Franky 每週兩項檢查 — SDK 部署落後、model 落後（ADR-070 D10 / S7b）。

由 ``agents.franky.health_check.run_once`` 每次 5-min tick 呼叫；本模組自己
決定「這週跑過了嗎」，沒到時間就直接跳過，不重複 git fetch / 打 OpenRouter。

- 檢查 1：VPS 已部署的 `claude-agent-sdk` 版本 是否落後 main 上 `requirements.txt`
  釘的版本。
- 檢查 2：我們實際在用的 model（``api_calls.model_actual``，近 7 天、
  ``lane_actual='subscription'``）是否落後 OpenRouter `/models` 上最新的
  Anthropic model。

兩者都用 ``shared.alerts.alert("error", ...)`` 直接 DM（不是 health_check 的
AlertV1 / alert_sink pipeline）；gate 狀態借用既有 ``health_probe_state`` 表的
``last_check_at`` 欄位（``sdk_deploy_lag`` / ``model_freshness`` 兩個新 target），
不新增表。
"""

from __future__ import annotations

import importlib.metadata
import re
import subprocess
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from datetime import time as dt_time
from pathlib import Path
from time import monotonic
from zoneinfo import ZoneInfo

import httpx
from packaging.version import Version

from agents.franky.health_check import _get_probe_state, _upsert_probe_state
from shared.alerts import alert
from shared.log import get_logger
from shared.schemas.franky import HealthProbeV1, ProbeTarget
from shared.state import _get_conn

logger = get_logger("nakama.franky.sdk_freshness")

_TAIPEI = ZoneInfo("Asia/Taipei")
_REPO_ROOT = Path(__file__).resolve().parents[2]

_SDK_PIN_RE = re.compile(r"^claude-agent-sdk==([0-9.]+)$", re.MULTILINE)

_FAMILIES: tuple[str, ...] = ("opus", "sonnet", "haiku")
_OPENROUTER_ID_RE = re.compile(r"^anthropic/claude-(opus|sonnet|haiku)-(.+)$")
_LOCAL_MODEL_RE = re.compile(r"^claude-(opus|sonnet|haiku)-(.+)$")
_DATE_SEGMENT_RE = re.compile(r"^\d{8}$")
_OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# 每週一次的 gate — 台北時間週日 09:00 之後第一次 tick 才跑
# ---------------------------------------------------------------------------


def _last_sunday_0900_boundary(now_taipei: datetime) -> datetime:
    """回傳「本次應該已經跑過」的那個 Sunday 09:00（台北時間）。

    Python weekday()：Monday=0…Sunday=6。距上一個週日的天數 = (weekday+1) % 7。
    若算出來的當週週日 09:00 還沒到（例如現在是週日 03:00），代表本週的視窗
    還沒開始，退回上週那個視窗。
    """
    days_since_sunday = (now_taipei.weekday() + 1) % 7
    sunday_date = (now_taipei - timedelta(days=days_since_sunday)).date()
    boundary = datetime.combine(sunday_date, dt_time(9, 0), tzinfo=_TAIPEI)
    if boundary > now_taipei:
        boundary -= timedelta(days=7)
    return boundary


def _should_run_weekly(last_check_at: datetime | None, now: datetime) -> bool:
    """尚未跑過（沒有紀錄）或上次跑的時間早於本週視窗邊界 → 該跑了。"""
    if last_check_at is None:
        return True
    now_taipei = now.astimezone(_TAIPEI)
    boundary = _last_sunday_0900_boundary(now_taipei)
    return last_check_at.astimezone(_TAIPEI) < boundary


def _read_last_check_at(target: ProbeTarget) -> datetime | None:
    prev = _get_probe_state(target)
    if prev is None or not prev.get("last_check_at"):
        return None
    return datetime.fromisoformat(prev["last_check_at"])


# ---------------------------------------------------------------------------
# 版本號解析 — 只取前兩段「數字」，日期段落（YYYYMMDD）不算
# ---------------------------------------------------------------------------


def _version_tuple(suffix: str) -> tuple[int, int]:
    segments = re.split(r"[.-]", suffix)
    numeric = [s for s in segments if s.isdigit() and not _DATE_SEGMENT_RE.match(s)]
    first_two = (numeric + ["0", "0"])[:2]
    return (int(first_two[0]), int(first_two[1]))


def _latest_per_family(
    ids: Iterable[str], pattern: re.Pattern[str]
) -> dict[str, tuple[tuple[int, int], str]]:
    """回傳每個 family 版本 tuple 最大的那個 id。"""
    best: dict[str, tuple[tuple[int, int], str]] = {}
    for id_ in ids:
        match = pattern.match(id_)
        if match is None:
            continue
        family, suffix = match.group(1), match.group(2)
        version = _version_tuple(suffix)
        if family not in best or version > best[family][0]:
            best[family] = (version, id_)
    return best


# ---------------------------------------------------------------------------
# 檢查 1 — VPS 部署落後
# ---------------------------------------------------------------------------


def _installed_sdk_version() -> str | None:
    try:
        return importlib.metadata.version("claude-agent-sdk")
    except importlib.metadata.PackageNotFoundError:
        logger.warning("sdk_freshness: claude-agent-sdk not installed locally")
        return None


def _main_pinned_sdk_version(repo_root: Path | None = None) -> str | None:
    repo_root = repo_root or _REPO_ROOT
    try:
        fetch = subprocess.run(
            ["git", "fetch", "origin", "main", "--quiet"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",  # Windows 預設編碼解不開 requirements.txt 的中文註解
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("sdk_freshness: git fetch failed: %s", exc)
        return None
    if fetch.returncode != 0:
        logger.warning(
            "sdk_freshness: git fetch failed rc=%s stderr=%s",
            fetch.returncode,
            fetch.stderr[:200],
        )
        return None

    try:
        show = subprocess.run(
            ["git", "show", "origin/main:requirements.txt"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",  # Windows 預設編碼解不開 requirements.txt 的中文註解
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("sdk_freshness: git show failed: %s", exc)
        return None
    if show.returncode != 0:
        logger.warning("sdk_freshness: git show failed rc=%s", show.returncode)
        return None

    match = _SDK_PIN_RE.search(show.stdout)
    if match is None:
        logger.warning("sdk_freshness: requirements.txt has no claude-agent-sdk pin on main")
        return None
    return match.group(1)


def check_sdk_deploy_lag(now: datetime | None = None) -> HealthProbeV1:
    now = now or _now()
    target: ProbeTarget = "sdk_deploy_lag"

    if not _should_run_weekly(_read_last_check_at(target), now):
        return HealthProbeV1(
            target=target,
            status="ok",
            checked_at=now,
            latency_ms=0,
            detail={"skipped": True, "reason": "not_due"},
        )

    started = monotonic()
    installed = _installed_sdk_version()
    main_version = _main_pinned_sdk_version()
    latency_ms = int((monotonic() - started) * 1000)

    if installed is None or main_version is None:
        probe = HealthProbeV1(
            target=target,
            status="fail",
            checked_at=now,
            latency_ms=latency_ms,
            error="could not determine installed or main-pinned SDK version",
        )
        _upsert_probe_state(probe, consecutive_fails=0)
        return probe

    if Version(main_version) > Version(installed):
        alert(
            "error",
            "franky",
            f"claude-agent-sdk 在 main 已升到 {main_version}，VPS 還是 {installed}。"
            f'請部署：ssh nakama-vps "cd /home/nakama && ./scripts/deploy_vps.sh"',
            dedupe_key=f"sdk-deploy-lag-{main_version}",
        )

    probe = HealthProbeV1(
        target=target,
        status="ok",
        checked_at=now,
        latency_ms=latency_ms,
        detail={"installed": installed, "main": main_version},
    )
    _upsert_probe_state(probe, consecutive_fails=0)
    return probe


# ---------------------------------------------------------------------------
# 檢查 2 — model 落後
# ---------------------------------------------------------------------------


def _fetch_openrouter_model_ids() -> list[str] | None:
    try:
        resp = httpx.get(_OPENROUTER_MODELS_URL, timeout=10.0)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("sdk_freshness: OpenRouter models fetch failed: %s", exc)
        return None
    try:
        data = resp.json()
    except ValueError as exc:
        logger.warning("sdk_freshness: OpenRouter models response not JSON: %s", exc)
        return None
    return [str(item.get("id", "")) for item in data.get("data", [])]


def _actual_model_versions(now: datetime) -> dict[str, tuple[tuple[int, int], str]]:
    conn = _get_conn()
    since = (now - timedelta(days=7)).isoformat()
    rows = conn.execute(
        "SELECT model_actual FROM api_calls "
        "WHERE lane_actual = 'subscription' AND model_actual IS NOT NULL AND called_at >= ?",
        (since,),
    ).fetchall()
    ids = [row["model_actual"] for row in rows]
    return _latest_per_family(ids, _LOCAL_MODEL_RE)


def check_model_freshness(now: datetime | None = None) -> HealthProbeV1:
    now = now or _now()
    target: ProbeTarget = "model_freshness"

    if not _should_run_weekly(_read_last_check_at(target), now):
        return HealthProbeV1(
            target=target,
            status="ok",
            checked_at=now,
            latency_ms=0,
            detail={"skipped": True, "reason": "not_due"},
        )

    started = monotonic()
    ids = _fetch_openrouter_model_ids()
    latency_ms = int((monotonic() - started) * 1000)

    if ids is None:
        probe = HealthProbeV1(
            target=target,
            status="fail",
            checked_at=now,
            latency_ms=latency_ms,
            error="OpenRouter /models fetch failed",
        )
        _upsert_probe_state(probe, consecutive_fails=0)
        return probe

    openrouter_latest = _latest_per_family(ids, _OPENROUTER_ID_RE)
    actual_latest = _actual_model_versions(now)

    behind: dict[str, str] = {}
    for family in _FAMILIES:
        actual_entry = actual_latest.get(family)
        if actual_entry is None:
            continue  # 7 天內沒有資料就跳過該 family
        or_entry = openrouter_latest.get(family)
        if or_entry is None:
            continue
        or_version, or_id = or_entry
        actual_version, actual_id = actual_entry
        if or_version <= actual_version:
            continue
        behind[family] = or_id
        alert(
            "error",
            "franky",
            f"OpenRouter 已有 {or_id}，我們的 {family} 還在跑 {actual_id}。"
            f"通常等 Dependabot 下次升 SDK 就會跟上；"
            f"如果超過兩週還沒跟上，檢查 Dependabot PR 是否卡住。",
            dedupe_key=f"model-behind-{family}-{or_version[0]}.{or_version[1]}",
        )

    probe = HealthProbeV1(
        target=target,
        status="ok",
        checked_at=now,
        latency_ms=latency_ms,
        detail={"behind_count": len(behind), "behind_families": ",".join(sorted(behind))},
    )
    _upsert_probe_state(probe, consecutive_fails=0)
    return probe
