from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from shared.thumbnail_idea import ParsedIdea
from thousand_sunny.routers.bridge_project_thumbnails import _brainstorm_user_message
from thousand_sunny.routers.bridge_project_thumbnails import _pick_youtube_host_with_reference_gate


def test_youtube_brainstorm_user_message_includes_concrete_reference_match_plan():
    parts = _brainstorm_user_message(
        title_candidates=["95% of People STILL Prompt ChatGPT-5 Wrong"],
        one_sentence="prompt correction tutorial",
        search_topic="ChatGPT prompting",
        content_type="youtube",
    )
    combined = "\n".join(part["text"] for part in parts if part.get("type") == "text")

    assert "Title-template match plan" in combined
    assert "Concrete Ali/Jeff reference match plan" in combined
    assert "jeff_command_panel / jeff_su_003" in combined
    assert "Do not mix a host pose" in combined


def test_podcast_brainstorm_user_message_does_not_include_youtube_reference_plan():
    parts = _brainstorm_user_message(
        title_candidates=["Episode title"],
        one_sentence="podcast episode",
        search_topic="interview",
        content_type="podcast",
    )
    combined = "\n".join(part["text"] for part in parts if part.get("type") == "text")

    assert "Concrete Ali/Jeff reference match plan" not in combined


def test_youtube_host_picker_uses_reference_gate_to_skip_wrong_direction(tmp_path, monkeypatch):
    vault = tmp_path
    wrong = vault / "Attachments" / "cutouts" / "shosho" / "explaining" / "wrong.png"
    right = vault / "Attachments" / "cutouts" / "shosho" / "explaining" / "right.png"
    wrong.parent.mkdir(parents=True)
    Image.new("RGBA", (180, 260), (48, 120, 200, 255)).save(wrong)
    Image.new("RGBA", (180, 260), (48, 120, 200, 255)).save(right)

    manifest_path = tmp_path / "pose_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "shosho_cutout_pose_manifest.v1",
                "entries": [
                    _manifest_entry(
                        "WRONG",
                        wrong,
                        hands="point_screen_right",
                        gaze="screen_left",
                    ),
                    _manifest_entry(
                        "RIGHT",
                        right,
                        hands="point_screen_left",
                        gaze="screen_left",
                    ),
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("NAKAMA_CUTOUT_POSE_MANIFEST", str(manifest_path))

    idea = ParsedIdea(
        hook="Do this instead",
        emotion_key="explaining",
        emotion_input="explaining",
        visual="template=jeff_command_panel",
        decoration="",
        bg="blurred office",
        reference_template_id="jeff_command_panel",
        title_pairing="95% of People STILL Prompt ChatGPT-5 Wrong",
        component_type="command_panel",
        component_text=("Wrong", "Do this instead"),
    )

    cutout_path, manifest = _pick_youtube_host_with_reference_gate(idea, vault)

    assert cutout_path == right
    assert manifest is not None
    assert manifest["selected"]["cutout_id"] == "RIGHT"
    assert manifest["reference_record"]["family_id"] == "jeff_command_panel"
    assert manifest["host_compatibility"]["ok"] is True


def _manifest_entry(cutout_id: str, path: Path, *, hands: str, gaze: str) -> dict:
    return {
        "cutout_id": cutout_id,
        "source_path": str(path),
        "vault_relative_path": str(path.relative_to(path.parents[4])),
        "original_emotion_folder": "explaining",
        "picker_policy": "eligible",
        "confidence": 0.9,
        "tags": {
            "expression_family": "mild_surprise",
            "intensity": "mild",
            "credibility": "high",
            "hands": hands,
            "body_angle": "three_quarter_right",
            "gaze": gaze,
        },
        "use_context": ["jeff_clean_tutorial"],
        "avoid_context": [],
    }
