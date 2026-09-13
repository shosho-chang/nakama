r"""The one durable record of a reviewable finished cut.

## 為什麼只剩一份

ADR-066 原本有四層：`StagedReleaseCandidate`（未封存）→ `FinishedCutRelease`
（封存）→ `highlights/releases/index/v3/<id>.json`（不可變版本）→
`current.v1.json`（pointer）。再加一份 `GlobalCutoverJournal` 管三支 cut 的原子
切換與回滾。

2026-09-12 實測整台機器：**這條鏈從來沒有跑過一次。** 6 支已經 `review_ready` 的
cut 全部有 `materialization.json`，`transaction_receipt_id` 全是 `null`，整個
`G:\Footages` 與 `E:\nakama\data` 裡一個 release receipt、一個 `current.v1.json`
都沒有。`publish_timeline.canonical_timeline_from_transactions` 要求
`status == "committed"` 才回 timeline 名——所以發布線其實一直查不到名字。

原因不是沒人用，是流程本來就不長這樣：`prepare` 那一刻 Resolve 裡的
canonical timeline 已經被改名成 `__fcp_backup__…`、衍生軌已經鋪上去、preview 已經
轉好。修修看的是那條 timeline，確認之後直接 render。**沒有第二個「上架」時刻**
需要封存、pointer 或跨 cut 的原子切換。

所以 ADR-069：Candidate／Release／pointer 合一為這份 plan record。它記的是
「這個 plan 鋪到了哪一條 timeline、成品是哪兩個檔、內容是哪些 event 與 component」
——發布線要的全部事實，一次讀完。

## 它守住什麼

一份紀錄不等於不檢查。寫入時仍然驗三件事，都便宜：

* 交易身分與狀態（`preview_ready`）——這份紀錄不能指到別人的交易。
* preview 的 duration 是正的有限數，而且**沒有 component 超出片長**——超出代表
  plan 與成品對不上，那是真的會發錯片。
* 兩個成品檔都在 episode 目錄內、非空，bytes 與 sha256 當場量。

讀回來時比寫入時**寬鬆**：退役詞彙（`supporting_title` 之類）要讀得回來，既有
紀錄不能因為詞彙表瘦身就變成讀不到。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, cast

from ._codec import RecordCodec, RecordCodecError
from ._correction import RunEventDiff
from ._digest import measure_file
from ._projection import RELEASE_PROJECTIONS
from ._records import (
    ArtifactView,
    ComponentLane,
    ComponentView,
    CutView,
    EventRecord,
    EventView,
    FinishedCutInspection,
    MaterializationPlan,
    ProbeValue,
    ProjectedComponent,
    ReleaseArtifact,
)

PLAN_RECORD_SCHEMA = "nakama.finished-cut-plan-record.v2"

#: `prepare` 之前的紀錄用這個 schema，payload 裡把事實包在 `candidate` 底下。
#: 讀得回來（見 `_lift_v1_payload`）——修修硬碟上那 6 支就是這個版本。
_LEGACY_RECORD_SCHEMA = "nakama.finished-cut-materialization.v1"

#: plan record 檔名沿用 v1 的名字。改名等於讓既有的 6 份紀錄變成孤兒，
#: 重進入（re-entrancy）會以為那些 run 沒做過而重跑一次 duplicate。
PLAN_RECORD_FILENAME = "materialization.json"

_STAGING_GLOB = "highlights/staging/finished-cut/*/" + PLAN_RECORD_FILENAME

PreviewProbe = Callable[[Path], Mapping[str, object]]


class _TransactionReader(Protocol):
    """Read-only transaction state the record needs."""

    def inspect_transaction(self, transaction_id: str) -> Mapping[str, object]: ...


class PlanRecordError(ValueError):
    """A plan record is missing, malformed, or disagrees with what is on disk."""

    def __init__(self, message: str, *, reason: str = "invalid") -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class PlanTimeline:
    """Which Resolve timeline this plan was laid onto."""

    name: str
    uid: str


@dataclass(frozen=True, slots=True)
class PlanRecord:
    """One reviewable finished cut, as recorded when its preview became readable."""

    plan_id: str
    command_id: str
    run_id: str
    episode_id: str
    cut_id: str
    format: Literal["long"]
    editorial_master_id: str
    winner_id: str
    tight_cut_id: str
    director_acceptance_id: str
    dp_acceptance_id: str
    visual_acceptance_id: str
    timeline: PlanTimeline
    transaction_id: str
    duration_sec: float
    preview: ReleaseArtifact
    subtitle: ReleaseArtifact
    events: tuple[EventRecord, ...]
    components: tuple[ProjectedComponent, ...]
    status: Literal["review_ready"] = "review_ready"
    #: 這一輪 vs 上一輪（ADR-069 階段 6）。算的時機是**鑄出這份紀錄的那一刻**——
    #: 那時 run 的驗收歷史還在手上；等到有人要看的時候才算，就得再去翻 run store，
    #: 而 Bridge 那條讀取路徑刻意沒有那個依賴。
    event_diff: tuple[RunEventDiff, ...] = ()
    #: 拿來比的上一輪是哪一次驗收。第一輪為 None。
    event_diff_previous_acceptance_id: str | None = None
    #: 每一個移動過的 event 位移都相同時的那個常數。見 `_correction._uniform_shift`。
    uniform_shift_sec: float | None = None


def _rehydrate_recorded_component(value: object) -> ProjectedComponent:
    """Read a recorded component, including a projection today's vocabulary retired."""

    if not isinstance(value, dict):
        raise RecordCodecError("recorded component is not an object")
    semantic_kind = str(value.get("semantic_kind", ""))
    implementation_kind = str(value.get("implementation_kind", ""))
    lane = str(value.get("lane", ""))
    # reader 比 writer 寬鬆（含退役詞彙）——那是刻意的，既有紀錄要讀得回來。
    # 名單本體在 `_projection.RELEASE_PROJECTIONS`，這裡不再抄一份。
    if (semantic_kind, implementation_kind, lane) not in RELEASE_PROJECTIONS:
        raise RecordCodecError("recorded component projection kinds are invalid")
    return ProjectedComponent(
        component_id=str(value["component_id"]),
        event_id=str(value["event_id"]),
        semantic_kind=semantic_kind,
        implementation_kind=implementation_kind,
        lane=cast(ComponentLane, lane),
        display=str(value["display"]),
        t0=float(value["t0"]),
        t1=float(value["t1"]),
        asset_ref=cast("str | None", value.get("asset_ref")),
    )


_CODEC = RecordCodec()
_CODEC.register(ProjectedComponent, load=_rehydrate_recorded_component)


class PlanRecordStore:
    """Write, re-read, and inspect the plan records of one episode."""

    def __init__(
        self,
        episode_root: str | Path,
        *,
        transactions: _TransactionReader | None = None,
        preview_probe: PreviewProbe | None = None,
    ) -> None:
        self.episode_root = Path(episode_root).resolve()
        self._transactions = transactions
        self._preview_probe = preview_probe

    # -- write -----------------------------------------------------------
    def stage(
        self,
        plan: MaterializationPlan,
        *,
        editorial_master_id: str,
        winner_id: str,
        tight_cut_id: str,
        transaction_id: str,
        timeline: PlanTimeline,
        preview_path: Path,
        subtitle_path: Path,
        event_diff: tuple[RunEventDiff, ...] = (),
        event_diff_previous_acceptance_id: str | None = None,
        uniform_shift_sec: float | None = None,
    ) -> PlanRecord:
        """Measure the two artifacts and mint the record for this prepared plan."""

        if self._transactions is None or self._preview_probe is None:
            raise PlanRecordError("staging a plan record requires Resolve and probe seams")
        transaction = self._transactions.inspect_transaction(transaction_id)
        if transaction.get("transaction_id") != transaction_id:
            raise PlanRecordError(
                "transaction identity does not match the requested transaction"
            )
        if transaction.get("status") != "preview_ready":
            raise PlanRecordError("a plan record requires a preview_ready transaction")
        probe = self._preview_probe(Path(preview_path))
        duration = probe.get("duration_sec")
        if (
            not isinstance(duration, (int, float))
            or isinstance(duration, bool)
            or not math.isfinite(float(duration))
            or float(duration) <= 0
        ):
            raise PlanRecordError("preview probe requires a positive finite duration_sec")
        if any(component.t1 > float(duration) for component in plan.components):
            raise PlanRecordError("projected component timing exceeds the preview duration")
        return PlanRecord(
            plan_id=plan.plan_id,
            command_id=plan.command_id,
            run_id=plan.run_id,
            episode_id=plan.episode_id,
            cut_id=plan.cut_id,
            format=plan.format,
            editorial_master_id=editorial_master_id,
            winner_id=winner_id,
            tight_cut_id=tight_cut_id,
            director_acceptance_id=plan.director_acceptance_id,
            dp_acceptance_id=plan.dp_acceptance_id,
            visual_acceptance_id=plan.visual_acceptance_id,
            timeline=timeline,
            transaction_id=transaction_id,
            duration_sec=float(duration),
            preview=self._artifact(
                Path(preview_path), duration_sec=float(duration), probe=probe
            ),
            subtitle=self._artifact(Path(subtitle_path)),
            events=plan.events,
            components=plan.components,
            event_diff=event_diff,
            event_diff_previous_acceptance_id=event_diff_previous_acceptance_id,
            uniform_shift_sec=uniform_shift_sec,
        )

    def verify_artifacts(self, record: PlanRecord) -> None:
        """Re-measure both artifacts; a changed byte means the record is stale."""

        for artifact in (record.preview, record.subtitle):
            path = (self.episode_root / artifact.path).resolve()
            try:
                path.relative_to(self.episode_root)
                size, digest = measure_file(path)
            except (ValueError, OSError) as error:
                raise PlanRecordError(
                    f"recorded artifact changed after the plan record: {artifact.path}"
                ) from error
            if size != artifact.bytes or digest != artifact.sha256:
                raise PlanRecordError(
                    f"recorded artifact changed after the plan record: {artifact.path}"
                )

    # -- read ------------------------------------------------------------
    def read(self, path: str | Path) -> PlanRecord:
        return _read_plan_record(Path(path))

    def records(self, episode_id: str) -> tuple[PlanRecord, ...]:
        """This episode's plan records, oldest staging directory first."""

        return tuple(
            record for record in self._all_records() if record.episode_id == episode_id
        )

    def resolve(self, plan_id: str) -> PlanRecord | None:
        return next(
            (record for record in self._all_records() if record.plan_id == plan_id),
            None,
        )

    def _all_records(self) -> tuple[PlanRecord, ...]:
        return tuple(_read_plan_record(path) for path in self._record_paths())

    def inspect(self, episode_id: str) -> FinishedCutInspection:
        """The public review surface: every recorded cut of this episode."""

        paths = self._record_paths()
        if not paths:
            return FinishedCutInspection(
                episode_id=episode_id,
                state="missing",
                error_code="plan_record_missing",
            )
        try:
            records = tuple(_read_plan_record(path) for path in paths)
        except PlanRecordError:
            return FinishedCutInspection(
                episode_id=episode_id,
                state="invalid",
                error_code="plan_record_invalid",
            )
        cuts = tuple(
            plan_record_cut_view(record) for record in records if record.episode_id == episode_id
        )
        if not cuts:
            return FinishedCutInspection(
                episode_id=episode_id,
                state="missing",
                error_code="plan_record_missing",
            )
        return FinishedCutInspection(episode_id=episode_id, state="ready", cuts=cuts)

    def _record_paths(self) -> tuple[Path, ...]:
        return tuple(sorted(self.episode_root.glob(_STAGING_GLOB)))

    # -- internals -------------------------------------------------------
    def _artifact(
        self,
        path: Path,
        *,
        duration_sec: float | None = None,
        probe: Mapping[str, object] | None = None,
    ) -> ReleaseArtifact:
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(self.episode_root)
        except ValueError as error:
            raise PlanRecordError(
                "recorded artifact must stay inside the episode root"
            ) from error
        try:
            size, digest = measure_file(resolved)
        except OSError as error:
            raise PlanRecordError(
                f"recorded artifact is not readable: {relative.as_posix()}"
            ) from error
        if not size:
            raise PlanRecordError(f"recorded artifact is empty: {relative.as_posix()}")
        return ReleaseArtifact(
            path=relative.as_posix(),
            bytes=size,
            sha256=digest,
            duration_sec=duration_sec,
            probe=tuple(
                sorted((str(key), _probe_value(value)) for key, value in (probe or {}).items())
            ),
        )


# -- envelope --------------------------------------------------------------
def write_plan_record(path: Path, record: PlanRecord) -> None:
    """Write the record once, atomically, with its own checksum."""

    payload = _CODEC.dump_record(record)
    envelope = {
        "schema": PLAN_RECORD_SCHEMA,
        "payload_sha256": hashlib.sha256(_canonical_json(payload)).hexdigest(),
        "payload": payload,
    }
    encoded = _canonical_json(envelope) + b"\n"
    staging = _staging_path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = staging.open("xb")
    except FileExistsError as error:
        # 這一份不是我們造的——上一次寫到一半死掉的，或另一支行程正在寫。
        # **不可以清掉**：清掉等於一邊說「有一份沒寫完的紀錄」、一邊把證據刪了，
        # 下一次重跑就安靜地成功了；併發時更糟，刪的是對方正在寫的那個檔。
        raise PlanRecordError(
            "an incomplete plan record is already staged", reason="incomplete"
        ) from error
    except OSError as error:
        raise PlanRecordError("plan record could not be written") from error
    try:
        with handle:
            handle.write(encoded)
            handle.flush()
            # flush 只把 bytes 交給作業系統。這份紀錄是這支 cut 唯一的耐久描述
            # （Candidate／Release 那條鏈已經退役），掉電後剩半截檔＝這個 run 再也
            # 結不了帳，所以要真的落盤，落完再回讀確認。
            os.fsync(handle.fileno())
        os.replace(staging, path)
        if path.read_bytes() != encoded:
            raise PlanRecordError("plan record bytes differ after atomic replace")
    except OSError as error:
        raise PlanRecordError("plan record could not be written") from error
    finally:
        staging.unlink(missing_ok=True)


def read_plan_record_at(path: Path) -> PlanRecord | None:
    """Read the record if it exists; `None` means this plan was never prepared."""

    staging = _staging_path(path)
    if staging.exists():
        raise PlanRecordError("an incomplete plan record exists", reason="incomplete")
    if not path.exists():
        return None
    return _read_plan_record(path)


def _read_plan_record(path: Path) -> PlanRecord:
    try:
        document = json.loads(path.read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PlanRecordError("plan record is unreadable") from error
    if (
        not isinstance(document, dict)
        or set(document) != {"schema", "payload_sha256", "payload"}
        or not isinstance(document.get("payload"), dict)
    ):
        raise PlanRecordError("plan record schema is invalid")
    payload = cast(dict, document["payload"])
    if document.get("payload_sha256") != hashlib.sha256(_canonical_json(payload)).hexdigest():
        raise PlanRecordError("plan record checksum differs")
    schema = document.get("schema")
    if schema == _LEGACY_RECORD_SCHEMA:
        payload = _lift_v1_payload(payload)
    elif schema != PLAN_RECORD_SCHEMA:
        raise PlanRecordError("plan record schema is invalid")
    try:
        return _CODEC.load_record(PlanRecord, payload)
    except RecordCodecError as error:
        raise PlanRecordError(f"plan record fields are invalid: {error}") from error


def _lift_v1_payload(payload: Mapping[str, object]) -> dict:
    """Flatten a pre-ADR-069 journal into a plan record.

    v1 把事實包在 `candidate` 底下，而且沒有記 timeline——那時候 timeline 名要從
    交易紀錄反查（`publish_timeline.canonical_timeline_from_transactions`，它要求
    `status == "committed"`，所以實際上永遠查不到）。既有紀錄照樣讀得回來，
    timeline 只能留空字串——發布線看到空字串就知道要人補，看不到欄位只會當機。
    """

    candidate = payload.get("candidate")
    plan = candidate.get("materialization_plan") if isinstance(candidate, dict) else None
    if not isinstance(candidate, dict) or not isinstance(plan, dict):
        raise PlanRecordError("legacy materialization journal has no candidate plan")
    lifted = {
        key: candidate[key]
        for key in (
            "command_id",
            "run_id",
            "episode_id",
            "cut_id",
            "format",
            "editorial_master_id",
            "winner_id",
            "tight_cut_id",
            "director_acceptance_id",
            "dp_acceptance_id",
            "visual_acceptance_id",
            "preview",
            "subtitle",
        )
        if key in candidate
    }
    lifted["plan_id"] = plan.get("plan_id")
    lifted["duration_sec"] = plan.get("duration_sec")
    lifted["events"] = plan.get("events")
    lifted["components"] = plan.get("components")
    lifted["transaction_id"] = candidate.get("preview_ready_transaction_id")
    lifted["timeline"] = {"name": "", "uid": ""}
    lifted["status"] = "review_ready"
    # v1 沒有 diff（那時候還沒有這個欄位）。欄位自己的預設值接手：空 diff 在
    # 頁面上讀作「這一輪沒有可比的上一輪」，跟第一輪一樣，不是謊。
    return lifted


# -- views -----------------------------------------------------------------
def plan_record_cut_view(record: PlanRecord) -> CutView:
    return CutView(
        plan_id=record.plan_id,
        cut_id=record.cut_id,
        format=record.format,
        timeline=record.timeline.name,
        preview=_artifact_view(record.preview),
        subtitle=_artifact_view(record.subtitle),
        events=tuple(_event_view(event) for event in record.events),
        components=tuple(_component_view(component) for component in record.components),
        event_diff=record.event_diff,
        event_diff_previous_acceptance_id=record.event_diff_previous_acceptance_id,
        uniform_shift_sec=record.uniform_shift_sec,
    )


def _artifact_view(artifact: ReleaseArtifact) -> ArtifactView:
    return ArtifactView(
        reference=artifact.path,
        bytes=artifact.bytes,
        sha256=artifact.sha256,
        duration_sec=artifact.duration_sec,
        probe=artifact.probe,
    )


def _event_view(event: EventRecord) -> EventView:
    return EventView(
        event_id=event.event_id,
        master_cue_ids=event.master_cue_ids,
        text=event.text,
        text_hash=event.text_hash,
        t0=event.t0,
        t1=event.t1,
        section_id=event.section_id,
        intent=event.intent,
        display=event.display,
        semantic_kind=event.semantic_kind,
        implementation_kind=event.implementation_kind,
        lane=event.lane,
        asset_ref=event.asset_ref,
        visual_status=event.visual_status,
        intentional_aroll=event.intentional_aroll,
    )


def _component_view(component: ProjectedComponent) -> ComponentView:
    return ComponentView(
        component_id=component.component_id,
        event_id=component.event_id,
        semantic_kind=component.semantic_kind,
        implementation_kind=component.implementation_kind,
        lane=component.lane,
        display=component.display,
        t0=component.t0,
        t1=component.t1,
        asset_ref=component.asset_ref,
    )


# -- helpers ---------------------------------------------------------------
def _probe_value(value: object) -> ProbeValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise PlanRecordError("preview probe values must be JSON scalars")


def _canonical_json(value: Mapping[str, object]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _staging_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.staging")


__all__ = [
    "PLAN_RECORD_FILENAME",
    "PLAN_RECORD_SCHEMA",
    "PlanRecord",
    "PlanRecordError",
    "PlanRecordStore",
    "plan_record_cut_view",
    "PlanTimeline",
    "PreviewProbe",
    "read_plan_record_at",
    "write_plan_record",
]
