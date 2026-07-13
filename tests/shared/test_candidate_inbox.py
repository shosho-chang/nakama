"""候選收件匣 + 事件流 — ADR-048 Phase 1（shared/candidate_inbox.py）.

涵蓋：upsert insert/update、source_refs round-trip、list_open 排序（強訊號置頂 →
新→舊 → proposed_priority tiebreak）、mark_carded 移出 open + 記事件、log_event /
list_events filter、count_open / get_candidate。

DB 隔離由 conftest 的 autouse ``isolated_db`` fixture 提供（每測一個 tmp DB，且重置
``candidate_inbox._SCHEMA_INITIALIZED``），故直接呼叫模組函式即可。
"""

from __future__ import annotations

import shared.candidate_inbox as ci
from shared.schemas.daily_review import CandidateCard, SourceRef


def _card(
    cid: str,
    title: str = "主張一句話",
    why: str = "觸發訊號",
    *,
    strong: bool = False,
    priority: int = 0,
    refs: list[SourceRef] | None = None,
) -> CandidateCard:
    return CandidateCard(
        candidate_id=cid,
        suggested_title=title,
        why=why,
        source_refs=refs or [],
        priority=priority,
        strong_signal=strong,
    )


# ── upsert：insert ────────────────────────────────────────────────────────────


def test_upsert_insert_creates_open_row_and_proposed_event():
    new = ci.upsert_candidate(_card("a-1", "標題A"), today="2026-06-20")
    assert new is True
    row = ci.get_candidate("a-1")
    assert row is not None
    assert row["status"] == "open"
    assert row["first_seen"] == "2026-06-20"
    assert row["last_seen"] == "2026-06-20"
    assert row["suggested_title"] == "標題A"
    events = ci.list_events(candidate_id="a-1")
    assert [e["event_type"] for e in events] == ["proposed"]
    assert events[0]["metadata"] == {"title": "標題A"}


# ── upsert：update（已存在）─────────────────────────────────────────────────────


def test_upsert_existing_open_touches_last_seen_no_duplicate_proposed():
    ci.upsert_candidate(_card("a-1", "原標題"), today="2026-06-20")
    new = ci.upsert_candidate(_card("a-1", "改了標題"), today="2026-06-22")
    assert new is False
    row = ci.get_candidate("a-1")
    assert row["first_seen"] == "2026-06-20"  # 首見不變
    assert row["last_seen"] == "2026-06-22"  # 最近觸新
    assert row["suggested_title"] == "改了標題"  # open 者刷新最新 P-1 措辭
    # proposed 事件只該有一筆（再提出不重記）
    assert len(ci.list_events(candidate_id="a-1", event_type="proposed")) == 1


def test_upsert_carded_not_resurrected():
    ci.upsert_candidate(_card("a-1"), today="2026-06-20")
    ci.mark_carded("a-1", carded_path="KB/Permanent/x")
    # 同日再跑（或同 id 再提）絕不可把 carded 翻回 open
    new = ci.upsert_candidate(_card("a-1", "想復活"), today="2026-06-22")
    assert new is False
    row = ci.get_candidate("a-1")
    assert row["status"] == "carded"
    assert row["suggested_title"] != "想復活"  # carded 不刷新內容
    assert row["last_seen"] == "2026-06-22"  # 僅觸 recency
    assert ci.list_open() == []  # carded 不再出現


# ── source_refs round-trip ────────────────────────────────────────────────────


def test_source_refs_round_trip():
    refs = [
        SourceRef(
            anchor="^cfi-1", literature_path="KB/Literature/x", quote="引文一", note="筆記一"
        ),
        SourceRef(anchor="t=750", literature_path="KB/Literature/yt", quote="引文二", note=""),
    ]
    ci.upsert_candidate(_card("a-1", refs=refs), today="2026-06-20")
    [card] = ci.list_open()
    assert len(card.source_refs) == 2
    assert card.source_refs[0].anchor == "^cfi-1"
    assert card.source_refs[0].literature_path == "KB/Literature/x"
    assert card.source_refs[0].quote == "引文一"
    assert card.source_refs[0].note == "筆記一"
    assert card.source_refs[1].anchor == "t=750"
    assert card.source_refs[1].note == ""


# ── list_open 排序 ─────────────────────────────────────────────────────────────


def test_list_open_strong_pinned_then_newest_first():
    ci.upsert_candidate(_card("old-weak"), today="2026-06-20")
    ci.upsert_candidate(_card("new-weak"), today="2026-06-22")
    ci.upsert_candidate(_card("old-strong", strong=True), today="2026-06-19")
    cards = ci.list_open()
    ids = [c.candidate_id for c in cards]
    # 強訊號置頂（即使最舊）；其餘新→舊
    assert ids == ["old-strong", "new-weak", "old-weak"]
    # 顯示 priority 依排序重編 0..n
    assert [c.priority for c in cards] == [0, 1, 2]


def test_list_open_same_day_uses_proposed_priority_not_alpha():
    # 同日、同強度 → 用 P-1 的 proposed_priority（非 candidate_id 字母序）
    ci.upsert_candidate(_card("z-card", priority=0), today="2026-06-20")
    ci.upsert_candidate(_card("a-card", priority=1), today="2026-06-20")
    ids = [c.candidate_id for c in ci.list_open()]
    assert ids == ["z-card", "a-card"]  # priority 0 在 1 前面，字母序會反過來


# ── mark_carded ───────────────────────────────────────────────────────────────


def test_mark_carded_excludes_from_open_and_logs_event():
    ci.upsert_candidate(_card("a-1"), today="2026-06-20")
    hit = ci.mark_carded("a-1", carded_path="KB/Permanent/系統優先於意志力")
    assert hit is True
    row = ci.get_candidate("a-1")
    assert row["status"] == "carded"
    assert row["carded_path"] == "KB/Permanent/系統優先於意志力"
    assert ci.list_open() == []
    card_events = ci.list_events(candidate_id="a-1", event_type="card")
    assert len(card_events) == 1
    assert card_events[0]["metadata"]["carded_path"] == "KB/Permanent/系統優先於意志力"


def test_mark_carded_unknown_id_returns_false_but_logs_event():
    hit = ci.mark_carded("ghost", carded_path="KB/Permanent/x")
    assert hit is False  # 收件匣無此候選（fleeting / 手建卡）
    assert len(ci.list_events(candidate_id="ghost", event_type="card")) == 1  # 事件仍記


# ── events ────────────────────────────────────────────────────────────────────


def test_log_event_and_list_events_filters():
    ci.log_event("a-1", "skip", reason="不相關")
    ci.log_event("a-1", "defer")
    ci.log_event("b-2", "skip")
    assert len(ci.list_events()) == 3
    assert len(ci.list_events(candidate_id="a-1")) == 2
    assert len(ci.list_events(event_type="skip")) == 2
    skip_a = ci.list_events(candidate_id="a-1", event_type="skip")
    assert skip_a[0]["reason"] == "不相關"


def test_list_events_since_filter():
    ci.log_event("a-1", "proposed", occurred_at="2026-06-01T00:00:00+00:00")
    ci.log_event("a-1", "card", occurred_at="2026-06-10T00:00:00+00:00")
    recent = ci.list_events(since="2026-06-05T00:00:00+00:00")
    assert [e["event_type"] for e in recent] == ["card"]


def test_unknown_event_type_logged_anyway():
    # append-only log 不擋未知型別（呼叫端新增型別時不該靜默失敗）
    eid = ci.log_event("a-1", "weird_new_type")
    assert eid > 0
    assert ci.list_events(candidate_id="a-1")[0]["event_type"] == "weird_new_type"


# ── inspectors ────────────────────────────────────────────────────────────────


def test_count_open_excludes_carded():
    ci.upsert_candidate(_card("a-1"), today="2026-06-20")
    ci.upsert_candidate(_card("b-2"), today="2026-06-20")
    assert ci.count_open() == 2
    ci.mark_carded("a-1")
    assert ci.count_open() == 1


def test_get_candidate_missing_returns_none():
    assert ci.get_candidate("nope") is None


def test_carded_among_returns_only_carded_subset():
    ci.upsert_candidate(_card("open-1"), today="2026-06-20")
    ci.upsert_candidate(_card("carded-1"), today="2026-06-20")
    ci.mark_carded("carded-1")
    assert ci.carded_among(["open-1", "carded-1", "never-seen", ""]) == {"carded-1"}


def test_carded_among_empty_input_returns_empty_set():
    assert ci.carded_among([]) == set()
    assert ci.carded_among([""]) == set()
