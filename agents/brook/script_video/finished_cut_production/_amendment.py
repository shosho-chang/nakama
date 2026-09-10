"""Amendment：套在**已封存 Release** 上的機械式、非語意變換。

ADR-066 定義了兩種改法：`request_correction`（`MaterializationPlan` 生出來之前）與
`request_revision`（對著確切的 current Release，會鑄新 run、重派語意 worker）。
兩者都接不住第三種需求——**沒有語意內容的改動**：

- 把某個 component 收掉，改成 intentional A-roll
- 把某個 event 的素材換掉、重新 render

它們沿用 base Release 的**整條 `AcceptedStage` 鏈**，只替換 `MaterializationPlan`。
走 `request_revision` 等於為了一個零語意的改動重派一輪語意工作。

**為什麼這件事值得做**：長片的 timeline 只在物化之後才存在，而 `request_correction`
在 plan 生出來的那一刻就關閉了——也就是說**等修修看得到成品，修改窗口已經關了**。
他 2026-09-10 的原話是「我希望長片也能快速改」。

本模組只負責**算出新的 plan**：純函式，不碰 Resolve、不碰 current pointer、不寫檔。
驅動那條交易鏈（prepare → commit → seal → pointer-last）是呼叫端的事。

歷史相容：`amendments/operations/` 底下那兩支釘死的腳本是這段邏輯的來源。
`tests/brook/script_video/test_finished_cut_amendment.py` 會拿同一份 release 同時餵給
它們與本模組，斷言鑄出來的 plan **完全相同**——那是這次一般化的驗收標準
（ADR-066 說的 plan equality，不是 preview byte equality）。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace

from ._records import (
    FinishedCutRelease,
    MaterializationPlan,
    _mint_materialization_plan,
    _mint_projected_component,
)


class AmendmentError(ValueError):
    """這個 amendment 算不出來——目標對不上 base Release。"""


@dataclass(frozen=True, slots=True)
class SuppressComponents:
    """把這些 event 的 component 收掉，改成 intentional A-roll。"""

    event_ids: tuple[str, ...]

    kind = "suppress_components"
    #: 新 amendment 的預設身分。歷史重放時由呼叫端覆寫成當初那一支腳本用的值。
    default_label = "suppress_components"
    default_plan_prefix = "plan-amendment-suppress-"


@dataclass(frozen=True, slots=True)
class ReplaceComponentAssets:
    """把這些 event 的 component 換素材；events 與版位完全不動。"""

    replacements: Mapping[str, str]  # event_id → asset_ref

    kind = "replace_component_assets"
    default_label = "replace_component_assets"
    default_plan_prefix = "plan-amendment-assets-"


AmendmentOperation = SuppressComponents | ReplaceComponentAssets


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _targets(release: FinishedCutRelease, event_ids: tuple[str, ...]) -> tuple[str, ...]:
    """驗目標存在且不重複；回傳去重後的原始順序。

    錯誤訊息要指名**哪一個** event 對不上——「target set differs」讀的人沒辦法從
    三個自由字串裡看出是哪一個 lane 或哪一支 component。
    """
    if not event_ids:
        raise AmendmentError("amendment 至少要指定一個 event_id")
    seen: set[str] = set()
    ordered: list[str] = []
    for event_id in event_ids:
        if event_id in seen:
            raise AmendmentError(f"event_id 重複：{event_id}")
        seen.add(event_id)
        ordered.append(event_id)
    present = {component.event_id for component in release.components}
    missing = [event_id for event_id in ordered if event_id not in present]
    if missing:
        raise AmendmentError(
            f"這些 event 在 base Release {release.release_id} 裡沒有 component："
            f"{missing}（它有的是 {sorted(present)}）"
        )
    return tuple(ordered)


def suppression_plan(
    release: FinishedCutRelease,
    event_ids: tuple[str, ...],
    *,
    label: str = SuppressComponents.default_label,
    plan_prefix: str = SuppressComponents.default_plan_prefix,
) -> MaterializationPlan:
    targets = _targets(release, event_ids)
    target_set = set(targets)
    suppressed = tuple(
        component for component in release.components if component.event_id in target_set
    )
    retained = tuple(
        component for component in release.components if component.event_id not in target_set
    )
    if len({component.component_id for component in retained}) != len(retained):
        raise AmendmentError("保留下來的 component 身分有重複，無法鑄 plan")
    events = tuple(
        replace(
            event,
            semantic_kind="intentional_aroll",
            implementation_kind="intentional_aroll",
            lane=None,
            asset_ref=None,
            intentional_aroll=True,
            visual_placement=None,
        )
        if event.event_id in target_set
        else event
        for event in release.events
    )
    identity = {
        "operation": label,
        "base_release_id": release.release_id,
        "base_plan_id": release.materialization_plan_id,
        "suppressed_component_ids": sorted(component.component_id for component in suppressed),
        "retained_components": [asdict(component) for component in retained],
    }
    plan_id = f"{plan_prefix}{hashlib.sha256(_canonical_json(identity)).hexdigest()[:24]}"
    return _plan(release, plan_id=plan_id, events=events, components=retained)


def asset_replacement_plan(
    release: FinishedCutRelease,
    replacements: Mapping[str, str],
    *,
    label: str = ReplaceComponentAssets.default_label,
    plan_prefix: str = ReplaceComponentAssets.default_plan_prefix,
) -> MaterializationPlan:
    _targets(release, tuple(replacements))
    components = tuple(
        _mint_projected_component(
            component_id=component.component_id,
            event_id=component.event_id,
            semantic_kind=component.semantic_kind,
            implementation_kind=component.implementation_kind,
            lane=component.lane,
            display=component.display,
            t0=component.t0,
            t1=component.t1,
            asset_ref=replacements[component.event_id],
        )
        if component.event_id in replacements
        else component
        for component in release.components
    )
    identity = {
        "operation": label,
        "base_release_id": release.release_id,
        "base_plan_id": release.materialization_plan_id,
        "replacements": sorted(replacements.items()),
        "components": [asdict(component) for component in components],
    }
    plan_id = f"{plan_prefix}{hashlib.sha256(_canonical_json(identity)).hexdigest()[:24]}"
    return _plan(release, plan_id=plan_id, events=release.events, components=components)


def amendment_plan(
    release: FinishedCutRelease,
    operation: AmendmentOperation,
    *,
    label: str | None = None,
    plan_prefix: str | None = None,
) -> MaterializationPlan:
    """鑄出這個 amendment 的新 plan。**語意權威一個位元都不動。**"""
    kwargs = {
        "label": label or operation.default_label,
        "plan_prefix": plan_prefix or operation.default_plan_prefix,
    }
    if isinstance(operation, SuppressComponents):
        return suppression_plan(release, operation.event_ids, **kwargs)
    if isinstance(operation, ReplaceComponentAssets):
        return asset_replacement_plan(release, operation.replacements, **kwargs)
    raise AmendmentError(f"不認得的 amendment operation：{type(operation).__name__}")


def _plan(release, *, plan_id, events, components) -> MaterializationPlan:
    """沿用 base Release 的整條 acceptance 鏈——amendment 不鑄造語意權威。"""
    return _mint_materialization_plan(
        plan_id=plan_id,
        run_id=release.run_id,
        command_id=release.command_id,
        episode_id=release.episode_id,
        cut_id=release.cut_id,
        format=release.format,
        director_acceptance_id=release.director_acceptance_id,
        dp_acceptance_id=release.dp_acceptance_id,
        visual_acceptance_id=release.visual_acceptance_id,
        events=events,
        components=components,
    )
