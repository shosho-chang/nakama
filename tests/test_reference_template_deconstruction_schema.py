from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "prompts" / "thumbnail" / "reference_template_deconstruction_schema_v1.json"
PROMPT_PATH = REPO_ROOT / "prompts" / "thumbnail" / "reference_template_deconstruction_v1.md"
SPEC_PATH = (
    REPO_ROOT
    / "docs"
    / "research"
    / "2026-05-29-ali-jeff-thumbnail-template-corpus-spec.md"
)
SHOT_LIST_PATH = (
    REPO_ROOT / "docs" / "research" / "2026-05-29-thumbnail-cutout-shot-list-v1.md"
)


def test_reference_template_deconstruction_schema_loads():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["title"] == "Ali/Jeff Reference Template Deconstruction v1"
    assert schema["properties"]["records"]["items"]["$ref"] == "#/$defs/record"
    assert "component" in schema["$defs"]
    assert "typography" in schema["$defs"]


def test_reference_template_deconstruction_schema_requires_renderer_fields():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    required = set(schema["$defs"]["record"]["required"])

    assert "host" in required
    assert "components" in required
    assert "typography" in required
    assert "generator_constraints" in required
    assert "evaluation_targets" in required


def test_reference_template_deconstruction_prompt_links_spec_and_schema_terms():
    prompt = PROMPT_PATH.read_text(encoding="utf-8")

    assert SPEC_PATH.exists()
    assert "2026-05-29-ali-jeff-thumbnail-template-corpus-spec.md" in prompt
    assert "ali_jeff_template_corpus_v1" in prompt
    assert "template_family_candidate" in prompt
    assert "evaluation_targets" in prompt


def test_thumbnail_cutout_shot_list_covers_situational_poses():
    text = SHOT_LIST_PATH.read_text(encoding="utf-8")

    assert "Pointing left" in text
    assert "Chin touch" in text
    assert "Wearing headphones" in text
    assert "Holding phone" in text
    assert "Center metric pose" in text
