"""Load and query the Ali/Jeff thumbnail reference-template corpus.

This module is intentionally data-layer only. It does not select final
templates or render thumbnails; it gives those later stages one stable source
for family definitions and reference-image assignments.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Literal


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TAXONOMY_PATH = (
    REPO_ROOT / "prompts" / "thumbnail" / "ali_jeff_template_family_taxonomy_v1.json"
)
DEFAULT_DECONSTRUCTIONS_PATH = (
    REPO_ROOT
    / "prompts"
    / "thumbnail"
    / "reference_corpus"
    / "ali_jeff_deconstructions_v1.jsonl"
)

ProductionStatus = Literal["renderable_v1", "reference_only", "archive"]
Priority = Literal["gold", "silver", "archive"]


def load_family_taxonomy(path: Path | None = None) -> dict[str, Any]:
    """Load the machine-readable Ali/Jeff family taxonomy."""

    taxonomy_path = path or DEFAULT_TAXONOMY_PATH
    taxonomy = json.loads(taxonomy_path.read_text(encoding="utf-8"))
    if taxonomy.get("schema_version") != "ali_jeff_template_family_taxonomy_v1":
        raise ValueError(f"unexpected thumbnail taxonomy schema: {taxonomy_path}")
    return taxonomy


def list_template_families(
    *,
    creator_style: str | None = None,
    production_status: ProductionStatus | None = None,
    priority: Priority | None = None,
    taxonomy: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return family records filtered by creator, status, or priority."""

    data = taxonomy or load_family_taxonomy()
    families = list(data.get("families", []))
    if creator_style:
        families = [item for item in families if item.get("creator_style") == creator_style]
    if production_status:
        families = [item for item in families if item.get("production_status") == production_status]
    if priority:
        families = [item for item in families if item.get("priority") == priority]
    return families


def get_template_family(
    family_id: str,
    *,
    taxonomy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return one family record by ID, raising clearly if it is unknown."""

    data = taxonomy or load_family_taxonomy()
    for family in data.get("families", []):
        if family.get("family_id") == family_id:
            return family
    raise KeyError(f"unknown thumbnail template family: {family_id}")


def list_reference_assignments(
    *,
    family_id: str | None = None,
    creator: str | None = None,
    renderable_v1: bool | None = None,
    priority: Priority | None = None,
    taxonomy: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return image-to-family assignment records."""

    data = taxonomy or load_family_taxonomy()
    assignments = list(data.get("image_family_assignments", []))
    if family_id:
        known_ids = {family["family_id"] for family in data.get("families", [])}
        if family_id not in known_ids:
            raise KeyError(f"unknown thumbnail template family: {family_id}")
        assignments = [item for item in assignments if item.get("family_id") == family_id]
    if creator:
        assignments = [item for item in assignments if item.get("creator") == creator]
    if renderable_v1 is not None:
        assignments = [item for item in assignments if bool(item.get("renderable_v1")) is renderable_v1]
    if priority:
        assignments = [item for item in assignments if item.get("priority") == priority]
    return assignments


def load_deconstruction_records(path: Path | None = None) -> list[dict[str, Any]]:
    """Load normalized per-image deconstruction JSONL records."""

    records_path = path or DEFAULT_DECONSTRUCTIONS_PATH
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(records_path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        record = json.loads(stripped)
        if record.get("schema_version") != "ali_jeff_template_corpus_v1":
            raise ValueError(f"unexpected deconstruction schema at {records_path}:{line_number}")
        records.append(record)
    return records


def list_deconstruction_records(
    *,
    family_id: str | None = None,
    creator: str | None = None,
    renderable_v1: bool | None = None,
    priority: Priority | None = None,
    taxonomy: dict[str, Any] | None = None,
    records: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return normalized per-image records filtered by family, creator, or status."""

    data = taxonomy or load_family_taxonomy()
    items = list(records if records is not None else load_deconstruction_records())
    if family_id:
        known_ids = {family["family_id"] for family in data.get("families", [])}
        if family_id not in known_ids:
            raise KeyError(f"unknown thumbnail template family: {family_id}")
        items = [item for item in items if item.get("template_family_candidate") == family_id]
    if creator:
        items = [item for item in items if item.get("creator") == creator]
    if renderable_v1 is not None:
        items = [item for item in items if bool(item.get("renderable_v1")) is renderable_v1]
    if priority:
        items = [item for item in items if item.get("priority") == priority]
    return items


def best_reference_examples(
    family_id: str,
    *,
    limit: int = 3,
    renderable_only: bool = True,
    taxonomy: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return the best concrete reference images for a family.

    Gold references are preferred, then silver, then archive. The original
    assignment order is used as the stable tie-breaker.
    """

    data = taxonomy or load_family_taxonomy()
    assignments = list_reference_assignments(
        family_id=family_id,
        renderable_v1=True if renderable_only else None,
        taxonomy=data,
    )
    priority_rank = {"gold": 0, "silver": 1, "archive": 2}
    assignments.sort(key=lambda item: (priority_rank.get(str(item.get("priority")), 9), item["reference_id"]))
    return assignments[: max(1, limit)]


def best_deconstruction_examples(
    family_id: str,
    *,
    limit: int = 3,
    renderable_only: bool = True,
    taxonomy: dict[str, Any] | None = None,
    records: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return the best concrete deconstruction records for a family."""

    data = taxonomy or load_family_taxonomy()
    items = list_deconstruction_records(
        family_id=family_id,
        renderable_v1=True if renderable_only else None,
        taxonomy=data,
        records=records,
    )
    priority_rank = {"gold": 0, "silver": 1, "archive": 2}
    items.sort(key=lambda item: (priority_rank.get(str(item.get("priority")), 9), item["reference_id"]))
    return items[: max(1, limit)]


def missing_reference_image_paths(*, taxonomy: dict[str, Any] | None = None) -> list[str]:
    """Return assignment paths that are no longer present on disk."""

    data = taxonomy or load_family_taxonomy()
    missing: list[str] = []
    for item in data.get("image_family_assignments", []):
        path = str(item.get("image_path") or "")
        if path and not Path(path).is_file():
            missing.append(path)
    return missing


def missing_deconstruction_image_paths(records: list[dict[str, Any]] | None = None) -> list[str]:
    """Return deconstruction image paths that are no longer present on disk."""

    missing: list[str] = []
    for item in records if records is not None else load_deconstruction_records():
        path = str(item.get("image_path") or "")
        if path and not Path(path).is_file():
            missing.append(path)
    return missing


def format_template_family_for_prompt(
    family_id: str,
    *,
    max_examples: int = 3,
    taxonomy: dict[str, Any] | None = None,
) -> str:
    """Format one family as compact prompt context for an LLM stage."""

    data = taxonomy or load_family_taxonomy()
    family = get_template_family(family_id, taxonomy=data)
    slot_model = family["slot_model"]
    host = slot_model["host"]
    payload = slot_model["primary_payload"]
    typography = slot_model["typography"]
    background = slot_model["background"]
    examples = best_reference_examples(family_id, limit=max_examples, taxonomy=data)
    example_lines = [
        f"- {item['reference_id']}: {item['title']} ({item['image_path']})"
        for item in examples
    ]

    lines = [
        f"family_id: {family['family_id']}",
        f"creator_style: {family['creator_style']}",
        f"label: {family['label']}",
        f"production_status: {family['production_status']}",
        f"best_for: {'; '.join(family['best_for'])}",
        f"avoid: {'; '.join(family['avoid'])}",
        f"host_placements: {', '.join(host['preferred_placements'])}",
        f"face_height_ratio: {host['face_height_ratio'][0]}-{host['face_height_ratio'][1]}",
        f"gaze_policy: {host['gaze_policy']}",
        f"required_poses: {', '.join(host['required_poses'])}",
        f"payload_types: {', '.join(payload['allowed_component_types'])}",
        f"payload_zones: {', '.join(payload['preferred_zones'])}",
        f"typography: {typography['headline_policy']}",
        f"background: {background['policy']}",
        f"template_breakers: {'; '.join(family['template_breakers'])}",
        "examples:",
        *example_lines,
    ]
    return "\n".join(lines)


def format_deconstruction_record_for_prompt(record: dict[str, Any]) -> str:
    """Format one concrete reference record as compact prompt context."""

    host = record["host"]
    component_lines = [
        (
            f"- {component['component_id']}: type={component['type']}; "
            f"role={component['role']}; box={component['box_pct']}; "
            f"text={' / '.join(component['text'])}"
        )
        for component in record["components"]
    ]
    lines = [
        f"reference_id: {record['reference_id']}",
        f"title: {record['title']}",
        f"creator: {record['creator']}",
        f"family: {record['template_family_candidate']}",
        f"priority: {record['priority']}",
        f"renderable_v1: {record['renderable_v1']}",
        f"image_path: {record['image_path']}",
        (
            "host: "
            f"present={host['present']}; placement={host['placement']}; "
            f"gaze={host['gaze']}; expression={host['expression']}; "
            f"face_height={host['face_height_ratio']}"
        ),
        "components:",
        *component_lines,
        f"template_breakers: {'; '.join(record['generator_constraints']['template_breakers'])}",
    ]
    return "\n".join(lines)


def family_ids(families: Iterable[dict[str, Any]]) -> set[str]:
    """Small test/helper utility for comparing family collections."""

    return {str(family["family_id"]) for family in families}
