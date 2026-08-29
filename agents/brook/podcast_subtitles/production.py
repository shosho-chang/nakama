"""Fail-closed Memo-first production composition for Podcast Subtitle V2."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .adapters import (
    FasterWhisperRecognizerAdapter,
    FFmpegSpeechCoverageAnalyzer,
    MemoRecognizerAdapter,
    MicEnergySpeakerAttributor,
    Qwen3ASRRecognizerAdapter,
    SemanticAnalyzerAdapter,
    VerifiedNormalizedAudioHandoffAdapter,
)
from .audio_audit_execution import AudioFullAuditExecutor, build_audio_audit_adapter_identity
from .audio_audit_selection import default_audio_audit_selection_policy
from .composition import FactoryContextV1
from .hashing import hash_file, hash_object
from .memo_boundary import MemoSrtBoundaryAuthorityV1
from .module import AdapterIdentity, NativeFullAuditBundle, PodcastSubtitleV2
from .recognition_policy import PRODUCTION_RECOGNITION_INDEPENDENCE_POLICY
from .store import GenerationStore
from .text_audit_execution import TextFullAuditExecutor, build_text_audit_adapter_identity

_PREFIX = "PODCAST_SUBTITLE_V2_"
_LEGACY_PAID_GEMINI_SETTING = f"{_PREFIX}ALLOW_PAID_GEMINI"
_PATH_SETTINGS = (
    "NORMALIZED_HANDOFF_MANIFEST",
    "MEMO_RECOGNITION_MANIFEST",
    "MEMO_RECOGNITION_SOURCE_EXPORT",
    "MEMO_RECOGNITION_ACCEPTANCE_RECEIPT",
    "MEMO_CUE_SOURCE_EXPORT",
    "MEMO_CUE_ACCEPTANCE_RECEIPT",
)
_KNOWN_SETTINGS = frozenset(
    {f"{_PREFIX}{name}" for name in _PATH_SETTINGS}
    | {
        _LEGACY_PAID_GEMINI_SETTING,
        f"{_PREFIX}ENABLE_QWEN_CORROBORATION",
        f"{_PREFIX}QWEN_MODEL",
        f"{_PREFIX}QWEN_MODEL_REVISION",
        f"{_PREFIX}QWEN_ALIGNER",
        f"{_PREFIX}QWEN_ALIGNER_REVISION",
        f"{_PREFIX}QWEN_DEVICE",
        f"{_PREFIX}QWEN_DTYPE",
        f"{_PREFIX}QWEN_LOCAL_FILES_ONLY",
        f"{_PREFIX}ENABLE_FASTER_WHISPER_CORROBORATION",
        f"{_PREFIX}FASTER_WHISPER_MODEL",
        f"{_PREFIX}FASTER_WHISPER_MODEL_REVISION",
        f"{_PREFIX}FASTER_WHISPER_DEVICE",
        f"{_PREFIX}FASTER_WHISPER_DEVICE_INDEX",
        f"{_PREFIX}FASTER_WHISPER_COMPUTE_TYPE",
        f"{_PREFIX}FASTER_WHISPER_CPU_THREADS",
        f"{_PREFIX}FASTER_WHISPER_NUM_WORKERS",
        f"{_PREFIX}FASTER_WHISPER_LOCAL_FILES_ONLY",
        f"{_PREFIX}TEXT_AUDIT_MODEL",
        f"{_PREFIX}TEXT_AUDIT_MODEL_VERSION",
        f"{_PREFIX}SEMANTIC_MODEL",
        f"{_PREFIX}SEMANTIC_MODEL_VERSION",
        f"{_PREFIX}AUDIO_AUDIT_MODEL",
        f"{_PREFIX}AUDIO_AUDIT_MODEL_VERSION",
        f"{_PREFIX}FFMPEG",
        f"{_PREFIX}FFPROBE",
        f"{_PREFIX}ENABLE_MIC_SPEAKER_ATTRIBUTION",
    }
)


class ProductionConfigurationError(ValueError):
    pass


def production_source_inventory_digest() -> str:
    """Measure the exact Podcast Subtitle V2 Python source inventory.

    Checkpoint compatibility must move when orchestration code moves, even if
    provider/model identities remain unchanged.  A sorted package-wide source
    inventory is explicit, reproducible, and fails closed for every production
    module participating in the local pipeline.
    """

    package_root = Path(__file__).resolve().parent
    sources = tuple(
        sorted(
            package_root.rglob("*.py"),
            key=lambda path: path.relative_to(package_root).as_posix(),
        )
    )
    if not sources:
        raise ProductionConfigurationError("Podcast Subtitle V2 source inventory is empty")
    return hash_object(
        {
            "schema_version": 1,
            "package": "agents.brook.podcast_subtitles",
            "files": tuple(
                {
                    "path": path.relative_to(package_root).as_posix(),
                    "sha256": hash_file(path),
                }
                for path in sources
            ),
        }
    )


@dataclass(frozen=True, slots=True)
class ProductionConfig:
    normalized_handoff_manifest: Path
    memo_recognition_manifest: Path
    memo_recognition_source_export: Path
    memo_recognition_acceptance_receipt: Path
    memo_cue_source_export: Path
    memo_cue_acceptance_receipt: Path
    enable_qwen_corroboration: bool
    qwen_model: str
    qwen_model_revision: str | None
    qwen_forced_aligner: str
    qwen_forced_aligner_revision: str | None
    qwen_device: str
    qwen_dtype: str
    enable_faster_whisper_corroboration: bool
    faster_whisper_model: str
    faster_whisper_model_revision: str | None
    faster_whisper_device: str
    faster_whisper_device_index: int
    faster_whisper_compute_type: str
    faster_whisper_cpu_threads: int
    faster_whisper_num_workers: int
    text_audit_model: str
    text_audit_model_version: str
    semantic_model: str
    semantic_model_version: str
    audio_audit_model: str
    audio_audit_model_version: str
    ffmpeg_executable: str
    ffprobe_executable: str
    enable_mic_speaker_attribution: bool


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name)
    if value is None or not value.strip() or value != value.strip():
        raise ProductionConfigurationError(f"{name} is required and must be trimmed")
    return value


def _optional(env: Mapping[str, str], name: str, default: str) -> str:
    value = env.get(name, default)
    if not value.strip() or value != value.strip():
        raise ProductionConfigurationError(f"{name} must be non-blank and trimmed")
    return value


def _bool(env: Mapping[str, str], name: str, default: bool = False) -> bool:
    raw = env.get(name)
    if raw is None:
        return default
    if raw not in {"true", "false"}:
        raise ProductionConfigurationError(f"{name} must be exactly true or false")
    return raw == "true"


def _integer(env: Mapping[str, str], name: str, default: int, minimum: int) -> int:
    raw = env.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ProductionConfigurationError(f"{name} must be an integer") from exc
    if str(value) != raw or value < minimum:
        raise ProductionConfigurationError(f"{name} must be >= {minimum}")
    return value


def _revision(env: Mapping[str, str], name: str, enabled: bool) -> str | None:
    if not enabled:
        return None
    value = _required(env, name)
    if re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise ProductionConfigurationError(f"{name} must be an exact commit revision")
    return value


def _immutable(env: Mapping[str, str], name: str) -> str:
    value = _required(env, name)
    if value.casefold() in {"latest", "main", "stable", "default"}:
        raise ProductionConfigurationError(f"{name} must be immutable")
    return value


def load_production_config(environ: Mapping[str, str]) -> ProductionConfig:
    legacy_paid_gemini = environ.get(_LEGACY_PAID_GEMINI_SETTING)
    if legacy_paid_gemini is not None and legacy_paid_gemini != "false":
        raise ProductionConfigurationError(
            f"unknown Podcast Subtitle V2 setting: {_LEGACY_PAID_GEMINI_SETTING}"
        )
    unknown = sorted(
        name for name in environ if name.startswith(_PREFIX) and name not in _KNOWN_SETTINGS
    )
    if unknown:
        raise ProductionConfigurationError(f"unknown Podcast Subtitle V2 setting: {unknown[0]}")
    paths = {name: Path(_required(environ, f"{_PREFIX}{name}")) for name in _PATH_SETTINGS}
    qwen = _bool(environ, f"{_PREFIX}ENABLE_QWEN_CORROBORATION")
    faster = _bool(environ, f"{_PREFIX}ENABLE_FASTER_WHISPER_CORROBORATION")
    if (
        _bool(environ, f"{_PREFIX}QWEN_LOCAL_FILES_ONLY", True) is not True
        or _bool(environ, f"{_PREFIX}FASTER_WHISPER_LOCAL_FILES_ONLY", True) is not True
    ):
        raise ProductionConfigurationError("corroborating local ASR must remain local-files-only")
    return ProductionConfig(
        normalized_handoff_manifest=paths["NORMALIZED_HANDOFF_MANIFEST"],
        memo_recognition_manifest=paths["MEMO_RECOGNITION_MANIFEST"],
        memo_recognition_source_export=paths["MEMO_RECOGNITION_SOURCE_EXPORT"],
        memo_recognition_acceptance_receipt=paths["MEMO_RECOGNITION_ACCEPTANCE_RECEIPT"],
        memo_cue_source_export=paths["MEMO_CUE_SOURCE_EXPORT"],
        memo_cue_acceptance_receipt=paths["MEMO_CUE_ACCEPTANCE_RECEIPT"],
        enable_qwen_corroboration=qwen,
        qwen_model=_optional(environ, f"{_PREFIX}QWEN_MODEL", "Qwen/Qwen3-ASR-1.7B"),
        qwen_model_revision=_revision(environ, f"{_PREFIX}QWEN_MODEL_REVISION", qwen),
        qwen_forced_aligner=_optional(
            environ, f"{_PREFIX}QWEN_ALIGNER", "Qwen/Qwen3-ForcedAligner-0.6B"
        ),
        qwen_forced_aligner_revision=_revision(environ, f"{_PREFIX}QWEN_ALIGNER_REVISION", qwen),
        qwen_device=_optional(environ, f"{_PREFIX}QWEN_DEVICE", "cuda:0"),
        qwen_dtype=_optional(environ, f"{_PREFIX}QWEN_DTYPE", "bfloat16"),
        enable_faster_whisper_corroboration=faster,
        faster_whisper_model=_optional(
            environ, f"{_PREFIX}FASTER_WHISPER_MODEL", "Systran/faster-whisper-large-v3"
        ),
        faster_whisper_model_revision=_revision(
            environ, f"{_PREFIX}FASTER_WHISPER_MODEL_REVISION", faster
        ),
        faster_whisper_device=_optional(environ, f"{_PREFIX}FASTER_WHISPER_DEVICE", "cuda"),
        faster_whisper_device_index=_integer(
            environ, f"{_PREFIX}FASTER_WHISPER_DEVICE_INDEX", 0, 0
        ),
        faster_whisper_compute_type=_optional(
            environ, f"{_PREFIX}FASTER_WHISPER_COMPUTE_TYPE", "float16"
        ),
        faster_whisper_cpu_threads=_integer(environ, f"{_PREFIX}FASTER_WHISPER_CPU_THREADS", 0, 0),
        faster_whisper_num_workers=_integer(environ, f"{_PREFIX}FASTER_WHISPER_NUM_WORKERS", 1, 1),
        text_audit_model=_required(environ, f"{_PREFIX}TEXT_AUDIT_MODEL"),
        text_audit_model_version=_immutable(environ, f"{_PREFIX}TEXT_AUDIT_MODEL_VERSION"),
        semantic_model=_required(environ, f"{_PREFIX}SEMANTIC_MODEL"),
        semantic_model_version=_immutable(environ, f"{_PREFIX}SEMANTIC_MODEL_VERSION"),
        audio_audit_model=_required(environ, f"{_PREFIX}AUDIO_AUDIT_MODEL"),
        audio_audit_model_version=_immutable(environ, f"{_PREFIX}AUDIO_AUDIT_MODEL_VERSION"),
        ffmpeg_executable=_optional(environ, f"{_PREFIX}FFMPEG", "ffmpeg"),
        ffprobe_executable=_optional(environ, f"{_PREFIX}FFPROBE", "ffprobe"),
        enable_mic_speaker_attribution=_bool(environ, f"{_PREFIX}ENABLE_MIC_SPEAKER_ATTRIBUTION"),
    )


def _transitional_identity(identity: object) -> AdapterIdentity:
    return AdapterIdentity(
        name=str(getattr(identity, "adapter_name")),
        version=str(getattr(identity, "adapter_version")),
        config_hash=str(getattr(identity, "config_hash")),
        execution_mode=getattr(identity, "execution_mode"),
    )


def build_production(context: FactoryContextV1) -> PodcastSubtitleV2:
    if not isinstance(context, FactoryContextV1) or context.protocol_version != 1:
        raise ProductionConfigurationError("build_production requires FactoryContextV1")
    config = load_production_config(os.environ)
    workspace = context.episode_root / ".subtitle-v2" / "subscription-work"
    repository = GenerationStore(context.episode_root).recognition_run_repository()
    memo = MemoRecognizerAdapter(
        config.memo_recognition_manifest,
        source_export=config.memo_recognition_source_export,
        acceptance_receipt=config.memo_recognition_acceptance_receipt,
    )
    recognizers: list[object] = [memo]
    if config.enable_qwen_corroboration:
        assert config.qwen_model_revision and config.qwen_forced_aligner_revision
        recognizers.append(
            Qwen3ASRRecognizerAdapter(
                model=config.qwen_model,
                model_revision=config.qwen_model_revision,
                forced_aligner=config.qwen_forced_aligner,
                forced_aligner_revision=config.qwen_forced_aligner_revision,
                device=config.qwen_device,
                dtype=config.qwen_dtype,
                local_files_only=True,
                recognition_run_repository=repository,
                logical_namespace=Qwen3ASRRecognizerAdapter.RECOGNITION_RUN_NAMESPACE,
            )
        )
    if config.enable_faster_whisper_corroboration:
        assert config.faster_whisper_model_revision
        recognizers.append(
            FasterWhisperRecognizerAdapter(
                model=config.faster_whisper_model,
                model_revision=config.faster_whisper_model_revision,
                device=config.faster_whisper_device,
                device_index=config.faster_whisper_device_index,
                compute_type=config.faster_whisper_compute_type,
                cpu_threads=config.faster_whisper_cpu_threads,
                num_workers=config.faster_whisper_num_workers,
                local_files_only=True,
                recognition_run_repository=repository,
                logical_namespace=FasterWhisperRecognizerAdapter.RECOGNITION_RUN_NAMESPACE,
            )
        )
    native = NativeFullAuditBundle(
        text_executor=TextFullAuditExecutor(
            identity=build_text_audit_adapter_identity(
                adapter="nakama-text-full-audit-subscription-worker",
                adapter_version="2",
                model=config.text_audit_model,
                model_version=config.text_audit_model_version,
                execution_mode="subscription",
            ),
            workspace_root=workspace,
        ),
        audio_executor=AudioFullAuditExecutor(
            identity=build_audio_audit_adapter_identity(
                adapter="nakama-audio-full-audit-subscription-worker",
                adapter_version="2",
                model=config.audio_audit_model,
                model_version=config.audio_audit_model_version,
                execution_mode="subscription",
            ),
            workspace_root=workspace,
        ),
        audio_selection_policy=default_audio_audit_selection_policy(),
    )
    semantic = SemanticAnalyzerAdapter(
        model=config.semantic_model,
        model_version=config.semantic_model_version,
        workspace_root=workspace,
        allow_paid_api=False,
        execution_mode="subscription",
    )
    bundle = context.reference_bundle

    def boundary_factory(evidence):
        return MemoSrtBoundaryAuthorityV1.load_verified(
            config.memo_cue_source_export,
            recognition_evidence=evidence,
            acceptance_receipt=config.memo_cue_acceptance_receipt,
        )

    speaker = MicEnergySpeakerAttributor() if config.enable_mic_speaker_attribution else None
    module = PodcastSubtitleV2(
        context.episode_root,
        normalizer=VerifiedNormalizedAudioHandoffAdapter(config.normalized_handoff_manifest),
        recognizers=tuple(recognizers),
        semantic_analyzer=semantic,
        native_full_audit=native,
        semantic_analyzer_identity=_transitional_identity(semantic.identity),
        speaker_attributor=speaker,
        speaker_attributor_identity=(
            AdapterIdentity(
                name=speaker.adapter_name,
                version=speaker.adapter_version,
                config_hash=speaker.adapter_config_hash,
                execution_mode="local",
            )
            if speaker
            else None
        ),
        reference_retriever=bundle.retriever if bundle else None,
        reference_retriever_identity=bundle.retriever_identity if bundle else None,
        reference_parser_registry=bundle.parser_registry if bundle else None,
        speech_coverage_analyzer=FFmpegSpeechCoverageAnalyzer(
            ffmpeg_executable=config.ffmpeg_executable, ffprobe_executable=config.ffprobe_executable
        ),
        recognition_independence_policy=(
            PRODUCTION_RECOGNITION_INDEPENDENCE_POLICY if len(recognizers) > 1 else None
        ),
        memo_boundary_authority_factory=boundary_factory,
        code_version=("production-source-inventory-v1:" + production_source_inventory_digest()),
    )
    if bundle:
        bundle.assert_module_binding(module)
    return module


__all__ = [
    "ProductionConfig",
    "ProductionConfigurationError",
    "build_production",
    "load_production_config",
    "production_source_inventory_digest",
]
