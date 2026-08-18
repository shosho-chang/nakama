"""Pose-aware cutout casting tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from shared.cutout_casting import (
    CutoutCastingError,
    CutoutCastRequest,
    build_cast_request_from_idea,
    cast_cutouts,
    pick_youtube_host_by_pose,
    write_candidate_contact_sheet,
)


def _png(path: Path, color: tuple[int, int, int, int] = (40, 120, 220, 255)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (80, 120), color).save(path)


def _entry(
    vault_root: Path,
    cutout_id: str,
    rel: str,
    *,
    expression_family: str,
    intensity: str = "mild",
    credibility: str = "high",
    use_context: list[str] | None = None,
    avoid_context: list[str] | None = None,
    policy: str = "eligible",
    confidence: float = 0.8,
) -> dict:
    path = vault_root / rel
    _png(path)
    return {
        "cutout_id": cutout_id,
        "source_path": str(path),
        "vault_relative_path": rel,
        "original_emotion_folder": Path(rel).parent.name,
        "tags": {
            "body_angle": "front",
            "gaze": "camera",
            "expression_family": expression_family,
            "intensity": intensity,
            "mouth": "slight_smile",
            "brow": "relaxed",
            "hands": "chin",
            "crop": "waist",
            "credibility": credibility,
        },
        "use_context": use_context or [],
        "avoid_context": avoid_context or [],
        "confidence": confidence,
        "picker_policy": policy,
    }


@pytest.fixture
def fake_manifest(tmp_path: Path) -> tuple[Path, Path]:
    vault_root = tmp_path / "vault"
    manifest = {
        "schema_version": "shosho_cutout_pose_manifest.v1",
        "entries": [
            _entry(
                vault_root,
                "C10",
                "Attachments/cutouts/shosho/thoughtful/1.png",
                expression_family="thoughtful",
                intensity="subtle",
                credibility="high",
                use_context=["ali_warm_explainer", "evidence_review"],
                confidence=0.92,
            ),
            _entry(
                vault_root,
                "C11",
                "Attachments/cutouts/shosho/explaining/1.png",
                expression_family="explain",
                intensity="subtle",
                credibility="high",
                use_context=["ali_warm_explainer"],
                confidence=0.8,
            ),
            _entry(
                vault_root,
                "C12",
                "Attachments/cutouts/shosho/surprised/1.png",
                expression_family="mild_surprise",
                intensity="extreme",
                credibility="low",
                use_context=["comedy_only"],
                avoid_context=["ali_warm_explainer", "evidence_review"],
                policy="manual_only",
                confidence=0.95,
            ),
            _entry(
                vault_root,
                "C13",
                "Attachments/cutouts/shosho/laughing/1.png",
                expression_family="warm_laugh",
                intensity="medium",
                credibility="medium",
                use_context=["personal_story"],
                avoid_context=["evidence_review"],
                confidence=0.7,
            ),
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path, vault_root


def test_build_request_infers_evidence_context_first():
    request = build_cast_request_from_idea(
        {
            "emotion_key": "thoughtful",
            "hook": "5g",
            "visual": "research evidence note",
            "decoration": "",
            "bg": "",
        }
    )

    assert request.expression_families[:2] == ("thoughtful", "soft_smile")
    assert request.use_contexts[0] == "evidence_review"
    assert request.max_intensity == "mild"
    assert request.min_credibility == "medium"


def test_cast_cutouts_excludes_manual_only_and_ranks_context(fake_manifest):
    manifest_path, vault_root = fake_manifest
    request = CutoutCastRequest(
        emotion_key="thoughtful",
        expression_families=("thoughtful", "explain"),
        use_contexts=("ali_warm_explainer", "evidence_review"),
        avoid_contexts=("comedy_only",),
        max_intensity="mild",
        min_credibility="medium",
        limit=6,
    )

    candidates = cast_cutouts(request, manifest_path=manifest_path, vault_root=vault_root)

    assert [c.cutout_id for c in candidates] == ["C10", "C11"]
    assert all(c.picker_policy != "manual_only" for c in candidates)
    assert candidates[0].tags["expression_family"] == "thoughtful"


def test_pick_youtube_host_by_pose_returns_ranked_selection(fake_manifest):
    manifest_path, vault_root = fake_manifest

    selection = pick_youtube_host_by_pose(
        {"emotion_key": "thoughtful", "visual": "research evidence", "hook": "5g"},
        vault_root,
        manifest_path=manifest_path,
    )

    assert selection is not None
    assert selection.candidate.cutout_id == "C10"
    assert selection.path.name == "1.png"
    assert selection.to_manifest()["selected"]["cutout_id"] == "C10"


def test_pick_youtube_host_by_pose_missing_manifest_returns_none(tmp_path: Path):
    selection = pick_youtube_host_by_pose(
        {"emotion_key": "thoughtful"},
        tmp_path,
        manifest_path=tmp_path / "missing.json",
    )
    assert selection is None


def test_pick_youtube_host_by_pose_raises_when_manifest_has_no_match(tmp_path: Path):
    manifest_path = tmp_path / "empty_manifest.json"
    manifest_path.write_text(json.dumps({"entries": []}), encoding="utf-8")

    with pytest.raises(CutoutCastingError):
        pick_youtube_host_by_pose(
            {"emotion_key": "thoughtful", "visual": "research evidence"},
            tmp_path,
            manifest_path=manifest_path,
        )


def test_write_candidate_contact_sheet(fake_manifest, tmp_path: Path):
    manifest_path, vault_root = fake_manifest
    request = CutoutCastRequest(
        emotion_key="thoughtful",
        expression_families=("thoughtful", "explain"),
        use_contexts=("ali_warm_explainer",),
        max_intensity="mild",
        min_credibility="medium",
        limit=2,
    )
    candidates = cast_cutouts(request, manifest_path=manifest_path, vault_root=vault_root)

    out = write_candidate_contact_sheet(candidates, tmp_path / "sheet.png")

    assert out.is_file()
    with Image.open(out) as sheet:
        assert sheet.size[0] > 0
        assert sheet.size[1] > 0
