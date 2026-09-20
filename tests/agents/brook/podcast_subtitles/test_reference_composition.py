from __future__ import annotations

import hashlib
import json
import os
import wave
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agents.brook.podcast_subtitles import __main__ as cli
from agents.brook.podcast_subtitles.canonical import review_target_fingerprint
from agents.brook.podcast_subtitles.composition import (
    FactoryContextV1,
    ReferenceManifestError,
    build_factory_context,
    build_reference_bundle,
    load_reference_manifest,
)
from agents.brook.podcast_subtitles.facade import PodcastSubtitleFacade
from agents.brook.podcast_subtitles.module import (
    AcceptedGeneration,
    CreateRequest,
    GenerationIsolationError,
    ModuleInvariantError,
    NeedsReview,
    PodcastSubtitleV2,
    ProjectRequest,
    ResolveRequest,
)
from agents.brook.podcast_subtitles.ports import CorrectionProposal
from agents.brook.podcast_subtitles.profiles import HORIZONTAL_16X9, SubtitlePolicy
from agents.brook.podcast_subtitles.source_program import (
    FrameRateV1,
    ResolveLosslessAudioSettingsV1,
    ResolveProjectIdentityV1,
    ResolveRendererIdentityV1,
    ResolveRenderJobV1,
    ResolveSourceProgramCaptureV1,
    ResolveTimelineIdentityV1,
    seal_source_program,
    source_program_receipt_bytes,
)
from agents.brook.podcast_subtitles.speech_coverage import verified_speech_coverage_hash
from shared.schemas.podcast_subtitles_v2 import (
    AudioAuditReceipt,
    CorrectionDecision,
    ReferenceAuthorityDescriptor,
    ReferenceRetrievalReceipt,
    SpeechCoverageReceipt,
    audio_audit_receipt_set_hash,
)
from tests.agents.brook.podcast_subtitles.test_module import (
    _INLINE_CORRECTOR_IDENTITY,
    _correction_run,
    _decision_for_created,
    _module,
    _replay_fixture_correction,
    _single_stream_module,
)


def _digest(path: Path) -> tuple[str, int]:
    payload = path.read_bytes()
    return hashlib.sha256(payload).hexdigest(), len(payload)


def _source_program_cli_args(tmp_path: Path, output: Path) -> list[str]:
    with wave.open(str(output), "wb") as target:
        target.setnchannels(2)
        target.setsampwidth(3)
        target.setframerate(48_000)
        target.writeframes(b"\0" * 48_000 * 2 * 3)
    capture = ResolveSourceProgramCaptureV1(
        episode_id="episode-author",
        renderer=ResolveRendererIdentityV1(
            product="DaVinci Resolve", version="20.1.1.0007"
        ),
        project=ResolveProjectIdentityV1(
            stable_id="project-reference-cli", name="Reference CLI fixture"
        ),
        timeline=ResolveTimelineIdentityV1(
            stable_id="timeline-reference-cli",
            name="Complete reference CLI fixture",
            frame_rate=FrameRateV1(numerator=24, denominator=1),
            start_frame=86_400,
            end_frame=86_423,
            start_timecode="01:00:00:00",
            end_timecode="01:00:00:23",
        ),
        render_job=ResolveRenderJobV1(
            stable_id="render-job-reference-cli",
            status="Complete",
            requested_at=datetime(2026, 8, 13, 2, 0, tzinfo=timezone.utc),
            completed_at=datetime(2026, 8, 13, 2, 1, tzinfo=timezone.utc),
            mark_in_frame=86_400,
            mark_out_frame=86_423,
            target_type="single_clip",
            audio=ResolveLosslessAudioSettingsV1(
                container="wav",
                codec="pcm_s24le",
                sample_rate_hz=48_000,
                bit_depth=24,
                channels=2,
                audio_stream_count=1,
            ),
        ),
    )
    receipt_path = tmp_path / "reference-cli-source-program-receipt.json"
    receipt_path.write_bytes(
        source_program_receipt_bytes(
            seal_source_program(capture=capture, output_path=output)
        )
    )
    return [
        "--source-program-receipt",
        str(receipt_path),
        "--source-program-kind",
        "resolve_direct_lossless_render",
    ]


def _published_book_authority(*, version_id: str = "first-edition") -> dict[str, object]:
    return {
        "schema_version": 1,
        "logical_source_id": "logical-author-book",
        "version_id": version_id,
        "version_status": "active",
        "release_status": "published",
        "supersedes": [],
        "source_kind": "book",
        "trust_tier": "authoritative",
        "role": "published_author_book",
        "subject": {
            "schema_version": 1,
            "kind": "publication",
            "stable_id": "book:deep-nutrition",
            "display_name": "Deep Nutrition",
        },
        "owner": {
            "schema_version": 1,
            "kind": "person",
            "stable_id": "person:guest-author",
            "display_name": "Guest Author",
        },
        "allowed_scopes": [
            "source_title",
            "source_author",
            "literal_terminology",
            "verbatim_source_text",
        ],
        "attestation": {
            "schema_version": 1,
            "confirmed": True,
            "provenance": "publisher_record",
            "attestor": {
                "schema_version": 1,
                "kind": "organization",
                "stable_id": "publisher:example",
                "display_name": "Example Press",
            },
            "record_sha256": "a" * 64,
        },
    }


def test_manifest_v2_preserves_exact_authority_descriptor_through_artifact(
    tmp_path: Path,
) -> None:
    source = tmp_path / "book.txt"
    source.write_text("Canonical terminology", encoding="utf-8")
    digest, size = _digest(source)
    authority = _published_book_authority()
    manifest_path = tmp_path / "references-v2.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "episode_id": "episode-authority",
                "sources": [
                    {
                        "source_id": "author-book-v1",
                        "kind": "book",
                        "path": source.name,
                        "title": "Deep Nutrition",
                        "version": "first-edition",
                        "document_date": "2024-05-20",
                        "trust_tier": "authoritative",
                        "sha256": digest,
                        "size_bytes": size,
                        "author": "Guest Author",
                        "publisher": "Example Press",
                        "authority": authority,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    bundle = build_reference_bundle(
        manifest_path,
        episode_root=tmp_path / "episode",
        expected_episode_id="episode-authority",
    )

    descriptor = bundle.source_specs[0].authority
    artifact = bundle.enrollments[0].artifact
    assert descriptor.model_dump(mode="json", exclude_none=False) == (
        artifact.authority.model_dump(mode="json", exclude_none=False)
    )
    assert artifact.authority.logical_source_id == "logical-author-book"
    assert artifact.authority.content_hash


def test_manifest_v1_and_missing_authority_fail_closed(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")
    entry = _source(
        source,
        source_id="source-v1",
        kind="other",
        title="Source",
        version="v1",
        document_date="undated",
        trust_tier="contextual",
    )
    manifest = _write_manifest(tmp_path, [entry], episode_id="episode-v2")
    raw = json.loads(manifest.read_text(encoding="utf-8"))
    raw["schema_version"] = 1
    manifest.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ReferenceManifestError, match="Input should be 2"):
        load_reference_manifest(manifest)

    raw["schema_version"] = 2
    raw["sources"][0].pop("authority")
    manifest.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ReferenceManifestError, match="authority"):
        load_reference_manifest(manifest)


def test_manifest_rejects_unbound_owner_and_tampered_authority_hash(tmp_path: Path) -> None:
    _audio, sources = _episode_sources(tmp_path)
    authority = sources[0]["authority"]
    assert isinstance(authority, dict)
    owner = authority["owner"]
    assert isinstance(owner, dict)
    owner["display_name"] = "Different Author"
    manifest = _write_manifest(tmp_path, sources)
    with pytest.raises(ReferenceManifestError, match="owner differs"):
        load_reference_manifest(manifest)

    _audio, fresh_sources = _episode_sources(tmp_path)
    fresh_authority = fresh_sources[0]["authority"]
    descriptor = ReferenceAuthorityDescriptor.model_validate(fresh_authority)
    tampered = descriptor.model_dump(mode="json", exclude_none=False)
    tampered["logical_source_id"] = "tampered-logical-source"
    fresh_sources[0]["authority"] = tampered
    manifest = _write_manifest(tmp_path, fresh_sources)
    with pytest.raises(ReferenceManifestError, match="content_hash mismatch"):
        load_reference_manifest(manifest)


def test_manifest_rejects_nonfinal_report_and_outline_scope_escalation(
    tmp_path: Path,
) -> None:
    _audio, sources = _episode_sources(tmp_path)
    report_authority = sources[1]["authority"]
    assert isinstance(report_authority, dict)
    report_authority["release_status"] = "approved"
    manifest = _write_manifest(tmp_path, sources)
    with pytest.raises(ReferenceManifestError, match="owner_final_report authority matrix"):
        load_reference_manifest(manifest)

    _audio, fresh_sources = _episode_sources(tmp_path)
    outline_authority = fresh_sources[2]["authority"]
    assert isinstance(outline_authority, dict)
    outline_authority["allowed_scopes"] = ["source_title"]
    manifest = _write_manifest(tmp_path, fresh_sources)
    with pytest.raises(ReferenceManifestError, match="cannot carry mutation authority"):
        load_reference_manifest(manifest)


@pytest.mark.parametrize("version_status", ["draft", "superseded"])
def test_inactive_book_cannot_masquerade_as_active_authority(
    tmp_path: Path,
    version_status: str,
) -> None:
    _audio, sources = _episode_sources(tmp_path)
    authority = sources[0]["authority"]
    assert isinstance(authority, dict)
    authority["version_status"] = version_status
    manifest = _write_manifest(tmp_path, sources)
    with pytest.raises(ReferenceManifestError, match="published_author_book authority matrix"):
        load_reference_manifest(manifest)


def test_manifest_rejects_duplicate_versions_self_supersedes_and_cycles(
    tmp_path: Path,
) -> None:
    first_path = tmp_path / "first.txt"
    second_path = tmp_path / "second.txt"
    first_path.write_text("first", encoding="utf-8")
    second_path.write_text("second", encoding="utf-8")
    first = _source(
        first_path,
        source_id="first",
        kind="other",
        title="First",
        version="v1",
        document_date="undated",
        trust_tier="contextual",
    )
    second = _source(
        second_path,
        source_id="second",
        kind="other",
        title="Second",
        version="v1",
        document_date="undated",
        trust_tier="contextual",
    )
    second_authority = second["authority"]
    first_authority = first["authority"]
    assert isinstance(first_authority, dict) and isinstance(second_authority, dict)
    second_authority["logical_source_id"] = first_authority["logical_source_id"]
    with pytest.raises(ReferenceManifestError, match="logical_source_id/version_id"):
        load_reference_manifest(_write_manifest(tmp_path, [first, second]))

    first_authority["supersedes"] = [
        {
            "schema_version": 1,
            "logical_source_id": first_authority["logical_source_id"],
            "version_id": first_authority["version_id"],
        }
    ]
    with pytest.raises(ReferenceManifestError, match="supersede itself"):
        load_reference_manifest(_write_manifest(tmp_path, [first]))

    first_authority["version_id"] = "v1"
    first_authority["version_status"] = "superseded"
    first_authority["supersedes"] = [
        {
            "schema_version": 1,
            "logical_source_id": first_authority["logical_source_id"],
            "version_id": "v2",
        }
    ]
    first["version"] = "v1"
    second_authority["logical_source_id"] = first_authority["logical_source_id"]
    second_authority["version_id"] = "v2"
    second_authority["version_status"] = "superseded"
    second_authority["supersedes"] = [
        {
            "schema_version": 1,
            "logical_source_id": first_authority["logical_source_id"],
            "version_id": "v1",
        }
    ]
    second["version"] = "v2"
    with pytest.raises(ReferenceManifestError, match="supersedes cycle"):
        load_reference_manifest(_write_manifest(tmp_path, [first, second]))


def test_manifest_accepts_explicit_active_successor_of_superseded_version(
    tmp_path: Path,
) -> None:
    old_path = tmp_path / "old.txt"
    new_path = tmp_path / "new.txt"
    old_path.write_text("old source", encoding="utf-8")
    new_path.write_text("new source", encoding="utf-8")
    old = _source(
        old_path,
        source_id="source-old",
        kind="other",
        title="Old Source",
        version="v1",
        document_date="2025-01-01",
        trust_tier="contextual",
    )
    new = _source(
        new_path,
        source_id="source-new",
        kind="other",
        title="New Source",
        version="v2",
        document_date="2026-01-01",
        trust_tier="contextual",
    )
    old_authority = old["authority"]
    new_authority = new["authority"]
    assert isinstance(old_authority, dict) and isinstance(new_authority, dict)
    logical_source_id = "logical:versioned-source"
    old_authority["logical_source_id"] = logical_source_id
    old_authority["version_status"] = "superseded"
    new_authority["logical_source_id"] = logical_source_id
    new_authority["supersedes"] = [
        {
            "schema_version": 1,
            "logical_source_id": logical_source_id,
            "version_id": "v1",
        }
    ]

    manifest, _payload = load_reference_manifest(_write_manifest(tmp_path, [old, new]))

    assert manifest.sources[0].authority.version_status == "superseded"
    assert manifest.sources[1].authority.supersedes[0].version_id == "v1"


def test_owner_approved_outline_glossary_is_scope_limited_and_round_trips(
    tmp_path: Path,
) -> None:
    _audio, sources = _episode_sources(tmp_path)
    outline = sources[2]
    authority = outline["authority"]
    assert isinstance(authority, dict)
    owner = authority["owner"]
    assert isinstance(owner, dict)
    outline["trust_tier"] = "curated"
    authority.update(
        {
            "trust_tier": "curated",
            "role": "owner_approved_outline_glossary",
            "release_status": "approved",
            "allowed_scopes": ["owner_approved_glossary_spelling"],
            "attestation": {
                "schema_version": 1,
                "confirmed": True,
                "provenance": "owner_approval_record",
                "attestor": owner,
                "record_sha256": "b" * 64,
            },
        }
    )
    manifest = _write_manifest(tmp_path, [outline])
    bundle = build_reference_bundle(
        manifest,
        episode_root=tmp_path / "episode",
        expected_episode_id="episode-author",
    )
    artifact = bundle.enrollments[0].artifact
    assert artifact.authority.role == "owner_approved_outline_glossary"
    assert artifact.authority.allowed_scopes == ("owner_approved_glossary_spelling",)


def _source(
    path: Path,
    *,
    source_id: str,
    kind: str,
    title: str,
    version: str,
    document_date: str,
    trust_tier: str,
    author: str | None = None,
    publisher: str | None = None,
    authority: dict[str, object] | None = None,
) -> dict[str, object]:
    digest, size = _digest(path)
    owner = (
        {
            "schema_version": 1,
            "kind": "person",
            "stable_id": f"owner:{source_id}",
            "display_name": author,
        }
        if author is not None
        else None
    )
    if authority is None:
        if kind == "book" and trust_tier == "authoritative":
            role = "published_author_book"
            subject_kind = "publication"
            release_status = "published"
            allowed_scopes = [
                "source_title",
                "source_author",
                "literal_terminology",
                "verbatim_source_text",
            ]
            provenance = "publisher_record" if publisher is not None else "author_record"
            attestor = (
                {
                    "schema_version": 1,
                    "kind": "organization",
                    "stable_id": f"publisher:{source_id}",
                    "display_name": publisher,
                }
                if publisher is not None
                else owner
            )
            confirmed = True
        elif kind == "research_report" and trust_tier == "authoritative":
            role = "owner_final_report"
            subject_kind = "report"
            release_status = "final"
            allowed_scopes = [
                "source_title",
                "literal_terminology",
                "verbatim_source_text",
            ]
            provenance = "owner_record"
            attestor = owner
            confirmed = True
        else:
            role = "curated_reference" if trust_tier == "curated" else "contextual_reference"
            subject_kind = "episode" if kind == "interview_outline" else "other"
            release_status = "approved" if kind == "interview_outline" else "not_applicable"
            allowed_scopes = []
            provenance = "none"
            attestor = None
            confirmed = False
        authority = {
            "schema_version": 1,
            "logical_source_id": f"logical:{source_id}",
            "version_id": version,
            "version_status": "active",
            "release_status": release_status,
            "supersedes": [],
            "source_kind": kind,
            "trust_tier": trust_tier,
            "role": role,
            "subject": {
                "schema_version": 1,
                "kind": subject_kind,
                "stable_id": "episode-author" if kind == "interview_outline" else source_id,
                "display_name": title,
            },
            "owner": owner,
            "allowed_scopes": allowed_scopes,
            "attestation": {
                "schema_version": 1,
                "confirmed": confirmed,
                "provenance": provenance,
                "attestor": attestor,
                "record_sha256": "a" * 64 if confirmed else None,
            },
        }
    return {
        "source_id": source_id,
        "kind": kind,
        "path": path.name,
        "title": title,
        "version": version,
        "document_date": document_date,
        "trust_tier": trust_tier,
        "sha256": digest,
        "size_bytes": size,
        "author": author,
        "publisher": publisher,
        "authority": authority,
    }


def _write_manifest(
    tmp_path: Path,
    sources: list[dict[str, object]],
    *,
    episode_id: str = "episode-author",
) -> Path:
    sources = json.loads(json.dumps(sources, ensure_ascii=False))
    for source in sources:
        if source["kind"] == "interview_outline":
            source["authority"]["subject"]["stable_id"] = episode_id
    path = tmp_path / "references.json"
    path.write_text(
        json.dumps(
            {"schema_version": 2, "episode_id": episode_id, "sources": sources},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _episode_sources(tmp_path: Path) -> tuple[Path, list[dict[str, object]]]:
    source_audio = tmp_path / "episode.wav"
    book = tmp_path / "book.txt"
    report = tmp_path / "report.md"
    outline = tmp_path / "outline.txt"
    book.write_text("深度營養 書中術語 米雪", encoding="utf-8")
    report.write_text("# 最終報告\n研究結果與專有名詞", encoding="utf-8")
    outline.write_text("訪談預計詢問作者的研究方法", encoding="utf-8")
    return source_audio, [
        _source(
            book,
            source_id="author-book",
            kind="book",
            title="深度營養",
            version="first-edition",
            document_date="2024-05-20",
            trust_tier="authoritative",
            author="受訪作者",
            publisher="測試出版社",
        ),
        _source(
            report,
            source_id="guest-final-report",
            kind="research_report",
            title="研究最終報告",
            version="final-v2",
            document_date="2026-07-01",
            trust_tier="authoritative",
            author="研究主持人",
        ),
        _source(
            outline,
            source_id="producer-outline",
            kind="interview_outline",
            title="本集訪綱",
            version="approved-v3",
            document_date="2026-08-10",
            trust_tier="contextual",
            author="製作人",
        ),
    ]


class _ReferenceScopeCorrector:
    """Hostile fixture that may cite a globally available, unpresented source."""

    def __init__(self, source_id: str) -> None:
        self.source_id = source_id
        self.proposal: CorrectionProposal | None = None
        self.reference_was_presented: bool | None = None

    @property
    def identity(self):
        return _INLINE_CORRECTOR_IDENTITY

    def propose(self, request):
        span = request.transcript.spans[-1]
        if span.id not in request.target_span_ids:
            return ()
        token_by_id = {token.id: token for token in request.transcript.tokens}
        selected = tuple(token_by_id[token_id] for token_id in span.token_ids)
        reference = next(
            item
            for item in request.available_reference_evidence
            if item.artifact.source_id == self.source_id
        )
        self.reference_was_presented = reference.id in {
            item.id for item in request.reference_evidence
        }
        evidence_token_ids = tuple(
            sorted(
                {
                    evidence_id.split(":", maxsplit=2)[-1]
                    for token in selected
                    for evidence_id in token.evidence_ids
                }
            )
        )
        self.proposal = CorrectionProposal(
            id=f"proposal-{self.source_id}",
            audio_span_ids=(span.id,),
            start_ms=span.start_ms,
            end_ms=span.end_ms,
            evidence_token_ids=evidence_token_ids,
            observed_text="".join(token.text for token in selected),
            candidate_text="betafixed",
            confidence=0.91,
            rationale="reference scope fixture",
            source="fixture-corrector",
            reference_evidence_ids=(reference.id,),
            evidence_basis="recognition_and_reference",
        )
        return (self.proposal,)

    def propose_with_receipt(self, request):
        return _correction_run(request, self.propose(request))

    def replay(self, request, **kwargs):
        return _replay_fixture_correction(request, **kwargs)


def _reference_scope_generation(
    tmp_path: Path,
    *,
    proposal_source_id: str,
) -> tuple[PodcastSubtitleV2, NeedsReview, _ReferenceScopeCorrector, dict[str, str]]:
    corrector = _ReferenceScopeCorrector(proposal_source_id)
    module, audio = _single_stream_module(
        tmp_path / "episode-workspace",
        texts=("alpha", "beta"),
        corrector=corrector,
    )
    source_specs: list[dict[str, object]] = []
    for source_id, content in (
        ("span-alpha", "alpha reference spelling"),
        ("span-beta-primary", "beta primary spelling"),
        ("span-beta-extra", "beta extra spelling"),
    ):
        path = tmp_path / f"{source_id}.txt"
        path.write_text(content, encoding="utf-8")
        source_specs.append(
            _source(
                path,
                source_id=source_id,
                kind="other",
                title=source_id,
                version="v1",
                document_date="2026-08-12",
                trust_tier="curated",
            )
        )
    manifest = _write_manifest(
        tmp_path,
        source_specs,
        episode_id="episode-reference-scope",
    )
    bundle = build_reference_bundle(
        manifest,
        episode_root=tmp_path / "episode-workspace",
        expected_episode_id="episode-reference-scope",
    )
    module._reference_retriever = bundle.retriever
    module._reference_retriever_identity = bundle.retriever_identity
    module._reference_parser_registry = bundle.parser_registry
    created = module.create(
        CreateRequest(
            episode_id="episode-reference-scope",
            source_audio=audio,
            reference_enrollments=bundle.enrollments,
            policy=SubtitlePolicy(
                full_audit_max_spans_per_request=1,
                full_audit_max_tokens_per_request=1,
            ),
        )
    )
    assert isinstance(created, NeedsReview)
    assert corrector.proposal is not None
    loaded = module._load_generation(created.generation_id, require_active=True)
    references_by_source = {
        item.artifact.source_id: item.id for item in loaded.references.evidence
    }
    return module, created, corrector, references_by_source


def _reference_proposal_decision(
    created: NeedsReview,
    proposal: CorrectionProposal,
    *,
    event_id: str,
    reference_evidence_ids: tuple[str, ...],
) -> CorrectionDecision:
    span_by_id = {span.id: span for span in created.transcript.spans}
    token_by_id = {token.id: token for token in created.transcript.tokens}
    selected = tuple(
        token_by_id[token_id]
        for span_id in proposal.audio_span_ids
        for token_id in span_by_id[span_id].token_ids
    )
    target_set = set(proposal.audio_span_ids)
    issue_ids = tuple(
        issue.id
        for issue in created.transcript.review_issues
        if issue.status == "unresolved"
        and issue.severity in {"medium", "high", "blocking"}
        and set(issue.span_ids).issubset(target_set)
    )
    return CorrectionDecision(
        event_id=event_id,
        episode_id=created.transcript.episode_id,
        generation_id=created.generation_id,
        target_span_ids=proposal.audio_span_ids,
        target_start_ms=proposal.start_ms,
        target_end_ms=proposal.end_ms,
        evidence_fingerprint=review_target_fingerprint(
            created.transcript,
            proposal.audio_span_ids,
        ),
        proposal_ids=(proposal.id,),
        issue_ids=issue_ids,
        audio_evidence_ids=tuple(
            sorted({item for token in selected for item in token.evidence_ids})
        ),
        reference_evidence_ids=reference_evidence_ids,
        evidence_basis="audio_and_reference",
        action="replace",
        replacement_text=proposal.candidate_text,
        replacement_lexemes=(proposal.candidate_text,),
        actor_kind="human",
        actor="reviewer",
        rationale="audio relisten with scoped reference spelling",
        timestamp=datetime(2026, 8, 12, tzinfo=timezone.utc),
    )


def test_cli_factory_receives_exact_book_report_outline_bundle_and_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source_audio, sources = _episode_sources(tmp_path)
    manifest = _write_manifest(tmp_path, sources)
    source_audio.write_bytes(b"audio")
    captured_contexts: list[FactoryContextV1] = []
    captured_requests = []
    module = object.__new__(PodcastSubtitleV2)

    def factory(context: FactoryContextV1) -> PodcastSubtitleV2:
        captured_contexts.append(context)
        assert context.reference_bundle is not None
        module._reference_retriever = context.reference_bundle.retriever
        module._reference_retriever_identity = context.reference_bundle.retriever_identity
        module._reference_parser_registry = context.reference_bundle.parser_registry
        return module

    class Facade:
        def __init__(self, configured: PodcastSubtitleV2) -> None:
            assert configured is module

        def run(self, request):
            captured_requests.append(request)
            return {"status": "captured"}

    monkeypatch.setattr(cli, "_load_factory", lambda _spec: factory)
    monkeypatch.setattr(cli, "PodcastSubtitleFacade", Facade)

    assert (
        cli.main(
            [
                "--episode-root",
                str(tmp_path / "episode-workspace"),
                "--factory",
                "fixture:build",
                "run",
                "--episode-id",
                "episode-author",
                "--source-audio",
                str(source_audio),
                *_source_program_cli_args(tmp_path, source_audio),
                "--reference-manifest",
                str(manifest),
            ]
        )
        == 0
    )

    context = captured_contexts[0]
    bundle = context.reference_bundle
    assert bundle is not None
    assert context.protocol_version == 1
    assert tuple(item.artifact.kind for item in bundle.enrollments) == (
        "book",
        "research_report",
        "interview_outline",
    )
    assert tuple(item.artifact.document_date for item in bundle.enrollments) == (
        "2024-05-20",
        "2026-07-01",
        "2026-08-10",
    )
    assert bundle.enrollments[-1].artifact.trust_tier == "contextual"
    assert captured_requests[0].reference_enrollments == bundle.enrollments
    assert bundle.index_hash == bundle.retriever.index.index_hash
    assert '"status": "captured"' in capsys.readouterr().out


def test_factory_context_without_references_remains_supported(tmp_path: Path) -> None:
    context = build_factory_context(
        episode_root=tmp_path / "episode",
        episode_id="episode-without-references",
    )
    assert context.protocol_version == 1
    assert context.reference_bundle is None
    assert context.reference_enrollments == ()


def test_cli_rejects_factory_that_ignores_enrolled_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_audio, sources = _episode_sources(tmp_path)
    manifest = _write_manifest(tmp_path, sources)
    source_audio.write_bytes(b"audio")
    module = object.__new__(PodcastSubtitleV2)
    module._reference_retriever = None
    module._reference_retriever_identity = None
    module._reference_parser_registry = None
    monkeypatch.setattr(cli, "_load_factory", lambda _spec: lambda _context: module)

    with pytest.raises(ModuleInvariantError, match="exact enrolled Reference bundle"):
        cli.main(
            [
                "--episode-root",
                str(tmp_path / "episode-workspace"),
                "--factory",
                "fixture:build",
                "run",
                "--episode-id",
                "episode-author",
                "--source-audio",
                str(source_audio),
                *_source_program_cli_args(tmp_path, source_audio),
                "--reference-manifest",
                str(manifest),
            ]
        )


def test_every_operator_verb_reconstructs_same_reference_bundle_in_fresh_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_audio, sources = _episode_sources(tmp_path)
    source_audio.write_bytes(b"audio")
    manifest = _write_manifest(tmp_path, sources)
    decision_json = tmp_path / "decision.json"
    decision_json.write_text("{}", encoding="utf-8")
    contexts: list[FactoryContextV1] = []
    calls: list[str] = []

    def factory(context: FactoryContextV1) -> PodcastSubtitleV2:
        # A new instance on every invocation models a new CLI process.  Binding
        # succeeds only when the exact rebuilt retriever/index is installed.
        module = object.__new__(PodcastSubtitleV2)
        bundle = context.reference_bundle
        assert bundle is not None
        module._reference_retriever = bundle.retriever
        module._reference_retriever_identity = bundle.retriever_identity
        module._reference_parser_registry = bundle.parser_registry
        contexts.append(context)
        return module

    class Facade:
        def __init__(self, _module: PodcastSubtitleV2) -> None:
            pass

        def run(self, _request):
            calls.append("run")
            return {"verb": "run"}

        def status(self):
            calls.append("status")
            return {"verb": "status"}

        def review(self, _generation_id):
            calls.append("review")
            return {"verb": "review"}

        def decide(self, _generation_id, _decision):
            calls.append("decide")
            return {"verb": "decide"}

        def project(self, _request):
            calls.append("project")
            return {"verb": "project"}

    class _DecisionContract:
        @staticmethod
        def model_validate_json(_payload: str) -> object:
            return object()

    monkeypatch.setattr(cli, "_load_factory", lambda _spec: factory)
    monkeypatch.setattr(cli, "PodcastSubtitleFacade", Facade)
    monkeypatch.setattr(cli, "CorrectionDecision", _DecisionContract)

    common = [
        "--episode-root",
        str(tmp_path / "episode-workspace"),
        "--factory",
        "fixture:build",
        "--reference-manifest",
        str(manifest),
    ]
    commands = (
        [
            "run",
            "--episode-id",
            "episode-author",
            "--source-audio",
            str(source_audio),
            *_source_program_cli_args(tmp_path, source_audio),
        ],
        ["status"],
        ["review"],
        [
            "decide",
            "--generation-id",
            "generation-1",
            "--decision-json",
            str(decision_json),
        ],
        ["project", "--generation-id", "generation-1"],
    )
    for command in commands:
        assert cli.main([*common, *command]) == 0

    assert calls == ["run", "status", "review", "decide", "project"]
    assert len({context.reference_bundle.content_hash for context in contexts}) == 1
    assert len({context.reference_bundle.index_hash for context in contexts}) == 1


def test_reference_backed_generation_reopens_for_status_review_decide_and_project(
    tmp_path: Path,
) -> None:
    episode_root = tmp_path / "episode-workspace"
    seed, audio = _module(episode_root)
    _unused_audio, sources = _episode_sources(tmp_path)
    manifest = _write_manifest(tmp_path, sources, episode_id="episode-anji")
    initial_bundle = build_reference_bundle(
        manifest,
        episode_root=episode_root,
        expected_episode_id="episode-anji",
    )
    seed._reference_retriever = initial_bundle.retriever
    seed._reference_retriever_identity = initial_bundle.retriever_identity
    seed._reference_parser_registry = initial_bundle.parser_registry
    created = seed.create(
        CreateRequest(
            episode_id="episode-anji",
            source_audio=audio,
            reference_enrollments=initial_bundle.enrollments,
            vocabulary=("米雪",),
        )
    )
    assert isinstance(created, NeedsReview)

    reopened_bundle = build_reference_bundle(
        manifest,
        episode_root=episode_root,
        expected_episode_id=None,
    )
    fresh = PodcastSubtitleV2(
        episode_root,
        normalizer=seed._normalizer,
        recognizers=seed._recognizers,
        semantic_analyzer=seed._semantic_analyzer,
        corrector=seed._corrector,
        audio_auditor=seed._audio_auditor,
        corrector_identity=seed._corrector_identity,
        semantic_analyzer_identity=seed._semantic_analyzer_identity,
        speaker_attributor=seed._speaker_attributor,
        speaker_attributor_identity=seed._speaker_attributor_identity,
        reference_retriever=reopened_bundle.retriever,
        reference_retriever_identity=reopened_bundle.retriever_identity,
        reference_parser_registry=reopened_bundle.parser_registry,
        arbiter=seed._arbiter,
        speech_coverage_analyzer=seed._speech_coverage_analyzer,
    )
    reopened_bundle.assert_module_binding(fresh)
    facade = PodcastSubtitleFacade(fresh)
    status = facade.status()
    assert status.active_generation_id == created.generation_id
    assert status.reference_source_ids == (
        "author-book",
        "guest-final-report",
        "producer-outline",
    )
    assert status.reference_retriever_config_hash == (
        reopened_bundle.retriever_identity.config_hash
    )
    assert status.reference_manifest_sha256s == (reopened_bundle.manifest_sha256,)
    assert facade.review(created.generation_id).generation_id == created.generation_id

    coverage = SpeechCoverageReceipt.model_validate(
        json.loads(
            fresh.store.read_artifact(
                created.generation_id, "speech_coverage_receipt.json"
            )
        )
    )
    assert coverage.status == "completed" and coverage.passed
    assert coverage.uncovered_intervals == ()
    assert (
        created.transcript.verified_speech_coverage_receipt_hash
        == verified_speech_coverage_hash(coverage)
    )
    audio_audits = tuple(
        AudioAuditReceipt.model_validate(item)
        for item in json.loads(
            fresh.store.read_artifact(
                created.generation_id, "audio_audit_receipts.json"
            )
        )
    )
    assert tuple(
        span_id for receipt in audio_audits for span_id in receipt.target_span_ids
    ) == tuple(span.id for span in created.transcript.spans)
    assert all(receipt.status != "unresolved" for receipt in audio_audits)
    assert (
        created.transcript.full_audit_receipt_set_hash
        == audio_audit_receipt_set_hash(list(audio_audits))
    )

    resolved = facade.decide(
        created.generation_id,
        _decision_for_created(created, event_id="event-reference-fresh-process"),
    )
    assert isinstance(resolved, AcceptedGeneration)
    assert not tuple(
        issue
        for issue in resolved.transcript.review_issues
        if issue.status == "unresolved"
        and issue.severity in {"medium", "high", "blocking"}
    )
    child_retrievals = tuple(
        ReferenceRetrievalReceipt.model_validate(item)
        for item in json.loads(
            fresh.store.read_artifact(
                resolved.generation_id,
                "reference_retrieval_receipts.json",
            )
        )
    )
    child_receipt_span_ids = tuple(item.audio_span_id for item in child_retrievals)
    assert child_receipt_span_ids == tuple(
        ancestor_id
        for span in resolved.transcript.spans
        for ancestor_id in (span.id, *span.lineage)
        if ancestor_id in set(child_receipt_span_ids)
    )
    # The fresh process reconstructed the same reference trust root, replayed
    # independent speech/audio attestations, published the reviewed child, and
    # can now project it without silently dropping source provenance.
    assert facade.project(
        ProjectRequest(resolved.generation_id, HORIZONTAL_16X9)
    ).generation_id == resolved.generation_id


def test_decision_accepts_exact_reference_retrieved_for_target_span(
    tmp_path: Path,
) -> None:
    module, created, corrector, references = _reference_scope_generation(
        tmp_path,
        proposal_source_id="span-beta-primary",
    )
    assert corrector.proposal is not None
    assert corrector.reference_was_presented is True
    decision = _reference_proposal_decision(
        created,
        corrector.proposal,
        event_id="legitimate-target-reference",
        reference_evidence_ids=(references["span-beta-primary"],),
    )

    resolved = module.resolve(ResolveRequest(created.generation_id, (decision,)))

    assert isinstance(resolved, AcceptedGeneration)
    assert module.store.active_generation_id() == resolved.generation_id
    assert (
        module._load_generation(
            resolved.generation_id, require_active=True
        ).result.transcript
        == resolved.transcript
    )


def test_decision_rejects_global_reference_not_retrieved_for_target_without_side_effects(
    tmp_path: Path,
) -> None:
    module, created, corrector, references = _reference_scope_generation(
        tmp_path,
        proposal_source_id="span-alpha",
    )
    assert corrector.proposal is not None
    assert corrector.reference_was_presented is False
    decision = _reference_proposal_decision(
        created,
        corrector.proposal,
        event_id="cross-span-reference",
        reference_evidence_ids=(references["span-alpha"],),
    )
    active_before = module.store.active_generation_id()
    ledger_before = module.ledger.entries()
    generations_before = tuple(sorted(module.store.generations_dir.iterdir()))

    with pytest.raises(GenerationIsolationError, match="target span lineage"):
        module.resolve(ResolveRequest(created.generation_id, (decision,)))

    assert module.store.active_generation_id() == active_before
    assert module.ledger.entries() == ledger_before
    assert tuple(sorted(module.store.generations_dir.iterdir())) == generations_before
    assert not module.store.resolution_journal_exists()


def test_proposal_backed_decision_rejects_extra_target_reference_without_side_effects(
    tmp_path: Path,
) -> None:
    module, created, corrector, references = _reference_scope_generation(
        tmp_path,
        proposal_source_id="span-beta-primary",
    )
    assert corrector.proposal is not None
    decision = _reference_proposal_decision(
        created,
        corrector.proposal,
        event_id="extra-proposal-reference",
        reference_evidence_ids=(
            references["span-beta-primary"],
            references["span-beta-extra"],
        ),
    )
    active_before = module.store.active_generation_id()
    ledger_before = module.ledger.entries()
    generations_before = tuple(sorted(module.store.generations_dir.iterdir()))

    with pytest.raises(GenerationIsolationError, match="cite exactly"):
        module.resolve(ResolveRequest(created.generation_id, (decision,)))

    assert module.store.active_generation_id() == active_before
    assert module.ledger.entries() == ledger_before
    assert tuple(sorted(module.store.generations_dir.iterdir())) == generations_before
    assert not module.store.resolution_journal_exists()


def test_direct_human_replace_cannot_claim_reference_without_typed_receipt(
    tmp_path: Path,
) -> None:
    module, created, corrector, references = _reference_scope_generation(
        tmp_path,
        proposal_source_id="span-beta-primary",
    )
    assert corrector.proposal is not None
    proposal_backed = _reference_proposal_decision(
        created,
        corrector.proposal,
        event_id="direct-human-reference",
        reference_evidence_ids=(references["span-beta-primary"],),
    )
    direct = proposal_backed.model_copy(
        update={
            "proposal_ids": (),
            "replacement_text": "humanfixed",
            "replacement_lexemes": ("humanfixed",),
        }
    )
    active_before = module.store.active_generation_id()
    ledger_before = module.ledger.entries()
    generations_before = tuple(sorted(module.store.generations_dir.iterdir()))

    with pytest.raises(ModuleInvariantError, match="HumanReferenceReviewReceipt"):
        module.resolve(ResolveRequest(created.generation_id, (direct,)))

    assert module.store.active_generation_id() == active_before
    assert module.ledger.entries() == ledger_before
    assert tuple(sorted(module.store.generations_dir.iterdir())) == generations_before
    assert not module.store.resolution_journal_exists()


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (lambda item: item.update(sha256="0" * 64), "bytes drifted"),
        (lambda item: item.update(size_bytes=item["size_bytes"] + 1), "bytes drifted"),
        (lambda item: item.update(title="bad\nmetadata"), "subject differs"),
        (lambda item: item.update(document_date="2026-02-30"), "document_date"),
        (
            lambda item: item.update(
                kind="interview_outline",
                trust_tier="authoritative",
            ),
            "source_kind differs",
        ),
    ],
)
def test_cli_rejects_drift_and_invalid_role_metadata_before_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mutation,
    expected: str,
) -> None:
    source_audio, sources = _episode_sources(tmp_path)
    source_audio.write_bytes(b"audio")
    mutation(sources[0])
    manifest = _write_manifest(tmp_path, sources)
    factory_loaded = False

    def load_factory(_spec: str):
        nonlocal factory_loaded
        factory_loaded = True
        raise AssertionError("factory must not load")

    monkeypatch.setattr(cli, "_load_factory", load_factory)
    with pytest.raises(SystemExit):
        cli.main(
            [
                "--episode-root",
                str(tmp_path / "episode"),
                "--factory",
                "fixture:build",
                "run",
                "--episode-id",
                "episode-author",
                "--source-audio",
                str(source_audio),
                *_source_program_cli_args(tmp_path, source_audio),
                "--reference-manifest",
                str(manifest),
            ]
        )
    assert expected in capsys.readouterr().err
    assert factory_loaded is False


def test_manifest_rejects_duplicate_ids_paths_and_unsupported_source(tmp_path: Path) -> None:
    _audio, sources = _episode_sources(tmp_path)
    duplicate_id = [sources[0], {**sources[1], "source_id": sources[0]["source_id"]}]
    manifest = _write_manifest(tmp_path, duplicate_id)
    with pytest.raises(ReferenceManifestError, match="source_id values must be unique"):
        build_reference_bundle(
            manifest,
            episode_root=tmp_path / "episode-a",
            expected_episode_id="episode-author",
        )

    duplicate_path = [sources[0], {**sources[1], "path": sources[0]["path"]}]
    manifest = _write_manifest(tmp_path, duplicate_path)
    with pytest.raises(ReferenceManifestError, match="one path more than once"):
        build_reference_bundle(
            manifest,
            episode_root=tmp_path / "episode-b",
            expected_episode_id="episode-author",
        )

    unsupported = tmp_path / "notes.csv"
    unsupported.write_text("term,spelling", encoding="utf-8")
    manifest = _write_manifest(
        tmp_path,
        [
            _source(
                unsupported,
                source_id="unsupported",
                kind="other",
                title="Unsupported",
                version="v1",
                document_date="undated",
                trust_tier="contextual",
            )
        ],
    )
    with pytest.raises(ReferenceManifestError, match="Unsupported Reference source format"):
        build_reference_bundle(
            manifest,
            episode_root=tmp_path / "episode-c",
            expected_episode_id="episode-author",
        )


def test_manifest_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    manifest = tmp_path / "duplicate-key.json"
    manifest.write_text(
        '{"schema_version":1,"schema_version":1,"episode_id":"episode","sources":[]}',
        encoding="utf-8",
    )
    with pytest.raises(ReferenceManifestError, match="repeats JSON key"):
        load_reference_manifest(manifest)


def test_manifest_rejects_source_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("reference", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("symlink creation is unavailable in this environment")
    manifest = _write_manifest(
        tmp_path,
        [
            _source(
                link,
                source_id="link",
                kind="other",
                title="Linked source",
                version="v1",
                document_date="undated",
                trust_tier="contextual",
            )
        ],
    )
    with pytest.raises(ReferenceManifestError, match="non-symlink"):
        build_reference_bundle(
            manifest,
            episode_root=tmp_path / "episode",
            expected_episode_id="episode-author",
        )
