"""N521 — Literature Note writer (shared/literature_writer.py).

驗收 (task prompt §5)：
- 書 render：章分組、cite 錨點、note 原文一字不差 (用 annotation fixture，不依賴真 vault)
- re-render 兩次 diff 為零、記帳欄保留
- 三路版型 (book / article / video) 各自正確

KB 檢索 (``🔗 KB 相關``) 在測試中 monkeypatch ``search_kb`` 回固定結果，避免依賴
真 kb_index.db / LLM。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import shared.literature_writer as lw
from shared.annotation_store import get_annotation_store
from shared.schemas.annotations import (
    AnnotationSetV3,
    AnnotationV3,
    HighlightV3,
    ReflectionV3,
)

_TS = "2026-05-25T00:00:00Z"


@pytest.fixture
def vault(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    # Freeze dates so re-render byte-equality isn't broken by a clock tick.
    monkeypatch.setattr(lw, "_now_date", lambda: "2026-06-11")
    return tmp_path


@pytest.fixture(autouse=True)
def _stub_kb_search(monkeypatch):
    """KB 檢索回固定結果，pilot 純 FTS5 (D-17)。回傳形狀照 search_kb hits。"""
    import agents.robin.kb_search as kb

    def _fake(query, vault_path, top_k=3, **kwargs):  # noqa: ARG001
        return [{"path": "KB/Wiki/Concepts/deliberate-practice", "relevance_reason": "x"}]

    monkeypatch.setattr(kb, "search_kb", _fake)


# ── fixtures: annotation sets ────────────────────────────────────────────────


def _save(ann_set: AnnotationSetV3) -> None:
    get_annotation_store().save(ann_set)


def _book_set() -> AnnotationSetV3:
    """《卡片盒筆記》風格 book set：兩章、含 highlight / annotation / reflection。"""
    return AnnotationSetV3(
        slug="卡片盒筆記",
        base="books",
        book_id="卡片盒筆記",
        book_version_hash="a" * 64,
        items=[
            HighlightV3(
                cfi="epubcfi(/6/14[ch2]!/4/2/122)",
                text_excerpt="財富階梯更像一種大致的判斷藝術，而非精確的科學。",
                text="財富階梯更像一種大致的判斷藝術，而非精確的科學。",
                book_version_hash="a" * 64,
                created_at=_TS,
                modified_at=_TS,
            ),
            AnnotationV3(
                cfi="epubcfi(/6/14[ch2]!/4/2/116)",
                text_excerpt="到底算哪一階？",
                note="這就是刻意練習的理論。",
                book_version_hash="a" * 64,
                created_at=_TS,
                modified_at=_TS,
            ),
            ReflectionV3(
                chapter_ref="ch2",
                cfi_anchor="epubcfi(/6/14[ch2]!/4/2/200)",
                book_version_hash="a" * 64,
                body="這章讓我重新想了財富分層。",
                created_at=_TS,
                modified_at=_TS,
            ),
            HighlightV3(
                cfi="epubcfi(/6/16[ch3]!/4/2/40)",
                text_excerpt="第三章的劃線。",
                text="第三章的劃線。",
                book_version_hash="a" * 64,
                created_at=_TS,
                modified_at=_TS,
            ),
        ],
    )


def _article_set() -> AnnotationSetV3:
    return AnnotationSetV3(
        slug="sedentary-risk",
        base="inbox",
        source_filename="sedentary-risk.md",
        items=[
            HighlightV3(
                text_excerpt="久坐與全因死亡率的關聯，在校正運動量後仍顯著。",
                text="久坐與全因死亡率的關聯，在校正運動量後仍顯著。",
                created_at=_TS,
                modified_at=_TS,
            ),
            AnnotationV3(
                text_excerpt="每多坐 1 小時，風險增加約 X%。",
                note="想連到久坐獨立風險因子。",
                created_at=_TS,
                modified_at=_TS,
            ),
        ],
    )


def _video_set() -> AnnotationSetV3:
    return AnnotationSetV3(
        slug="youtube_abc123",
        base="inbox",
        items=[
            AnnotationV3(
                cfi="t=750-760",
                text_excerpt="多巴胺的 peak 之後一定伴隨基線回落。",
                note="解釋了為什麼爽完反而更空虛。",
                speaker="Huberman",
                created_at=_TS,
                modified_at=_TS,
            ),
            HighlightV3(
                cfi="t=1685-1690",
                text_excerpt="間歇性給予獎勵會拉高動機。",
                text="間歇性給予獎勵會拉高動機。",
                speaker="Huberman",
                created_at=_TS,
                modified_at=_TS,
            ),
        ],
    )


# ── video title (ADR-046) ───────────────────────────────────────────────────


def _write_watchlist_manifest(vault: Path, video_id: str, **fields) -> None:
    import json

    entry = vault / "Watchlist" / "youtube" / video_id
    entry.mkdir(parents=True, exist_ok=True)
    (entry / "manifest.json").write_text(json.dumps(fields, ensure_ascii=False), encoding="utf-8")


def test_video_literature_title_from_manifest(vault: Path):
    _write_watchlist_manifest(
        vault,
        "abc123",
        channel="Huberman Lab",
        title="Dopamine Nation",
        cast=["Andrew Huberman", "Anna Lembke"],
    )
    md = lw.render_literature_markdown(_video_set(), "video", vault)
    expect = "Huberman Lab｜Dopamine Nation｜Andrew Huberman、Anna Lembke"
    assert f'title: "{expect}"' in md
    assert f"# 文獻筆記：{expect}" in md
    # slug / annotation link stay on the slug (identifier unchanged).
    assert "slug: youtube_abc123" in md
    assert 'annotations: "[[Annotations/youtube_abc123]]"' in md


def test_video_literature_title_fallback_no_manifest(vault: Path):
    # No watchlist manifest → title falls back to slug; render must not break.
    md = lw.render_literature_markdown(_video_set(), "video", vault)
    assert 'title: "youtube_abc123"' in md
    assert "# 文獻筆記：youtube_abc123" in md


# ── book render ──────────────────────────────────────────────────────────────


def test_book_render_chapter_grouping_and_anchors(vault: Path):
    _save(_book_set())
    report = lw.write_literature_note("卡片盒筆記")
    assert report.rendered
    assert report.source_kind == "book"

    p = vault / "KB" / "Literature" / "卡片盒筆記.md"
    assert p.exists()
    text = p.read_text(encoding="utf-8")

    # frontmatter
    assert "type: literature" in text
    assert "source_kind: book" in text
    assert "anchor_type: cfi" in text
    assert "status: digested" in text
    assert 'annotations: "[[Annotations/卡片盒筆記]]"' in text

    # chapter grouping (two chapters) — 無 EPUB TOC → 乾淨的「章節 N」fallback（非 spine/ch 鍵）
    assert "### 章節 1" in text
    assert "### 章節 2" in text

    # cite anchors derived from CFI
    assert "^cfi-6-14-122" in text
    assert "^cfi-6-14-116" in text

    # note verbatim — 原文一字不差
    assert "**note::** 這就是刻意練習的理論。" in text
    # reflection rendered as 章末心得
    assert "**章末心得**：這章讓我重新想了財富分層。" in text
    # deep link back to Reader
    assert "📖 [開回 Reader](/robin/books/卡片盒筆記#cfi=" in text


def test_book_chapter_title_resolved_into_heading(vault: Path, monkeypatch):
    # 有 EPUB TOC 解出標題時，heading 用真章節名取代 spine/ch key
    _save(_book_set())
    monkeypatch.setattr(lw, "_book_chapter_titles", lambda slug: {"ch2": "第二章｜心流"})
    md = lw.render_literature_markdown(_book_set(), "book", vault)
    assert "### 第二章｜心流" in md


def test_book_chapter_title_fallback_when_no_epub(vault: Path):
    # 查無此書 EPUB → {} → render fallback 回乾淨的「章節 N」（不外露 spine/ch 鍵、不中斷）
    assert lw._book_chapter_titles("no-such-book-xyz") == {}
    _save(_book_set())
    md = lw.render_literature_markdown(_book_set(), "book", vault)
    assert "### 章節 1" in md
    assert "spine-" not in md  # 機器鍵不外露


# ── 多段引文 / 多段筆記 同一區塊（修修回報的顯示問題）────────────────────────


def test_bq_multiline_quote_all_in_one_blockquote():
    out = lw._bq("第一段\n\n第二段")
    # 每行都有 > 前綴（含空行 >）→ markdown 視為同一個 blockquote
    assert out == "> 第一段\n>\n> 第二段"


def test_note_block_multiparagraph_joined_with_br():
    out = lw._note_block("筆記第一段\n\n筆記第二段")
    # 多段筆記接成單一段落（<br>），不會跑成獨立段落
    assert out == "**note::** 筆記第一段<br><br>筆記第二段"


def test_multiline_quote_stays_in_blockquote_in_render(vault: Path):
    from shared.schemas.annotations import AnnotationSetV3, HighlightV3

    aset = AnnotationSetV3(
        slug="多段測試",
        base="books",
        book_id="多段測試",
        book_version_hash="b" * 64,
        items=[
            HighlightV3(
                cfi="epubcfi(/6/14[c]!/4/2/1)",
                text_excerpt="x",
                text="引文段一\n\n引文段二",
                book_version_hash="b" * 64,
                created_at=_TS,
                modified_at=_TS,
            ),
        ],
    )
    md = lw.render_literature_markdown(aset, "book", vault)
    # 兩段引文都帶 > 前綴（同一 blockquote），第二段不會變框外普通段落
    assert "> 引文段一" in md
    assert "> 引文段二" in md


def test_book_highlight_text_verbatim(vault: Path):
    _save(_book_set())
    lw.write_literature_note("卡片盒筆記")
    text = (vault / "KB" / "Literature" / "卡片盒筆記.md").read_text(encoding="utf-8")
    assert "> 財富階梯更像一種大致的判斷藝術，而非精確的科學。 ^cfi-6-14-122" in text


# ── article render (^p-N) ────────────────────────────────────────────────────


def test_article_render_paragraph_anchors(vault: Path):
    _save(_article_set())
    report = lw.write_literature_note("sedentary-risk")
    assert report.source_kind == "article"
    text = (vault / "KB" / "Literature" / "sedentary-risk.md").read_text(encoding="utf-8")

    assert "source_kind: article" in text
    assert "anchor_type: excerpt" in text
    assert "^p-1" in text
    assert "^p-2" in text
    assert "**note::** 想連到久坐獨立風險因子。" in text


# ── video render (timeline + speaker + seek) ─────────────────────────────────


def test_video_render_timeline_speaker_seek(vault: Path):
    _save(_video_set())
    report = lw.write_literature_note("youtube_abc123")
    assert report.source_kind == "video"
    text = (vault / "KB" / "Literature" / "youtube_abc123.md").read_text(encoding="utf-8")

    assert "source_kind: video" in text
    assert "anchor_type: timestamp" in text
    # 12:30 = 750s, speaker label
    assert "**[12:30] Huberman**" in text
    assert "▶ [跳到此刻](https://youtu.be/abc123?t=750)" in text
    # second item at 1685s = 28:05
    assert "**[28:05] Huberman**" in text


# ── idempotent re-render (v0.2 §9) ───────────────────────────────────────────


def test_rerender_byte_identical(vault: Path):
    _save(_book_set())
    lw.write_literature_note("卡片盒筆記")
    p = vault / "KB" / "Literature" / "卡片盒筆記.md"
    first = p.read_text(encoding="utf-8")
    lw.write_literature_note("卡片盒筆記")
    second = p.read_text(encoding="utf-8")
    assert first == second, "re-render 必須 byte-identical (idempotent)"


def test_rerender_preserves_ledger_and_bookkeeping(vault: Path):
    _save(_book_set())
    lw.write_literature_note("卡片盒筆記")
    p = vault / "KB" / "Literature" / "卡片盒筆記.md"

    # Simulate Phase 5 善後: human marks 已開卡 in ledger + AI backfills frontmatter.
    text = p.read_text(encoding="utf-8")
    text = text.replace("status: digested", "status: mined").replace(
        "mined_concepts: []",
        'mined_concepts:\n  - "[[Permanent/刻意練習要有立即回饋]]"',
    )
    text = text.replace(
        lw.LEDGER_END,
        "- ✓ 已開卡 [[Permanent/刻意練習要有立即回饋]]\n" + lw.LEDGER_END,
    )
    p.write_text(text, encoding="utf-8")

    # Re-render (e.g. user read more, re-ingested).
    lw.write_literature_note("卡片盒筆記")
    after = p.read_text(encoding="utf-8")

    # Bookkeeping preserved — render 不得倒退 status / mined_concepts.
    assert "status: mined" in after
    assert "[[Permanent/刻意練習要有立即回饋]]" in after
    # Ledger 已開卡 marker preserved verbatim.
    assert "- ✓ 已開卡 [[Permanent/刻意練習要有立即回饋]]" in after


def test_rerender_after_bookkeeping_is_stable(vault: Path):
    """記帳欄被改後，再連render 兩次仍 byte-identical。"""
    _save(_book_set())
    lw.write_literature_note("卡片盒筆記")
    p = vault / "KB" / "Literature" / "卡片盒筆記.md"
    text = p.read_text(encoding="utf-8").replace("status: digested", "status: mined")
    p.write_text(text, encoding="utf-8")

    lw.write_literature_note("卡片盒筆記")
    a = p.read_text(encoding="utf-8")
    lw.write_literature_note("卡片盒筆記")
    b = p.read_text(encoding="utf-8")
    assert a == b


# ── error paths ──────────────────────────────────────────────────────────────


def test_missing_annotations_returns_error(vault: Path):
    report = lw.write_literature_note("does-not-exist")
    assert not report.rendered
    assert report.errors


def test_explicit_source_kind_override(vault: Path):
    _save(_article_set())
    report = lw.write_literature_note("sedentary-risk", source_kind="paper")
    assert report.source_kind == "paper"
    text = (vault / "KB" / "Literature" / "sedentary-risk.md").read_text(encoding="utf-8")
    assert "source_kind: paper" in text


# ── pure render function ─────────────────────────────────────────────────────


def test_render_markdown_pure_no_write(vault: Path):
    md = lw.render_literature_markdown(_book_set(), "book", vault)
    assert lw.RENDER_BEGIN in md
    assert lw.RENDER_END in md
    assert lw.LEDGER_BEGIN in md
    # nothing written to disk by the pure function
    assert not (vault / "KB" / "Literature" / "卡片盒筆記.md").exists()


# ── 🔗 KB 相關：濾掉來源自身頁面 ──────────────────────────────────────────────


def test_is_own_source_path_segment_match():
    slug = "卡片盒筆記"
    # 自身的 Annotations / Wiki Source 子頁 → True
    assert lw._is_own_source_path(f"KB/Annotations/{slug}", slug)
    assert lw._is_own_source_path(f"KB/Wiki/Sources/Books/{slug}/digest", slug)
    # 別的頁面 → False；prefix 撞名（{slug}-延伸）也不算自身
    assert not lw._is_own_source_path("KB/Wiki/Concepts/zettelkasten", slug)
    assert not lw._is_own_source_path(f"KB/Annotations/{slug}-延伸", slug)


def test_kb_related_excludes_own_source_pages(vault: Path, monkeypatch):
    """🔗 KB 相關 濾掉來源自身頁面，只留跨來源關聯（自我命中無價值）。"""
    import agents.robin.kb_search as kb

    slug = "卡片盒筆記"

    def _fake(query, vault_path, top_k=3, **kwargs):  # noqa: ARG001
        # 前兩筆是來源自身頁面（應被濾掉），第三筆才是真正的跨來源關聯。
        return [
            {"path": f"KB/Annotations/{slug}", "relevance_reason": "self"},
            {"path": f"KB/Wiki/Sources/Books/{slug}/digest", "relevance_reason": "self"},
            {"path": "KB/Wiki/Concepts/zettelkasten", "relevance_reason": "real"},
        ]

    monkeypatch.setattr(kb, "search_kb", _fake)

    md = lw.render_literature_markdown(_book_set(), "book", vault)

    assert f"[[KB/Annotations/{slug}]]" not in md
    assert f"[[KB/Wiki/Sources/Books/{slug}/digest]]" not in md
    assert "[[KB/Wiki/Concepts/zettelkasten]]" in md
