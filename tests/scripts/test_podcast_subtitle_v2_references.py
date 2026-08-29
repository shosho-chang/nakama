"""Operator builder for strict Podcast Subtitle V2 Reference Manifests."""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.brook.podcast_subtitles.composition import (
    build_reference_bundle,
    load_reference_manifest,
)
from agents.brook.podcast_subtitles.hashing import canonical_json_bytes, hash_file
from scripts import podcast_subtitle_v2_references as references_cli


def test_outline_defaults_to_contextual_without_any_authority_scope(tmp_path: Path) -> None:
    outline = tmp_path / "訪綱.md"
    outline.write_text("# 抹布訪綱\n\n高薪賽道與科技工作講。\n", encoding="utf-8")
    plan = tmp_path / "reference-plan.json"
    plan.write_bytes(
        canonical_json_bytes(
            {
                "episode_id": "20260814-moboo",
                "schema_version": 1,
                "sources": [
                    {
                        "author": "修修",
                        "document_date": "2026-08-13",
                        "kind": "interview_outline",
                        "path": str(outline),
                        "publisher": None,
                        "source_id": "moboo-interview-outline-v1",
                        "title": "抹布訪綱",
                        "version": "v1",
                    }
                ],
            }
        )
    )
    review = tmp_path / "reference-review.json"
    manifest_path = tmp_path / "episode-references.v2.json"

    assert (
        references_cli.main(["prepare", "--source-plan", str(plan), "--output", str(review)]) == 0
    )
    assert (
        references_cli.main(
            [
                "accept",
                "--review",
                str(review),
                "--reviewer",
                "shosho",
                "--accepted-at",
                "2026-08-19T03:00:00+08:00",
                "--confirm-reviewed",
                "--output",
                str(manifest_path),
            ]
        )
        == 0
    )

    manifest, _ = load_reference_manifest(manifest_path)
    source = manifest.sources[0]
    assert source.trust_tier == "contextual"
    assert source.authority.role == "contextual_reference"
    assert source.authority.allowed_scopes == ()
    assert source.authority.attestation.confirmed is False
    assert source.authority.subject.stable_id == manifest.episode_id
    bundle = build_reference_bundle(
        manifest_path,
        episode_root=tmp_path / "episode",
        expected_episode_id="20260814-moboo",
    )
    assert tuple(spec.source_id for spec in bundle.source_specs) == ("moboo-interview-outline-v1",)


def test_authority_requires_an_explicit_source_bound_user_attestation(tmp_path: Path) -> None:
    manuscript = tmp_path / "高薪賽道.txt"
    manuscript.write_text("高薪賽道\n", encoding="utf-8")
    plan = tmp_path / "reference-plan.json"
    plan.write_bytes(
        canonical_json_bytes(
            {
                "episode_id": "20260814-moboo",
                "schema_version": 1,
                "sources": [
                    {
                        "author": "抹布",
                        "document_date": "2026-08-01",
                        "kind": "book",
                        "path": str(manuscript),
                        "publisher": None,
                        "source_id": "gaoxin-saidou-manuscript",
                        "title": "高薪賽道",
                        "version": "owner-final-v1",
                    }
                ],
            }
        )
    )
    review = tmp_path / "reference-review.json"
    references_cli.main(["prepare", "--source-plan", str(plan), "--output", str(review)])
    attestation = tmp_path / "book-authority-attestation.json"
    attestation.write_bytes(
        canonical_json_bytes(
            {
                "accepted_at": "2026-08-19T03:00:00+08:00",
                "attestor": {
                    "display_name": "抹布",
                    "kind": "person",
                    "schema_version": 1,
                    "stable_id": "person:moboo",
                },
                "confirmed": True,
                "contract": "podcast-reference-authority-attestation-v1",
                "provenance": "author_record",
                "reviewer": "shosho",
                "role": "published_author_book",
                "schema_version": 1,
                "source_id": "gaoxin-saidou-manuscript",
                "source_sha256": hash_file(manuscript),
                "source_size_bytes": manuscript.stat().st_size,
            }
        )
    )
    manifest_path = tmp_path / "episode-references.v2.json"

    assert (
        references_cli.main(
            [
                "accept",
                "--review",
                str(review),
                "--reviewer",
                "shosho",
                "--accepted-at",
                "2026-08-19T03:05:00+08:00",
                "--confirm-reviewed",
                "--authority-attestation",
                str(attestation),
                "--output",
                str(manifest_path),
            ]
        )
        == 0
    )

    manifest, _ = load_reference_manifest(manifest_path)
    source = manifest.sources[0]
    assert source.trust_tier == "authoritative"
    assert source.authority.role == "published_author_book"
    assert source.authority.allowed_scopes == (
        "source_title",
        "source_author",
        "literal_terminology",
        "verbatim_source_text",
    )
    assert source.authority.attestation.record_sha256 == hash_file(attestation)


def test_reference_acceptance_fails_when_source_bytes_drift(tmp_path: Path) -> None:
    outline, review = _prepared_outline_review(tmp_path)
    outline.write_text("changed after review", encoding="utf-8")
    with pytest.raises(ValueError, match="drifted after review"):
        references_cli.main(
            [
                "accept",
                "--review",
                str(review),
                "--reviewer",
                "shosho",
                "--accepted-at",
                "2026-08-19T03:05:00+08:00",
                "--confirm-reviewed",
                "--output",
                str(tmp_path / "episode-references.v2.json"),
            ]
        )


def test_attestation_cannot_escalate_outline_to_book_authority(tmp_path: Path) -> None:
    outline, review = _prepared_outline_review(tmp_path)
    attestation = tmp_path / "invalid-attestation.json"
    attestation.write_bytes(
        canonical_json_bytes(
            {
                "accepted_at": "2026-08-19T03:00:00+08:00",
                "attestor": {
                    "display_name": "修修",
                    "kind": "person",
                    "schema_version": 1,
                    "stable_id": "person:shosho",
                },
                "confirmed": True,
                "contract": "podcast-reference-authority-attestation-v1",
                "provenance": "author_record",
                "reviewer": "shosho",
                "role": "published_author_book",
                "schema_version": 1,
                "source_id": "moboo-interview-outline-v1",
                "source_sha256": hash_file(outline),
                "source_size_bytes": outline.stat().st_size,
            }
        )
    )
    with pytest.raises(ValueError, match="requires book author metadata"):
        references_cli.main(
            [
                "accept",
                "--review",
                str(review),
                "--reviewer",
                "shosho",
                "--accepted-at",
                "2026-08-19T03:05:00+08:00",
                "--confirm-reviewed",
                "--authority-attestation",
                str(attestation),
                "--output",
                str(tmp_path / "episode-references.v2.json"),
            ]
        )


def _prepared_outline_review(tmp_path: Path) -> tuple[Path, Path]:
    outline = tmp_path / "訪綱.md"
    outline.write_text("# 抹布訪綱\n", encoding="utf-8")
    plan = tmp_path / "reference-plan.json"
    plan.write_bytes(
        canonical_json_bytes(
            {
                "episode_id": "20260814-moboo",
                "schema_version": 1,
                "sources": [
                    {
                        "author": "修修",
                        "document_date": "2026-08-13",
                        "kind": "interview_outline",
                        "path": str(outline),
                        "publisher": None,
                        "source_id": "moboo-interview-outline-v1",
                        "title": "抹布訪綱",
                        "version": "v1",
                    }
                ],
            }
        )
    )
    review = tmp_path / "reference-review.json"
    references_cli.main(["prepare", "--source-plan", str(plan), "--output", str(review)])
    return outline, review
