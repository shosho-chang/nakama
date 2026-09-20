from __future__ import annotations

import hashlib
import io
import json
import wave
from pathlib import Path

import pytest

from agents.brook.podcast_subtitles.gold_reviewer_kit import (
    assemble_transcript_gold_suite,
    compile_adjudication_responses,
    compile_first_pass_responses,
    load_adjudication_reviewer_kit,
    load_first_pass_reviewer_kit,
    prepare_adjudication_reviewer_kit,
    prepare_first_pass_reviewer_kit,
    verify_reviewer_kit_directory,
)
from agents.brook.podcast_subtitles.transcript_gold import (
    AudioClipBinding,
    TranscriptAnnotationPacket,
    TranscriptAnnotationProtocol,
    load_transcript_gold_suite,
)


def _wav_bytes(frames: bytes, *, rate: int = 1_000) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(1)
        writer.setframerate(rate)
        writer.writeframes(frames)
    return output.getvalue()


def _packet_inputs(tmp_path: Path) -> tuple[Path, tuple[Path, ...], TranscriptAnnotationPacket]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    normalized = _wav_bytes(bytes(index % 251 for index in range(3_000)))
    clip_bytes = (_wav_bytes(b"a" * 1_000), _wav_bytes(b"b" * 1_000))
    clips = tuple(
        AudioClipBinding.build(
            clip_id=f"source-private-clip-{index}",
            start_ms=(index - 1) * 1_000,
            end_ms=index * 1_000,
            clip_audio_hash=hashlib.sha256(raw).hexdigest(),
            clip_audio_size_bytes=len(raw),
            normalized_audio_hash=hashlib.sha256(normalized).hexdigest(),
            normalized_audio_size_bytes=len(normalized),
        )
        for index, raw in enumerate(clip_bytes, start=1)
    )
    packet = TranscriptAnnotationPacket.build(
        packet_id="private-episode-packet",
        episode_id="private-episode-and-guest",
        normalized_audio_hash=clips[0].normalized_audio_hash,
        normalized_audio_size_bytes=clips[0].normalized_audio_size_bytes,
        instruction_profile_id="audio-only-transcript-two-pass-adjudication-v1",
        protocol=TranscriptAnnotationProtocol(protocol_id="two-pass-third-adjudication-v1"),
        clips=clips,
    )
    packet_path = tmp_path / "annotation-packet.json"
    packet_path.write_bytes(packet.canonical_bytes())
    audio_paths = tuple(tmp_path / f"clip-{index}.wav" for index in range(1, 3))
    for path, raw in zip(audio_paths, clip_bytes, strict=True):
        path.write_bytes(raw)
    return packet_path, audio_paths, packet


def test_first_pass_kit_is_neutral_candidate_free_and_compiles_strict_submissions(
    tmp_path: Path,
) -> None:
    packet_path, audio_paths, packet = _packet_inputs(tmp_path)
    forbidden_candidate_hash = "a" * 64
    kit_root = tmp_path / "reviewer-a-kit"

    prepared = prepare_first_pass_reviewer_kit(
        annotation_packet_path=packet_path,
        clip_audio_paths=audio_paths,
        output_dir=kit_root,
        kit_id="anji-pilot-first-pass-a",
        forbidden_candidate_hashes=(forbidden_candidate_hash,),
    )

    manifest_path = kit_root / "reviewer-kit.v1.json"
    manifest = load_first_pass_reviewer_kit(manifest_path)
    assert manifest == prepared
    assert [item.neutral_clip_id for item in manifest.clips] == ["item-0001", "item-0002"]
    assert [item.audio_relpath for item in manifest.clips] == [
        "audio/0001.wav",
        "audio/0002.wav",
    ]
    exported_names = sorted(path.relative_to(kit_root).as_posix() for path in kit_root.rglob("*"))
    assert exported_names == [
        "audio",
        "audio/0001.wav",
        "audio/0002.wav",
        "first-pass.response.template.v1.json",
        "reviewer-kit.v1.json",
    ]
    exported_text = manifest_path.read_text(encoding="utf-8")
    assert packet.episode_id not in exported_text
    assert packet.packet_id not in exported_text
    assert all(clip.clip_id not in exported_text for clip in packet.clips)
    assert forbidden_candidate_hash not in exported_text
    assert "candidate_outputs_hidden" in exported_text
    assert "reference_material_hidden" in exported_text
    assert (
        verify_reviewer_kit_directory(
            kit_root,
            kind="first-pass",
            forbidden_candidate_hashes=(forbidden_candidate_hash,),
        )
        == prepared
    )

    response = json.loads(
        (kit_root / "first-pass.response.template.v1.json").read_text(encoding="utf-8")
    )
    response["annotator_id"] = "reviewer-a"
    response["answers"][0].update(
        outcome="accepted",
        text="安吉說 hello",
    )
    response_path = tmp_path / "reviewer-a.response.json"
    response_path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
    compiled_root = tmp_path / "reviewer-a-submissions"

    submissions = compile_first_pass_responses(
        annotation_packet_path=packet_path,
        reviewer_kit_manifest_path=manifest_path,
        response_path=response_path,
        output_dir=compiled_root,
    )

    assert len(submissions) == 2
    assert submissions[0].clip == packet.clips[0]
    assert submissions[0].annotator_id == "reviewer-a"
    assert submissions[0].text == "安吉說 hello"
    assert submissions[1].outcome == "needs_review"
    assert len(tuple(compiled_root.glob("*.audio-only-submission.json"))) == 2


def test_first_pass_kit_rejects_wrong_clip_bytes_and_duplicate_json_keys(tmp_path: Path) -> None:
    packet_path, audio_paths, _ = _packet_inputs(tmp_path)
    audio_paths[0].write_bytes(b"not the sealed clip")
    with pytest.raises(ValueError, match="clip audio bytes differ"):
        prepare_first_pass_reviewer_kit(
            annotation_packet_path=packet_path,
            clip_audio_paths=audio_paths,
            output_dir=tmp_path / "bad-kit",
            kit_id="bad-kit",
        )

    packet_path, audio_paths, _ = _packet_inputs(tmp_path / "second")
    kit_root = tmp_path / "good-kit"
    prepare_first_pass_reviewer_kit(
        annotation_packet_path=packet_path,
        clip_audio_paths=audio_paths,
        output_dir=kit_root,
        kit_id="good-kit",
    )
    response = (kit_root / "first-pass.response.template.v1.json").read_text(encoding="utf-8")
    duplicated = response.replace(
        '"annotator_id":""',
        '"annotator_id":"reviewer-a","annotator_id":"reviewer-b"',
    )
    response_path = tmp_path / "duplicate.response.json"
    response_path.write_text(duplicated, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate JSON key"):
        compile_first_pass_responses(
            annotation_packet_path=packet_path,
            reviewer_kit_manifest_path=kit_root / "reviewer-kit.v1.json",
            response_path=response_path,
            output_dir=tmp_path / "never-written",
        )


def _compile_reviewer(
    *,
    tmp_path: Path,
    packet_path: Path,
    audio_paths: tuple[Path, ...],
    reviewer_id: str,
    texts: tuple[str, str],
) -> tuple[Path, ...]:
    kit_root = tmp_path / f"{reviewer_id}-kit"
    prepare_first_pass_reviewer_kit(
        annotation_packet_path=packet_path,
        clip_audio_paths=audio_paths,
        output_dir=kit_root,
        kit_id=f"first-pass-{reviewer_id}",
    )
    response = json.loads(
        (kit_root / "first-pass.response.template.v1.json").read_text(encoding="utf-8")
    )
    response["annotator_id"] = reviewer_id
    for answer, value in zip(response["answers"], texts, strict=True):
        answer.update(outcome="accepted", text=value)
    response_path = tmp_path / f"{reviewer_id}.response.json"
    response_path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
    compiled = tmp_path / f"{reviewer_id}-compiled"
    compile_first_pass_responses(
        annotation_packet_path=packet_path,
        reviewer_kit_manifest_path=kit_root / "reviewer-kit.v1.json",
        response_path=response_path,
        output_dir=compiled,
    )
    return tuple(sorted(compiled.glob("*.audio-only-submission.json")))


def test_third_person_kit_hides_identities_and_compiles_candidate_blind_adjudication(
    tmp_path: Path,
) -> None:
    packet_path, audio_paths, packet = _packet_inputs(tmp_path)
    reviewer_a = _compile_reviewer(
        tmp_path=tmp_path,
        packet_path=packet_path,
        audio_paths=audio_paths,
        reviewer_id="reviewer-a",
        texts=("安吉", "政大"),
    )
    reviewer_b = _compile_reviewer(
        tmp_path=tmp_path,
        packet_path=packet_path,
        audio_paths=audio_paths,
        reviewer_id="reviewer-b",
        texts=("安琪", "政大"),
    )
    candidate_hash = "b" * 64
    kit_root = tmp_path / "adjudicator-kit"

    prepared = prepare_adjudication_reviewer_kit(
        annotation_packet_path=packet_path,
        clip_audio_paths=audio_paths,
        submission_paths=(*reviewer_a, *reviewer_b),
        output_dir=kit_root,
        kit_id="anji-pilot-third-pass",
        forbidden_candidate_hashes=(candidate_hash,),
    )

    manifest_path = kit_root / "reviewer-kit.v1.json"
    manifest = load_adjudication_reviewer_kit(manifest_path)
    assert manifest == prepared
    raw = manifest_path.read_text(encoding="utf-8")
    assert "reviewer-a" not in raw
    assert "reviewer-b" not in raw
    assert packet.episode_id not in raw
    assert all(clip.clip_id not in raw for clip in packet.clips)
    assert candidate_hash not in raw
    assert manifest.clips[0].first_passes[0].pass_id == "pass-a"
    assert {item.text for item in manifest.clips[0].first_passes} == {"安吉", "安琪"}
    assert (
        verify_reviewer_kit_directory(
            kit_root,
            kind="adjudication",
            forbidden_candidate_hashes=(candidate_hash,),
        )
        == prepared
    )

    response = json.loads(
        (kit_root / "adjudication.response.template.v1.json").read_text(encoding="utf-8")
    )
    expected_unattested_coverage = {
        "entity_labels_exhaustive": False,
        "code_switch_labels_exhaustive": False,
        "numeric_labels_exhaustive": False,
        "critical_omission_labels_exhaustive": False,
    }
    assert all(
        answer["metric_coverage"] == expected_unattested_coverage
        for answer in response["answers"]
    )
    response["adjudicator_id"] = "reviewer-c"
    response["answers"][0].update(
        final_expected_outcome="accepted",
        final_text="安吉",
        span_labels=[
            {"label_id": "guest-name", "kind": "entity", "token_start": 0, "token_end": 2}
        ],
    )
    # A missing legacy attestation fails closed even when a label is present.
    response["answers"][0].pop("metric_coverage")
    response["answers"][1].update(
        final_expected_outcome="accepted",
        final_text="政大",
        # Empty label lists plus true means the human exhaustively scanned the
        # accepted text and found zero instances; it is not a positive label.
        metric_coverage={key: True for key in expected_unattested_coverage},
    )
    response_path = tmp_path / "reviewer-c.response.json"
    response_path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
    compiled_root = tmp_path / "adjudications"

    records = compile_adjudication_responses(
        annotation_packet_path=packet_path,
        reviewer_kit_manifest_path=manifest_path,
        submission_paths=(*reviewer_a, *reviewer_b),
        response_path=response_path,
        output_dir=compiled_root,
    )

    assert len(records) == 2
    assert records[0].adjudicator_id == "reviewer-c"
    assert records[0].final_text == "安吉"
    assert records[0].final_span_labels[0].expected_text == "安吉"
    assert records[0].metric_coverage.model_dump() == expected_unattested_coverage
    assert records[1].final_span_labels == ()
    assert records[1].final_omission_labels == ()
    assert all(records[1].metric_coverage.model_dump().values())
    assert records[0].first_pass_submission_hashes == tuple(
        item.submission_hash for item in manifest.clips[0].first_passes
    )
    assert len(tuple(compiled_root.glob("*.transcript-adjudication.json"))) == 2

    gold_path = tmp_path / "transcript-gold-suite.v1.json"
    gold = assemble_transcript_gold_suite(
        annotation_packet_path=packet_path,
        submission_paths=(*reviewer_a, *reviewer_b),
        adjudication_paths=tuple(sorted(compiled_root.glob("*.transcript-adjudication.json"))),
        output_path=gold_path,
        suite_id="anji-pilot-human-gold",
        forbidden_candidate_hashes=(candidate_hash,),
    )
    assert gold.complete is True
    assert len(gold.labels) == len(packet.clips)
    assert (
        gold.labels[0].provenance.adjudication_record.metric_coverage.model_dump()
        == expected_unattested_coverage
    )
    assert all(
        gold.labels[1]
        .provenance.adjudication_record.metric_coverage.model_dump()
        .values()
    )
    assert load_transcript_gold_suite(gold_path) == gold

    response["adjudicator_id"] = "reviewer-a"
    bad_path = tmp_path / "same-person.response.json"
    bad_path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="third adjudicator must differ"):
        compile_adjudication_responses(
            annotation_packet_path=packet_path,
            reviewer_kit_manifest_path=manifest_path,
            submission_paths=(*reviewer_a, *reviewer_b),
            response_path=bad_path,
            output_dir=tmp_path / "same-person-output",
        )

    response["adjudicator_id"] = "reviewer-c"
    response["answers"][0].update(
        final_expected_outcome="needs_review",
        final_text=None,
        metric_coverage={
            **expected_unattested_coverage,
            "entity_labels_exhaustive": True,
        },
        span_labels=[],
        omission_labels=[],
    )
    false_coverage_path = tmp_path / "needs-review-false-coverage.response.json"
    false_coverage_path.write_text(
        json.dumps(response, ensure_ascii=False), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="invalid adjudication response batch"):
        compile_adjudication_responses(
            annotation_packet_path=packet_path,
            reviewer_kit_manifest_path=manifest_path,
            submission_paths=(*reviewer_a, *reviewer_b),
            response_path=false_coverage_path,
            output_dir=tmp_path / "needs-review-false-coverage-output",
        )
