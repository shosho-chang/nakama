#!/usr/bin/env python3
"""Prepare and explicitly accept strict Podcast Subtitle V2 references."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.brook.podcast_subtitles.composition import (  # noqa: E402
    ReferenceManifestSourceV2,
    ReferenceManifestV2,
)
from agents.brook.podcast_subtitles.hashing import (  # noqa: E402
    canonical_json_bytes,
    measure_regular_file,
)
from shared.schemas.podcast_subtitles_v2 import (  # noqa: E402
    ReferenceAuthorityAttestation,
    ReferenceAuthorityDescriptor,
    ReferenceAuthorityPrincipal,
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _SourcePlanV1(_StrictModel):
    source_id: str
    kind: Literal[
        "book",
        "research_report",
        "interview_outline",
        "knowledge_base",
    ]
    path: Path
    title: str
    version: str
    document_date: str
    author: str | None = None
    publisher: str | None = None


class _ReferencePlanV1(_StrictModel):
    schema_version: Literal[1] = 1
    episode_id: str
    sources: tuple[_SourcePlanV1, ...] = Field(min_length=1)


class _ReferenceReviewSourceV1(_SourcePlanV1):
    sha256: str
    size_bytes: int = Field(gt=0)


class _ReferenceReviewV1(_StrictModel):
    schema_version: Literal[1] = 1
    contract: Literal["podcast-reference-review-v1"] = "podcast-reference-review-v1"
    episode_id: str
    sources: tuple[_ReferenceReviewSourceV1, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self) -> "_ReferenceReviewV1":
        source_ids = tuple(source.source_id for source in self.sources)
        if len(set(source_ids)) != len(source_ids):
            raise ValueError("reference review source IDs must be unique")
        return self


class _AuthorityAttestationV1(_StrictModel):
    schema_version: Literal[1] = 1
    contract: Literal["podcast-reference-authority-attestation-v1"] = (
        "podcast-reference-authority-attestation-v1"
    )
    confirmed: Literal[True]
    source_id: str
    source_sha256: str
    source_size_bytes: int = Field(gt=0)
    role: Literal[
        "published_author_book",
        "owner_final_report",
        "owner_approved_outline_glossary",
    ]
    provenance: Literal[
        "publisher_record",
        "author_record",
        "owner_record",
        "owner_approval_record",
    ]
    attestor: ReferenceAuthorityPrincipal
    reviewer: str
    accepted_at: datetime

    @model_validator(mode="after")
    def _explicit(self) -> "_AuthorityAttestationV1":
        if (
            not self.source_id.strip()
            or not self.reviewer.strip()
            or self.reviewer != self.reviewer.strip()
            or self.accepted_at.tzinfo is None
            or len(self.source_sha256) != 64
            or any(char not in "0123456789abcdef" for char in self.source_sha256)
        ):
            raise ValueError("reference authority attestation is incomplete")
        return self


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_canonical(path: Path, model: type[BaseModel]) -> BaseModel:
    payload = path.read_bytes()
    json.loads(payload, object_pairs_hook=_pairs)
    value = model.model_validate_json(payload, strict=True)
    if canonical_json_bytes(value) != payload:
        raise ValueError(f"{path} must use canonical JSON bytes")
    return value


def _write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(payload)


def _prepare(args: argparse.Namespace) -> int:
    plan_path = Path(args.source_plan)
    loaded = _load_canonical(plan_path, _ReferencePlanV1)
    assert isinstance(loaded, _ReferencePlanV1)
    sources: list[_ReferenceReviewSourceV1] = []
    for source in loaded.sources:
        path = source.path
        if not path.is_absolute():
            path = (plan_path.parent / path).resolve()
        digest, size = measure_regular_file(path)
        sources.append(
            _ReferenceReviewSourceV1(
                **source.model_dump(exclude={"path"}),
                path=path,
                sha256=digest,
                size_bytes=size,
            )
        )
    review = _ReferenceReviewV1(episode_id=loaded.episode_id, sources=tuple(sources))
    _write_new(Path(args.output), canonical_json_bytes(review))
    return 0


def _principal_kind(kind: str) -> str:
    return {
        "book": "publication",
        "research_report": "report",
        "interview_outline": "episode",
        "knowledge_base": "other",
    }[kind]


def _contextual_authority(
    source: _ReferenceReviewSourceV1, *, episode_id: str
) -> ReferenceAuthorityDescriptor:
    owner = (
        ReferenceAuthorityPrincipal(
            kind="person",
            stable_id=f"person:{source.source_id}",
            display_name=source.author,
        )
        if source.author is not None
        else None
    )
    return ReferenceAuthorityDescriptor(
        logical_source_id=f"reference:{source.source_id}",
        version_id=source.version,
        version_status="active",
        release_status="not_applicable",
        source_kind=source.kind,
        trust_tier="contextual",
        role="contextual_reference",
        subject=ReferenceAuthorityPrincipal(
            kind=_principal_kind(source.kind),
            stable_id=(
                episode_id
                if source.kind == "interview_outline"
                else f"source:{source.source_id}"
            ),
            display_name=source.title,
        ),
        owner=owner,
        allowed_scopes=(),
        attestation=ReferenceAuthorityAttestation(
            confirmed=False,
            provenance="none",
            attestor=None,
            record_sha256=None,
        ),
    )


def _attested_authority(
    source: _ReferenceReviewSourceV1,
    *,
    episode_id: str,
    attestation: _AuthorityAttestationV1,
    attestation_sha256: str,
) -> ReferenceAuthorityDescriptor:
    if (attestation.source_sha256, attestation.source_size_bytes) != (
        source.sha256,
        source.size_bytes,
    ):
        raise ValueError("authority attestation belongs to other source bytes")
    if attestation.role == "published_author_book":
        if source.kind != "book" or source.author is None:
            raise ValueError("published book authority requires book author metadata")
        if attestation.provenance == "author_record":
            if attestation.attestor.kind != "person" or (
                attestation.attestor.display_name != source.author
            ):
                raise ValueError("book author attestor must equal source author")
            owner = attestation.attestor
        elif attestation.provenance == "publisher_record":
            if (
                source.publisher is None
                or attestation.attestor.kind != "organization"
                or attestation.attestor.display_name != source.publisher
            ):
                raise ValueError("book publisher attestor must equal source publisher")
            owner = ReferenceAuthorityPrincipal(
                kind="person",
                stable_id=f"person:{source.source_id}",
                display_name=source.author,
            )
        else:
            raise ValueError("published book authority requires author/publisher provenance")
        trust_tier = "authoritative"
        release_status = "published"
        subject_kind = "publication"
        subject_id = f"publication:{source.source_id}"
        scopes = (
            "source_title",
            "source_author",
            "literal_terminology",
            "verbatim_source_text",
        )
    elif attestation.role == "owner_final_report":
        if (
            source.kind != "research_report"
            or source.author is None
            or attestation.provenance != "owner_record"
            or attestation.attestor.kind != "person"
            or attestation.attestor.display_name != source.author
        ):
            raise ValueError("final report authority requires its owner attestation")
        owner = attestation.attestor
        trust_tier = "authoritative"
        release_status = "final"
        subject_kind = "report"
        subject_id = f"report:{source.source_id}"
        scopes = ("source_title", "literal_terminology", "verbatim_source_text")
    else:
        if (
            source.kind != "interview_outline"
            or source.author is None
            or attestation.provenance != "owner_approval_record"
            or attestation.attestor.kind != "person"
            or attestation.attestor.display_name != source.author
        ):
            raise ValueError("outline glossary authority requires its owner attestation")
        owner = attestation.attestor
        trust_tier = "curated"
        release_status = "approved"
        subject_kind = "episode"
        subject_id = episode_id
        scopes = ("owner_approved_glossary_spelling",)
    return ReferenceAuthorityDescriptor(
        logical_source_id=f"reference:{source.source_id}",
        version_id=source.version,
        version_status="active",
        release_status=release_status,
        source_kind=source.kind,
        trust_tier=trust_tier,
        role=attestation.role,
        subject=ReferenceAuthorityPrincipal(
            kind=subject_kind,
            stable_id=subject_id,
            display_name=source.title,
        ),
        owner=owner,
        allowed_scopes=scopes,
        attestation=ReferenceAuthorityAttestation(
            confirmed=True,
            provenance=attestation.provenance,
            attestor=attestation.attestor,
            record_sha256=attestation_sha256,
        ),
    )


def _load_attestations(
    paths: list[str], *, reviewer: str
) -> dict[str, tuple[_AuthorityAttestationV1, str]]:
    attestations: dict[str, tuple[_AuthorityAttestationV1, str]] = {}
    for raw_path in paths:
        path = Path(raw_path)
        loaded = _load_canonical(path, _AuthorityAttestationV1)
        assert isinstance(loaded, _AuthorityAttestationV1)
        if loaded.reviewer != reviewer:
            raise ValueError("authority attestation reviewer differs from manifest reviewer")
        if loaded.source_id in attestations:
            raise ValueError("duplicate authority attestation for one source")
        digest, _ = measure_regular_file(path)
        attestations[loaded.source_id] = (loaded, digest)
    return attestations


def _accept(args: argparse.Namespace) -> int:
    review_path = Path(args.review)
    loaded = _load_canonical(review_path, _ReferenceReviewV1)
    assert isinstance(loaded, _ReferenceReviewV1)
    accepted_at = datetime.fromisoformat(args.accepted_at)
    if accepted_at.tzinfo is None or not args.reviewer.strip():
        raise ValueError("reference acceptance requires reviewer and timezone-aware time")
    attestations = _load_attestations(args.authority_attestation, reviewer=args.reviewer)
    known_source_ids = {source.source_id for source in loaded.sources}
    unknown = set(attestations) - known_source_ids
    if unknown:
        raise ValueError(f"authority attestation references unknown source: {sorted(unknown)!r}")
    sources: list[ReferenceManifestSourceV2] = []
    for source in loaded.sources:
        digest, size = measure_regular_file(source.path)
        if (digest, size) != (source.sha256, source.size_bytes):
            raise ValueError(f"reference source drifted after review: {source.source_id}")
        attested = attestations.get(source.source_id)
        authority = (
            _attested_authority(
                source,
                episode_id=loaded.episode_id,
                attestation=attested[0],
                attestation_sha256=attested[1],
            )
            if attested is not None
            else _contextual_authority(source, episode_id=loaded.episode_id)
        )
        sources.append(
            ReferenceManifestSourceV2(
                source_id=source.source_id,
                kind=source.kind,
                path=source.path,
                title=source.title,
                version=source.version,
                document_date=source.document_date,
                trust_tier=authority.trust_tier,
                sha256=source.sha256,
                size_bytes=source.size_bytes,
                author=source.author,
                publisher=source.publisher,
                authority=authority,
            )
        )
    manifest = ReferenceManifestV2(
        schema_version=2,
        episode_id=loaded.episode_id,
        sources=tuple(sources),
    )
    _write_new(Path(args.output), canonical_json_bytes(manifest))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--source-plan", required=True)
    prepare.add_argument("--output", required=True)
    prepare.set_defaults(handler=_prepare)
    accept = commands.add_parser("accept")
    accept.add_argument("--review", required=True)
    accept.add_argument("--reviewer", required=True)
    accept.add_argument("--accepted-at", required=True)
    accept.add_argument("--confirm-reviewed", action="store_true", required=True)
    accept.add_argument("--authority-attestation", action="append", default=[])
    accept.add_argument("--output", required=True)
    accept.set_defaults(handler=_accept)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
