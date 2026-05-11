"""E2E integration test: 5-page real vault → index → query → assert hits.

Uses real model2vec (potion-base-8M) — first run downloads ~25 MB model;
subsequent runs use the HuggingFace cache.  The test is tagged `integration`
but NOT marked as anything that would skip it in standard CI.

No mocks: real FTS5 + real vec0 + real model2vec embeddings.
"""

from __future__ import annotations

import pytest

from shared.kb_hybrid_search import make_conn
from shared.kb_indexer import index_vault

# ---------------------------------------------------------------------------
# Fixture vault (5 pages: mix Concept/Source/Entity)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def vault_5(tmp_path_factory):
    """5-page vault — one per unique topic so relevance is easy to verify."""
    tmp = tmp_path_factory.mktemp("vault5")
    wiki = tmp / "KB" / "Wiki"

    (wiki / "Concepts").mkdir(parents=True)
    (wiki / "Sources").mkdir(parents=True)
    (wiki / "Entities").mkdir(parents=True)

    pages = [
        (
            "Concepts",
            "overtraining",
            "過度訓練",
            (
                "## 定義\n"
                "過度訓練（Overtraining Syndrome）是指運動量超過身體恢復能力的狀態，"
                "導致表現下降與慢性疲勞。\n\n"
                "## 症狀\n"
                "常見症狀包括持續疲勞、情緒低落、睡眠障礙和免疫力下降。"
            ),
        ),
        (
            "Concepts",
            "sleep-quality",
            "睡眠品質",
            (
                "## 定義\n"
                "睡眠品質指睡眠的整體效果，包含入睡時間、連續性和深眠比例。\n\n"
                "## 改善方法\n"
                "規律作息、避免藍光、限制咖啡因攝入有助改善睡眠品質。"
            ),
        ),
        (
            "Sources",
            "why-we-sleep",
            "Why We Sleep",
            (
                "## 摘要\n"
                "Matthew Walker 的《Why We Sleep》探討睡眠科學，"
                "說明睡眠不足對認知、情緒和壽命的深遠影響。\n\n"
                "## 關鍵論點\n"
                "每晚八小時睡眠是最佳目標；慢波睡眠與記憶鞏固直接相關。"
            ),
        ),
        (
            "Sources",
            "zone2-training",
            "Zone 2 Training Research",
            (
                "## 摘要\n"
                "Zone 2 訓練（低強度有氧）能提升粒線體密度和脂肪氧化效率。\n\n"
                "## 方法\n"
                "受試者以最大心率 60-70% 進行每週 150 分鐘有氧訓練，持續 12 週。"
            ),
        ),
        (
            "Entities",
            "matt-walker",
            "Matt Walker",
            (
                "## 背景\n"
                "Matt Walker 是加州大學柏克萊分校神經科學家，"
                "專攻睡眠科學，著有《Why We Sleep》。\n\n"
                "## 影響力\n"
                "其 TED Talk「Sleep is your superpower」累積超過 2000 萬次觀看。"
            ),
        ),
    ]

    for subdir, stem, title, body in pages:
        path = wiki / subdir / f"{stem}.md"
        path.write_text(f"---\ntitle: {title}\n---\n{body}", encoding="utf-8")

    return tmp


# ---------------------------------------------------------------------------
# Integration test
# ---------------------------------------------------------------------------


def test_e2e_index_and_query(vault_5, monkeypatch):
    """Full pipeline: index 5 pages → query → top-k contains expected slugs."""
    # Pin to potion (256-d) so this E2E test doesn't pull BGE-M3 (~2.3 GB).
    monkeypatch.setenv("NAKAMA_EMBED_BACKEND", "potion")
    import importlib

    from shared import kb_embedder as _kb_embedder

    importlib.reload(_kb_embedder)

    conn = make_conn(dim=256)
    stats = index_vault(vault_5, conn)

    assert stats.files_indexed == 5
    assert stats.chunks_added > 0

    # Query 1: sleep-related query should surface sleep-quality and why-we-sleep
    from shared.kb_hybrid_search import search

    hits = search("sleep quality improvement", top_k=5, db=conn)
    assert hits, "Expected at least one hit for 'sleep quality improvement'"
    hit_paths = [h.path for h in hits]
    # At least one of the sleep-related pages should appear
    sleep_pages = {"KB/Wiki/Concepts/sleep-quality", "KB/Wiki/Sources/why-we-sleep"}
    assert any(p in sleep_pages for p in hit_paths), (
        f"Expected a sleep page in results; got {hit_paths}"
    )

    # Query 2: exercise/training query should surface zone2-training or overtraining
    hits2 = search("aerobic training performance", top_k=5, db=conn)
    assert hits2, "Expected at least one hit for 'aerobic training'"
    hit_paths2 = [h.path for h in hits2]
    exercise_pages = {
        "KB/Wiki/Sources/zone2-training",
        "KB/Wiki/Concepts/overtraining",
    }
    assert any(p in exercise_pages for p in hit_paths2), (
        f"Expected an exercise page in results; got {hit_paths2}"
    )

    # Verify SearchHit schema completeness
    for h in hits:
        assert h.path.startswith("KB/Wiki/")
        assert isinstance(h.rrf_score, float)
        assert h.rrf_score > 0
        assert isinstance(h.lane_ranks, dict)
        assert h.chunk_text  # non-empty
