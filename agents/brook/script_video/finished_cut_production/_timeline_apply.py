"""Format-neutral projection of typed plans into pre-rendered Timeline placements."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from . import _force
from ._brand_badge import BRAND_BADGE_SLUG_SECONDS
from ._projection import _MINTABLE_PROJECTION_COMBINATIONS, LANE_TRACKS
from ._records import ComponentLane, MaterializationPlan

#: 鋪上 timeline 的那一刻，看的是「這張卡鋪不鋪得出來」——`VOCABULARY` 描述得出
#: track、版位與 renderer 就鋪得出來，含退役的。退役擋的是新提案，不是既有的卡
#: 再鋪一次；那道鎖在 worker 提案端（`_engine._ALLOWED_PROJECTION`）。
_ALLOWED_PROJECTIONS = _MINTABLE_PROJECTION_COMBINATIONS

#: 品牌 badge 的投影三元組。badge 不是 component（沒有 event、沒有 Active Store
#: reference），但它要鋪上 timeline，所以在 `VOCABULARY` 裡有自己的 lane 與 track。
BRAND_BADGE_SEMANTIC_KIND = "brand_badge"
BRAND_BADGE_IMPLEMENTATION_KIND = "brand_badge"
BRAND_BADGE_LANE: ComponentLane = "brand_badge"

#: badge 素材的副檔名。定長預合成、含 alpha，所以是 ProRes .mov。
BRAND_BADGE_MEDIA_SUFFIX = ".mov"

#: badge 素材放在每集資料夾的哪裡（`<episode>/assets/broll/<slug>.mov`）。
BRAND_BADGE_ASSET_DIRNAMES = ("assets", "broll")

#: 實際片長與 slug 宣告秒數的容忍量：一格（30fps）。
#:
#: 不對稱是刻意的——**不准比宣告的短**（差一格 `_resolve_fusion.append_pre_rendered`
#: 就會在 Resolve 交易中間丟 "media is shorter than its placement"，那時已經動過
#: timeline 了），最多可以長一格（容器與串流 duration 的取整差）。
BRAND_BADGE_DURATION_TOLERANCE_SEC = 1.0 / 30.0

_BRAND_BADGE_SECONDS_BY_SLUG: dict[str, float] = dict(BRAND_BADGE_SLUG_SECONDS)


class TimelineApplyError(ValueError):
    """A typed plan cannot be projected into exact derived Timeline lanes."""


def _reject(message: str, *, gate: str) -> None:
    """擋下這道門——除非 `--force` 開著，那就記一筆警告並讓路。

    仍然直接 `raise` 的地方，是沒有那個東西就走不下去的事：素材檔不在、
    catalog 沒有那一筆、投影詞彙表裡沒有那個 lane（`_resolve_fusion` 下一步就要拿
    `_LANE_TRACKS[lane]` 查軌道）。
    """

    if _force.let_pass(gate, message):
        return
    raise TimelineApplyError(message)


@dataclass(frozen=True, slots=True)
class PreRenderedAsset:
    reference: str
    path: Path

    def __post_init__(self) -> None:
        if not self.reference.strip():
            raise TimelineApplyError("pre-rendered asset reference is empty")
        object.__setattr__(self, "path", Path(self.path))


class PreRenderedAssetCatalog:
    """Immutable exact-reference catalog used only for mechanical application."""

    def __init__(self, assets: Iterable[PreRenderedAsset]) -> None:
        items = tuple(assets)
        if len({asset.reference for asset in items}) != len(items):
            raise TimelineApplyError("pre-rendered asset reference is ambiguous")
        self._by_reference = {asset.reference: asset for asset in items}

    def resolve(self, reference: str) -> Path:
        try:
            asset = self._by_reference[reference]
        except KeyError as exc:
            raise TimelineApplyError("typed component asset is not in the exact catalog") from exc
        try:
            path = asset.path.resolve(strict=True)
        except OSError as exc:
            raise TimelineApplyError("pre-rendered asset is missing") from exc
        if not path.is_file():
            raise TimelineApplyError("pre-rendered asset is not a regular file")
        return path


def brand_badge_root(episode_root: Path | str) -> Path:
    """這一集的 badge 素材目錄。`_materialization` 與 `_composition` 都讀這一份。"""
    return Path(episode_root).joinpath(*BRAND_BADGE_ASSET_DIRNAMES)


class BrandBadgeAssetCatalog:
    """Resolve brand badge slugs against one episode's own brand assets.

    badge **不在 Active Store**：它不是 worker 取得的素材，也不是 core 渲染出來的
    產物，而是每集資料夾裡跨集 byte 相同的品牌資產，所以走不了 component 那條
    content-addressed（`asset-sha256:…`）路徑（`_brand_badge` 模組 docstring）。

    驗檔在這裡做，因為 badge 是**定長預合成**：fade in／out 烘在檔案裡。檔案長度跟
    slug 宣告的秒數對不上，就代表資料夾裡那支不是它宣稱的那一支——照鋪下去要嘛淡出
    被切掉，要嘛死在 Resolve 交易中間。
    """

    def __init__(
        self,
        root: Path | str,
        *,
        duration_probe: Callable[[Path], float],
    ) -> None:
        self._root = Path(root)
        self._duration_probe = duration_probe
        # 一支片有 6 段 badge，但只有兩三支素材，而 preflight 與 apply 又各投影一次。
        # 檔案在不在**每次都重驗**（那是這道門的意義），量到的片長記住就好——生產線
        # 的 probe 會順帶跑一次完整解碼，一支 ProRes 重跑十幾次只是白等。
        self._durations: dict[Path, float] = {}

    def resolve(self, slug: str) -> Path:
        seconds = _BRAND_BADGE_SECONDS_BY_SLUG.get(slug)
        if seconds is None:
            raise TimelineApplyError(f"brand badge slug is not a declared asset: {slug!r}")
        candidate = self._root / f"{slug}{BRAND_BADGE_MEDIA_SUFFIX}"
        try:
            path = candidate.resolve(strict=True)
        except OSError as exc:
            raise TimelineApplyError(f"brand badge asset is missing: {candidate}") from exc
        if not path.is_file():
            raise TimelineApplyError(f"brand badge asset is not a regular file: {path}")
        duration = self._durations.get(path)
        if duration is None:
            try:
                duration = float(self._duration_probe(path))
            except TimelineApplyError:
                raise
            except Exception as exc:
                raise TimelineApplyError(
                    f"brand badge asset duration is unreadable: {path}"
                ) from exc
            self._durations[path] = duration
        if (
            not math.isfinite(duration)
            or duration < seconds - 1e-6
            or duration > seconds + BRAND_BADGE_DURATION_TOLERANCE_SEC
        ):
            _reject(
                f"brand badge asset is not its declared length: {path} runs {duration}s, "
                f"{slug!r} declares {seconds}s",
                gate="brand_badge_asset_unavailable",
            )
        return path


@dataclass(frozen=True, slots=True)
class TimelinePlacement:
    """One clip to append to one derived lane.

    品牌 badge 也用這個型別——它就是「一段素材鋪在一條衍生軌上」，跟 component 在
    Resolve 那一端做的事完全一樣（`_resolve_fusion.append_pre_rendered` 只讀
    `source_path` / `lane` / `implementation_kind` / `t0` / `t1`）。差別在身分：badge
    沒有 event，`component_id` 放的是 overlay id（`badge:opening`），`event_id` 是
    空字串。`is_brand_badge` 讓下游不必去比對字串前綴。
    """

    component_id: str
    event_id: str
    semantic_kind: str
    implementation_kind: str
    lane: ComponentLane
    display: str
    t0: float
    t1: float
    source_path: Path

    @property
    def is_brand_badge(self) -> bool:
        return self.implementation_kind == BRAND_BADGE_IMPLEMENTATION_KIND


@dataclass(frozen=True, slots=True)
class TimelineApplication:
    plan_id: str
    episode_id: str
    cut_id: str
    placements: tuple[TimelinePlacement, ...]


def project_timeline_application(
    plan: MaterializationPlan,
    assets: PreRenderedAssetCatalog,
    *,
    brand_badge_assets: BrandBadgeAssetCatalog | None = None,
) -> TimelineApplication:
    """Resolve every typed component before exposing any Timeline mutation input."""

    event_ids = {event.event_id for event in plan.events}
    component_ids = [component.component_id for component in plan.components]
    if len(component_ids) != len(set(component_ids)):
        _reject(
            "typed plan contains duplicate component identities",
            gate="materialization_plan_invalid",
        )
    placements: list[TimelinePlacement] = []
    for component in plan.components:
        projection = (
            component.semantic_kind,
            component.implementation_kind,
            component.lane,
        )
        if projection not in _ALLOWED_PROJECTIONS:
            raise TimelineApplyError("typed component classification is not mechanically valid")
        if component.event_id not in event_ids:
            _reject(
                "typed component event is not in the materialization plan",
                gate="materialization_plan_invalid",
            )
        if not component.asset_ref:
            raise TimelineApplyError("typed component has no final materialized asset")
        if (
            not math.isfinite(component.t0)
            or not math.isfinite(component.t1)
            or component.t0 < 0
            or component.t0 >= component.t1
        ):
            _reject(
                "typed component time range is invalid",
                gate="materialization_plan_invalid",
            )
        placements.append(
            TimelinePlacement(
                component_id=component.component_id,
                event_id=component.event_id,
                semantic_kind=component.semantic_kind,
                implementation_kind=component.implementation_kind,
                lane=component.lane,
                display=component.display,
                t0=component.t0,
                t1=component.t1,
                source_path=assets.resolve(component.asset_ref),
            )
        )
    placements.extend(_project_brand_badges(plan, brand_badge_assets))
    return TimelineApplication(
        plan_id=plan.plan_id,
        episode_id=plan.episode_id,
        cut_id=plan.cut_id,
        placements=tuple(placements),
    )


def _project_brand_badges(
    plan: MaterializationPlan,
    catalog: BrandBadgeAssetCatalog | None,
) -> tuple[TimelinePlacement, ...]:
    """Project the rule-derived brand badge overlays into the same lane mechanics.

    2026-09-09 的 regression 就是這一段不存在：落點算得出來、沒有人鋪上去，於是
    每張滿版轉場卡之後的品牌動畫整批消失。`plan.brand_badge_overlays` 有東西卻沒有
    素材目錄，是接線錯誤，不是「這一支沒有 badge」——所以這裡 fail loud，不靜默跳過。
    """

    overlays = plan.brand_badge_overlays
    if not overlays:
        return ()
    if catalog is None:
        raise TimelineApplyError(
            "typed plan carries brand badge overlays but no brand badge asset catalog"
        )
    expected_track = LANE_TRACKS[BRAND_BADGE_LANE]
    placements: list[TimelinePlacement] = []
    previous_end: float | None = None
    for overlay in sorted(overlays, key=lambda item: item.t0):
        if overlay.track_index != expected_track:
            # 落點推導與視覺詞彙表對 badge 的 track 有兩種說法時，這裡就是那個矛盾
            # 落地成畫面之前的最後一關。
            _reject(
                f"brand badge overlay {overlay.overlay_id!r} declares track "
                f"{overlay.track_index}, the vocabulary lane is track {expected_track}",
                gate="brand_badge_overlay_invalid",
            )
        if (
            not math.isfinite(overlay.t0)
            or not math.isfinite(overlay.t1)
            or overlay.t0 < 0
            or overlay.t0 >= overlay.t1
        ):
            _reject(
                f"brand badge overlay {overlay.overlay_id!r} time range is invalid",
                gate="brand_badge_overlay_invalid",
            )
        if previous_end is not None and overlay.t0 < previous_end - 1e-6:
            # 同一條軌上疊兩段——Resolve 的 append 會擠掉其中一段，而那要等到成品
            # render 出來才看得見。
            _reject(
                f"brand badge overlay {overlay.overlay_id!r} overlaps the previous overlay",
                gate="brand_badge_overlay_invalid",
            )
        previous_end = overlay.t1
        placements.append(
            TimelinePlacement(
                component_id=overlay.overlay_id,
                # badge 不是任何 event 的實現——留空，不要借一個 component 的 event。
                event_id="",
                semantic_kind=BRAND_BADGE_SEMANTIC_KIND,
                implementation_kind=BRAND_BADGE_IMPLEMENTATION_KIND,
                lane=BRAND_BADGE_LANE,
                display=overlay.slug,
                t0=overlay.t0,
                t1=overlay.t1,
                source_path=catalog.resolve(overlay.slug),
            )
        )
    return tuple(placements)
