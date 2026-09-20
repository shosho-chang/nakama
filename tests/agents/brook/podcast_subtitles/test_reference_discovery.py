from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agents.brook.podcast_subtitles.reference_discovery import (
    ReferenceDiscoveryError,
    discover_reference_candidates,
    main,
)


def test_discovery_lists_supported_files_as_candidates_without_enrolling_them(
    tmp_path: Path,
) -> None:
    allowed_root = tmp_path / "interviews"
    person_folder = allowed_root / "2026-04-15-安吉"
    research_lane = person_folder / "research"
    research_lane.mkdir(parents=True)
    outline = person_folder / "安吉訪綱.txt"
    report = research_lane / "研究報告.pdf"
    unsupported = person_folder / "recording.wav"
    outline.write_text("must not be read by discovery", encoding="utf-8")
    report.write_bytes(b"not a real pdf")
    unsupported.write_bytes(b"audio")

    result = discover_reference_candidates(
        allowed_root=allowed_root,
        person_folder=person_folder,
    )

    assert tuple(item.relative_path for item in result) == (
        "research/研究報告.pdf",
        "安吉訪綱.txt",
    )
    assert all(item.status == "candidate_only_not_enrolled" for item in result)
    assert all(item.absolute_path.is_absolute() for item in result)
    assert all(item.size_bytes > 0 for item in result)


def test_discovery_ignores_hidden_and_deletion_marked_items(tmp_path: Path) -> None:
    allowed_root = tmp_path / "interviews"
    person_folder = allowed_root / "2026-04-15-安吉"
    hidden_folder = person_folder / ".research"
    deleted_folder = person_folder / "_to_delete_old-lane"
    hidden_folder.mkdir(parents=True)
    deleted_folder.mkdir()
    (person_folder / "approved-outline.txt").write_text("visible", encoding="utf-8")
    (person_folder / ".draft-outline.txt").write_text("hidden", encoding="utf-8")
    (hidden_folder / "report.pdf").write_bytes(b"hidden")
    (deleted_folder / "report.pdf").write_bytes(b"deleted")
    (person_folder / "notes_to_delete_draft.md").write_text("deleted", encoding="utf-8")

    result = discover_reference_candidates(
        allowed_root=allowed_root,
        person_folder=person_folder,
    )

    assert tuple(item.relative_path for item in result) == ("approved-outline.txt",)


def test_discovery_does_not_follow_or_list_file_symlinks(tmp_path: Path) -> None:
    allowed_root = tmp_path / "interviews"
    person_folder = allowed_root / "2026-04-15-安吉"
    person_folder.mkdir(parents=True)
    outside = allowed_root / "outside.txt"
    outside.write_text("must not be discovered", encoding="utf-8")
    linked = person_folder / "linked.txt"
    try:
        os.symlink(outside, linked)
    except OSError:
        pytest.skip("symlink creation is unavailable in this environment")

    result = discover_reference_candidates(
        allowed_root=allowed_root,
        person_folder=person_folder,
    )

    assert result == ()


def test_discovery_cli_lists_candidates_without_manifest_or_authority_fields(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    allowed_root = tmp_path / "interviews"
    person_folder = allowed_root / "2026-04-15-安吉"
    person_folder.mkdir(parents=True)
    (person_folder / "outline-v1.txt").write_text("v1", encoding="utf-8")
    (person_folder / "outline-v2.txt").write_text("v2", encoding="utf-8")

    exit_code = main(
        [
            "--allowed-root",
            str(allowed_root),
            "--person-folder",
            str(person_folder),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["scope"] == "candidate_only_not_enrolled"
    assert [item["relative_path"] for item in payload["candidates"]] == [
        "outline-v1.txt",
        "outline-v2.txt",
    ]
    assert all("sha256" not in item for item in payload["candidates"])
    assert all("trust_tier" not in item for item in payload["candidates"])
    assert all("authority" not in item for item in payload["candidates"])


def test_discovery_requires_one_absolute_folder_below_the_allowed_root(
    tmp_path: Path,
) -> None:
    allowed_root = tmp_path / "interviews"
    person_folder = allowed_root / "2026-04-15-安吉"
    outside_folder = tmp_path / "other" / "2026-04-15-安吉"
    person_folder.mkdir(parents=True)
    outside_folder.mkdir(parents=True)

    with pytest.raises(ReferenceDiscoveryError, match="specific descendant"):
        discover_reference_candidates(
            allowed_root=allowed_root,
            person_folder=allowed_root,
        )
    with pytest.raises(ReferenceDiscoveryError, match="specific descendant"):
        discover_reference_candidates(
            allowed_root=allowed_root,
            person_folder=outside_folder,
        )
    with pytest.raises(ReferenceDiscoveryError, match="absolute paths"):
        discover_reference_candidates(
            allowed_root=Path("interviews"),
            person_folder=Path("interviews") / "2026-04-15-安吉",
        )


@pytest.mark.parametrize("folder_name", [".hidden-person", "_to_delete_old-person"])
def test_discovery_rejects_an_ignored_person_folder(
    tmp_path: Path,
    folder_name: str,
) -> None:
    allowed_root = tmp_path / "interviews"
    person_folder = allowed_root / folder_name
    person_folder.mkdir(parents=True)
    (person_folder / "outline.txt").write_text("must stay ignored", encoding="utf-8")

    with pytest.raises(ReferenceDiscoveryError, match="hidden or deletion-marked"):
        discover_reference_candidates(
            allowed_root=allowed_root,
            person_folder=person_folder,
        )


def test_discovery_reads_metadata_without_opening_candidate_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    allowed_root = tmp_path / "interviews"
    person_folder = allowed_root / "2026-04-15-安吉"
    person_folder.mkdir(parents=True)
    (person_folder / "outline.txt").write_text("private source text", encoding="utf-8")

    def forbidden_read(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("discovery must not open candidate bytes")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read)
    monkeypatch.setattr(Path, "read_text", forbidden_read)
    monkeypatch.setattr("builtins.open", forbidden_read)

    result = discover_reference_candidates(
        allowed_root=allowed_root,
        person_folder=person_folder,
    )

    assert tuple(item.relative_path for item in result) == ("outline.txt",)


def test_discovery_cli_can_use_the_configured_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    allowed_root = tmp_path / "interviews"
    person_folder = allowed_root / "2026-04-15-安吉"
    person_folder.mkdir(parents=True)
    (person_folder / "outline.txt").write_text("outline", encoding="utf-8")
    monkeypatch.setenv("INTERVIEW_RESEARCH_ROOT", str(allowed_root))

    assert main(["--person-folder", str(person_folder)]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["allowed_root"] == str(allowed_root.resolve())
