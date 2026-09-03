"""打卡入帳受 ``scored_sources`` 管制的行為測試。

2026-09-02 之前打卡繞過了這道閘門：``SanjiLoop.cycle`` 把 ``checkin_submitted``
分流到 ``_handle_checkin``，而名單過濾只掛在 else 分支上。結果是挑戰一上線，
打卡就會無視分階段上線設定直接入帳。閘門後來收進 ``award_checkin``（loop 與
reconcile 的共同必經點），本檔釘住這個行為。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.sanji.loop import award_checkin
from agents.sanji.settings import SanjiConfig
from agents.sanji.store import Store


class FakeClient:
    """只記下被呼叫了什麼——入帳與公開回覆都是對外副作用，要能斷言「沒發生」。"""

    def __init__(self) -> None:
        self.granted: list[list[dict]] = []
        self.comments: list[tuple[int, str]] = []

    def grants(self, payload: list[dict]) -> dict:
        self.granted.append(payload)
        return {
            "results": [
                {"idempotency_key": g["idempotency_key"], "status": "created"} for g in payload
            ]
        }

    def balance(self, user_id: int, rebuild: bool = False) -> dict:
        return {"xp_total": 0}

    def comment(self, feed_id: int, text: str) -> None:
        self.comments.append((feed_id, text))


def _cfg(scored: set[str]) -> SanjiConfig:
    return SanjiConfig(
        wp_base_url="https://example.test",
        wp_user="sanji",
        wp_app_password="x",
        sanji_user_id=126,
        scored_sources=frozenset(scored),
    )


@pytest.fixture()
def store(tmp_path: Path):
    s = Store(db_path=tmp_path / "test.db")
    yield s
    s.close()


def _award(client: FakeClient, store: Store, cfg: SanjiConfig) -> None:
    award_checkin(
        client,
        store,
        cfg,
        user_id=42,
        feed_id=900,
        day="2026-10-07",
        season="2026Q4",
        ref_event_id=1,
    )


def test_checkin_not_scored_grants_nothing_and_stays_silent(store: Store):
    client = FakeClient()
    _award(client, store, _cfg({"like_received", "comment_received", "bookmark_received"}))

    assert client.granted == [], "打卡不在名單時不得入帳"
    assert client.comments == [], "沒入帳就不該公開回覆（回覆會宣稱拿到 XP）"


def test_checkin_state_still_recorded_while_unscored(store: Store):
    """狀態照記——否則日後開啟計分時 streak 會出現假斷點。"""
    client = FakeClient()
    _award(client, store, _cfg({"like_received"}))

    assert store.has_checkin(42, "2026-10-07")


def test_checkin_scored_grants_and_replies(store: Store):
    client = FakeClient()
    _award(client, store, _cfg({"like_received", "checkin_day"}))

    assert len(client.granted) == 1
    sources = [g["source"] for g in client.granted[0]]
    assert "checkin_day" in sources
    assert len(client.comments) == 1


def test_reply_never_mentions_retired_currency(store: Store):
    client = FakeClient()
    _award(client, store, _cfg({"checkin_day"}))

    _, text = client.comments[0]
    assert "貝里" not in text
    assert "XP" in text
