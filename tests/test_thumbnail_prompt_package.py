from __future__ import annotations

from shared.thumbnail_idea import ParsedIdea
from shared.thumbnail_prompt_package import build_thumbnail_prompt_package


def _idea(**overrides) -> ParsedIdea:
    base = dict(
        hook="不只增肌",
        emotion_key="explaining",
        emotion_input="explaining",
        visual="template=shosho_benefit_list_card; component=benefit_list_card; host=left",
        decoration="6",
        bg="warm creator studio",
        archetype_tags=("T-A1", "T-V3"),
        lane="Ali Warm Explainer",
        recipe_id="ali_warm_evidence_list",
        reference_template_id="shosho_benefit_list_card",
        title_pairing="肌酸的 6 個健康效益：不只是增肌，更是護腦與抗老",
        asset_queries=("creatine bottle", "benefit note card"),
        component_type="benefit_list_card",
        component_text=("護腦", "抗老", "增力"),
        host_directive="face large on left third; gaze toward benefit card",
        viewer_promise="Creatine has credible benefits beyond muscle.",
        evidence_fit="research-backed multi-benefit claim",
        trust_risk="avoid medical cure framing",
    )
    base.update(overrides)
    return ParsedIdea(**base)


def test_prompt_package_defaults_to_gpt_image_2_and_overlay_policy():
    package = build_thumbnail_prompt_package(_idea(), idea_index=0)

    assert package.model == "gpt-image-2"
    assert package.size == "1536x1024"
    assert package.quality == "high"
    assert package.overlay_text == "不只增肌"
    assert package.visual_strategy.text_rendering_policy == "deterministic_overlay"
    assert "Nakama can overlay: 不只增肌" in package.prompt_text
    assert "Do not render the final Traditional Chinese headline text" in package.prompt_text


def test_prompt_package_has_stable_reference_order():
    package = build_thumbnail_prompt_package(_idea(), idea_index=1)

    labels = [binding.provider_label for binding in package.reference_bindings]
    roles = [binding.role for binding in package.reference_bindings]

    assert package.package_id == "thumb-v3-prompt-02"
    assert labels == ["img 1", "img 2", "img 3"]
    assert roles == ["person_reference", "object_reference", "style_reference"]
    assert package.reference_bindings[0].required is True
    assert package.reference_bindings[-1].reference_id == "shosho_benefit_list_card"


def test_prompt_package_negative_prompt_blocks_known_failure_modes():
    package = build_thumbnail_prompt_package(_idea())

    negative = package.negative_prompt
    assert "No extra labels" in negative
    assert "No hospital" in negative
    assert "No distorted face" in negative
    assert "No cluttered object pile" in negative


def test_prompt_package_falls_back_to_jeff_template_from_lane():
    package = build_thumbnail_prompt_package(
        _idea(
            reference_template_id="",
            lane="Jeff Clean Tutorial",
            recipe_id="",
            component_type="tool_panel",
            asset_queries=(),
        )
    )

    assert package.visual_strategy.reference_template_id == "jeff_tool_header_panel"
    assert package.visual_strategy.style_contract_id == "jeff_clean_component"
    assert "Jeff Su clean component clarity" in package.prompt_text


def test_prompt_package_marks_host_reference_ready_when_file_exists(tmp_path):
    host_photo = tmp_path / "host.png"
    host_photo.write_bytes(b"fake image")

    package = build_thumbnail_prompt_package(
        _idea(),
        host_reference_path=str(host_photo),
    )

    host = package.reference_bindings[0]
    assert host.role == "person_reference"
    assert host.status == "ready"
    assert host.readiness_label == "Ready"
    assert host.local_path == str(host_photo)


def test_prompt_package_marks_object_reference_ready_from_manifest(tmp_path):
    object_photo = tmp_path / "creatine.png"
    object_photo.write_bytes(b"fake image")
    manifest = {
        "items": [
            {
                "idea_index": 2,
                "asset_need_id": "idea03-asset01",
                "query": "creatine bottle on clean desk",
                "status": "licensed",
                "provenance": {"local_path": str(object_photo)},
            }
        ]
    }

    package = build_thumbnail_prompt_package(
        _idea(),
        idea_index=2,
        asset_manifest=manifest,
    )

    object_binding = next(
        binding for binding in package.reference_bindings if binding.role == "object_reference"
    )
    assert object_binding.status == "ready"
    assert object_binding.asset_need_id == "idea03-asset01"
    assert object_binding.search_query == "creatine bottle on clean desk"
    assert object_binding.local_path == str(object_photo)


def test_prompt_package_ignores_malformed_manifest_idea_index():
    package = build_thumbnail_prompt_package(
        _idea(),
        idea_index=1,
        asset_manifest={"items": [{"idea_index": "bad", "status": "licensed"}]},
    )

    object_binding = next(
        binding for binding in package.reference_bindings if binding.role == "object_reference"
    )
    assert object_binding.status == "optional_missing"


def test_prompt_package_marks_style_reference_ready(monkeypatch, tmp_path):
    style_reference = tmp_path / "style.jpg"
    style_reference.write_bytes(b"fake image")

    class FakeReferenceTemplate:
        reference_paths = (str(style_reference),)

    monkeypatch.setattr(
        "shared.thumbnail_prompt_package.get_reference_template",
        lambda _template_id: FakeReferenceTemplate(),
    )

    package = build_thumbnail_prompt_package(_idea())

    style = package.reference_bindings[-1]
    assert style.role == "style_reference"
    assert style.status == "ready"
    assert style.local_path == str(style_reference)
