"""Tests for thousand_sunny.routers.kb_review — N523 每日回顧 Web UI + 開卡 endpoint.

驗收（task prompt §5）：
- POST /kb/api/permanent 寫入帶 ``author: human``
- 空正文 → 422
- typed edges 正確組進 body（``支持:: [[...]]`` 格式）
- skip / later 動作更新 state 檔
- GET /kb/review 渲染不爆（mock DailyReviewBundle fixture）
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from shared.schemas.daily_review import (
    CandidateCard,
    DailyReviewBundle,
    FleetingItem,
    RelatedCard,
    RelatedMoc,
    SourceRef,
    TypedEdgeChip,
)


@pytest.fixture
def vault(tmp_path: Path, monkeypatch) -> Path:
    """Point VAULT_PATH at a tmp vault; pre-create the KB skeleton dirs."""
    v = tmp_path / "vault"
    (v / "KB" / "Permanent").mkdir(parents=True)
    (v / "KB" / "Literature").mkdir(parents=True)
    (v / "KB" / "Fleeting").mkdir(parents=True)
    (v / "KB" / ".centaur").mkdir(parents=True)
    monkeypatch.setenv("VAULT_PATH", str(v))
    return v


@pytest.fixture
def mock_bundle() -> DailyReviewBundle:
    """A representative bundle: 1 fleeting + 1 candidate (with edges)."""
    return DailyReviewBundle(
        generated_at="2026-06-11T08:00:00Z",
        review_date="2026-06-11",
        weekly_sweep=False,
        candidates=[
            CandidateCard(
                candidate_id="卡片盒筆記-abc12345",
                suggested_title="理解是分層次的，寫作把你推向更深層",
                why="延伸了你今天已開的卡",
                source_refs=[
                    SourceRef(
                        anchor="cfi-6-48-146",
                        literature_path="KB/Literature/卡片盒筆記",
                        quote="只要我們嘗試用自己的文字來解說「我讀了什麼」的話，這種美好的感覺會迅速消失。",
                        note="理解也是分層次的。",
                    )
                ],
                edges=[
                    TypedEdgeChip(
                        edge_type="support",
                        direction="forward",
                        target_card="KB/Permanent/用自己的話寫是檢驗真懂的唯一方法",
                        target_title="用自己的話寫是檢驗真懂的唯一方法",
                    )
                ],
                related_pool=[
                    RelatedCard(
                        card_path="KB/Permanent/理解是分層次的",
                        title="理解是分層次的",
                        status="seedling",
                        bm25_rank=0,
                    )
                ],
                related_mocs=[
                    RelatedMoc(
                        moc_path="KB/MOCs/學習與刻意練習",
                        name="學習與刻意練習",
                        card_count=5,
                    )
                ],
                priority=0,
                strong_signal=False,
            )
        ],
        fleeting=[
            FleetingItem(
                path="KB/Fleeting/2026-06-10-2114-專注.md",
                created="2026-06-10T21:14:00",
                via="slack",
                text="專注其實是光譜不是開關？",
            )
        ],
        sweep=[],
        warnings=[],
    )


@pytest.fixture
def client(vault, mock_bundle, monkeypatch):
    """TestClient with auth disabled + daily-review bundle compute stubbed."""
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    monkeypatch.delenv("WEB_SECRET", raising=False)
    monkeypatch.delenv("DISABLE_ROBIN", raising=False)

    import thousand_sunny.app as app_module
    import thousand_sunny.auth as auth_module
    import thousand_sunny.routers.kb_review as kb_module

    importlib.reload(auth_module)
    importlib.reload(kb_module)
    importlib.reload(app_module)

    # Stub the (LLM-backed) bundle compute so the page renders deterministically.
    monkeypatch.setattr(app_module.kb_review, "_compute_bundle", lambda **kw: mock_bundle)

    return TestClient(app_module.app, follow_redirects=False), kb_module, vault


# ---------------------------------------------------------------------------
# GET /kb/review
# ---------------------------------------------------------------------------


def test_review_page_renders(client):
    tc, _kb, _v = client
    r = tc.get("/kb/review")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    assert "每日回顧" in r.text
    # JSON island carries the candidate so the static JS can render it.
    assert "理解是分層次的，寫作把你推向更深層" in r.text
    # static JS + CSS wired (CSP-safe, no inline script logic)
    assert "/static/kb_review.js" in r.text


def test_review_page_empty_bundle(client, monkeypatch):
    tc, kb, _v = client
    empty = DailyReviewBundle(generated_at="x", review_date="2026-06-11")
    import thousand_sunny.app as app_module

    monkeypatch.setattr(app_module.kb_review, "_compute_bundle", lambda **kw: empty)
    r = tc.get("/kb/review")
    assert r.status_code == 200
    # empty states present, not a blank page
    assert "沒有待處理的靈感卡" in r.text
    assert "昨天沒有夠強的劃線浮上來" in r.text


# ---------------------------------------------------------------------------
# POST /kb/api/permanent
# ---------------------------------------------------------------------------


def test_create_permanent_writes_author_human(client):
    tc, _kb, vault = client
    payload = {
        "title": "意志力要用在與目標對齊的艱難任務",
        "body": "意志力是稀缺資源，該花在對齊的艱難任務上。",
        "edges": [],
        "source_refs": [{"literature_path": "KB/Literature/卡片盒筆記", "anchor": "cfi-6-26-106"}],
    }
    r = tc.post("/kb/api/permanent", json=payload)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert data["author"] == "human"

    card = vault / "KB" / "Permanent" / "意志力要用在與目標對齊的艱難任務.md"
    assert card.exists()
    content = card.read_text(encoding="utf-8")
    assert "author: human" in content
    assert "type: permanent" in content
    assert "status: seedling" in content
    assert "意志力是稀缺資源" in content
    # source_ref preserved as wikilink (KB/ prefix stripped) + anchor
    assert "[[Literature/卡片盒筆記]] ^cfi-6-26-106" in content


def test_create_permanent_empty_body_422(client):
    tc, _kb, vault = client
    r = tc.post(
        "/kb/api/permanent",
        json={"title": "一句宣告句", "body": "   \n  "},
    )
    assert r.status_code == 422
    # nothing written
    assert not (vault / "KB" / "Permanent" / "一句宣告句.md").exists()


def test_create_permanent_empty_title_422(client):
    tc, _kb, _v = client
    r = tc.post("/kb/api/permanent", json={"title": "  ", "body": "有正文"})
    assert r.status_code == 422


def test_create_permanent_typed_edges_inline(client):
    tc, _kb, vault = client
    payload = {
        "title": "降低摩擦力是高產出的前提",
        "body": "魯曼的高產出來自從不強迫自己。",
        "edges": [
            {
                "edge_type": "support",
                "target": "KB/Permanent/好系統讓你不需要意志力",
                "reason": "摩擦力工程就是系統設計的具體手段",
            },
            {
                "edge_type": "refute",
                "target": "寫作產出靠的是紀律",
                "reason": "它假設人人都是村上春樹",
            },
            {"edge_type": "extend", "target": "[[Hell yeah or no]]", "reason": ""},
        ],
    }
    r = tc.post("/kb/api/permanent", json=payload)
    assert r.status_code == 200, r.text
    content = (vault / "KB" / "Permanent" / "降低摩擦力是高產出的前提.md").read_text(
        encoding="utf-8"
    )
    # typed edges as inline Dataview fields, 方向「本卡 → 對方」, leaf-only links
    assert "支持:: [[好系統讓你不需要意志力]] — 摩擦力工程就是系統設計的具體手段" in content
    assert "反駁:: [[寫作產出靠的是紀律]] — 它假設人人都是村上春樹" in content
    # no reason → no trailing dash
    assert "延伸:: [[Hell yeah or no]]\n" in content or content.rstrip().endswith(
        "延伸:: [[Hell yeah or no]]"
    )


def test_create_permanent_conflict_409(client):
    tc, _kb, vault = client
    p = {"title": "重複卡名", "body": "正文一"}
    assert tc.post("/kb/api/permanent", json=p).status_code == 200
    r2 = tc.post("/kb/api/permanent", json={"title": "重複卡名", "body": "正文二"})
    assert r2.status_code == 409


# ---------------------------------------------------------------------------
# ADR-048 Phase 1 — 開卡標記候選 carded（修 create_permanent 收 candidate_id 卻沒用）
# ---------------------------------------------------------------------------


def test_create_permanent_marks_candidate_carded(client):
    """帶 candidate_id 開卡 → 收件匣該候選 open→carded（不再每日重現）+ 記 card 事件。"""
    tc, _kb, _v = client
    from shared import candidate_inbox
    from shared.schemas.daily_review import CandidateCard

    candidate_inbox.upsert_candidate(
        CandidateCard(candidate_id="卡片盒筆記-deadbeef", suggested_title="主張", why="x"),
        today="2026-06-20",
    )
    assert candidate_inbox.count_open() == 1

    r = tc.post(
        "/kb/api/permanent",
        json={
            "title": "開卡後該候選要消失",
            "body": "正文。",
            "candidate_id": "卡片盒筆記-deadbeef",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["phase5"]["candidate_recorded"] is True

    row = candidate_inbox.get_candidate("卡片盒筆記-deadbeef")
    assert row["status"] == "carded"
    assert row["carded_path"] == r.json()["path"]
    assert candidate_inbox.count_open() == 0
    card_events = candidate_inbox.list_events(candidate_id="卡片盒筆記-deadbeef", event_type="card")
    assert len(card_events) == 1


def test_create_permanent_without_candidate_id_no_inbox_write(client):
    """手建卡 / fleeting 開卡（無 candidate_id）→ 不碰收件匣，candidate_recorded=False。"""
    tc, _kb, _v = client
    from shared import candidate_inbox

    r = tc.post("/kb/api/permanent", json={"title": "手建卡無候選來源", "body": "正文。"})
    assert r.status_code == 200, r.text
    assert r.json()["phase5"]["candidate_recorded"] is False
    assert candidate_inbox.count_open() == 0


def test_review_skip_logs_event(client):
    """略過候選 → state JSON 仍是過濾權威，且額外記 skip 事件（ground truth）。"""
    tc, _kb, _v = client
    from shared import candidate_inbox

    r = tc.post("/kb/api/review/skip", json={"candidate_id": "卡片盒筆記-skipme"})
    assert r.status_code == 200, r.text
    skips = candidate_inbox.list_events(candidate_id="卡片盒筆記-skipme", event_type="skip")
    assert len(skips) == 1


def test_review_later_logs_defer_event(client):
    """之後再說 → 記 defer 事件。"""
    tc, _kb, _v = client
    from shared import candidate_inbox

    r = tc.post("/kb/api/review/later", json={"candidate_id": "卡片盒筆記-laterme"})
    assert r.status_code == 200, r.text
    defers = candidate_inbox.list_events(candidate_id="卡片盒筆記-laterme", event_type="defer")
    assert len(defers) == 1


def test_filter_actioned_drops_carded_candidate(vault):
    """_filter_actioned 即時濾掉已開卡候選（快照凍結在開卡前，同日 reload 不該再現）。"""
    import thousand_sunny.routers.kb_review as kb
    from shared import candidate_inbox
    from shared.schemas.daily_review import CandidateCard, DailyReviewBundle

    candidate_inbox.upsert_candidate(
        CandidateCard(candidate_id="c-open", suggested_title="o", why=""), today="2026-06-20"
    )
    candidate_inbox.upsert_candidate(
        CandidateCard(candidate_id="c-carded", suggested_title="c", why=""), today="2026-06-20"
    )
    candidate_inbox.mark_carded("c-carded")

    bundle = DailyReviewBundle(
        generated_at="t",
        review_date="2026-06-20",
        candidates=[
            CandidateCard(candidate_id="c-open", suggested_title="o", why=""),
            CandidateCard(candidate_id="c-carded", suggested_title="c", why=""),
        ],
    )
    filtered = kb._filter_actioned(bundle, vault)
    assert [c.candidate_id for c in filtered.candidates] == ["c-open"]


# ---------------------------------------------------------------------------
# Phase 5 善後
# ---------------------------------------------------------------------------


def test_create_permanent_phase5_log_and_index(client):
    tc, _kb, vault = client
    r = tc.post(
        "/kb/api/permanent",
        json={"title": "可信任的系統讓大腦敢放手", "body": "系統的第一要務是可信任。"},
    )
    assert r.status_code == 200, r.text
    phase5 = r.json()["phase5"]
    assert phase5["log_appended"] is True
    assert phase5["index_updated"] is True

    log = (vault / "KB" / "log.md").read_text(encoding="utf-8")
    assert "[centaur:open_card]" in log
    assert "author=human" in log
    index = (vault / "KB" / "index.md").read_text(encoding="utf-8")
    assert "[[Permanent/可信任的系統讓大腦敢放手]]" in index


def test_phase5_literature_mined_backfill(client):
    tc, _kb, vault = client
    # seed a Literature note with frontmatter mined_concepts/status to backfill
    lit = vault / "KB" / "Literature" / "卡片盒筆記.md"
    lit.write_text(
        "---\n"
        "type: literature\n"
        "source_kind: book\n"
        "slug: 卡片盒筆記\n"
        "mined_concepts: []\n"
        "status: digested\n"
        "captured: 2026-06-10\n"
        "ingested: 2026-06-11\n"
        "schema_version: 3\n"
        "---\n\n劃線內容（render 區）\n",
        encoding="utf-8",
    )
    r = tc.post(
        "/kb/api/permanent",
        json={
            "title": "理解是分層次的",
            "body": "讀到的東西若不能用自己的話重述，就還不是你的。",
            "literature_slug": "卡片盒筆記",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["phase5"]["literature_backfilled"] is True
    content = lit.read_text(encoding="utf-8")
    assert "理解是分層次的" in content  # added to mined_concepts
    assert "status: mined" in content
    assert "status: digested" not in content


def test_phase5_fleeting_status_and_recycle(client, monkeypatch):
    tc, kb, vault = client
    fleet = vault / "KB" / "Fleeting" / "2026-06-10-2114-專注.md"
    fleet.write_text(
        "---\ntype: fleeting\ncreated: 2026-06-10T21:14:00\n"
        "via: slack\nstatus: open\n---\n專注是光譜\n",
        encoding="utf-8",
    )
    # stub recycle-bin so the test does not actually shell out / delete
    recycled = {}

    def fake_recycle(p):
        recycled["path"] = Path(p)

    monkeypatch.setattr("shared.discard_service._send_to_recycle_bin", fake_recycle)

    r = tc.post(
        "/kb/api/permanent",
        json={
            "title": "專注是光譜而非開關",
            "body": "專注其實是一個光譜，不是開關。",
            "fleeting_path": "KB/Fleeting/2026-06-10-2114-專注.md",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["phase5"]["fleeting_processed"] is True
    # status flipped to processed BEFORE recycle (written verbatim otherwise)
    assert "status: processed" in fleet.read_text(encoding="utf-8")
    assert recycled["path"].name == "2026-06-10-2114-專注.md"


# ---------------------------------------------------------------------------
# skip / later → state file
# ---------------------------------------------------------------------------


def _state(vault: Path) -> dict:
    p = vault / "KB" / ".centaur" / "daily_review_state.json"
    return json.loads(p.read_text(encoding="utf-8"))


def test_review_skip_updates_state(client):
    tc, _kb, vault = client
    r = tc.post("/kb/api/review/skip", json={"candidate_id": "卡片盒筆記-abc12345"})
    assert r.status_code == 200, r.text
    st = _state(vault)
    assert "卡片盒筆記-abc12345" in st["skipped"]


def test_review_later_updates_state(client):
    tc, _kb, vault = client
    r = tc.post("/kb/api/review/later", json={"candidate_id": "cand-xyz"})
    assert r.status_code == 200, r.text
    st = _state(vault)
    assert "cand-xyz" in st["deferred"]
    # value is today's ISO date (N522 expiry reads this)
    from datetime import date

    assert st["deferred"]["cand-xyz"] == date.today().isoformat()


def test_review_skip_after_later_removes_from_deferred(client):
    tc, _kb, vault = client
    tc.post("/kb/api/review/later", json={"candidate_id": "c1"})
    tc.post("/kb/api/review/skip", json={"candidate_id": "c1"})
    st = _state(vault)
    assert "c1" in st["skipped"]
    assert "c1" not in st["deferred"]


def test_review_state_roundtrips_with_n522_loader(client):
    """State written here must be readable by N522 ``load_review_state``."""
    tc, _kb, vault = client
    tc.post("/kb/api/review/skip", json={"candidate_id": "skipme"})
    tc.post("/kb/api/review/later", json={"candidate_id": "laterme"})

    from agents.robin.daily_review import load_review_state

    state = load_review_state(vault)
    assert "skipme" in state["skipped"]
    assert "laterme" in state["deferred"]


# ---------------------------------------------------------------------------
# N528 卡片畫布模式
# ---------------------------------------------------------------------------


def test_canvas_assets_and_markup_wired(client):
    """畫布模式：template 注入 kb_canvas.{css,js} + 工作桌 / MOC 盒 / 回收盒 markup。"""
    tc, _kb, _v = client
    r = tc.get("/kb/review")
    assert r.status_code == 200
    # 畫布資產（CSP-safe：external，不是 inline）
    assert "/static/kb_canvas.css" in r.text
    assert "/static/kb_canvas.js" in r.text
    # N528⑥：開卡一律進畫布，線性 drawer + 模式切換已移除。
    assert 'id="mode-canvas"' not in r.text
    assert 'id="mode-linear"' not in r.text
    assert 'id="drawer"' not in r.text
    # 工作桌 + 三個落點語意 + 兜底盒 + 拖曳頂層
    assert 'id="kbc-desk"' in r.text
    assert 'id="kbc-field"' in r.text
    assert 'id="kbc-mocbox"' in r.text
    assert 'id="kbc-inbox"' in r.text
    assert 'id="kbc-draglayer"' in r.text


def test_canvas_bundle_exposes_three_layers(client):
    """JSON island 帶 N527 三層相關資料（高 edges / 中 related_pool / 外 related_mocs），
    供 kb_canvas.js 渲染三帶卡片場。"""
    tc, _kb, _v = client
    r = tc.get("/kb/review")
    assert r.status_code == 200
    # 中圈字面卡 + 外圈 MOC 疊卡都在 island 內
    assert "理解是分層次的" in r.text
    assert "學習與刻意練習" in r.text
    assert "related_pool" in r.text
    assert "related_mocs" in r.text


def test_canvas_no_inline_script(client):
    """CSP 紅線：/kb/review 不得有可執行 inline <script>（JSON island 除外）。"""
    import re

    tc, _kb, _v = client
    html = tc.get("/kb/review").text
    for m in re.finditer(r"<script\b([^>]*)>", html, re.IGNORECASE):
        attrs = m.group(1)
        # JSON data island 允許（type="application/json"，非執行）。
        if "application/json" in attrs:
            continue
        # 其餘 <script> 必須是 external（帶 src=），無 inline 邏輯。
        assert "src=" in attrs, f"inline executable <script> found: {m.group(0)}"


def test_canvas_save_writes_refute_edge_author_human(client):
    """畫布拖卡入「反駁」格 → 存入：vault 卡含 ``反駁:: [[…]] — 理由`` + ``author: human``。

    模擬 kb_canvas.js saveCard() 組出的 payload（edge_type=refute）。
    """
    tc, _kb, vault = client
    payload = {
        "title": "意志力要用在與目標對齊的艱難任務",
        "body": "意志力是稀缺資源——花在對齊目標的艱難任務，而非降低摩擦力本身。",
        "edges": [
            {
                "edge_type": "refute",
                "target": "降低摩擦力是高產出的前提",
                "reason": "它把手段當成了目的",
            }
        ],
        "source_refs": [{"literature_path": "", "anchor": "", "raw": "[[理解是分層次的]]"}],
        "candidate_id": "卡片盒筆記-abc12345",
    }
    r = tc.post("/kb/api/permanent", json=payload)
    assert r.status_code == 200, r.text
    assert r.json()["author"] == "human"
    content = (vault / "KB" / "Permanent" / "意志力要用在與目標對齊的艱難任務.md").read_text(
        encoding="utf-8"
    )
    assert "author: human" in content
    assert "反駁:: [[降低摩擦力是高產出的前提]] — 它把手段當成了目的" in content
    # 拖入「來源」格的卡 → raw wikilink source_ref
    assert "[[理解是分層次的]]" in content


def test_canvas_save_empty_reason_blocked_by_backend(client):
    """空理由的 edge：前端阻擋是主防線；後端容忍空理由（reason 可空，line 無 — 尾）。

    這裡驗證後端契約——空理由不致 500，且寫出的 edge line 不帶尾隨「 — 」。
    """
    tc, _kb, vault = client
    payload = {
        "title": "降低摩擦力是高產出的前提",
        "body": "魯曼的高產出來自從不強迫自己。",
        "edges": [{"edge_type": "support", "target": "好系統讓你不需要意志力", "reason": ""}],
    }
    r = tc.post("/kb/api/permanent", json=payload)
    assert r.status_code == 200, r.text
    content = (vault / "KB" / "Permanent" / "降低摩擦力是高產出的前提.md").read_text(
        encoding="utf-8"
    )
    assert "支持:: [[好系統讓你不需要意志力]]" in content
    assert "好系統讓你不需要意志力]] —" not in content  # 無理由 → 無尾隨破折號


def test_canvas_empty_body_422(client):
    """畫布存卡也走同一寫入口——空正文後端兜底擋（前端 toast 為主防線）。"""
    tc, _kb, vault = client
    r = tc.post(
        "/kb/api/permanent",
        json={"title": "畫布空正文", "body": "  ", "candidate_id": "x"},
    )
    assert r.status_code == 422
    assert not (vault / "KB" / "Permanent" / "畫布空正文.md").exists()


# ---------------------------------------------------------------------------
# GET /kb/api/moc/members — 疊卡 lazy load
# ---------------------------------------------------------------------------


def test_moc_members_returns_shape(client):
    """疊卡攤平 lazy-load：回 ``{moc_path, members:[{card_path,title,status}]}``。"""
    tc, _kb, vault = client
    # 建 MOC 檔（人寫分組區 [[卡]] 連結）+ 一張成員永久卡（讀其 status）。
    (vault / "KB" / "MOCs").mkdir(parents=True, exist_ok=True)
    (vault / "KB" / "MOCs" / "學習與刻意練習.md").write_text(
        "---\ntype: moc\n---\n\n## 分組\n- [[刻意練習需要立即回饋]]\n",
        encoding="utf-8",
    )
    (vault / "KB" / "Permanent" / "刻意練習需要立即回饋.md").write_text(
        "---\ntype: permanent\nstatus: evergreen\n---\n正文\n",
        encoding="utf-8",
    )
    r = tc.get("/kb/api/moc/members", params={"moc_path": "KB/MOCs/學習與刻意練習"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["ok"] is True
    members = j["members"]
    assert len(members) == 1
    m = members[0]
    assert m["title"] == "刻意練習需要立即回饋"
    assert m["card_path"] == "KB/Permanent/刻意練習需要立即回饋"
    assert m["status"] == "evergreen"


def test_moc_members_empty_path_422(client):
    tc, _kb, _v = client
    r = tc.get("/kb/api/moc/members", params={"moc_path": "  "})
    assert r.status_code == 422


# ── _compute_bundle 與 dashboard 卡片同源（讀持久化快照、不每次重跑 LLM）─────────


def _today_bundle(cands):
    # review_date 以台北日曆計（與 _compute_bundle 的新舊判斷對齊），
    # 否則 UTC CI 在 16:00–24:00 UTC 跑時會誤判快照過期而 flaky。
    from agents.robin.daily_review import _local_today

    return DailyReviewBundle(
        generated_at="2026-01-01T05:00:00Z",
        review_date=_local_today().isoformat(),
        weekly_sweep=False,
        candidates=cands,
        fleeting=[],
        sweep=[],
        warnings=[],
    )


def test_compute_bundle_reads_persisted_snapshot_no_recompute(vault, monkeypatch):
    import thousand_sunny.routers.kb_review as kb
    from agents.robin.daily_review import save_review_bundle

    save_review_bundle(
        vault,
        _today_bundle([CandidateCard(candidate_id="c1", suggested_title="持久化卡", why="x")]),
    )

    def _boom(**kw):
        raise AssertionError("不該重算——應讀持久化快照")

    monkeypatch.setattr("agents.robin.daily_review.run_daily_review", _boom)
    out = kb._compute_bundle()
    assert [c.candidate_id for c in out.candidates] == ["c1"]


def test_compute_bundle_filters_skipped_and_deferred(vault):
    import thousand_sunny.routers.kb_review as kb
    from agents.robin.daily_review import save_review_bundle, save_review_state

    save_review_bundle(
        vault,
        _today_bundle(
            [
                CandidateCard(candidate_id="keep", suggested_title="留", why="x"),
                CandidateCard(candidate_id="skip", suggested_title="略過", why="x"),
                CandidateCard(candidate_id="later", suggested_title="之後", why="x"),
            ]
        ),
    )
    save_review_state(vault, {"skipped": ["skip"], "deferred": {"later": "2026-06-13"}})
    out = kb._compute_bundle()
    assert [c.candidate_id for c in out.candidates] == ["keep"]


def test_compute_bundle_recomputes_and_persists_when_no_snapshot(vault, monkeypatch):
    import thousand_sunny.routers.kb_review as kb
    from agents.robin.daily_review import load_review_bundle

    fresh = _today_bundle([CandidateCard(candidate_id="new", suggested_title="重算", why="x")])
    monkeypatch.setattr("agents.robin.daily_review.run_daily_review", lambda **kw: fresh)
    assert load_review_bundle(vault) is None
    out = kb._compute_bundle()
    assert [c.candidate_id for c in out.candidates] == ["new"]
    assert load_review_bundle(vault) is not None  # 已持久化 → 卡片從此一致


def test_compute_bundle_monday_reads_snapshot_no_llm(vault, monkeypatch):
    """週一（weekly=True）也先讀今天的快照、不重跑 LLM——修掉開頁卡頓 + 徽章/頁面數字不一致。

    舊行為：weekly 路徑無條件 run_daily_review(weekly=True)，每次開頁都打 LLM。
    """
    import thousand_sunny.routers.kb_review as kb
    from agents.robin.daily_review import save_review_bundle

    save_review_bundle(
        vault,
        _today_bundle([CandidateCard(candidate_id="mon", suggested_title="週一卡", why="x")]),
    )

    def _boom(**kw):
        raise AssertionError("週一不該重跑 LLM——應讀今天的持久化快照")

    monkeypatch.setattr("agents.robin.daily_review.run_daily_review", _boom)
    out = kb._compute_bundle(weekly=True)
    assert [c.candidate_id for c in out.candidates] == ["mon"]


def test_compute_bundle_stale_recompute_forwards_weekly_flag(vault, monkeypatch):
    """快照過期/缺時補算，要把當天 weekly 旗標帶進 run_daily_review（週一補算含週清掃）。"""
    import thousand_sunny.routers.kb_review as kb
    from agents.robin.daily_review import _local_today

    captured: dict = {}

    def _capture(**kw):
        captured.update(kw)
        return DailyReviewBundle(
            generated_at="2026-01-01T05:00:00Z",
            review_date=_local_today().isoformat(),
            weekly_sweep=bool(kw.get("weekly")),
            candidates=[],
            fleeting=[],
            sweep=[],
            warnings=[],
        )

    monkeypatch.setattr("agents.robin.daily_review.run_daily_review", _capture)
    kb._compute_bundle(weekly=True)  # 無快照 → 補算，旗標應透傳
    assert captured.get("weekly") is True
