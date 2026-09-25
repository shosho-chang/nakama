"""ADR-070 D5（S2a，issue #1321）：``GET /api/llm-lane`` — 唯讀 lane 狀態。

桌機透過這支端點讀 VPS 的權威 lane 狀態（``shared.llm_lane.get_dispatch_state``
的快取來源，見該模組 docstring）。純 JSON、無 HTML 頁面；沒有寫入端點——手動覆寫
一律走 VPS 本機的 ``python -m shared.llm_lane`` CLI，不透過 Bridge（S2a 邊界：
不做 Bridge 決策頁的 UI，見 issue #1321）。
"""

from __future__ import annotations

import dataclasses

from fastapi import APIRouter, Depends

from shared import llm_lane
from thousand_sunny.auth import require_auth_or_key

router = APIRouter(tags=["llm-lane"])


@router.get("/api/llm-lane", dependencies=[Depends(require_auth_or_key)])
async def get_llm_lane_state() -> dict:
    return dataclasses.asdict(llm_lane.get_state())
