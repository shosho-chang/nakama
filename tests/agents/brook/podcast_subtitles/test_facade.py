from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agents.brook.podcast_subtitles.adapters.fixtures import (
    FixtureArbiterAdapter,
    FixtureAudioAuditorAdapter,
    FixtureCorrectorAdapter,
)
from agents.brook.podcast_subtitles.canonical import review_target_fingerprint
from agents.brook.podcast_subtitles.errors import GenerationIsolationError
from agents.brook.podcast_subtitles.facade import PodcastSubtitleFacade
from agents.brook.podcast_subtitles.module import (
    AcceptedGeneration,
    CreateRequest,
    Interrupted,
    NeedsReview,
)
from agents.brook.podcast_subtitles.ports import ArbitrationVerdict
from agents.brook.podcast_subtitles.profiles import SubtitlePolicy
from shared.schemas.podcast_subtitles_v2 import CorrectionDecision
from tests.agents.brook.podcast_subtitles.test_module import (
    _LastSpanProposalCorrector,
    _module,
    _PendingArbiter,
    _PendingAudioAuditor,
    _PendingCorrector,
    _single_stream_module,
)


def _decision(created: NeedsReview) -> CorrectionDecision:
    spans = tuple(span.id for span in created.transcript.spans)
    tokens = created.transcript.tokens
    return CorrectionDecision(
        event_id="facade-decision-graduation",
        episode_id=created.transcript.episode_id,
        generation_id=created.generation_id,
        target_span_ids=spans,
        target_start_ms=tokens[0].start_ms,
        target_end_ms=tokens[-1].end_ms,
        evidence_fingerprint=review_target_fingerprint(created.transcript, spans),
        issue_ids=tuple(
            issue.id for issue in created.transcript.review_issues if issue.status == "unresolved"
        ),
        audio_evidence_ids=tuple(sorted({item for token in tokens for item in token.evidence_ids})),
        evidence_basis="audio",
        action="replace",
        replacement_text="哥大畢業典禮",
        replacement_lexemes=("哥大", "畢業典禮"),
        actor_kind="human",
        actor="reviewer",
        rationale="audio relisten",
        timestamp=datetime(2026, 8, 12, tzinfo=timezone.utc),
    )


def test_status_reports_resolved_generation_as_revision_of_terminal_create(
    tmp_path: Path,
) -> None:
    module, source = _module(tmp_path)
    facade = PodcastSubtitleFacade(module)
    created = facade.run(CreateRequest(episode_id="episode-anji", source_audio=source))
    assert isinstance(created, NeedsReview)
    resolved = facade.decide(created.generation_id, _decision(created))
    assert isinstance(resolved, AcceptedGeneration)

    status = facade.status()

    assert status.state == "revised"
    assert status.active_generation_id == resolved.generation_id
    assert status.checkpoint_generation_id == created.generation_id
    assert status.revision == resolved.transcript.revision


def test_status_rejects_resolved_child_when_ledger_no_longer_contains_its_head(
    tmp_path: Path,
) -> None:
    module, source = _module(tmp_path)
    facade = PodcastSubtitleFacade(module)
    created = facade.run(CreateRequest(episode_id="episode-anji", source_audio=source))
    assert isinstance(created, NeedsReview)
    resolved = facade.decide(created.generation_id, _decision(created))
    assert isinstance(resolved, AcceptedGeneration)
    module.ledger.path.write_bytes(b"")

    with pytest.raises(GenerationIsolationError):
        facade.status()


def test_status_reports_not_started_without_fabricating_completion(
    tmp_path: Path,
) -> None:
    module, _ = _module(tmp_path)
    status = PodcastSubtitleFacade(module).status()
    assert status.state == "not_started"
    assert status.active_generation_id is None
    assert status.checkpoint_stage is None


@pytest.mark.parametrize(
    "checkpoint_stage",
    ("evidence_ready", "correction_ready", "audio_audit_ready"),
)
def test_status_reports_exact_partial_create_stage_without_active_generation(
    tmp_path: Path,
    checkpoint_stage: str,
) -> None:
    if checkpoint_stage == "evidence_ready":
        corrector = _PendingCorrector(tmp_path / "corrector-work")
        module, source = _single_stream_module(tmp_path, texts=("Podcast",), corrector=corrector)
    elif checkpoint_stage == "correction_ready":
        auditor = _PendingAudioAuditor(FixtureAudioAuditorAdapter(), tmp_path / "audio-work")
        module, source = _single_stream_module(
            tmp_path,
            texts=("Podcast",),
            corrector=FixtureCorrectorAdapter(),
            audio_auditor=auditor,
        )
    else:
        verdict = ArbitrationVerdict(
            proposal_id="proposal-last-span",
            status="accepted",
            selected_text="correct candidate",
            confidence=0.99,
            rationale="fixture exact audio",
        )
        arbiter = _PendingArbiter(
            FixtureArbiterAdapter({"proposal-last-span": verdict}),
            tmp_path / "arbiter-work",
        )
        module, source = _single_stream_module(
            tmp_path,
            texts=("original", "words"),
            corrector=_LastSpanProposalCorrector(),
            arbiter=arbiter,
        )
    pending = module.create(
        CreateRequest(episode_id=f"episode-status-{checkpoint_stage}", source_audio=source)
    )
    assert isinstance(pending, Interrupted)

    status = PodcastSubtitleFacade(module).status()

    assert status.state == "partial"
    assert status.active_generation_id is None
    assert status.checkpoint_stage == checkpoint_stage
    assert status.checkpoint_id is not None


def test_status_reports_complete_checkpoint_when_active_pointer_is_missing(
    tmp_path: Path,
) -> None:
    module, source = _single_stream_module(
        tmp_path, texts=("Podcast",), corrector=FixtureCorrectorAdapter()
    )
    created = module.create(
        CreateRequest(episode_id="episode-status-complete", source_audio=source)
    )
    assert not isinstance(created, Interrupted)
    (module.store.root / "active-generation.json").unlink()

    status = PodcastSubtitleFacade(module).status()

    assert status.state == "complete_pending_activation"
    assert status.checkpoint_stage == "complete"


def test_status_keeps_old_active_distinct_from_new_partial_create(
    tmp_path: Path,
) -> None:
    corrector = _PendingCorrector(tmp_path / "corrector-work")
    corrector.ready = True
    module, source = _single_stream_module(tmp_path, texts=("Podcast",), corrector=corrector)
    first = module.create(
        CreateRequest(
            episode_id="episode-status-active-partial",
            source_audio=source,
            policy=SubtitlePolicy(permit_unresolved_low_risk=True),
        )
    )
    assert isinstance(first, AcceptedGeneration)
    corrector.ready = False
    second = module.create(
        CreateRequest(
            episode_id="episode-status-active-partial",
            source_audio=source,
            policy=SubtitlePolicy(permit_unresolved_low_risk=False),
        )
    )
    assert isinstance(second, Interrupted)

    status = PodcastSubtitleFacade(module).status()

    assert status.state == "active_with_partial_create"
    assert status.active_generation_id == first.generation_id
    assert status.checkpoint_stage == "evidence_ready"
    assert status.expected_active_generation_id == first.generation_id


def test_facade_has_no_public_benchmark_comparator(tmp_path: Path) -> None:
    module, _ = _module(tmp_path)
    assert not hasattr(PodcastSubtitleFacade(module), "compare")
