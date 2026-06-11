"""N522 — 每日回顧 daily job (agents/robin/daily_review.py).

驗收 (task prompt §5)：
- 過期歸檔 (14 天) 的確定性單元測試 (核心，必做)
- 孤兒卡 / stale seedling 偵測單元測試 (link graph 程式邏輯)
- P-1 用 fixture annotation 跑 (不依賴真 vault)，驗證強訊號置頂
- P-2 負面測試：財富階梯 ↔ wingate「表面相似」必須被丟棄
- LLM 呼叫全 mock (monkeypatch module-level _ask_p1_llm / _ask_p2_llm)
- 輸出 schema 測試

所有 LLM seam 在測試中 monkeypatch，KB 檢索 monkeypatch search_kb——不打 API、不依賴真
kb_index.db / 真 vault E:\\Shosho LifeOS。
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

import agents.robin.daily_review as dr
from shared.kb_hybrid_search import make_conn
from shared.schemas.annotations import (
    AnnotationSetV3,
    AnnotationV3,
    HighlightV3,
    ReflectionV3,
)
from shared.schemas.daily_review import (
    CandidateCard,
    DailyReviewBundle,
    SourceRef,
    TypedEdgeChip,
)

_NOW = datetime(2026, 6, 11, 7, 0, 0, tzinfo=timezone.utc)
_YESTERDAY = "2026-06-10T08:43:00Z"  # 「必須重複三次」的劃線時刻 (task §5)
_YESTERDAY_2 = "2026-06-10T06:41:00Z"  # 「這句是我想的」(task §5)
_OLD = "2026-05-01T00:00:00Z"  # 非昨日，不該進候選


# ── fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def vault(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    return tmp_path


def _write_annotations(vault_path: Path, ann_set: AnnotationSetV3) -> None:
    from shared.annotation_store import _serialize

    d = vault_path / "KB" / "Annotations"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{ann_set.slug}.md").write_text(_serialize(ann_set), encoding="utf-8")


def _card_box_set() -> AnnotationSetV3:
    """《卡片盒筆記》mock fixture：含兩條強評價 note + 一條純 highlight + 舊條目。

    強訊號條 (task §5)：「必須重複三次」(08:43)、「這句是我想的，應該要記起來」(06:41)。
    """
    return AnnotationSetV3(
        slug="卡片盒筆記",
        base="books",
        book_id="卡片盒筆記",
        book_version_hash="a" * 64,
        items=[
            AnnotationV3(
                cfi="epubcfi(/6/14[ch2]!/4/2/116)",
                text_excerpt="間隔重複是記憶的關鍵。",
                note="必須重複三次才會記得，這是 spacing effect。",
                book_version_hash="a" * 64,
                created_at=_YESTERDAY,
                modified_at=_YESTERDAY,
            ),
            AnnotationV3(
                cfi="epubcfi(/6/14[ch2]!/4/2/200)",
                text_excerpt="寫卡片就是在跟自己對話。",
                note="這句是我想的，應該要記起來——卡片盒是思考的對手。",
                book_version_hash="a" * 64,
                created_at=_YESTERDAY_2,
                modified_at=_YESTERDAY_2,
            ),
            HighlightV3(
                cfi="epubcfi(/6/14[ch2]!/4/2/300)",
                text_excerpt="這是一條純 highlight，沒有 note。",
                text="這是一條純 highlight，沒有 note。",
                book_version_hash="a" * 64,
                created_at=_YESTERDAY,
                modified_at=_YESTERDAY,
            ),
            ReflectionV3(
                chapter_ref="ch2",
                cfi_anchor="epubcfi(/6/14[ch2]!/4/2/400)",
                book_version_hash="a" * 64,
                body="舊心得，不該進今天的候選。",
                created_at=_OLD,
                modified_at=_OLD,
            ),
        ],
    )


# ── 過期歸檔 (14 天) — 確定性核心測試 ────────────────────────────────────────


def test_expire_deferred_past_14_days_archived():
    today = date(2026, 6, 11)
    state = {
        "skipped": [],
        "deferred": {
            "old-card": "2026-05-20",  # 22 天前 → 過期
            "edge-card": "2026-05-28",  # 14 天前 → 過期 (>= 邊界)
            "fresh-card": "2026-06-05",  # 6 天前 → 留
        },
    }
    new_state, expired = dr.expire_deferred(state, today=today)
    assert set(expired) == {"old-card", "edge-card"}
    assert new_state["deferred"] == {"fresh-card": "2026-06-05"}


def test_expire_deferred_boundary_exactly_14_days():
    today = date(2026, 6, 11)
    state = {"deferred": {"on-boundary": "2026-05-28"}}  # 正好 14 天
    _, expired = dr.expire_deferred(state, today=today)
    assert expired == ["on-boundary"]


def test_expire_deferred_13_days_kept():
    today = date(2026, 6, 11)
    state = {"deferred": {"almost": "2026-05-29"}}  # 13 天
    new_state, expired = dr.expire_deferred(state, today=today)
    assert expired == []
    assert "almost" in new_state["deferred"]


def test_expire_deferred_corrupt_date_treated_as_expired():
    today = date(2026, 6, 11)
    state = {"deferred": {"junk": "not-a-date"}}
    new_state, expired = dr.expire_deferred(state, today=today)
    assert expired == ["junk"]
    assert new_state["deferred"] == {}


def test_expire_deferred_empty_queue():
    new_state, expired = dr.expire_deferred({"deferred": {}}, today=date(2026, 6, 11))
    assert expired == []
    assert new_state["deferred"] == {}


def test_review_state_round_trip(vault: Path):
    state = {"skipped": ["a", "b"], "deferred": {"c": "2026-06-01"}}
    dr.save_review_state(vault, state)
    loaded = dr.load_review_state(vault)
    assert loaded == state


def test_load_review_state_missing_file_returns_skeleton(vault: Path):
    assert dr.load_review_state(vault) == {"skipped": [], "deferred": {}}


def test_load_review_state_corrupt_json_returns_skeleton(vault: Path):
    p = dr._state_path(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json", encoding="utf-8")
    assert dr.load_review_state(vault) == {"skipped": [], "deferred": {}}


# ── 孤兒卡偵測 (link graph 程式算) ────────────────────────────────────────────


def _write_permanent(
    vault_path: Path, name: str, body: str, *, status="seedling", created="2026-06-01"
):
    d = vault_path / "KB" / "Permanent"
    d.mkdir(parents=True, exist_ok=True)
    fm = f"---\ntype: permanent\nstatus: {status}\ncreated: {created}\n---\n\n"
    (d / f"{name}.md").write_text(fm + body, encoding="utf-8")


def test_detect_orphans_finds_unlinked_card(vault: Path):
    _write_permanent(vault, "孤兒卡", "沒有任何連結的卡。")
    _write_permanent(vault, "有連結卡", "支持:: [[孤兒卡]] — 理由")
    conn = make_conn(":memory:")
    # only 有連結卡 → 孤兒卡 typed edge; "有連結卡" is a src, "孤兒卡" is a dst.
    conn.execute(
        "INSERT INTO kb_typed_edges(src_path, edge_type, dst_path, reason) VALUES (?,?,?,?)",
        ("KB/Permanent/有連結卡", "support", "KB/Permanent/孤兒卡", "理由"),
    )
    conn.commit()
    orphans = dr.detect_orphans(vault, conn=conn)
    paths = {o.path for o in orphans}
    # 孤兒卡 is now a dst (linked); neither is orphan.
    assert paths == set()


def test_detect_orphans_truly_unlinked(vault: Path):
    _write_permanent(vault, "完全孤兒", "誰也不連。")
    _write_permanent(vault, "連結對_甲", "")
    _write_permanent(vault, "連結對_乙", "")
    conn = make_conn(":memory:")
    conn.execute(
        "INSERT INTO kb_wikilinks(src_path, dst_path) VALUES (?,?)",
        ("KB/Permanent/連結對_甲", "KB/Permanent/連結對_乙"),
    )
    conn.commit()
    orphans = dr.detect_orphans(vault, conn=conn)
    assert {o.path for o in orphans} == {"KB/Permanent/完全孤兒"}
    assert orphans[0].kind == "orphan_card"


def test_detect_orphans_empty_when_no_cards(vault: Path):
    conn = make_conn(":memory:")
    assert dr.detect_orphans(vault, conn=conn) == []


# ── stale seedling 偵測 ──────────────────────────────────────────────────────


def test_detect_stale_seedling_over_30_days(vault: Path):
    _write_permanent(vault, "陳年種子", "放很久了。", status="seedling", created="2026-05-01")
    _write_permanent(vault, "新種子", "上週才寫。", status="seedling", created="2026-06-09")
    _write_permanent(vault, "已升級", "升級過。", status="evergreen", created="2026-01-01")
    today = date(2026, 6, 11)
    stale = dr.detect_stale_seedlings(vault, today=today)
    assert {s.path for s in stale} == {"KB/Permanent/陳年種子"}
    assert stale[0].kind == "stale_seedling"
    assert stale[0].age_days == 41


def test_detect_stale_seedling_boundary_exactly_30_days_kept(vault: Path):
    # created 距今正好 30 天 → 不算 stale (規則：> 30)
    _write_permanent(vault, "邊界種子", "", status="seedling", created="2026-05-12")
    stale = dr.detect_stale_seedlings(vault, today=date(2026, 6, 11))
    assert stale == []


def test_detect_stale_seedling_ignores_evergreen(vault: Path):
    _write_permanent(vault, "老常青", "", status="evergreen", created="2025-01-01")
    assert dr.detect_stale_seedlings(vault, today=date(2026, 6, 11)) == []


# ── 昨日 delta 掃描 ──────────────────────────────────────────────────────────


def test_collect_yesterday_items_filters_by_date(vault: Path):
    _write_annotations(vault, _card_box_set())
    items = dr.collect_yesterday_items(vault, yesterday=date(2026, 6, 10))
    # 3 條昨日 (2 annotation + 1 highlight)；舊 reflection 排除。
    assert len(items) == 3
    types = sorted(it["type"] for it in items)
    assert types == ["annotation", "annotation", "highlight"]
    assert all(it["literature_path"] == "KB/Literature/卡片盒筆記" for it in items)


def test_collect_yesterday_items_empty_when_no_annotations(vault: Path):
    assert dr.collect_yesterday_items(vault, yesterday=date(2026, 6, 10)) == []


# ── P-1 候選篩選 + 強訊號置頂 (fixture, LLM mock) ────────────────────────────


def test_build_candidates_strong_signal_pinned_top(vault: Path, monkeypatch):
    """P-1：強評價訊號置頂。模擬 LLM 回兩條候選 (一條普通先、一條強訊號後)，
    build_candidates 必須把強訊號條重排到 priority 0。"""
    items = [
        {
            "slug": "卡片盒筆記",
            "anchor": "^cfi-6-14-200",
            "quote": "寫卡片就是在跟自己對話。",
            "note": "可以 connect 到費曼的學習法。",  # 延伸思考但無強評價詞
            "type": "annotation",
            "literature_path": "KB/Literature/卡片盒筆記",
        },
        {
            "slug": "卡片盒筆記",
            "anchor": "^cfi-6-14-116",
            "quote": "間隔重複是記憶的關鍵。",
            "note": "必須重複三次才會記得。",
            "type": "annotation",
            "literature_path": "KB/Literature/卡片盒筆記",
        },
    ]

    def _fake_p1(prompt):  # noqa: ARG001
        # LLM 故意把弱的放前面、強的放後面——build_candidates 要靠 strong_signal 重排。
        return [
            {
                "suggested_title": "卡片盒是思考的對手",
                "why": "使用者 connect 到費曼",
                "anchors": ["^cfi-6-14-200"],
                "source_quote": "寫卡片就是在跟自己對話。",
                "user_note": "可以 connect 到費曼的學習法。",
                "strong_signal": False,
            },
            {
                "suggested_title": "間隔重複需要重複三次",
                "why": "note 含『必須重複三次』強訊號",
                "anchors": ["^cfi-6-14-116"],
                "source_quote": "間隔重複是記憶的關鍵。",
                "user_note": "必須重複三次才會記得。",
                "strong_signal": True,
            },
        ]

    monkeypatch.setattr(dr, "_ask_p1_llm", _fake_p1)
    cards, warnings = dr.build_candidates(items, "（index）")
    assert len(cards) == 2
    assert cards[0].strong_signal is True
    assert cards[0].suggested_title == "間隔重複需要重複三次"
    assert cards[0].priority == 0
    assert cards[1].priority == 1
    # source_refs 對回原 item（quote/note 原文照錄）
    assert cards[0].source_refs[0].quote == "間隔重複是記憶的關鍵。"
    assert cards[0].source_refs[0].note == "必須重複三次才會記得。"
    assert warnings == []


def test_build_candidates_safety_net_pins_missed_strong_signal(monkeypatch):
    """安全網：LLM 漏標 strong_signal=False，但 note 明含強評價詞 → 仍判為強訊號。"""
    items = [
        {
            "slug": "s",
            "anchor": "^p-1",
            "quote": "q",
            "note": "這句是我想的，應該要記起來。",
            "type": "annotation",
            "literature_path": "KB/Literature/s",
        }
    ]

    def _fake_p1(prompt):  # noqa: ARG001
        return [
            {
                "suggested_title": "主張",
                "why": "x",
                "anchors": ["^p-1"],
                "source_quote": "q",
                "user_note": "這句是我想的，應該要記起來。",
                "strong_signal": False,  # LLM 漏標
            }
        ]

    monkeypatch.setattr(dr, "_ask_p1_llm", _fake_p1)
    cards, _ = dr.build_candidates(items, "idx")
    assert cards[0].strong_signal is True  # 程式碼安全網補上


def test_build_candidates_caps_at_max(vault: Path, monkeypatch):
    items = [
        {
            "slug": "s",
            "anchor": f"^p-{i}",
            "quote": f"q{i}",
            "note": f"n{i}",
            "type": "annotation",
            "literature_path": "KB/Literature/s",
        }
        for i in range(10)
    ]

    def _fake_p1(prompt):  # noqa: ARG001
        return [
            {
                "suggested_title": f"主張 {i}",
                "why": "x",
                "anchors": [f"^p-{i}"],
                "source_quote": f"q{i}",
                "user_note": f"n{i}",
                "strong_signal": False,
            }
            for i in range(10)
        ]

    monkeypatch.setattr(dr, "_ask_p1_llm", _fake_p1)
    cards, _ = dr.build_candidates(items, "idx", max_candidates=7)
    assert len(cards) == 7


def test_build_candidates_empty_items():
    cards, warnings = dr.build_candidates([], "idx")
    assert cards == []


# ── P-2 typed-edge：表面相似負面測試 (task §5) ──────────────────────────────


def test_judge_edges_discards_surface_similarity(vault: Path, monkeypatch):
    """財富階梯劃線撈到 wingate test：表面相似 ≠ 概念關係，P-2 應丟棄 (空陣列)。"""
    candidate = CandidateCard(
        candidate_id="財富-x",
        suggested_title="財富階梯是判斷藝術而非精確科學",
        why="x",
        source_refs=[
            SourceRef(
                anchor="^cfi-1",
                literature_path="KB/Literature/財富階梯",
                quote="財富階梯更像一種大致的判斷藝術。",
                note="",
            )
        ],
    )

    # FTS5 撈回一張表面共詞 (「階梯」/「測試」) 但概念無關的卡。
    def _fake_fts(query, vault_path, top_k=6, **kwargs):  # noqa: ARG001
        return [
            {
                "path": "KB/Permanent/wingate-test-是無氧功率指標",
                "title": "wingate-test-是無氧功率指標",
                "chunk_text": "Wingate test 用 30 秒全力衝刺量測無氧功率階梯。",
                "preview": "",
            }
        ]

    import agents.robin.kb_search as kb

    monkeypatch.setattr(kb, "search_kb", _fake_fts)
    # P-2 正確判斷：表面相似 → 全空。
    monkeypatch.setattr(
        dr, "_ask_p2_llm", lambda prompt: {"supports": [], "refutes": [], "extends": []}
    )

    chips = dr.judge_edges(candidate, vault)
    assert chips == []


def test_judge_edges_real_relationship_kept(vault: Path, monkeypatch):
    candidate = CandidateCard(
        candidate_id="系統-x",
        suggested_title="好系統讓你不需要意志力",
        why="x",
        source_refs=[
            SourceRef(
                anchor="^cfi-2",
                literature_path="KB/Literature/原子習慣",
                quote="系統勝過目標。",
                note="",
            )
        ],
    )

    def _fake_fts(query, vault_path, top_k=6, **kwargs):  # noqa: ARG001
        return [
            {
                "path": "KB/Permanent/意志力是有限資源",
                "title": "意志力是有限資源",
                "chunk_text": "意志力像肌肉會疲勞。",
                "preview": "",
            }
        ]

    import agents.robin.kb_search as kb

    monkeypatch.setattr(kb, "search_kb", _fake_fts)
    monkeypatch.setattr(
        dr,
        "_ask_p2_llm",
        lambda prompt: {
            "supports": [
                {
                    "target_path": "KB/Permanent/意志力是有限資源",
                    "target_title": "意志力是有限資源",
                    "direction": "forward",
                    "internal_rationale": "系統把消耗意志力的環節自動化",
                }
            ],
            "refutes": [],
            "extends": [],
        },
    )

    chips = dr.judge_edges(candidate, vault)
    assert len(chips) == 1
    assert chips[0].edge_type == "support"
    assert chips[0].direction == "forward"
    assert chips[0].target_card == "KB/Permanent/意志力是有限資源"
    # internal_rationale 絕不外露 (chip 無 rationale 欄)
    assert not hasattr(chips[0], "internal_rationale")
    assert "internal_rationale" not in chips[0].model_dump()


def test_judge_edges_caps_each_group_at_3(vault: Path, monkeypatch):
    candidate = CandidateCard(
        candidate_id="c",
        suggested_title="t",
        why="",
        source_refs=[SourceRef(anchor="^a", literature_path="KB/Literature/x", quote="q", note="")],
    )
    import agents.robin.kb_search as kb

    monkeypatch.setattr(
        kb,
        "search_kb",
        lambda *a, **k: [
            {"path": "KB/Permanent/p", "title": "p", "chunk_text": "b", "preview": ""}
        ],
    )
    monkeypatch.setattr(
        dr,
        "_ask_p2_llm",
        lambda prompt: {
            "supports": [
                {
                    "target_path": f"KB/Permanent/c{i}",
                    "target_title": f"c{i}",
                    "direction": "forward",
                    "internal_rationale": "r",
                }
                for i in range(5)
            ],
            "refutes": [],
            "extends": [],
        },
    )
    chips = dr.judge_edges(candidate, vault)
    assert len(chips) == 3  # capped


def test_judge_edges_no_fts_hits_returns_empty(vault: Path, monkeypatch):
    candidate = CandidateCard(
        candidate_id="c",
        suggested_title="t",
        why="",
        source_refs=[SourceRef(anchor="^a", literature_path="KB/Literature/x", quote="q", note="")],
    )
    import agents.robin.kb_search as kb

    monkeypatch.setattr(kb, "search_kb", lambda *a, **k: [])

    called = {"p2": False}

    def _p2(prompt):  # noqa: ARG001
        called["p2"] = True
        return {}

    monkeypatch.setattr(dr, "_ask_p2_llm", _p2)
    assert dr.judge_edges(candidate, vault) == []
    assert called["p2"] is False  # FTS 無命中時不浪費 LLM call


# ── Fleeting 掃描 ─────────────────────────────────────────────────────────────


def test_collect_open_fleeting(vault: Path):
    d = vault / "KB" / "Fleeting"
    d.mkdir(parents=True, exist_ok=True)
    (d / "20260610-083200-想法.md").write_text(
        "---\ntype: fleeting\ncreated: 2026-06-10T08:32:00\nvia: slack\nstatus: open\n---\n"
        "讀書時想到的一個點子。",
        encoding="utf-8",
    )
    (d / "20260609-已處理.md").write_text(
        "---\ntype: fleeting\ncreated: 2026-06-09T10:00:00\nvia: slack\nstatus: processed\n---\n"
        "已經處理過了。",
        encoding="utf-8",
    )
    items = dr.collect_open_fleeting(vault)
    assert len(items) == 1
    assert items[0].text == "讀書時想到的一個點子。"
    assert items[0].via == "slack"
    assert items[0].path == "KB/Fleeting/20260610-083200-想法.md"


def test_collect_open_fleeting_missing_dir(vault: Path):
    assert dr.collect_open_fleeting(vault) == []


# ── 輸出 schema 測試 ─────────────────────────────────────────────────────────


def test_bundle_schema_round_trips():
    bundle = DailyReviewBundle(
        generated_at="2026-06-11T07:00:00Z",
        review_date="2026-06-11",
        weekly_sweep=False,
        candidates=[
            CandidateCard(
                candidate_id="c1",
                suggested_title="主張一句話",
                why="使用者標記要記起來",
                source_refs=[
                    SourceRef(
                        anchor="^cfi-1", literature_path="KB/Literature/x", quote="q", note="n"
                    )
                ],
                edges=[
                    TypedEdgeChip(
                        edge_type="support",
                        direction="forward",
                        target_card="KB/Permanent/y",
                        target_title="y",
                    )
                ],
                priority=0,
                strong_signal=True,
            )
        ],
    )
    dumped = bundle.model_dump()
    restored = DailyReviewBundle.model_validate(dumped)
    assert restored == bundle
    assert restored.schema_version == 1
    # internal_rationale never present anywhere in the contract
    assert "internal_rationale" not in str(dumped)


def test_bundle_rejects_unknown_field():
    with pytest.raises(Exception):
        DailyReviewBundle(generated_at="t", review_date="d", bogus_field="x")


def test_typed_edge_chip_has_no_rationale_field():
    chip = TypedEdgeChip(edge_type="extend", target_card="KB/Permanent/z")
    assert "internal_rationale" not in chip.model_dump()
    assert "reason" not in chip.model_dump()


# ── 端到端 orchestrator (全 mock) ───────────────────────────────────────────


def test_run_daily_review_end_to_end(vault: Path, monkeypatch):
    """整條跑通：昨日 annotation → P-1 → P-2 → bundle，不發通知、不打 API。"""
    _write_annotations(vault, _card_box_set())
    (vault / "KB").mkdir(exist_ok=True)
    (vault / "KB" / "index.md").write_text("# index\n", encoding="utf-8")

    def _fake_p1(prompt):  # noqa: ARG001
        return [
            {
                "suggested_title": "間隔重複需要重複三次",
                "why": "強訊號",
                "anchors": ["^cfi-6-14-116"],
                "source_quote": "間隔重複是記憶的關鍵。",
                "user_note": "必須重複三次才會記得。",
                "strong_signal": True,
            }
        ]

    monkeypatch.setattr(dr, "_ask_p1_llm", _fake_p1)
    import agents.robin.kb_search as kb

    monkeypatch.setattr(kb, "search_kb", lambda *a, **k: [])  # no FTS hits → no P-2

    bundle = dr.run_daily_review(now=_NOW, weekly=False, vault_path=vault, notify=False)
    assert isinstance(bundle, DailyReviewBundle)
    assert bundle.review_date == "2026-06-11"
    assert bundle.weekly_sweep is False
    assert len(bundle.candidates) == 1
    assert bundle.candidates[0].strong_signal is True
    assert bundle.candidates[0].edges == []  # no FTS hits
    # log.md appended
    log = (vault / "KB" / "log.md").read_text(encoding="utf-8")
    assert "centaur:daily_review" in log


def test_run_daily_review_skips_already_skipped(vault: Path, monkeypatch):
    _write_annotations(vault, _card_box_set())

    def _fake_p1(prompt):  # noqa: ARG001
        return [
            {
                "suggested_title": "間隔重複需要重複三次",
                "why": "x",
                "anchors": ["^cfi-6-14-116"],
                "source_quote": "間隔重複是記憶的關鍵。",
                "user_note": "必須重複三次才會記得。",
                "strong_signal": True,
            }
        ]

    monkeypatch.setattr(dr, "_ask_p1_llm", _fake_p1)
    import agents.robin.kb_search as kb

    monkeypatch.setattr(kb, "search_kb", lambda *a, **k: [])

    # First run to learn the candidate_id, then mark it skipped.
    first = dr.run_daily_review(now=_NOW, weekly=False, vault_path=vault, notify=False)
    cid = first.candidates[0].candidate_id
    dr.save_review_state(vault, {"skipped": [cid], "deferred": {}})

    second = dr.run_daily_review(now=_NOW, weekly=False, vault_path=vault, notify=False)
    assert second.candidates == []


def test_run_daily_review_weekly_includes_sweep(vault: Path, monkeypatch):
    _write_permanent(vault, "陳年種子", "放很久。", status="seedling", created="2026-05-01")
    _write_permanent(vault, "完全孤兒", "誰也不連。", status="evergreen", created="2026-01-01")
    monkeypatch.setattr(dr, "_ask_p1_llm", lambda prompt: [])
    import agents.robin.kb_search as kb

    monkeypatch.setattr(kb, "search_kb", lambda *a, **k: [])
    # provide an in-memory conn with no edges → both cards count toward orphan check
    conn = make_conn(":memory:")
    monkeypatch.setattr(dr, "get_kb_conn", lambda: conn)

    bundle = dr.run_daily_review(now=_NOW, weekly=True, vault_path=vault, notify=False)
    assert bundle.weekly_sweep is True
    kinds = {s.kind for s in bundle.sweep}
    assert "stale_seedling" in kinds
    assert "orphan_card" in kinds


def test_run_daily_review_weekly_expires_deferred(vault: Path, monkeypatch):
    dr.save_review_state(vault, {"skipped": [], "deferred": {"old-x": "2026-05-01"}})
    monkeypatch.setattr(dr, "_ask_p1_llm", lambda prompt: [])
    import agents.robin.kb_search as kb

    monkeypatch.setattr(kb, "search_kb", lambda *a, **k: [])
    conn = make_conn(":memory:")
    monkeypatch.setattr(dr, "get_kb_conn", lambda: conn)

    bundle = dr.run_daily_review(now=_NOW, weekly=True, vault_path=vault, notify=False)
    expired = [s for s in bundle.sweep if s.kind == "expired_defer"]
    assert len(expired) == 1
    assert expired[0].path == "old-x"
    # state file no longer carries the expired entry
    assert dr.load_review_state(vault)["deferred"] == {}
