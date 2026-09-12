"""Private, fail-closed coordination from an exact plan to a recorded preview."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal, Protocol

from ._assets import AssetContractError, AssetKind, AssetResolver, ResolvedAsset
from ._commands import ApprovedCutCommand, _is_authoritative_approved_cut
from ._context import EditorialCutContext
from ._plan_record import (
    PLAN_RECORD_FILENAME,
    PlanRecord,
    PlanRecordError,
    PlanRecordStore,
    PlanTimeline,
    read_plan_record_at,
    write_plan_record,
)
from ._records import MaterializationPlan
from ._resolve import (
    ResolveTransaction,
    ResolveTransactionError,
    ResolveTransactionManager,
    TimelineIdentity,
    TimelineSnapshot,
)
from ._resolve_davinci import ResolveTimelineState


class MaterializationError(ValueError):
    """An exact Finished Cut plan cannot safely reach Resolve preview_ready."""

    def __init__(self, message: str, *, reason_code: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class _ProductionRunReader(Protocol):
    def load_run(self, command_id: str) -> object | None: ...


@dataclass(frozen=True, slots=True)
class CanonicalTimelineInspection:
    """UID-bound, read-only base facts captured before the first mutation."""

    episode_id: str
    cut_id: str
    canonical: TimelineIdentity
    editorial_master_content_hash: str
    editorial_master_media_sha256: str
    timeline_frame_rate: float
    editorial_master_frame_rate: float
    editorial_master_duration_sec: float
    state: ResolveTimelineState
    baseline: TimelineSnapshot


@dataclass(frozen=True, slots=True)
class MaterializationPreparation:
    """The result of one exact preview-ready preparation, plus its record."""

    command_id: str
    run_id: str
    plan_id: str
    status: Literal["preview_ready"]
    transaction_id: str
    subtitle_sha256: str
    record: PlanRecord


class _CanonicalTimelineAuthority(Protocol):
    def inspect(
        self,
        *,
        episode_id: str,
        cut_id: str,
        editorial_master_content_hash: str,
    ) -> tuple[CanonicalTimelineInspection, ...]: ...


class MaterializationCoordinator:
    """Keep all read-only preflight ahead of the first Timeline mutation."""

    def __init__(
        self,
        *,
        run_store: _ProductionRunReader,
        canonical_authority: _CanonicalTimelineAuthority,
        assets: AssetResolver,
        transactions: ResolveTransactionManager,
        records: PlanRecordStore,
        episode_root: Path,
    ) -> None:
        self._run_store = run_store
        self._canonical_authority = canonical_authority
        self._assets = assets
        self._transactions = transactions
        self._records = records
        self._episode_root = Path(episode_root)

    def prepare(self, command_id: str) -> MaterializationPreparation:
        stored = self._run_store.load_run(command_id)
        if stored is None:
            raise MaterializationError(
                "ProductionRun is missing",
                reason_code="production_run_missing",
            )
        view = stored.view
        if view.status != "review_ready":
            raise MaterializationError(
                "ProductionRun is not review_ready",
                reason_code="production_run_not_review_ready",
            )
        if view.materialization_plan is None:
            raise MaterializationError(
                "MaterializationPlan is missing",
                reason_code="materialization_plan_missing",
            )
        plan = view.materialization_plan
        context = getattr(view, "editorial_context", None)
        command = stored.command
        if not isinstance(plan, MaterializationPlan) or not isinstance(
            context, EditorialCutContext
        ):
            raise MaterializationError(
                "Materialization authority is not a typed current plan and context",
                reason_code="authority_chain_mismatch",
            )
        if (
            not isinstance(command, ApprovedCutCommand)
            or not _is_authoritative_approved_cut(command, command_id)
            or re.fullmatch(r"[0-9a-f]{64}", command.editorial_master_id) is None
            or (
                command.command_id,
                command.episode_id,
                command.cut_id,
                command.format,
                command.editorial_master_id,
                command.tight_cut_id,
            )
            != (
                command_id,
                plan.episode_id,
                plan.cut_id,
                plan.format,
                context.editorial_master_id,
                context.tight_cut_id,
            )
        ):
            raise MaterializationError(
                "command, plan, and Editorial Cut Context do not share one authority chain",
                reason_code="authority_chain_mismatch",
            )
        if (
            view.command_id != command_id
            or plan.command_id != command_id
            or plan.run_id != view.run_id
            or (
                context.episode_id,
                context.cut_id,
                context.format,
            )
            != (plan.episode_id, plan.cut_id, plan.format)
        ):
            raise MaterializationError(
                "command, run, plan, and context identities differ",
                reason_code="authority_chain_mismatch",
            )
        _validate_final_assets(plan, self._assets)
        subtitle_path, preview_path = _materialization_paths(
            self._episode_root,
            plan,
            context,
        )
        record_path = subtitle_path.parent / PLAN_RECORD_FILENAME
        try:
            prior_record = read_plan_record_at(record_path)
        except PlanRecordError as error:
            raise MaterializationError(
                f"persisted plan record is unusable: {error}",
                reason_code=(
                    "materialization_journal_incomplete"
                    if error.reason == "incomplete"
                    else "materialization_journal_invalid"
                ),
            ) from error
        if prior_record is not None:
            return self._reopen_prior_record(
                prior_record,
                command=command,
                plan=plan,
                context=context,
                subtitle_path=subtitle_path,
                preview_path=preview_path,
                record_path=record_path,
            )
        try:
            inspections = self._canonical_authority.inspect(
                episode_id=plan.episode_id,
                cut_id=plan.cut_id,
                editorial_master_content_hash=context.editorial_master_id,
            )
        except ValueError as error:
            reason_code = getattr(error, "reason_code", None)
            raise MaterializationError(
                "canonical Timeline authority rejected materialization preflight",
                reason_code=(
                    reason_code
                    if isinstance(reason_code, str) and reason_code
                    else "resolve_binding_mismatch"
                ),
            ) from error
        if not inspections:
            raise MaterializationError(
                "canonical Timeline UID is unknown",
                reason_code="resolve_binding_mismatch",
            )
        if len(inspections) != 1:
            raise MaterializationError(
                "canonical Timeline UID is ambiguous",
                reason_code="resolve_binding_mismatch",
            )
        inspection = inspections[0]
        _validate_editorial_base(inspection, context)
        # 這一步要等 canonical 綁定驗過才做：交易層是 Timeline 的門，read-only
        # preflight 沒過之前不碰它（`test_exact_uid_binding_rejects_unknown_or_
        # ambiguous_canonical_before_assets` 釘的就是這個順序）。
        resumable = self._transactions.find_prepared(plan)
        if resumable is not None:
            # 這個 plan 的交易已經做完了，只是後面某一步失敗、`materialization.json`
            # 沒寫成。不要再開一次交易——canonical 現在就是上一次的 work，再
            # duplicate 一次會把衍生軌疊第二層。
            return self._resume_prepared_transaction(
                resumable,
                command=command,
                plan=plan,
                context=context,
                inspection=inspection,
                subtitle_path=subtitle_path,
                preview_path=preview_path,
                record_path=record_path,
            )
        subtitle_sha256 = _stage_review_subtitle(subtitle_path, context)
        try:
            transaction = self._transactions.prepare(
                plan,
                canonical=inspection.canonical,
                preview_path=preview_path,
                subtitle_path=subtitle_path,
                expected_baseline=inspection.baseline,
            )
        except ResolveTransactionError as error:
            # 把底層訊息帶出來。只回 `resolve_prepare_failed` 的話，操作的人手上
            # 沒有任何線索——Resolve 那一層的失敗理由（軌數不足、媒體對不上、
            # 算圖佇列拒絕…）全被吞掉，只能逐一猜。
            raise MaterializationError(
                f"Resolve transaction could not prepare the exact plan: {error}",
                reason_code="resolve_prepare_failed",
            ) from error
        _validate_prepared_transaction(
            transaction,
            plan=plan,
            inspection=inspection,
            preview_path=preview_path,
            subtitle_path=subtitle_path,
            context=context,
        )
        record = self._stage_record(
            plan,
            command=command,
            context=context,
            transaction_id=transaction.transaction_id,
            timeline=_transaction_timeline(transaction),
            preview_path=preview_path,
            subtitle_path=subtitle_path,
        )
        write_plan_record(record_path, record)
        return MaterializationPreparation(
            command_id=command_id,
            run_id=plan.run_id,
            plan_id=plan.plan_id,
            status="preview_ready",
            transaction_id=transaction.transaction_id,
            subtitle_sha256=subtitle_sha256,
            record=record,
        )

    def _resume_prepared_transaction(
        self,
        transaction: ResolveTransaction,
        *,
        command: ApprovedCutCommand,
        plan: MaterializationPlan,
        context: EditorialCutContext,
        inspection: CanonicalTimelineInspection,
        subtitle_path: Path,
        preview_path: Path,
        record_path: Path,
    ) -> MaterializationPreparation:
        """把一筆已經做完、但帳沒結成的交易接回來。

        `_transaction_id` 把 canonical 的名字與 UID 算進去，而交易成功那一刻
        canonical 就換人了（work 頂上原名）。所以同一個 plan 重跑 `prepare`
        必然算出另一個 id、必然 `load` 落空、必然從已經套用過的 timeline 再
        duplicate 一次——衍生軌疊第二層。只要交易之後任何一步失敗，那個 run
        以前就永遠結不了帳（20260721 punch-L03 卡在 preview 探測）。

        這條路只做交易之後**還沒做完**的事：確認輸出物還在、量成品、把 plan
        record 補上。不碰 Resolve。

        `transaction.canonical` 與 `inspection.canonical` 刻意不比對——那兩者
        本來就該不一樣，正是「交易已經生效」的證據。plan_id 與 plan_fingerprint
        的相符由 `find_prepared` 保證，計畫一改就不會走到這裡。
        """
        if transaction.status != "preview_ready":
            raise MaterializationError(
                "resumable materialization transaction is not preview_ready",
                reason_code="materialization_journal_conflict",
            )
        if (
            transaction.episode_id != plan.episode_id
            or transaction.cut_id != plan.cut_id
            or transaction.plan_id != plan.plan_id
            or transaction.subtitle_path != subtitle_path
            or transaction.preview.path != preview_path
        ):
            raise MaterializationError(
                "resumable transaction does not bind the exact plan and artifacts",
                reason_code="preview_transaction_mismatch",
            )
        preview = transaction.preview
        if (
            preview.video_codec.lower() not in {"h264", "avc1"}
            or preview.audio_codec is None
            or preview.audio_codec.lower() != "aac"
            or not _preview_matches_timeline(preview.duration_sec, inspection)
            or not preview.path.is_file()
        ):
            raise MaterializationError(
                "Resolve preview codec, duration, or object contract differs",
                reason_code="preview_probe_failed",
            )
        try:
            subtitle_payload = subtitle_path.read_bytes()
        except OSError as error:
            raise MaterializationError(
                "persisted materialization subtitle is unavailable",
                reason_code="materialization_journal_conflict",
            ) from error
        subtitle_sha256 = hashlib.sha256(subtitle_payload).hexdigest()
        _verify_srt_bytes(subtitle_payload, context, expected_digest=subtitle_sha256)
        record = self._stage_record(
            plan,
            command=command,
            context=context,
            transaction_id=transaction.transaction_id,
            timeline=_transaction_timeline(transaction),
            preview_path=preview_path,
            subtitle_path=subtitle_path,
        )
        write_plan_record(record_path, record)
        return MaterializationPreparation(
            command_id=command.command_id,
            run_id=plan.run_id,
            plan_id=plan.plan_id,
            status="preview_ready",
            transaction_id=transaction.transaction_id,
            subtitle_sha256=subtitle_sha256,
            record=record,
        )

    def _reopen_prior_record(
        self,
        prior: PlanRecord,
        *,
        command: ApprovedCutCommand,
        plan: MaterializationPlan,
        context: EditorialCutContext,
        subtitle_path: Path,
        preview_path: Path,
        record_path: Path,
    ) -> MaterializationPreparation:
        """Return the same preparation this plan already produced, or refuse.

        重進入的守則只有一條：**同一個 plan 只能有一份紀錄**。所以這裡不是「相信
        磁碟上那份」，而是把事實重新量一遍（字幕 bytes、preview bytes、交易狀態），
        算出這一刻**應該**是什麼樣子，再跟磁碟上那份逐欄位比。相等就回傳，不等就
        停——那代表有人動過成品，或者 plan 換了但紀錄沒換。

        ADR-069 之前這裡還有一條 `committed` 分支，用來重建「交易已封存、不能再
        stage」的 Candidate。封存鏈退役之後沒有任何路徑會 commit，那條分支連同它
        的 payload 重建器一起刪掉——留著只會讓人以為系統還有第二種狀態。
        """

        try:
            subtitle_payload = subtitle_path.read_bytes()
        except OSError as error:
            raise MaterializationError(
                "persisted materialization subtitle is unavailable",
                reason_code="materialization_journal_conflict",
            ) from error
        subtitle_sha256 = hashlib.sha256(subtitle_payload).hexdigest()
        _verify_srt_bytes(subtitle_payload, context, expected_digest=subtitle_sha256)
        try:
            transaction = self._transactions.inspect_transaction(prior.transaction_id)
        except ResolveTransactionError as error:
            raise MaterializationError(
                "persisted materialization transaction is unavailable",
                reason_code="materialization_journal_conflict",
            ) from error
        if (
            transaction.get("transaction_id") != prior.transaction_id
            or transaction.get("cut_id") != plan.cut_id
            or transaction.get("status") != "preview_ready"
        ):
            raise MaterializationError(
                "persisted materialization transaction is not exact",
                reason_code="materialization_journal_conflict",
            )
        timeline = _inspected_timeline(transaction)
        fresh = self._stage_record(
            plan,
            command=command,
            context=context,
            transaction_id=prior.transaction_id,
            timeline=timeline or prior.timeline,
            preview_path=preview_path,
            subtitle_path=subtitle_path,
        )
        if not prior.timeline.name and not prior.timeline.uid and timeline is not None:
            # ADR-069 之前的紀錄沒有記 timeline（那時候要從交易反查，而反查要求
            # `status == "committed"`，所以永遠查不到）。補上並改寫成 v2——既有的
            # run 因此不必重跑就能被發布線讀懂。
            prior = replace(prior, timeline=timeline)
            write_plan_record(record_path, prior)
        if prior != fresh:
            raise MaterializationError(
                "persisted plan record differs from exact current preparation",
                reason_code="materialization_journal_conflict",
            )
        return MaterializationPreparation(
            command_id=command.command_id,
            run_id=plan.run_id,
            plan_id=plan.plan_id,
            status="preview_ready",
            transaction_id=prior.transaction_id,
            subtitle_sha256=subtitle_sha256,
            record=prior,
        )

    def _stage_record(
        self,
        plan: MaterializationPlan,
        *,
        command: ApprovedCutCommand,
        context: EditorialCutContext,
        transaction_id: str,
        timeline: PlanTimeline,
        preview_path: Path,
        subtitle_path: Path,
    ) -> PlanRecord:
        try:
            return self._records.stage(
                plan,
                editorial_master_id=context.editorial_master_id,
                winner_id=command.winner_id,
                tight_cut_id=context.tight_cut_id,
                transaction_id=transaction_id,
                timeline=timeline,
                preview_path=preview_path,
                subtitle_path=subtitle_path,
            )
        except PlanRecordError as error:
            raise MaterializationError(
                f"preview_ready transaction cannot record its exact plan: {error}",
                reason_code="plan_record_staging_failed",
            ) from error


def _transaction_timeline(transaction: ResolveTransaction) -> PlanTimeline:
    """The timeline this plan was actually laid onto—the work copy, not canonical."""

    return PlanTimeline(
        name=transaction.workspace.work.name,
        uid=transaction.workspace.work.uid,
    )


def _inspected_timeline(transaction: Mapping[str, object]) -> PlanTimeline | None:
    value = transaction.get("timeline")
    if not isinstance(value, Mapping):
        return None
    name = value.get("name")
    uid = value.get("uid")
    if not isinstance(name, str) or not isinstance(uid, str) or not name or not uid:
        return None
    return PlanTimeline(name=name, uid=uid)


def _validate_prepared_transaction(
    transaction: ResolveTransaction,
    *,
    plan: MaterializationPlan,
    inspection: CanonicalTimelineInspection,
    preview_path: Path,
    subtitle_path: Path,
    context: EditorialCutContext,
) -> None:
    preview = transaction.preview
    if (
        transaction.status != "preview_ready"
        or transaction.episode_id != plan.episode_id
        or transaction.cut_id != plan.cut_id
        or transaction.plan_id != plan.plan_id
        or transaction.canonical != inspection.canonical
        or transaction.baseline != inspection.baseline
        or transaction.subtitle_path != subtitle_path
        or preview.path != preview_path
    ):
        raise MaterializationError(
            "Resolve transaction does not bind the exact plan, base, and artifacts",
            reason_code="preview_transaction_mismatch",
        )
    if (
        preview.video_codec.lower() not in {"h264", "avc1"}
        or preview.audio_codec is None
        or preview.audio_codec.lower() != "aac"
        or not math.isfinite(preview.duration_sec)
        or not _preview_matches_timeline(preview.duration_sec, inspection)
        or not preview.path.is_file()
    ):
        raise MaterializationError(
            "Resolve preview codec, duration, or object contract differs",
            reason_code="preview_probe_failed",
        )


def _preview_matches_timeline(
    preview_duration_sec: float, inspection: CanonicalTimelineInspection
) -> bool:
    """輸出檔的長度要對著**它渲染自的那條 timeline** 比，不是對著 context 的浮點秒數和。

    preview 是從 timeline 渲出來的，timeline 才是它的參照。context.duration_sec 是
    ApprovedCut `source_ranges` 的浮點和，跟 timeline 之間本來就容許一格
    （`_validate_editorial_base` 已經單獨把關過）；再讓 preview 隔著它去比，等於要
    preview 同時吸收兩層量化誤差，而第二層根本不是它造成的。

    20260721 punch-L03：timeline 16347 格、context 544.865s、preview 544.917333s。
    preview 比 timeline 多出的 0.017s 是 AAC 尾巴（一個 1024-sample frame ≈ 21ms，
    mp4 的容器長度取影像與聲音的較大者）——對 timeline 差 1 格，對 context 差 2 格。
    整條物化就卡在一個它管不到的誤差上。
    """
    fps = inspection.timeline_frame_rate
    state = inspection.state
    timeline_frames = state.end_frame - state.start_frame
    if (
        not math.isfinite(preview_duration_sec)
        or not math.isfinite(fps)
        or fps <= 0
        or timeline_frames <= 0
    ):
        return False
    return abs(round(preview_duration_sec * fps) - timeline_frames) <= 1


def _within_one_frame(measured_sec: float, expected_sec: float, fps: float) -> bool:
    """「差不超過一格」——兩邊都先取格數再比，不混用單位。

    ApprovedCut 的 `source_ranges` 是秒，剪輯卻活在格線上：每個邊界最多帶半格的
    表示誤差，段數一多就累積。拿 frame-exact 的量測（timeline 長度、輸出檔長度）
    去比這個浮點和、卻用「一格」當容忍度，等於讓表示誤差把容忍度吃掉。

    20260721 punch-L03：59 段，timeline 16347 格、`source_ranges` 量化後 16346 格
    ——內容就只差一格，本來該過；可是浮點和額外帶了 0.0017 秒，舊式子算成 1.05 格
    而擋下，而且 timeline 與 preview 兩處各擋一次。

    規則一字不改（仍然是「差超過一格就擋」），只是改用格來表述。
    """
    if not math.isfinite(measured_sec) or not math.isfinite(fps) or fps <= 0:
        return False
    return abs(round(measured_sec * fps) - round(expected_sec * fps)) <= 1


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise MaterializationError(
            "materialization identity is not canonical JSON",
            reason_code="materialization_journal_invalid",
        ) from error


def _materialization_paths(
    episode_root: Path,
    plan: MaterializationPlan,
    context: EditorialCutContext,
) -> tuple[Path, Path]:
    identity = hashlib.sha256(
        json.dumps(
            {
                "plan": asdict(plan),
                "editorial_master_id": context.editorial_master_id,
                "tight_cut_id": context.tight_cut_id,
                "cues": [asdict(cue) for cue in context.cues],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()[:24]
    workspace = Path(episode_root).resolve() / "highlights" / "staging" / "finished-cut" / identity
    return workspace / "review.srt", workspace / "preview.mp4"


def _stage_review_subtitle(path: Path, context: EditorialCutContext) -> str:
    payload = _render_srt(context)
    expected_digest = hashlib.sha256(payload).hexdigest()
    if path.exists():
        try:
            current = path.read_bytes()
        except OSError as error:
            raise MaterializationError(
                "staged review subtitle is unreadable",
                reason_code="subtitle_staging_conflict",
            ) from error
        if current != payload:
            raise MaterializationError(
                "staged review subtitle differs from current cue authority",
                reason_code="subtitle_staging_conflict",
            )
        _verify_srt_bytes(current, context, expected_digest=expected_digest)
        return expected_digest
    staging = path.with_name(f".{path.name}.staging")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with staging.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staging, path)
        written = path.read_bytes()
    except OSError as error:
        raise MaterializationError(
            "review subtitle could not be staged atomically",
            reason_code="subtitle_staging_failed",
        ) from error
    finally:
        staging.unlink(missing_ok=True)
    _verify_srt_bytes(written, context, expected_digest=expected_digest)
    return expected_digest


def _render_srt(context: EditorialCutContext) -> bytes:
    blocks: list[str] = []
    previous_end_ms = -1
    for index, cue in enumerate(context.cues, start=1):
        start_ms = _cue_milliseconds(cue.t0)
        end_ms = _cue_milliseconds(cue.t1)
        if (
            not cue.text
            or cue.text.startswith("\ufeff")
            or "\x00" in cue.text
            or start_ms < previous_end_ms
            or start_ms >= end_ms
            or end_ms > _cue_milliseconds(context.duration_sec)
        ):
            raise MaterializationError(
                "current cue timing or text cannot produce a review subtitle",
                reason_code="protected_track_drift",
            )
        blocks.append(
            f"{index}\n{_srt_timestamp(start_ms)} --> {_srt_timestamp(end_ms)}\n{cue.text}"
        )
        previous_end_ms = end_ms
    if not blocks:
        raise MaterializationError(
            "current cue authority is empty",
            reason_code="protected_track_drift",
        )
    return ("\n\n".join(blocks) + "\n").encode("utf-8")


def _cue_milliseconds(value: float) -> int:
    if isinstance(value, bool) or not math.isfinite(value) or value < 0:
        raise MaterializationError(
            "current cue time is invalid",
            reason_code="protected_track_drift",
        )
    return round(value * 1000)


def _srt_timestamp(milliseconds: int) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{millis:03}"


def _verify_srt_bytes(
    payload: bytes,
    context: EditorialCutContext,
    *,
    expected_digest: str,
) -> None:
    try:
        decoded = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise MaterializationError(
            "staged review subtitle is not UTF-8",
            reason_code="subtitle_staging_conflict",
        ) from error
    if decoded.startswith("\ufeff") or payload != _render_srt(context):
        raise MaterializationError(
            "staged review subtitle cue contract differs",
            reason_code="subtitle_staging_conflict",
        )
    if hashlib.sha256(payload).hexdigest() != expected_digest:
        raise MaterializationError(
            "staged review subtitle digest differs",
            reason_code="subtitle_staging_conflict",
        )


def _validate_final_assets(plan: MaterializationPlan, assets: AssetResolver) -> None:
    verified: dict[str, ResolvedAsset] = {}
    for component in plan.components:
        reference = component.asset_ref
        if not isinstance(reference, str) or not reference:
            raise MaterializationError(
                "materialized component has no final asset reference",
                reason_code="final_asset_unavailable",
            )
        resolved = verified.get(reference)
        if resolved is None:
            try:
                resolved = assets.resolve_active_asset(reference)
            except (AssetContractError, OSError) as error:
                raise MaterializationError(
                    "component final asset is unavailable in the Active Store",
                    reason_code="final_asset_unavailable",
                ) from error
            if resolved.record.reference != reference or resolved.record.compact_receipt is None:
                raise MaterializationError(
                    "Active Store result does not bind the exact component reference",
                    reason_code="final_asset_identity_mismatch",
                )
            path = resolved.path
            if path is None:
                raise MaterializationError(
                    "component final asset has no materializable object",
                    reason_code="final_asset_unavailable",
                )
            try:
                if not path.is_file() or _file_sha256(path) != resolved.record.digest:
                    raise MaterializationError(
                        "component object bytes differ from its Active Store digest",
                        reason_code="final_asset_identity_mismatch",
                    )
            except OSError as error:
                raise MaterializationError(
                    "component final asset object is unreadable",
                    reason_code="final_asset_unavailable",
                ) from error
            verified[reference] = resolved
        if component.implementation_kind == "stock_video":
            width = resolved.record.width
            height = resolved.record.height
            if (
                resolved.record.kind is not AssetKind.STOCK
                or type(width) is not int
                or type(height) is not int
                or width <= height
                or width * 9 != height * 16
            ):
                raise MaterializationError(
                    "Stock component is not native 16:9 landscape",
                    reason_code="stock_not_landscape_16_9",
                )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_editorial_base(
    inspection: CanonicalTimelineInspection,
    context: EditorialCutContext,
) -> None:
    if (
        inspection.episode_id,
        inspection.cut_id,
    ) != (context.episode_id, context.cut_id) or not (
        inspection.canonical.name and inspection.canonical.uid
    ):
        raise MaterializationError(
            "canonical Timeline inspection belongs to another cut",
            reason_code="resolve_binding_mismatch",
        )
    if (
        re.fullmatch(r"[0-9a-f]{64}", inspection.editorial_master_content_hash) is None
        or context.editorial_master_id != inspection.editorial_master_content_hash
    ):
        raise MaterializationError(
            "Editorial Cut Context does not bind the verified ADR-064 receipt",
            reason_code="editorial_master_mismatch",
        )
    if re.fullmatch(r"[0-9a-f]{64}", inspection.editorial_master_media_sha256) is None:
        raise MaterializationError(
            "verified ADR-064 Master media identity is invalid",
            reason_code="editorial_master_mismatch",
        )
    master_duration = inspection.editorial_master_duration_sec
    if (
        isinstance(master_duration, bool)
        or not math.isfinite(master_duration)
        or master_duration <= 0
    ):
        raise MaterializationError(
            "verified ADR-064 Master duration is invalid",
            reason_code="editorial_master_mismatch",
        )
    if any(source.t1 > master_duration + 1e-6 for source in context.source_ranges):
        raise MaterializationError(
            "ApprovedCut source range exceeds the verified ADR-064 Master",
            reason_code="source_range_outside_editorial_master",
        )
    timeline_fps = inspection.timeline_frame_rate
    master_fps = inspection.editorial_master_frame_rate
    if (
        isinstance(timeline_fps, bool)
        or isinstance(master_fps, bool)
        or not math.isfinite(timeline_fps)
        or not math.isfinite(master_fps)
        or timeline_fps <= 0
        or master_fps <= 0
        or not math.isclose(timeline_fps, master_fps, rel_tol=0.0, abs_tol=1e-6)
    ):
        raise MaterializationError(
            "canonical Timeline frame rate differs from the Editorial Master",
            reason_code="protected_track_drift",
        )
    state = inspection.state
    actual_frames = state.end_frame - state.start_frame
    if actual_frames <= 0 or not _within_one_frame(
        actual_frames / timeline_fps, context.duration_sec, timeline_fps
    ):
        raise MaterializationError(
            "canonical Timeline duration differs by more than one frame",
            reason_code="resolve_binding_mismatch",
        )

    protected_tracks = tuple(
        track
        for track in state.tracks
        if track.track_type in {"audio", "subtitle"}
        or (track.track_type == "video" and track.track_index == 1)
    )
    kinds = {track.track_type for track in protected_tracks if track.enabled}
    if kinds != {"video", "audio", "subtitle"} or any(
        not track.enabled for track in protected_tracks
    ):
        raise MaterializationError(
            "protected video, audio, or subtitle track contract differs",
            reason_code="protected_track_drift",
        )
    items_by_track = {
        (track.track_type, track.track_index): tuple(
            sorted(
                (
                    item
                    for item in state.items
                    if (item.track_type, item.track_index) == (track.track_type, track.track_index)
                ),
                key=lambda item: (item.start_frame, item.item_id),
            )
        )
        for track in protected_tracks
    }
    if any(
        track.item_ids
        != tuple(item.item_id for item in items_by_track[(track.track_type, track.track_index)])
        for track in protected_tracks
    ):
        raise MaterializationError(
            "protected track inventory differs from its item contract",
            reason_code="protected_track_drift",
        )

    base_tracks = tuple(
        items
        for (track_type, track_index), items in items_by_track.items()
        if (track_type == "video" and track_index == 1) or (track_type == "audio" and items)
    )
    if not base_tracks or any(len(items) != len(context.source_ranges) for items in base_tracks):
        raise MaterializationError(
            "protected V1 or audio source inventory differs",
            reason_code="protected_track_drift",
        )
    for items in base_tracks:
        record_cursor = state.start_frame
        for item, source in zip(items, context.source_ranges, strict=True):
            # 秒→影格要用**截斷**，跟 timeline 實際被切開的方式一致。
            #
            # 這裡本來用 `round`，於是每個落在半格以上的邊界都會多算一格：
            # punch-L03 六段裡有三段對不上（1593.364s → floor 47800、round 47801），
            # 一路報 `protected_track_drift`，可是 timeline 跟 ApprovedCut 其實描述的是
            # 同一個剪點。字幕軌 377 條逐格對得上、只有 source_ranges 對不上，就是
            # 這個量化方式不一致造成的，不是資料真的漂了。
            #
            # 記錄端的長度直接取來源長度：上面已經驗過 timeline 與 Master 同幀率，
            # 沒有變速，兩者必然相等；再獨立算一次只會再引入一次量化誤差。
            expected_source_in = int(source.t0 * master_fps)
            expected_source_out = int(source.t1 * master_fps)
            expected_record_end = record_cursor + (expected_source_out - expected_source_in)
            if item.media_digest != inspection.editorial_master_media_sha256:
                raise MaterializationError(
                    "protected V1 or audio media is not the ADR-064 Master",
                    reason_code="editorial_master_mismatch",
                )
            if (
                item.start_frame != record_cursor
                or item.end_frame != expected_record_end
                or item.source_in_frame != expected_source_in
                or item.source_out_frame != expected_source_out
            ):
                raise MaterializationError(
                    "protected V1 or audio source range differs from ApprovedCut",
                    reason_code="protected_track_drift",
                )
            record_cursor = expected_record_end
        # 允許差一格：timeline 的結束影格是**所有軌道**的最大值，字幕軌常常比
        # 影音多壓一格（punch-L02 的字幕收在 16541、V1 與音軌都收在 16540）。
        # 那一格不是覆蓋缺口，是字幕尾巴。少一格以上、或影音反而超出，仍然擋下。
        if not 0 <= state.end_frame - record_cursor <= 1:
            raise MaterializationError(
                "protected V1 or audio record spans do not cover the exact cut",
                reason_code="protected_track_drift",
            )

    subtitle_items = tuple(
        item
        for (track_type, _), items in items_by_track.items()
        if track_type == "subtitle"
        for item in items
    )
    if len(subtitle_items) != len(context.cues):
        raise MaterializationError(
            "protected subtitle cue count differs from tight context",
            reason_code="protected_track_drift",
        )
    for item, cue in zip(subtitle_items, context.cues, strict=True):
        # 時間允許差一格，文字必須逐字相同。
        #
        # cue 的秒數乘上幀率常常正好落在 .5（punch-L02 有五處：207.75s × 30 = 6232.5），
        # 這時「進位到哪一邊」在 Python 與 Resolve 之間沒有共識——Python 的 round 是
        # 銀行家捨入、Resolve 又要讓相鄰字幕首尾相接，兩邊各自合理但答案差一格。
        # 一格是 33 毫秒，字幕看不出來；真正對錯位的字幕差距遠大於一格。
        # 文字不放寬：字幕內容錯了就是錯了。
        if (
            abs(item.start_frame - (state.start_frame + round(cue.t0 * timeline_fps))) > 1
            or abs(item.end_frame - (state.start_frame + round(cue.t1 * timeline_fps))) > 1
            or _subtitle_text(item.properties) != cue.text
        ):
            raise MaterializationError(
                "protected subtitle timing or text differs from tight context",
                reason_code="protected_track_drift",
            )


def _subtitle_text(properties: tuple[tuple[str, object], ...]) -> str | None:
    values = dict(properties)
    direct = values.get("Text")
    if isinstance(direct, str):
        return direct
    encoded = values.get("timeline_properties")
    if not isinstance(encoded, str):
        return None
    try:
        decoded = json.loads(encoded)
    except json.JSONDecodeError:
        return None
    text = decoded.get("Text") if isinstance(decoded, dict) else None
    return text if isinstance(text, str) else None
