"""Explicit valid Reference Authority descriptors for test artifacts.

These helpers keep fixtures honest: production contracts never infer or default
authority when a caller omits it.
"""

from __future__ import annotations

from shared.schemas.podcast_subtitles_v2 import (
    ReferenceAuthorityAttestation,
    ReferenceAuthorityDescriptor,
    ReferenceAuthorityPrincipal,
)


def reference_authority_fixture(
    *,
    source_id: str,
    kind: str,
    title: str,
    version: str,
    trust_tier: str,
    author: str | None = None,
    publisher: str | None = None,
) -> ReferenceAuthorityDescriptor:
    owner = (
        ReferenceAuthorityPrincipal(
            kind="person",
            stable_id=f"owner:{source_id}",
            display_name=author,
        )
        if author is not None
        else None
    )
    if kind == "book" and trust_tier == "authoritative":
        if owner is None or publisher is None:
            raise ValueError("authoritative book fixture requires author and publisher")
        role = "published_author_book"
        release_status = "published"
        subject_kind = "publication"
        allowed_scopes = (
            "source_title",
            "source_author",
            "literal_terminology",
            "verbatim_source_text",
        )
        attestation = ReferenceAuthorityAttestation(
            confirmed=True,
            provenance="publisher_record",
            attestor=ReferenceAuthorityPrincipal(
                kind="organization",
                stable_id=f"publisher:{source_id}",
                display_name=publisher,
            ),
            record_sha256="f" * 64,
        )
    else:
        role = "curated_reference" if trust_tier == "curated" else "contextual_reference"
        release_status = "not_applicable"
        subject_kind = (
            "report"
            if kind == "research_report"
            else "episode"
            if kind == "interview_outline"
            else "other"
        )
        allowed_scopes = ()
        attestation = ReferenceAuthorityAttestation(
            confirmed=False,
            provenance="none",
            attestor=None,
            record_sha256=None,
        )
    return ReferenceAuthorityDescriptor(
        logical_source_id=f"logical:{source_id}",
        version_id=version,
        version_status="active",
        release_status=release_status,  # type: ignore[arg-type]
        source_kind=kind,  # type: ignore[arg-type]
        trust_tier=trust_tier,  # type: ignore[arg-type]
        role=role,  # type: ignore[arg-type]
        subject=ReferenceAuthorityPrincipal(
            kind=subject_kind,  # type: ignore[arg-type]
            stable_id=f"subject:{source_id}",
            display_name=title,
        ),
        owner=owner,
        allowed_scopes=allowed_scopes,  # type: ignore[arg-type]
        attestation=attestation,
    )


__all__ = ["reference_authority_fixture"]
