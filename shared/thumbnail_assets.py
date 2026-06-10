"""Asset-need manifest for title-driven thumbnail workflows.

The brainstorm LLM may describe stock/Envato needs, but it must not download
anything. This module turns those free-form needs into a small auditable
manifest a future sourcing agent can fill in with provenance and license
evidence.
"""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any
from urllib.parse import quote, quote_plus
from zoneinfo import ZoneInfo

from shared.thumbnail_idea import ParsedIdea

ASSET_MANIFEST_SCHEMA_VERSION = "thumbnail_asset_manifest.v1"
ASSET_NEED_STATUS = "needed"
ASSET_NEED_CANDIDATE_STATUS = "candidate_found"
ASSET_NEED_LICENSED_STATUS = "licensed"
ASSET_NEED_REJECTED_STATUS = "rejected"
ASSET_NEED_STATUSES = (
    ASSET_NEED_STATUS,
    ASSET_NEED_CANDIDATE_STATUS,
    ASSET_NEED_LICENSED_STATUS,
    ASSET_NEED_REJECTED_STATUS,
)
ASSET_NEED_STATUS_LABELS = {
    ASSET_NEED_STATUS: "Needed",
    ASSET_NEED_CANDIDATE_STATUS: "Candidate found",
    ASSET_NEED_LICENSED_STATUS: "Licensed",
    ASSET_NEED_REJECTED_STATUS: "Rejected",
}
DEFAULT_PROVIDERS = ("envato_elements", "stock")
DEFAULT_CANDIDATE_LIMIT = 3


def build_thumbnail_asset_manifest(
    *,
    slug: str,
    ideas: list[ParsedIdea],
    generated_at: str | None = None,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create an auditable asset-need manifest from parsed thumbnail ideas."""

    ts = generated_at or datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds")
    items: list[dict[str, Any]] = []
    for idea_index, idea in enumerate(ideas):
        for need_index, query in enumerate(idea.asset_queries, start=1):
            cleaned = query.strip()
            if not cleaned:
                continue
            items.append(
                {
                    "asset_need_id": f"idea{idea_index + 1:02d}-asset{need_index:02d}",
                    "idea_index": idea_index,
                    "query": cleaned,
                    "need_type": classify_asset_need(cleaned),
                    "preferred_providers": list(DEFAULT_PROVIDERS),
                    "candidate_limit": DEFAULT_CANDIDATE_LIMIT,
                    "status": ASSET_NEED_STATUS,
                    "title_pairing": idea.title_pairing,
                    "recipe_id": idea.recipe_id,
                    "reference_template_id": idea.reference_template_id,
                    "component_type": idea.component_type,
                    "component_text": list(idea.component_text),
                    "provenance": empty_asset_provenance(),
                }
            )

    return {
        "schema_version": ASSET_MANIFEST_SCHEMA_VERSION,
        "slug": slug,
        "generated_at": ts,
        "source": source or {},
        "policy": {
            "download_allowed": False,
            "bulk_download_allowed": False,
            "requires_manual_license_registration": True,
            "notes": (
                "This manifest is search intent only. Fill provenance before "
                "using any asset in a committed thumbnail."
            ),
        },
        "items": items,
    }


def empty_asset_provenance() -> dict[str, str]:
    """Return the provenance fields a sourcing step must fill before use."""

    return {
        "provider": "",
        "asset_url": "",
        "provider_asset_id": "",
        "author": "",
        "license_name": "",
        "license_registration": "",
        "downloaded_at": "",
        "local_path": "",
        "evidence_path": "",
        "notes": "",
    }


def asset_search_urls(query: str, need_type: str = "unknown") -> list[dict[str, str]]:
    """Return bounded human/agent search links for one asset need.

    Links are intentionally search-only. The workflow records provenance after a
    licensed asset is selected; it does not automate downloads.
    """

    cleaned = " ".join(query.split()).strip()
    if not cleaned:
        return []

    encoded_path = quote_plus(cleaned)
    encoded_query = quote(cleaned)
    slug_query = quote(cleaned.replace(" ", "-"))
    links = [
        {
            "provider": "envato_elements",
            "label": "Envato Elements",
            "url": f"https://elements.envato.com/all-items/{encoded_path}",
        },
    ]

    if need_type in {"photo", "background", "object", "unknown"}:
        links.extend(
            [
                {
                    "provider": "stock",
                    "label": "Pexels",
                    "url": f"https://www.pexels.com/search/{encoded_query}/",
                },
                {
                    "provider": "stock",
                    "label": "Unsplash",
                    "url": f"https://unsplash.com/s/photos/{slug_query}",
                },
            ]
        )
    return links[:DEFAULT_CANDIDATE_LIMIT]


def is_asset_candidate_identified(provenance: dict[str, Any]) -> bool:
    """Return True when a concrete provider asset has been identified."""

    provider = str(provenance.get("provider") or "").strip()
    locator = str(provenance.get("asset_url") or provenance.get("provider_asset_id") or "").strip()
    return bool(provider and locator)


def is_asset_provenance_complete(provenance: dict[str, Any]) -> bool:
    """Return True when the asset is traceable enough to use in a render."""

    if not is_asset_candidate_identified(provenance):
        return False
    license_evidence = str(
        provenance.get("license_registration") or provenance.get("evidence_path") or ""
    ).strip()
    local_path = str(provenance.get("local_path") or "").strip()
    return bool(license_evidence and local_path)


def infer_asset_need_status(provenance: dict[str, Any]) -> str:
    """Infer the strongest safe status from provenance fields."""

    if is_asset_provenance_complete(provenance):
        return ASSET_NEED_LICENSED_STATUS
    if is_asset_candidate_identified(provenance):
        return ASSET_NEED_CANDIDATE_STATUS
    return ASSET_NEED_STATUS


def enrich_asset_manifest_for_ui(manifest: dict[str, Any] | None) -> dict[str, Any]:
    """Return a defensive copy decorated with UI-only sourcing helpers."""

    enriched = copy.deepcopy(manifest) if isinstance(manifest, dict) else {}
    enriched.setdefault("schema_version", ASSET_MANIFEST_SCHEMA_VERSION)
    enriched.setdefault("slug", "")
    enriched.setdefault("generated_at", "")
    enriched.setdefault("source", {})
    enriched.setdefault(
        "policy",
        {
            "download_allowed": False,
            "bulk_download_allowed": False,
            "requires_manual_license_registration": True,
            "notes": (
                "This manifest is search intent only. Fill provenance before "
                "using any asset in a committed thumbnail."
            ),
        },
    )

    items = enriched.get("items")
    if not isinstance(items, list):
        items = []
        enriched["items"] = items

    status_counts = {status: 0 for status in ASSET_NEED_STATUSES}
    for item in items:
        if not isinstance(item, dict):
            continue
        item.setdefault("asset_need_id", "")
        item.setdefault("query", "")
        item.setdefault("need_type", classify_asset_need(str(item.get("query") or "")))
        item.setdefault("preferred_providers", list(DEFAULT_PROVIDERS))
        item.setdefault("candidate_limit", DEFAULT_CANDIDATE_LIMIT)
        item["provenance"] = _normalized_provenance(item.get("provenance"))

        status = str(item.get("status") or "").strip()
        if status not in ASSET_NEED_STATUSES:
            status = infer_asset_need_status(item["provenance"])
        item["status"] = status
        item["status_label"] = ASSET_NEED_STATUS_LABELS[status]
        item["status_options"] = [
            {"value": value, "label": ASSET_NEED_STATUS_LABELS[value]}
            for value in ASSET_NEED_STATUSES
        ]
        item["search_urls"] = asset_search_urls(str(item["query"]), str(item["need_type"]))
        item["candidate_identified"] = is_asset_candidate_identified(item["provenance"])
        item["provenance_complete"] = is_asset_provenance_complete(item["provenance"])
        status_counts[status] += 1

    enriched["status_counts"] = status_counts
    enriched["items_count"] = len(items)
    return enriched


def update_asset_manifest_item(
    manifest: dict[str, Any],
    *,
    asset_need_id: str,
    provenance_patch: dict[str, Any],
    status: str | None = None,
) -> dict[str, Any]:
    """Return a copy of ``manifest`` with one asset need updated.

    ``candidate_found`` and ``licensed`` are guarded so Bridge cannot mark an
    asset usable without enough provenance.
    """

    updated = copy.deepcopy(manifest)
    items = updated.get("items")
    if not isinstance(items, list):
        raise KeyError(asset_need_id)

    target = None
    for item in items:
        if isinstance(item, dict) and item.get("asset_need_id") == asset_need_id:
            target = item
            break
    if target is None:
        raise KeyError(asset_need_id)

    provenance = _normalized_provenance(target.get("provenance"))
    allowed_fields = set(empty_asset_provenance())
    for key, value in provenance_patch.items():
        if key in allowed_fields:
            provenance[key] = str(value or "").strip()
    target["provenance"] = provenance

    requested_status = str(status or "").strip()
    if requested_status:
        if requested_status not in ASSET_NEED_STATUSES:
            raise ValueError(f"unknown asset status: {requested_status}")
        if requested_status == ASSET_NEED_CANDIDATE_STATUS and not is_asset_candidate_identified(
            provenance
        ):
            raise ValueError("candidate_found requires provider and asset URL or provider asset id")
        if requested_status == ASSET_NEED_LICENSED_STATUS and not is_asset_provenance_complete(
            provenance
        ):
            raise ValueError(
                "licensed requires provider, asset URL or id, license evidence, and local path"
            )
        target["status"] = requested_status
    else:
        target["status"] = infer_asset_need_status(provenance)

    return updated


def merge_existing_asset_provenance(
    new_manifest: dict[str, Any],
    existing_manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    """Carry provenance/status forward for unchanged asset needs."""

    merged = copy.deepcopy(new_manifest)
    old_items = existing_manifest.get("items") if isinstance(existing_manifest, dict) else None
    new_items = merged.get("items")
    if not isinstance(old_items, list) or not isinstance(new_items, list):
        return merged

    by_id_query: dict[tuple[str, str], dict[str, Any]] = {}
    by_idea_query: dict[tuple[int, str], dict[str, Any]] = {}
    for old in old_items:
        if not isinstance(old, dict):
            continue
        query_key = _query_key(old.get("query"))
        if not query_key:
            continue
        asset_need_id = str(old.get("asset_need_id") or "")
        if asset_need_id:
            by_id_query[(asset_need_id, query_key)] = old
        idea_index = old.get("idea_index")
        if isinstance(idea_index, int):
            by_idea_query[(idea_index, query_key)] = old

    for new in new_items:
        if not isinstance(new, dict):
            continue
        query_key = _query_key(new.get("query"))
        asset_need_id = str(new.get("asset_need_id") or "")
        old = by_id_query.get((asset_need_id, query_key))
        idea_index = new.get("idea_index")
        if old is None and isinstance(idea_index, int):
            old = by_idea_query.get((idea_index, query_key))
        if old is None:
            continue
        new["provenance"] = _normalized_provenance(old.get("provenance"))
        old_status = str(old.get("status") or "").strip()
        if old_status in ASSET_NEED_STATUSES:
            new["status"] = old_status
        else:
            new["status"] = infer_asset_need_status(new["provenance"])

    return merged


def classify_asset_need(query: str) -> str:
    """Best-effort type classification for a free-form asset search query."""

    q = query.lower()
    if any(token in q for token in ("icon", "icons", "vector", "svg", "symbol")):
        return "icon"
    if any(token in q for token in ("ui", "dashboard", "app", "mockup", "panel", "card")):
        return "ui"
    if any(token in q for token in ("texture", "paper", "grain", "highlighter")):
        return "texture"
    if any(token in q for token in ("background", "backdrop", "gradient")):
        return "background"
    if any(token in q for token in ("cutout", "jar", "scoop", "bottle", "object", "supplement")):
        return "object"
    if any(token in q for token in ("photo", "lifestyle", "stock", "desk", "table", "adult")):
        return "photo"
    return "unknown"


def _normalized_provenance(value: object) -> dict[str, str]:
    provenance = empty_asset_provenance()
    if isinstance(value, dict):
        for key in provenance:
            provenance[key] = str(value.get(key) or "").strip()
    return provenance


def _query_key(value: object) -> str:
    return " ".join(str(value or "").lower().split())
