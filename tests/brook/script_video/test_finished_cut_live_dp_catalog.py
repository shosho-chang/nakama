"""DP 的素材目錄必須在發請求當下重讀，不是登錄那一刻的快照。"""

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agents.brook.script_video.finished_cut_production._engine import (  # noqa: E402
    _live_dp_catalog,
    _RunState,
)


@dataclass(frozen=True)
class _Item:
    reference: str


@dataclass(frozen=True)
class _Catalog:
    rows: tuple[_Item, ...]

    def items(self) -> tuple[_Item, ...]:
        return self.rows


class _Resolver:
    """素材庫：登錄之後又進了新素材，正是 Director 跑完才知道要買的那些。"""

    def __init__(self, catalog: _Catalog) -> None:
        self.catalog = catalog

    def worker_selection_catalog(self) -> _Catalog:
        return self.catalog


def _run(snapshot: _Catalog, resolver: _Resolver | None) -> _RunState:
    return _RunState(
        command=None,  # type: ignore[arg-type]
        view=None,  # type: ignore[arg-type]
        worker_catalog=snapshot,  # type: ignore[arg-type]
        asset_resolver=resolver,  # type: ignore[arg-type]
    )


def test_dp_catalog_is_reread_so_later_acquisitions_are_selectable():
    """登錄後才買的素材必須看得到——否則「重挑」只能在同一批錯的素材裡重挑。"""
    snapshot = _Catalog((_Item("asset-sha256:old"),))
    live = _Catalog((_Item("asset-sha256:old"), _Item("asset-sha256:acquired-later")))

    catalog = _live_dp_catalog(_run(snapshot, _Resolver(live)))

    assert [item.reference for item in catalog.items()] == [
        "asset-sha256:old",
        "asset-sha256:acquired-later",
    ]


def test_without_a_resolver_the_stored_snapshot_still_answers():
    """沒有 resolver 的 run（重播、測試替身）不能因此拿不到目錄。"""
    snapshot = _Catalog((_Item("asset-sha256:old"),))

    assert _live_dp_catalog(_run(snapshot, None)) is snapshot
