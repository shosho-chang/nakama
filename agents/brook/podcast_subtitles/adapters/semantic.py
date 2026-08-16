"""Token-ID semantic constraint Adapter with complete-coverage validation."""

from __future__ import annotations

import json
import os
import platform
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from importlib import metadata
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from shared.schemas.podcast_subtitles_v2 import ArtifactDigest, SemanticUnit

from ..boundary_constraints import (
    BoundaryConstraintError,
    validate_semantic_non_degeneracy,
)
from ..hashing import canonical_json_bytes, hash_file, hash_object, sha256_bytes
from ..ports import (
    AdapterInputError,
    AdapterIntegrityError,
    AdapterUnavailableError,
    AdapterWorkPending,
    SemanticAnalysisRequest,
    SemanticExecutionReceipt,
    SemanticModelIdentity,
    SemanticRunResult,
)

SemanticRunner = Callable[..., object]

class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _BoundaryPayload(_StrictModel):
    edge_index: int = Field(ge=1)
    left_token_id: str
    right_token_id: str
    cue_relation: Literal["forbidden", "discouraged", "preferred", "neutral"]
    line_relation: Literal["forbidden", "discouraged", "preferred", "neutral"]
    strength: float = Field(ge=0.0, le=1.0)


class _SemanticResponse(_StrictModel):
    schema_version: Literal[5]
    work_packet_id: str
    boundaries: tuple[_BoundaryPayload, ...]


_SYSTEM = """你是繁體中文（台灣）Podcast 字幕的語意邊界分析員。
你只能評估 request 中 owned_boundaries 列出的相鄰 Canonical token edge，絕對不能改字、
增字、刪字、重排或新增 edge。每個 owned edge 必須恰好回覆一次，edge_index 與左右
token IDs 必須原樣複製。context_before / context_after 只供理解，不可成為輸出 target。
cue_relation 與 line_relation 分別使用 forbidden、discouraged、preferred、neutral；
forbidden 僅用於跨該 edge 會破壞不可拆語意的情況，preferred 表示自然句法／語意結束，
discouraged 表示仍可切但會削弱理解，neutral 表示沒有足夠語意訊號。非 neutral strength
必須大於零，neutral 必須為零。語意完整優先於固定字數，不得按字數硬切。
輸出必須完全符合指定 JSON schema。"""


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key is forbidden: {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is forbidden: {value}")


def _raw_response_bytes(payload: object) -> bytes:
    """Preserve subscription/provider bytes; canonicalise runner objects once."""

    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, str):
        return payload.encode("utf-8")
    return canonical_json_bytes(payload)


def _coerce_response(payload: object) -> _SemanticResponse:
    if isinstance(payload, _SemanticResponse):
        return payload
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AdapterIntegrityError("Semantic Analyzer response is not UTF-8") from exc
    if isinstance(payload, str):
        try:
            payload = json.loads(
                payload,
                object_pairs_hook=_strict_object,
                parse_constant=_reject_constant,
            )
        except (json.JSONDecodeError, ValueError) as exc:
            raise AdapterIntegrityError(f"malformed Semantic Analyzer JSON: {exc}") from exc
    try:
        return _SemanticResponse.model_validate(payload)
    except ValidationError as exc:
        raise AdapterIntegrityError(f"Semantic Analyzer response violates schema: {exc}") from exc


def _artifact(kind: str, payload: bytes) -> ArtifactDigest:
    digest = sha256_bytes(payload)
    return ArtifactDigest(
        uri=f"projection-artifact://semantic/{kind}s/{digest}",
        sha256=digest,
        size_bytes=len(payload),
    )


def _runtime_hash() -> str:
    """Hash the concrete JSON/schema runtime executing this Adapter."""

    try:
        pydantic_version = metadata.version("pydantic")
    except metadata.PackageNotFoundError:  # pragma: no cover - imported above
        pydantic_version = "unavailable"
    return hash_object(
        {
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
            "python_cache_tag": sys.implementation.cache_tag,
            "byteorder": sys.byteorder,
            "pydantic_version": pydantic_version,
        }
    )


class SemanticAnalyzerAdapter:
    """Partition canonical tokens without ever changing their text."""

    def __init__(
        self,
        *,
        model: str,
        model_version: str,
        runner: SemanticRunner | None = None,
        allow_paid_api: bool = False,
        workspace_root: str | Path | None = None,
        max_target_tokens_per_packet: int = 48,
        context_tokens_per_side: int = 8,
        execution_mode: Literal["fixture", "local", "paid_api", "subscription", "other"]
        | None = None,
    ) -> None:
        if not model.strip() or not model_version.strip():
            raise ValueError("Semantic Analyzer requires explicit model and model_version")
        if max_target_tokens_per_packet < 2:
            raise ValueError("Semantic Analyzer packet size must be at least two")
        if context_tokens_per_side < 1:
            raise ValueError("Semantic Analyzer context size must be positive")
        self._model = model
        self._model_version = model_version
        self._runner = runner
        self._allow_paid_api = allow_paid_api
        self._workspace_root = Path(workspace_root) if workspace_root is not None else None
        self._max_tokens = max_target_tokens_per_packet
        self._context_tokens = context_tokens_per_side
        inferred_mode: Literal["fixture", "local", "paid_api", "subscription", "other"]
        if runner is None:
            inferred_mode = "paid_api" if allow_paid_api else "subscription"
        else:
            inferred_mode = "other"
        if execution_mode is not None:
            if runner is None and allow_paid_api and execution_mode != "paid_api":
                raise ValueError("paid Semantic execution must declare paid_api mode")
            if runner is None and not allow_paid_api and execution_mode != "subscription":
                raise ValueError("packet Semantic execution must declare subscription mode")
            inferred_mode = execution_mode
        config_hash = hash_object(
            {
                "schema": "podcast-subtitle-v2-semantic-boundary-6",
                "system": _SYSTEM,
                "max_target_tokens_per_packet": max_target_tokens_per_packet,
                "context_tokens_per_side": context_tokens_per_side,
                "request_serialization": "canonical-json-utf8-v1",
                "runner_object_response_serialization": "canonical-json-utf8-v1",
                "packet_overlap_tokens": 1,
                "token_ownership": "leading-overlap-owned-by-preceding-packet-v1",
                "boundary_ownership": "canonical-adjacent-edge-v1",
                "response_shape": "one-assessment-per-owned-boundary-v1",
                "name_term_line_break_policy": "explicit-analyzer-output-only-v1",
                "allow_paid_api": allow_paid_api,
            }
        )
        self._source_hash = hash_file(Path(__file__))
        self._identity = SemanticModelIdentity(
            adapter_name="llm-semantic-analyzer",
            adapter_version="6",
            model=model,
            model_version=model_version,
            runtime_hash=_runtime_hash(),
            adapter_code_hash=self._source_hash,
            config_hash=config_hash,
            execution_mode=inferred_mode,
        )

    @property
    def identity(self) -> SemanticModelIdentity:
        if hash_file(Path(__file__)) != self._source_hash:
            raise AdapterIntegrityError(
                "Semantic Analyzer Adapter code changed after initialization"
            )
        if _runtime_hash() != self._identity.runtime_hash:
            raise AdapterIntegrityError("Semantic Analyzer runtime changed after initialization")
        return self._identity

    @property
    def model_identity(self) -> str:
        return f"{self.identity.model}@{self.identity.model_version}"

    def export_work_packets(
        self, request: SemanticAnalysisRequest
    ) -> tuple[dict[str, object], ...]:
        tokens = request.transcript.tokens
        packets: list[dict[str, object]] = []
        start = 0
        while start < len(tokens):
            end = min(len(tokens), start + self._max_tokens)
            target = tokens[start:end]
            body: dict[str, object] = {
                "schema_version": 5,
                "task": "podcast_subtitle_v2_semantic_boundary_assessment",
                "episode_id": request.transcript.episode_id,
                "generation_id": request.transcript.generation_id,
                "canonical_content_hash": request.transcript.content_hash,
                "policy_hash": request.policy_hash,
                "protected_range_set_hash": request.protected_range_set_hash,
                "protected_ranges": [
                    protected.model_dump(mode="json")
                    for protected in request.protected_ranges
                    if set(protected.token_ids).intersection(
                        token.id for token in target
                    )
                ],
                "language": request.language,
                "model": self._model,
                "model_version": self._model_version,
                "adapter": self.identity.adapter_name,
                "adapter_version": self.identity.adapter_version,
                "runtime_hash": self.identity.runtime_hash,
                "adapter_code_hash": self.identity.adapter_code_hash,
                "config_hash": self.identity.config_hash,
                "adapter_identity_hash": self.identity.content_hash,
                "target_tokens": [
                    {
                        "id": token.id,
                        "text": token.text,
                        "speaker": token.speaker,
                        "start_ms": token.start_ms,
                        "end_ms": token.end_ms,
                        "timing_basis": token.timing_basis,
                    }
                    for token in target
                ],
                "owned_target_token_ids": [
                    token.id for token in (target if start == 0 else target[1:])
                ],
                "owned_boundaries": [
                    {
                        "edge_index": edge_index,
                        "left_token_id": tokens[edge_index - 1].id,
                        "right_token_id": tokens[edge_index].id,
                    }
                    for edge_index in range(start + 1, end)
                ],
                "context_before": [
                    {
                        "id": token.id,
                        "text": token.text,
                        "speaker": token.speaker,
                        "start_ms": token.start_ms,
                        "end_ms": token.end_ms,
                        "timing_basis": token.timing_basis,
                        "non_target_context": True,
                    }
                    for token in tokens[max(0, start - self._context_tokens) : start]
                ],
                "context_after": [
                    {
                        "id": token.id,
                        "text": token.text,
                        "speaker": token.speaker,
                        "start_ms": token.start_ms,
                        "end_ms": token.end_ms,
                        "timing_basis": token.timing_basis,
                        "non_target_context": True,
                    }
                    for token in tokens[end : min(len(tokens), end + self._context_tokens)]
                ],
            }
            packets.append(body)
            if end == len(tokens):
                break
            # Consecutive target packets share one canonical token.  Therefore
            # every token boundary is internal to a target packet instead of
            # becoming an artificial model seam that needs guessed stitching.
            start = end - 1
        packet_count = len(packets)
        for packet_index, body in enumerate(packets):
            body["packet_index"] = packet_index
            body["packet_count"] = packet_count
            body["work_packet_id"] = f"semantic-work-{hash_object(body)}"
            body["response_contract"] = {
                "schema_version": 5,
                "work_packet_id": body["work_packet_id"],
                "boundaries": [
                    {
                        "edge_index": "exact owned edge index",
                        "left_token_id": "exact owned left token ID",
                        "right_token_id": "exact owned right token ID",
                        "cue_relation": "forbidden|discouraged|preferred|neutral",
                        "line_relation": "forbidden|discouraged|preferred|neutral",
                        "strength": "0..1",
                    }
                ],
                "coverage": "every owned boundary exactly once; no unowned boundary",
            }
        return tuple(packets)

    def export_work_packet(self, request: SemanticAnalysisRequest) -> dict[str, object]:
        packets = self.export_work_packets(request)
        if len(packets) != 1:
            raise AdapterInputError(
                f"request expands to {len(packets)} packets; use export_work_packets"
            )
        return packets[0]

    def _validate_response(
        self,
        packet: Mapping[str, object],
        response: _SemanticResponse,
    ) -> tuple[SemanticUnit, ...]:
        packet_id = str(packet["work_packet_id"])
        if response.work_packet_id != packet_id:
            raise AdapterIntegrityError(
                "Semantic Analyzer response is bound to another work packet"
            )
        expected_boundaries = {
            int(item["edge_index"]): (
                str(item["left_token_id"]),
                str(item["right_token_id"]),
            )
            for item in packet["owned_boundaries"]
        }
        actual_boundaries: dict[int, _BoundaryPayload] = {}
        for boundary in response.boundaries:
            if boundary.edge_index in actual_boundaries:
                raise AdapterIntegrityError(
                    f"duplicate Semantic boundary edge: {boundary.edge_index}"
                )
            actual_boundaries[boundary.edge_index] = boundary
        if set(actual_boundaries) != set(expected_boundaries):
            missing = sorted(set(expected_boundaries) - set(actual_boundaries))
            unexpected = sorted(set(actual_boundaries) - set(expected_boundaries))
            raise AdapterIntegrityError(
                "Semantic response must assess every owned boundary exactly once: "
                f"missing={missing!r}, unexpected={unexpected!r}"
            )
        units: list[SemanticUnit] = []
        for edge_index, expected_endpoints in expected_boundaries.items():
            payload = actual_boundaries[edge_index]
            if (payload.left_token_id, payload.right_token_id) != expected_endpoints:
                raise AdapterIntegrityError(
                    f"Semantic boundary {edge_index} token endpoints drifted"
                )
            is_neutral = (
                payload.cue_relation == "neutral"
                and payload.line_relation == "neutral"
            )
            if is_neutral != (payload.strength == 0):
                raise AdapterIntegrityError(
                    "neutral Semantic boundary requires zero strength and every "
                    "non-neutral boundary requires positive strength"
                )
            unit_payload = {
                "edge_index": edge_index,
                "token_ids": expected_endpoints,
                "kind": "boundary_pair",
                "strength": payload.strength,
                "cue_relation": payload.cue_relation,
                "line_relation": payload.line_relation,
                "adapter_identity_hash": self.identity.content_hash,
            }
            units.append(
                SemanticUnit(
                    id=f"semantic-{hash_object(unit_payload)}",
                    token_ids=expected_endpoints,
                    kind="boundary_pair",
                    strength=payload.strength,
                    forbid_cue_breaks=payload.cue_relation == "forbidden",
                    forbid_line_breaks=payload.line_relation == "forbidden",
                    cue_boundary_relation=payload.cue_relation,
                    line_boundary_relation=payload.line_relation,
                )
            )
        if not units:
            # A one-token Canonical Transcript has no adjacent edge, but still
            # needs one replay-accounted Semantic Unit for exact token coverage.
            owned_ids = tuple(str(item) for item in packet["owned_target_token_ids"])
            if len(owned_ids) != 1:
                raise AdapterIntegrityError("Semantic packet unexpectedly owns no boundary")
            unit_payload = {
                "token_ids": owned_ids,
                "kind": "token",
                "strength": 0.0,
                "adapter_identity_hash": self.identity.content_hash,
            }
            units.append(
                SemanticUnit(
                    id=f"semantic-{hash_object(unit_payload)}",
                    token_ids=owned_ids,
                    kind="token",
                    strength=0.0,
                )
            )
        if len({unit.id for unit in units}) != len(units):
            raise AdapterIntegrityError("Semantic response contains duplicate Semantic Units")
        return tuple(units)

    def import_work_results_with_receipts(
        self,
        request: SemanticAnalysisRequest,
        payloads: Sequence[object],
    ) -> SemanticRunResult:
        """Strictly import responses and retain exact execution bytes."""

        packets = self.export_work_packets(request)
        if len(payloads) != len(packets):
            raise AdapterIntegrityError(
                f"expected {len(packets)} Semantic results, received {len(payloads)}"
            )
        by_id: dict[str, tuple[_SemanticResponse, bytes]] = {}
        for payload in payloads:
            response_bytes = _raw_response_bytes(payload)
            response = _coerce_response(payload)
            if response.work_packet_id in by_id:
                raise AdapterIntegrityError("duplicate Semantic work_packet_id")
            by_id[response.work_packet_id] = (response, response_bytes)
        ordered: list[tuple[SemanticUnit, ...]] = []
        receipts: list[SemanticExecutionReceipt] = []
        request_payloads: list[bytes] = []
        response_payloads: list[bytes] = []
        for packet in packets:
            packet_id = str(packet["work_packet_id"])
            try:
                response, response_bytes = by_id.pop(packet_id)
            except KeyError as exc:
                raise AdapterIntegrityError(
                    f"missing Semantic result for work packet {packet_id!r}"
                ) from exc
            packet_units = self._validate_response(packet, response)
            ordered.append(packet_units)
            request_bytes = canonical_json_bytes(packet)
            receipts.append(
                SemanticExecutionReceipt(
                    episode_id=request.transcript.episode_id,
                    generation_id=request.transcript.generation_id,
                    canonical_content_hash=request.transcript.content_hash,
                    work_packet_id=packet_id,
                    packet_index=int(packet["packet_index"]),
                    packet_count=int(packet["packet_count"]),
                    target_token_ids=tuple(
                        str(item["id"]) for item in packet["target_tokens"]
                    ),
                    owned_token_ids=tuple(
                        str(item) for item in packet["owned_target_token_ids"]
                    ),
                    request=_artifact("request", request_bytes),
                    response=_artifact("response", response_bytes),
                    adapter_identity=self.identity,
                    semantic_unit_ids=tuple(unit.id for unit in packet_units),
                    owned_boundary_indices=tuple(
                        int(item["edge_index"])
                        for item in packet["owned_boundaries"]
                    ),
                    boundary_ownership_contract="canonical-adjacent-edge-v1",
                )
            )
            request_payloads.append(request_bytes)
            response_payloads.append(response_bytes)
        if by_id:
            raise AdapterIntegrityError(f"unknown Semantic work packets: {sorted(by_id)!r}")

        units: list[SemanticUnit] = []
        units_by_id: dict[str, SemanticUnit] = {}
        units_by_range: dict[tuple[str, ...], SemanticUnit] = {}
        for batch in ordered:
            for unit in batch:
                existing_range = units_by_range.get(unit.token_ids)
                if existing_range is not None:
                    if existing_range != unit:
                        raise AdapterIntegrityError(
                            "Semantic Unit range has conflicting cross-packet content"
                        )
                    continue
                existing = units_by_id.get(unit.id)
                if existing is not None:
                    if existing != unit:
                        raise AdapterIntegrityError(
                            "Semantic Unit ID has conflicting packet content"
                        )
                    continue
                units_by_id[unit.id] = unit
                units_by_range[unit.token_ids] = unit
                units.append(unit)
        covered = {token_id for unit in units for token_id in unit.token_ids}
        expected = {token.id for token in request.transcript.tokens}
        if covered != expected:
            raise AdapterIntegrityError(
                "Semantic Units do not completely cover the Canonical Transcript"
            )
        try:
            validate_semantic_non_degeneracy(request.transcript.tokens, units)
        except BoundaryConstraintError as exc:
            raise AdapterIntegrityError(str(exc)) from exc
        try:
            return SemanticRunResult(
                canonical_token_ids=tuple(token.id for token in request.transcript.tokens),
                units=tuple(units),
                execution_receipts=tuple(receipts),
                request_bytes=tuple(request_payloads),
                response_bytes=tuple(response_payloads),
            )
        except (TypeError, ValueError) as exc:
            raise AdapterIntegrityError(f"Semantic execution proof is invalid: {exc}") from exc

    def import_work_results(
        self,
        request: SemanticAnalysisRequest,
        payloads: Sequence[object],
    ) -> tuple[SemanticUnit, ...]:
        return self.import_work_results_with_receipts(request, payloads).units

    def import_work_result(
        self,
        request: SemanticAnalysisRequest,
        payload: object,
    ) -> tuple[SemanticUnit, ...]:
        return self.import_work_results(request, (payload,))

    def _paid_runner(self, prompt: str, **kwargs: object) -> object:
        if not self._allow_paid_api:
            raise AdapterUnavailableError(
                "paid Semantic API is disabled; export work packets and use a "
                "Codex/subagent subscription worker, or explicitly set allow_paid_api=True"
            )
        import shared.llm

        return shared.llm.ask(
            prompt,
            system=str(kwargs["system"]),
            model=self._model,
            task="podcast-subtitle-v2-semantic",
            max_tokens=4096,
            temperature=0.0,
        )

    @staticmethod
    def _atomic_emit(path: Path, encoded: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != encoded:
                raise AdapterIntegrityError(
                    f"existing Semantic work packet conflicts with deterministic content: {path}"
                )
            return
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            if path.exists():
                if path.read_bytes() != encoded:
                    raise AdapterIntegrityError(f"concurrent Semantic packet conflict: {path}")
            else:
                os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _read_response(path: Path) -> bytes:
        try:
            return path.read_bytes()
        except OSError as exc:
            raise AdapterIntegrityError(
                f"cannot read Semantic work response {path}: {exc}"
            ) from exc

    def _workspace_partition(
        self,
        request: SemanticAnalysisRequest,
        packets: tuple[dict[str, object], ...],
    ) -> SemanticRunResult:
        assert self._workspace_root is not None
        packet_dir = self._workspace_root / "semantic" / "packets"
        response_dir = self._workspace_root / "semantic" / "responses"
        response_dir.mkdir(parents=True, exist_ok=True)
        packet_paths = tuple(packet_dir / f"{packet['work_packet_id']}.json" for packet in packets)
        response_paths = tuple(
            response_dir / f"{packet['work_packet_id']}.response.json" for packet in packets
        )
        for path, packet in zip(packet_paths, packets):
            self._atomic_emit(path, canonical_json_bytes(packet))
        missing = tuple(path for path in response_paths if not path.is_file())
        if missing:
            raise AdapterWorkPending(
                "Semantic Analyzer subscription work is pending",
                packet_paths=packet_paths,
                response_paths=response_paths,
            )
        return self.import_work_results_with_receipts(
            request,
            tuple(self._read_response(path) for path in response_paths),
        )

    def partition_with_receipts(self, request: SemanticAnalysisRequest) -> SemanticRunResult:
        packets = self.export_work_packets(request)
        if self._runner is None and not self._allow_paid_api and self._workspace_root is not None:
            return self._workspace_partition(request, packets)
        if self._runner is None and not self._allow_paid_api:
            raise AdapterUnavailableError(
                "paid Semantic API is disabled; call export_work_packets and import_work_results"
            )
        runner = self._runner or self._paid_runner
        responses: list[object] = []
        for packet in packets:
            prompt = canonical_json_bytes(packet).decode("utf-8")
            try:
                responses.append(
                    runner(
                        prompt,
                        system=_SYSTEM,
                        model=self._model,
                        model_version=self._model_version,
                        response_schema=_SemanticResponse,
                    )
                )
            except Exception as exc:
                raise AdapterUnavailableError("Semantic Analyzer runner failed") from exc
        return self.import_work_results_with_receipts(request, responses)

    def replay(
        self,
        request: SemanticAnalysisRequest,
        *,
        units: tuple[SemanticUnit, ...],
        execution_receipts: tuple[SemanticExecutionReceipt, ...],
        request_bytes: tuple[bytes, ...],
        response_bytes: tuple[bytes, ...],
    ) -> SemanticRunResult:
        """Reparse stored raw responses through the current executable Adapter."""

        try:
            stored = SemanticRunResult(
                canonical_token_ids=tuple(token.id for token in request.transcript.tokens),
                units=units,
                execution_receipts=execution_receipts,
                request_bytes=request_bytes,
                response_bytes=response_bytes,
            )
        except (TypeError, ValueError) as exc:
            raise AdapterIntegrityError(
                f"stored Semantic execution proof is invalid: {exc}"
            ) from exc
        if any(receipt.adapter_identity != self.identity for receipt in execution_receipts):
            raise AdapterIntegrityError("Semantic Analyzer executable identity drifted")
        replayed = self.import_work_results_with_receipts(request, response_bytes)
        if replayed != stored:
            raise AdapterIntegrityError(
                "stored Semantic execution does not replay to the same requests, "
                "receipts, and units"
            )
        return replayed

    def partition(self, request: SemanticAnalysisRequest) -> tuple[SemanticUnit, ...]:
        return self.partition_with_receipts(request).units


__all__ = ["SemanticAnalyzerAdapter", "SemanticRunner"]
