"""Striking-distance keyword filter — GSC raw rows → `StrikingDistanceV1`.

ADR-009 triangulation T6 契約：
    GSC raw rows 必須在 skill 層**先 filter range [10.0, 21.0]** 才建
    `StrikingDistanceV1` 物件；不符合 range 的 row 用 `drop` 處理，
    **絕不**以 try/except ValidationError 當 filter
    （浪費算力 + 錯誤訊號污染 logs / metrics）。

Design：為什麼 filter-first-then-build
--------------------------------------
`StrikingDistanceV1.current_position` 宣告 `confloat(ge=10.0, le=21.0)`。
若直接把整批 GSC rows 塞進 schema，range 外的 row 會 raise
`pydantic.ValidationError` — 這在 SEO 脈絡下**是預期行為（不是 error）**：
GSC 傳回的是整個 site 每個 query × page 的 ranking，大多數 row 本來就不
在 striking-distance band。把常態 drop 當 error，會讓：

- logs 被 ValidationError 灌爆，真正的 bug 訊號被淹沒
- try/except 迴圈比 range check 慢 ~10x（pydantic 驗完整個 model 才 raise）
- 呼叫端要寫 except + retry pattern，耦合成本高

所以：先 cheap `float` range check → 通過才建 pydantic object。

Shape 契約
-----------
GSC row shape（dimensions=["query", "page"]）::

    {
        "keys": [keyword: str, url: str],   # [0]=query, [1]=page URL
        "clicks": int,
        "impressions": int,
        "ctr": float,
        "position": float,
    }

`keys[1]` missing（dimensions=["query"] only）= caller bug，會 raise
`IndexError` — 這是**應該**發生的 crash，因為 T6 契約要求完整 shape。
"""

from __future__ import annotations

from shared.log import get_logger
from shared.schemas.publishing import StrikingDistanceV1

logger = get_logger("nakama.seo_enrich.striking_distance")

# Striking-distance band（ADR-009 §D3 / schema `confloat(ge=10.0, le=21.0)`）。
# 業界慣例 11-20；schema 留 ±1 緩衝吸收 GSC 小數 position。兩端皆 inclusive。
_MIN_POSITION = 10.0
_MAX_POSITION = 21.0


def filter_striking_distance(rows: list[dict]) -> list[StrikingDistanceV1]:
    """Filter GSC raw rows to striking-distance band [10.0, 21.0], inclusive.

    See module docstring for T6 contract and rationale on filter-first-then-build.

    Args:
        rows: GSC API rows with dimensions=["query", "page"]. Each row must have
            `keys[0]` (keyword), `keys[1]` (URL), `impressions`, and `position`.

    Returns:
        List of `StrikingDistanceV1`. Rows outside [10.0, 21.0] are silently
        dropped (not raised). A single `info` log records totals for debugging.

    Raises:
        IndexError: `keys[1]` missing — caller violated T6 dimension contract.
        KeyError: Required row field missing.
        ValidationError: Remaining field violates schema after range check
            (indicates upstream GSC data corruption, not a filter false-positive).
    """
    kept: list[StrikingDistanceV1] = []
    dropped = 0

    for row in rows:
        position = row["position"]
        if not (_MIN_POSITION <= position <= _MAX_POSITION):
            dropped += 1
            continue

        keys = row["keys"]
        kept.append(
            StrikingDistanceV1(
                keyword=keys[0],
                url=keys[1],
                current_position=position,
                impressions_last_28d=row["impressions"],
                suggested_actions=[],  # Phase 2 — generation 邏輯待升級
            )
        )

    logger.info(
        "filter_striking_distance kept=%d dropped=%d total=%d",
        len(kept),
        dropped,
        len(rows),
    )
    return kept
