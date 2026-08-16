"""Versioned operator composition for explicit episode Reference Evidence.

The generic CLI owns enrollment because source validation must finish before a
factory can initialize Auphonic or any model provider.  A trusted factory then
receives the exact frozen retriever, parser registry, and Enrollment bytes it
must install in the Module; it cannot silently rebuild or ignore that index.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    ValidationError,
    field_validator,
    model_validator,
)

from shared.schemas.podcast_subtitles_v2 import (
    ReferenceAuthorityDescriptor,
    validate_reference_authority_binding,
)

from .adapters.reference import (
    LocalReferenceRetriever,
    ReferenceKind,
    ReferenceSourceSpec,
    ReferenceTrustTier,
    TrustedReferenceParserRegistry,
    default_trusted_parser_registry,
)
from .hashing import hash_object, sha256_bytes
from .module import (
    AdapterIdentity,
    PodcastSubtitleV2,
    ReferenceEnrollment,
)
from .ports import AdapterError


class ReferenceManifestError(ValueError):
    """The caller-provided reference manifest is malformed or no longer exact."""


class _ManifestContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReferenceManifestSourceV2(_ManifestContract):
    """One explicit source; no folder or vault enrollment is permitted."""

    source_id: str
    kind: ReferenceKind
    path: Path
    title: str
    version: str
    document_date: str
    trust_tier: ReferenceTrustTier = "contextual"
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: StrictInt = Field(gt=0)
    author: str | None = None
    publisher: str | None = None
    authority: ReferenceAuthorityDescriptor

    @field_validator("document_date")
    @classmethod
    def _valid_document_date(cls, value: str) -> str:
        if value == "undated":
            return value
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("document_date must be YYYY-MM-DD or 'undated'") from exc
        if parsed.isoformat() != value:
            raise ValueError("document_date must use canonical YYYY-MM-DD")
        return value

    @model_validator(mode="after")
    def _role_appropriate_authority(self) -> ReferenceManifestSourceV2:
        validate_reference_authority_binding(
            self.authority,
            kind=self.kind,
            title=self.title,
            author=self.author,
            publisher=self.publisher,
            version=self.version,
            trust_tier=self.trust_tier,
        )
        return self


class ReferenceManifestV2(_ManifestContract):
    schema_version: Literal[2]
    episode_id: str
    sources: tuple[ReferenceManifestSourceV2, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_sources_and_acyclic_versions(self) -> ReferenceManifestV2:
        source_ids = [item.source_id for item in self.sources]
        if len(set(source_ids)) != len(source_ids):
            raise ValueError("reference manifest source_id values must be unique")
        digests = [item.sha256 for item in self.sources]
        if len(set(digests)) != len(digests):
            raise ValueError(
                "reference manifest cannot enroll the same source bytes more than once"
            )
        if not self.episode_id.strip():
            raise ValueError("reference manifest episode_id must not be blank")

        by_version: dict[tuple[str, str], ReferenceManifestSourceV2] = {}
        active_by_logical: dict[str, list[str]] = {}
        for item in self.sources:
            authority = item.authority
            key = (authority.logical_source_id, authority.version_id)
            if key in by_version:
                raise ValueError(
                    "reference manifest logical_source_id/version_id pairs must be unique"
                )
            by_version[key] = item
            if authority.version_status == "active":
                active_by_logical.setdefault(authority.logical_source_id, []).append(
                    authority.version_id
                )
            if item.kind == "interview_outline":
                if authority.subject.kind != "episode" or (
                    authority.subject.stable_id != self.episode_id
                ):
                    raise ValueError(
                        "interview_outline authority subject must bind the manifest episode_id"
                    )
        conflicting_active = {
            logical_id: versions
            for logical_id, versions in active_by_logical.items()
            if len(versions) > 1
        }
        if conflicting_active:
            raise ValueError(
                "reference manifest has multiple active versions for a logical source: "
                f"{conflicting_active!r}"
            )

        graph: dict[tuple[str, str], tuple[tuple[str, str], ...]] = {}
        for key, item in by_version.items():
            predecessors = tuple(
                (reference.logical_source_id, reference.version_id)
                for reference in item.authority.supersedes
                if (reference.logical_source_id, reference.version_id) in by_version
            )
            for predecessor in predecessors:
                if by_version[predecessor].authority.version_status != "superseded":
                    raise ValueError(
                        "an explicitly superseded enrolled version must have "
                        "version_status='superseded'"
                    )
            graph[key] = predecessors

        visiting: set[tuple[str, str]] = set()
        visited: set[tuple[str, str]] = set()

        def visit(node: tuple[str, str]) -> None:
            if node in visiting:
                raise ValueError("reference manifest contains a supersedes cycle")
            if node in visited:
                return
            visiting.add(node)
            for predecessor in graph[node]:
                visit(predecessor)
            visiting.remove(node)
            visited.add(node)

        for node in graph:
            visit(node)
        return self


@dataclass(frozen=True, slots=True)
class ReferenceOperatorBundleV1:
    """Frozen enrollment handed intact to one versioned composition factory."""

    protocol_version: Literal[1]
    episode_id: str
    manifest_path: Path
    manifest_sha256: str
    source_specs: tuple[ReferenceSourceSpec, ...]
    retriever: LocalReferenceRetriever
    parser_registry: TrustedReferenceParserRegistry
    retriever_identity: AdapterIdentity
    enrollments: tuple[ReferenceEnrollment, ...]
    index_hash: str

    @property
    def content_hash(self) -> str:
        return hash_object(
            {
                "protocol_version": self.protocol_version,
                "episode_id": self.episode_id,
                "manifest_sha256": self.manifest_sha256,
                "index_hash": self.index_hash,
                "retriever_identity": self.retriever_identity,
                "artifacts": tuple(item.artifact for item in self.enrollments),
            }
        )

    def assert_module_binding(self, module: PodcastSubtitleV2) -> None:
        module.assert_reference_composition(
            retriever=self.retriever,
            identity=self.retriever_identity,
            parser_registry=self.parser_registry,
            index_hash=self.index_hash,
        )


@dataclass(frozen=True, slots=True)
class FactoryContextV1:
    """The sole V2 CLI factory argument; future protocols get a new type/version."""

    protocol_version: Literal[1]
    episode_root: Path
    reference_bundle: ReferenceOperatorBundleV1 | None

    @property
    def reference_enrollments(self) -> tuple[ReferenceEnrollment, ...]:
        return self.reference_bundle.enrollments if self.reference_bundle is not None else ()


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ReferenceManifestError(f"reference manifest repeats JSON key {key!r}")
        result[key] = value
    return result


def _read_manifest_once(path: Path) -> bytes:
    original = path.absolute()
    try:
        before = os.lstat(original)
    except OSError as exc:
        raise ReferenceManifestError(f"reference manifest is missing: {path}") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise ReferenceManifestError("reference manifest must be a regular non-symlink file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(original, flags)
    except OSError as exc:
        raise ReferenceManifestError("reference manifest could not be opened safely") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ReferenceManifestError("reference manifest is not a regular file")
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            payload = stream.read()
            after = os.fstat(stream.fileno())
        if (
            opened.st_dev != after.st_dev
            or opened.st_ino != after.st_ino
            or opened.st_size != after.st_size
            or opened.st_mtime_ns != after.st_mtime_ns
            or len(payload) != after.st_size
        ):
            raise ReferenceManifestError("reference manifest changed while reading")
        return payload
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def load_reference_manifest(path: str | Path) -> tuple[ReferenceManifestV2, bytes]:
    manifest_path = Path(path)
    payload = _read_manifest_once(manifest_path)
    try:
        decoded = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReferenceManifestError("reference manifest must be UTF-8 JSON") from exc
    try:
        raw = json.loads(decoded, object_pairs_hook=_reject_duplicate_json_keys)
        manifest = ReferenceManifestV2.model_validate(raw)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ReferenceManifestError(f"invalid reference manifest: {exc}") from exc
    return manifest, payload


def build_reference_bundle(
    manifest_path: str | Path,
    *,
    episode_root: str | Path,
    expected_episode_id: str | None,
) -> ReferenceOperatorBundleV1:
    """Validate, snapshot, parse, and bind references before provider creation."""

    manifest_file = Path(manifest_path).resolve()
    manifest, manifest_bytes = load_reference_manifest(manifest_file)
    if expected_episode_id is not None and manifest.episode_id != expected_episode_id:
        raise ReferenceManifestError(
            "reference manifest episode_id does not match the requested episode"
        )
    manifest_sha256 = sha256_bytes(manifest_bytes)
    source_specs: list[ReferenceSourceSpec] = []
    expected: dict[str, ReferenceManifestSourceV2] = {}
    resolved_paths: set[str] = set()
    for item in manifest.sources:
        source_path = item.path
        if not source_path.is_absolute():
            source_path = manifest_file.parent / source_path
        source_path = source_path.absolute()
        path_key = os.path.normcase(str(source_path))
        if path_key in resolved_paths:
            raise ReferenceManifestError("reference manifest cannot enroll one path more than once")
        resolved_paths.add(path_key)
        expected[item.source_id] = item
        try:
            source_specs.append(
                ReferenceSourceSpec(
                    path=source_path,
                    source_id=item.source_id,
                    kind=item.kind,
                    title=item.title,
                    version=item.version,
                    document_date=item.document_date,
                    enrollment_manifest_sha256=manifest_sha256,
                    trust_tier=item.trust_tier,
                    authority=item.authority,
                    author=item.author,
                    publisher=item.publisher,
                )
            )
        except ValueError as exc:
            raise ReferenceManifestError(str(exc)) from exc

    root = Path(episode_root).resolve()
    parser_registry = default_trusted_parser_registry()
    try:
        retriever = LocalReferenceRetriever(
            root / ".subtitle-v2" / "reference-operator" / manifest_sha256,
            tuple(source_specs),
            trusted_parser_registry=parser_registry,
        )
    except (OSError, ValueError, AdapterError) as exc:
        raise ReferenceManifestError(f"reference enrollment failed: {exc}") from exc
    for artifact in retriever.index.artifacts:
        declared = expected[artifact.source_id]
        if (
            artifact.digest.sha256 != declared.sha256
            or artifact.digest.size_bytes != declared.size_bytes
            or artifact.enrollment_manifest_sha256 != manifest_sha256
        ):
            raise ReferenceManifestError(
                f"reference source bytes drifted from manifest: {artifact.source_id}"
            )
    identity = AdapterIdentity(
        name="local-reference",
        version=LocalReferenceRetriever.RETRIEVER_VERSION,
        config_hash=hash_object(
            {
                "operator_protocol": 1,
                "index_hash": retriever.index.index_hash,
                "manifest_sha256": manifest_sha256,
            }
        ),
        execution_mode="local",
    )
    enrollments = tuple(
        ReferenceEnrollment(
            artifact=artifact,
            source_snapshot=retriever.source_snapshot(artifact),
            extraction_snapshot=retriever.extraction_snapshot(artifact),
        )
        for artifact in retriever.index.artifacts
    )
    return ReferenceOperatorBundleV1(
        protocol_version=1,
        episode_id=manifest.episode_id,
        manifest_path=manifest_file,
        manifest_sha256=manifest_sha256,
        source_specs=tuple(source_specs),
        retriever=retriever,
        parser_registry=parser_registry,
        retriever_identity=identity,
        enrollments=enrollments,
        index_hash=retriever.index.index_hash,
    )


def build_factory_context(
    *,
    episode_root: str | Path,
    episode_id: str | None = None,
    reference_manifest: str | Path | None = None,
) -> FactoryContextV1:
    root = Path(episode_root).resolve()
    bundle = (
        build_reference_bundle(
            reference_manifest,
            episode_root=root,
            expected_episode_id=episode_id,
        )
        if reference_manifest is not None
        else None
    )
    return FactoryContextV1(protocol_version=1, episode_root=root, reference_bundle=bundle)


__all__ = [
    "FactoryContextV1",
    "ReferenceManifestError",
    "ReferenceManifestSourceV2",
    "ReferenceManifestV2",
    "ReferenceOperatorBundleV1",
    "build_factory_context",
    "build_reference_bundle",
    "load_reference_manifest",
]
