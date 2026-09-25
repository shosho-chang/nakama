"""ADR-070 S2a：``shared.llm_lane`` 狀態機的行為契約（issue #1321 驗收清單）。

涵蓋：注入假 RateLimitEvent 訊號走完整條狀態機（interactive / batch 分開）、
上限累計／台北日歸零、CAS 衝突、``SubscriptionExhausted`` 不可重試（沿用
``shared.agent_sdk`` 既有的例外類別，本檔只驗證 llm_lane 這邊的狀態轉換）。

**不打網路、不呼叫 LLM**：全部直接呼叫 ``shared.llm_lane`` 的函式並檢查
``state.db`` 的 ``llm_lane_state`` 表；alert 一律 monkeypatch 成記錄用的假函式。
"""

from __future__ import annotations

import sqlite3

import pytest

from shared import llm_lane, state


@pytest.fixture(autouse=True)
def _no_slack(monkeypatch):
    """所有測試都攔掉真的 alert dispatch（不打 Slack）。"""
    fired: list[tuple] = []
    monkeypatch.setattr(llm_lane, "_fire_alert", lambda *a, **k: fired.append((a, k)))
    return fired


# ── 基本讀寫 / 預設值 ────────────────────────────────────────────────────


def test_get_state_defaults_to_subscription():
    s = llm_lane.get_state()
    assert s.interactive.status == "subscription"
    assert s.batch.status == "subscription"
    assert s.interactive.blocked_family is None
    assert s.version == 0


# ── 狀態機：subscription → exhausted → openrouter_* → subscription ──────


def test_interactive_rate_limit_rejected_auto_switches_to_openrouter():
    """interactive 額度用完，當日上限還有剩 → 自動轉 openrouter_auto。"""
    s = llm_lane.record_exhausted(
        "interactive", model="sonnet", rate_limit_type="five_hour", resets_at=1_800_000_000
    )
    assert s.interactive.status == "openrouter_auto"
    assert s.interactive.blocked_family is None  # five_hour 是全域額度，擋全部
    assert s.interactive.rate_limit_type == "five_hour"
    assert s.interactive.resets_at == 1_800_000_000
    assert s.batch.status == "subscription"  # 兩類分開


def test_batch_rate_limit_rejected_stays_exhausted_until_approved():
    """batch 額度用完 → 停在 exhausted，等重置或修修核准，不自動轉。"""
    s = llm_lane.record_exhausted(
        "batch", model="opus", rate_limit_type="seven_day_opus", resets_at=1_800_000_000
    )
    assert s.batch.status == "exhausted"
    assert s.batch.blocked_family == "opus"


def test_only_blocked_family_is_blocked():
    """seven_day_opus 只擋 opus；sonnet/haiku 不受影響。"""
    s = llm_lane.record_exhausted(
        "batch", model="opus", rate_limit_type="seven_day_opus", resets_at=1_800_000_000
    )
    cls = s.batch
    assert cls.blocks("opus") is True
    assert cls.blocks("sonnet") is False
    assert cls.blocks("haiku") is False


def test_global_rate_limit_blocks_all_families():
    s = llm_lane.record_exhausted(
        "batch", model="sonnet", rate_limit_type="five_hour", resets_at=1_800_000_000
    )
    cls = s.batch
    assert cls.blocks("opus") is True
    assert cls.blocks("sonnet") is True
    assert cls.blocks(None) is True


def test_billing_error_blocks_globally_with_no_rate_limit_type():
    s = llm_lane.record_exhausted(
        "interactive", model="sonnet", rate_limit_type=None, resets_at=None
    )
    # 沒有 rate_limit_type 的 billing_error：全域擋下（family=None）
    assert s.interactive.blocked_family is None
    assert s.interactive.status in ("exhausted", "openrouter_auto")


def test_switch_to_subscription_clears_blocked_fields():
    llm_lane.record_exhausted(
        "interactive", model="sonnet", rate_limit_type="five_hour", resets_at=1
    )
    s = llm_lane.switch_to_subscription("interactive")
    assert s.interactive.status == "subscription"
    assert s.interactive.blocked_family is None
    assert s.interactive.rate_limit_type is None
    assert s.interactive.switched_at is not None


def test_approve_batch_sets_openrouter_approved_with_cap():
    llm_lane.record_exhausted("batch", model="opus", rate_limit_type="seven_day_opus", resets_at=1)
    s = llm_lane.approve_batch(cap_usd=20.0)
    assert s.batch.status == "openrouter_approved"
    assert s.batch.cap_usd == 20.0
    assert s.batch.spend_usd == 0.0


# ── 上限累計 ──────────────────────────────────────────────────────────


def test_interactive_spend_accumulates_and_trips_cap():
    llm_lane.record_exhausted(
        "interactive", model="sonnet", rate_limit_type="five_hour", resets_at=1
    )
    s = llm_lane.get_state()
    assert s.interactive.status == "openrouter_auto"  # 剛用完，上限還沒花

    s = llm_lane.record_openrouter_spend("interactive", cost_usd=3.0)
    assert s.interactive.status == "openrouter_auto"
    assert s.interactive.spend_usd == pytest.approx(3.0)

    s = llm_lane.record_openrouter_spend("interactive", cost_usd=2.5)
    # 3.0 + 2.5 = 5.5 > 5.0 上限 → 轉回 exhausted
    assert s.interactive.status == "exhausted"
    assert s.interactive.spend_usd == pytest.approx(5.5)


def test_batch_spend_accumulates_against_approved_cap():
    llm_lane.record_exhausted("batch", model="opus", rate_limit_type="seven_day_opus", resets_at=1)
    llm_lane.approve_batch(cap_usd=10.0)
    s = llm_lane.record_openrouter_spend("batch", cost_usd=6.0)
    assert s.batch.status == "openrouter_approved"
    s = llm_lane.record_openrouter_spend("batch", cost_usd=5.0)
    assert s.batch.status == "exhausted"  # 6+5=11 > 10


def test_interactive_spend_resets_on_new_taipei_day(monkeypatch):
    llm_lane.record_exhausted(
        "interactive", model="sonnet", rate_limit_type="five_hour", resets_at=1
    )
    llm_lane.record_openrouter_spend("interactive", cost_usd=4.5)
    s = llm_lane.get_state()
    assert s.interactive.spend_usd == pytest.approx(4.5)

    monkeypatch.setattr(llm_lane, "today_taipei", lambda: "2099-01-01")
    # 換日後同一個呼叫類別再次用完額度 → 花費重新累計
    s = llm_lane.record_exhausted(
        "interactive", model="sonnet", rate_limit_type="five_hour", resets_at=2
    )
    assert s.interactive.status == "openrouter_auto"
    s = llm_lane.record_openrouter_spend("interactive", cost_usd=1.0)
    assert s.interactive.spend_usd == pytest.approx(1.0)  # 不是 5.5


# ── CAS 衝突 ──────────────────────────────────────────────────────────


def test_cas_conflict_retries_and_succeeds():
    """模擬另一個 process 在讀寫之間搶先改了 version：CAS 失敗要重讀重試，不能遺失更新。"""
    s0 = llm_lane.get_state()
    assert s0.version == 0

    calls = {"n": 0}
    real_cas = llm_lane._cas_update

    def _flaky_cas(expected_version, updates):
        calls["n"] += 1
        if calls["n"] == 1:
            # 第一次嘗試前，偷偷讓另一個 writer 搶先把 version 推進去
            conn = sqlite3.connect(str(state.get_db_path()))
            conn.execute("UPDATE llm_lane_state SET version = version + 1 WHERE id = 1")
            conn.commit()
            conn.close()
        return real_cas(expected_version, updates)

    llm_lane._cas_update = _flaky_cas
    try:
        s1 = llm_lane.record_exhausted(
            "interactive", model="sonnet", rate_limit_type="five_hour", resets_at=1
        )
    finally:
        llm_lane._cas_update = real_cas

    assert calls["n"] >= 2  # 第一次因為 version 不符而失敗，重讀後重試才成功
    assert s1.interactive.status == "openrouter_auto"
    assert s1.version == 2  # 對手那次 +1，這次 transition 再 +1


def test_cas_gives_up_after_max_retries_raises():
    real_cas = llm_lane._cas_update
    llm_lane._cas_update = lambda expected_version, updates: False  # 永遠失敗
    try:
        with pytest.raises(llm_lane.LaneCasConflict):
            llm_lane.record_exhausted(
                "interactive", model="sonnet", rate_limit_type="five_hour", resets_at=1
            )
    finally:
        llm_lane._cas_update = real_cas


# ── model_family / family_from_rate_limit_type ──────────────────────────


@pytest.mark.parametrize(
    "model,expected",
    [
        ("opus", "opus"),
        ("sonnet", "sonnet"),
        ("haiku", "haiku"),
        ("fable", "fable"),
        ("claude-opus-4-7", "opus"),
        ("claude-sonnet-4-6", "sonnet"),
        ("claude-haiku-4-5-20251001", "haiku"),
        (None, None),
        ("gpt-5", None),
    ],
)
def test_model_family(model, expected):
    assert llm_lane.model_family(model) == expected


@pytest.mark.parametrize(
    "rate_limit_type,expected",
    [
        ("seven_day_opus", "opus"),
        ("five_hour", None),
        ("seven_day", None),
        (None, None),
    ],
)
def test_family_from_rate_limit_type(rate_limit_type, expected):
    assert llm_lane.family_from_rate_limit_type(rate_limit_type) == expected


# ── 手動覆寫 ──────────────────────────────────────────────────────────


def test_set_state_manual_override():
    llm_lane.record_exhausted("batch", model="opus", rate_limit_type="seven_day_opus", resets_at=1)
    s = llm_lane.set_state("batch", status="subscription")
    assert s.batch.status == "subscription"
    assert s.batch.blocked_family is None


def test_set_state_rejects_unknown_status():
    with pytest.raises(ValueError):
        llm_lane.set_state("batch", status="bogus")


def test_set_state_rejects_unknown_call_class():
    with pytest.raises(ValueError):
        llm_lane.set_state("urgent", status="subscription")


# ── alert 通知 ────────────────────────────────────────────────────────


def test_exhausted_transition_fires_alert(_no_slack):
    llm_lane.record_exhausted("batch", model="opus", rate_limit_type="seven_day_opus", resets_at=1)
    assert len(_no_slack) == 1
    args, kwargs = _no_slack[0]
    assert kwargs.get("dedupe_key") or any("llm_lane" in str(a) for a in args)


def test_cap_exhausted_fires_alert(_no_slack):
    llm_lane.record_exhausted(
        "interactive", model="sonnet", rate_limit_type="five_hour", resets_at=1
    )
    _no_slack.clear()
    llm_lane.record_openrouter_spend("interactive", cost_usd=10.0)  # 超過 $5 上限
    assert len(_no_slack) == 1


# ── get_dispatch_state：桌機讀 Bridge，快取 + fallback ──────────────────


@pytest.fixture(autouse=True)
def _reset_remote_cache():
    llm_lane._remote_cache["state"] = None
    llm_lane._remote_cache["fetched_at"] = 0.0
    yield
    llm_lane._remote_cache["state"] = None
    llm_lane._remote_cache["fetched_at"] = 0.0


def _as_desktop(monkeypatch):
    monkeypatch.setattr(llm_lane, "_is_lane_authority", lambda: False)


def _as_authority(monkeypatch):
    monkeypatch.setattr(llm_lane, "_is_lane_authority", lambda: True)


def test_dispatch_state_authority_reads_local(monkeypatch):
    """權威機器（VPS）：get_dispatch_state 就是本機 get_state，不打網路。"""
    _as_authority(monkeypatch)
    llm_lane.record_exhausted("batch", model="opus", rate_limit_type="seven_day_opus", resets_at=1)

    def _boom(*a, **k):
        raise AssertionError("不該打網路")

    monkeypatch.setattr(llm_lane, "_http_get", _boom)
    s = llm_lane.get_dispatch_state()
    assert s.batch.status == "exhausted"


def test_dispatch_state_desktop_fetches_and_caches(monkeypatch):
    _as_desktop(monkeypatch)
    monkeypatch.setenv("NAKAMA_VPS_API_KEY", "key")
    calls = {"n": 0}

    def _fake_get(url, headers):
        calls["n"] += 1
        return {
            "version": 3,
            "updated_at": "2026-09-25T00:00:00+00:00",
            "interactive": {"status": "openrouter_auto", "spend_usd": 1.0},
            "batch": {"status": "subscription", "spend_usd": 0.0},
        }

    monkeypatch.setattr(llm_lane, "_http_get", _fake_get)
    s1 = llm_lane.get_dispatch_state()
    s2 = llm_lane.get_dispatch_state()
    assert calls["n"] == 1  # 第二次命中 60 秒快取，不再打網路
    assert s1.interactive.status == "openrouter_auto"
    assert s2 is s1


def test_dispatch_state_desktop_falls_back_to_last_cached_value(monkeypatch):
    _as_desktop(monkeypatch)
    monkeypatch.setenv("NAKAMA_VPS_API_KEY", "key")
    good = {
        "version": 1,
        "updated_at": "x",
        "interactive": {"status": "openrouter_auto", "spend_usd": 0.0},
        "batch": {"status": "subscription", "spend_usd": 0.0},
    }
    calls = {"n": 0}

    def _flaky_get(url, headers):
        calls["n"] += 1
        if calls["n"] == 1:
            return good
        raise ConnectionError("network down")

    monkeypatch.setattr(llm_lane, "_http_get", _flaky_get)
    s1 = llm_lane.get_dispatch_state()
    llm_lane._remote_cache["fetched_at"] = 0.0  # 強制過期，逼下一次重新打網路
    s2 = llm_lane.get_dispatch_state()
    assert s1.interactive.status == "openrouter_auto"
    assert s2.interactive.status == "openrouter_auto"  # 網路失敗，沿用上一次讀到的值


def test_dispatch_state_desktop_no_cache_and_network_down_assumes_subscription(monkeypatch):
    _as_desktop(monkeypatch)
    monkeypatch.setenv("NAKAMA_VPS_API_KEY", "key")

    def _always_fail(url, headers):
        raise ConnectionError("network down")

    monkeypatch.setattr(llm_lane, "_http_get", _always_fail)
    s = llm_lane.get_dispatch_state()
    assert s.interactive.status == "subscription"
    assert s.batch.status == "subscription"


def test_dispatch_state_desktop_sends_api_key_header(monkeypatch):
    _as_desktop(monkeypatch)
    monkeypatch.setenv("NAKAMA_VPS_API_KEY", "s3cr3t")
    monkeypatch.setenv("NAKAMA_VPS_API_BASE", "https://nakama.example")
    seen = {}

    def _fake_get(url, headers):
        seen["url"] = url
        seen["headers"] = headers
        return {
            "version": 0,
            "updated_at": "x",
            "interactive": {"status": "subscription", "spend_usd": 0.0},
            "batch": {"status": "subscription", "spend_usd": 0.0},
        }

    monkeypatch.setattr(llm_lane, "_http_get", _fake_get)
    llm_lane.get_dispatch_state()
    assert seen["url"] == "https://nakama.example/api/llm-lane"
    assert seen["headers"] == {"X-Robin-Key": "s3cr3t"}


def test_fetch_remote_state_defaults_to_https_vps_base(monkeypatch):
    monkeypatch.delenv("NAKAMA_VPS_API_BASE", raising=False)
    monkeypatch.setenv("NAKAMA_VPS_API_KEY", "key")
    seen = {}

    def _fake_get(url, headers):
        seen["url"] = url
        return {
            "version": 0,
            "updated_at": "x",
            "interactive": {"status": "subscription", "spend_usd": 0.0},
            "batch": {"status": "subscription", "spend_usd": 0.0},
        }

    monkeypatch.setattr(llm_lane, "_http_get", _fake_get)
    llm_lane._fetch_remote_state()
    assert seen["url"] == "https://nakama.shosho.tw/api/llm-lane"


def test_fetch_remote_state_without_key_raises(monkeypatch):
    monkeypatch.delenv("NAKAMA_VPS_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="NAKAMA_VPS_API_KEY"):
        llm_lane._fetch_remote_state()


def test_dispatch_state_desktop_without_key_falls_back_to_cache(monkeypatch):
    """沒設 ``NAKAMA_VPS_API_KEY``：走 fallback（有快取用快取、沒快取當作訂閱可用），
    不會讓呼叫端炸掉。"""
    _as_desktop(monkeypatch)
    monkeypatch.delenv("NAKAMA_VPS_API_KEY", raising=False)

    s = llm_lane.get_dispatch_state()
    assert s.interactive.status == "subscription"
    assert s.batch.status == "subscription"


# ── 非權威機器呼叫寫入函式 → raise ──────────────────────────────────────


def test_non_authority_record_exhausted_raises(monkeypatch):
    _as_desktop(monkeypatch)
    with pytest.raises(llm_lane.LaneAuthorityError):
        llm_lane.record_exhausted(
            "batch", model="opus", rate_limit_type="seven_day_opus", resets_at=1
        )


def test_non_authority_record_openrouter_spend_raises(monkeypatch):
    _as_desktop(monkeypatch)
    with pytest.raises(llm_lane.LaneAuthorityError):
        llm_lane.record_openrouter_spend("batch", cost_usd=1.0)


def test_non_authority_switch_to_subscription_raises(monkeypatch):
    _as_desktop(monkeypatch)
    with pytest.raises(llm_lane.LaneAuthorityError):
        llm_lane.switch_to_subscription("batch")


def test_non_authority_approve_batch_raises(monkeypatch):
    _as_desktop(monkeypatch)
    with pytest.raises(llm_lane.LaneAuthorityError):
        llm_lane.approve_batch(cap_usd=20.0)


def test_non_authority_set_state_raises(monkeypatch):
    _as_desktop(monkeypatch)
    with pytest.raises(llm_lane.LaneAuthorityError):
        llm_lane.set_state("batch", status="subscription")


# ── CLI ─────────────────────────────────────────────────────────────────


def test_cli_show_prints_json(capsys):
    assert llm_lane.main(["show"]) == 0
    out = capsys.readouterr().out
    assert '"interactive"' in out
    assert '"subscription"' in out


def test_cli_approve_batch(capsys):
    llm_lane.record_exhausted("batch", model="opus", rate_limit_type="seven_day_opus", resets_at=1)
    assert llm_lane.main(["approve-batch", "--cap", "15"]) == 0
    out = capsys.readouterr().out
    assert '"openrouter_approved"' in out
    assert llm_lane.get_state().batch.cap_usd == 15.0


def test_cli_set(capsys):
    assert llm_lane.main(["set", "--call-class", "interactive", "--status", "subscription"]) == 0
    assert llm_lane.get_state().interactive.status == "subscription"
