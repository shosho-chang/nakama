"""素材目錄要在用到的當下重讀，不是登錄那一刻的快照。"""

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from agents.brook.script_video.finished_cut_production._engine import (  # noqa: E402
    _derived_build_failed,
    _live_catalog,
    _retry_with_live_catalog,
    _with_live_catalog,
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


def test_catalog_is_reread_so_later_acquisitions_are_selectable():
    """登錄後才買的素材必須看得到——否則「重挑」只能在同一批錯的素材裡重挑。"""
    snapshot = _Catalog((_Item("asset-sha256:old"),))
    live = _Catalog((_Item("asset-sha256:old"), _Item("asset-sha256:acquired-later")))

    catalog = _live_catalog(_Resolver(live), snapshot)  # type: ignore[arg-type]

    assert [item.reference for item in catalog.items()] == [
        "asset-sha256:old",
        "asset-sha256:acquired-later",
    ]


def test_without_a_resolver_the_stored_snapshot_still_answers():
    """沒有 resolver 的 run（重播、測試替身）不能因此拿不到目錄。"""
    snapshot = _Catalog((_Item("asset-sha256:old"),))

    assert _live_catalog(None, snapshot) is snapshot  # type: ignore[arg-type]


@dataclass(frozen=True)
class _Request:
    """StageRequest 的最小替身——只有這條路徑會動到的欄位。"""

    stage: str
    request_id: str
    attempt: int
    worker_asset_refs: tuple[str, ...]
    worker_catalog_items: tuple[_Item, ...]


def test_dp_retry_carries_the_catalog_as_it_is_now_not_as_it_failed():
    """DP 之所以失敗常常正是因為當時櫃子裡沒有對的素材——重試要看得到補買的那些。

    20260721 punch-L03：登錄時素材庫是空的（DP 還沒跑，沒人知道要買什麼），補買八支
    之後 `retry-failed-dispatch`，packet 的 catalog 仍然是 []，DP 連一個 asset_ref
    都引用不到，而它的契約就是「只能選、不能買」。
    """
    failed = _Request(
        stage="dp",
        request_id="request-old",
        attempt=1,
        worker_asset_refs=(),
        worker_catalog_items=(),
    )
    live = _Catalog((_Item("asset-sha256:acquired-after-the-failure"),))

    retry = _retry_with_live_catalog(failed, "request-new", live)  # type: ignore[arg-type]

    assert retry.request_id == "request-new"
    assert retry.attempt == 2
    assert retry.worker_asset_refs == ("asset-sha256:acquired-after-the-failure",)
    assert retry.worker_catalog_items == live.items()


def test_non_dp_retry_keeps_its_empty_catalog():
    """Director／visual_review 的請求本來就不帶目錄，硬塞會弄壞一致性檢查。"""
    failed = _Request(
        stage="director",
        request_id="request-old",
        attempt=2,
        worker_asset_refs=(),
        worker_catalog_items=(),
    )
    live = _Catalog((_Item("asset-sha256:irrelevant-here"),))

    retry = _retry_with_live_catalog(failed, "request-new", live)  # type: ignore[arg-type]

    assert retry.attempt == 3
    assert retry.worker_asset_refs == ()
    assert retry.worker_catalog_items == ()


@dataclass(frozen=True)
class _Stage:
    stage: str


@dataclass(frozen=True)
class _View:
    derived_asset_request: object
    status: str
    accepted_stages: tuple[_Stage, ...] = ()
    outstanding_request: object = None


def test_a_failed_derived_build_is_not_current_work():
    """建置死了就要讓得開——否則上游造成的失敗沒有任何合法出路。

    20260721 punch-L03：hero 字卡 23 個字撐破 8 秒上限，長度是 Director 寫的、DP
    改不了，而 request_correction 原本用「有 derived_asset_request 且 status 不是
    pending」擋住上游修正——那個條件正好就是建置失敗的條件。
    """
    view = _View(derived_asset_request=object(), status="needs_review")

    assert _derived_build_failed(view)  # type: ignore[arg-type]


def test_a_build_still_in_flight_is_not_treated_as_failed():
    view = _View(derived_asset_request=object(), status="pending")

    assert not _derived_build_failed(view)  # type: ignore[arg-type]


def test_a_build_already_handed_to_visual_review_is_not_failed():
    """已經交出去給 visual_review 的建置是 current work，不該被上游修正推翻。"""
    accepted = _View(
        derived_asset_request=object(),
        status="needs_review",
        accepted_stages=(_Stage("visual_review"),),
    )
    outstanding = _View(
        derived_asset_request=object(),
        status="needs_review",
        outstanding_request=_Stage("visual_review"),
    )

    assert not _derived_build_failed(accepted)  # type: ignore[arg-type]
    assert not _derived_build_failed(outstanding)  # type: ignore[arg-type]


def test_no_build_request_at_all_is_not_a_failure():
    view = _View(derived_asset_request=None, status="needs_review")

    assert not _derived_build_failed(view)  # type: ignore[arg-type]


def test_first_dispatch_also_carries_the_live_catalog():
    """第一次派發也要換目錄——DP 的請求是登錄那一刻鑄的，那時候櫃子必然是空的。

    20260721 value-L02：素材庫已經 52 支，packet 的 catalog 仍是 []。重試那條路先前
    修過了，但第一次派發走 `_advance_existing`，直接把儲存的請求原樣丟出去。
    """
    stored = _Request(
        stage="dp",
        request_id="request-minted-at-registration",
        attempt=1,
        worker_asset_refs=(),
        worker_catalog_items=(),
    )
    live = _Catalog((_Item("asset-sha256:bought-after-registration"),))

    sent = _with_live_catalog(stored, live)  # type: ignore[arg-type]

    assert sent.request_id == "request-minted-at-registration"  # 身分不動
    assert sent.attempt == 1
    assert sent.worker_asset_refs == ("asset-sha256:bought-after-registration",)


def test_non_dp_request_is_returned_untouched():
    stored = _Request(
        stage="visual_review",
        request_id="request-x",
        attempt=1,
        worker_asset_refs=(),
        worker_catalog_items=(),
    )

    assert _with_live_catalog(stored, _Catalog((_Item("a"),))) is stored  # type: ignore[arg-type]
