from __future__ import annotations

from shared.thumbnail_assets import (
    ASSET_MANIFEST_SCHEMA_VERSION,
    asset_search_urls,
    build_thumbnail_asset_manifest,
    classify_asset_need,
    enrich_asset_manifest_for_ui,
    is_asset_provenance_complete,
    merge_existing_asset_provenance,
    update_asset_manifest_item,
)
from shared.thumbnail_idea import ParsedIdea


def _idea(*, asset_queries: tuple[str, ...], recipe_id: str = "jeff_clean_tutorial_dual_zone"):
    return ParsedIdea(
        hook="80% 重點",
        emotion_key="explaining",
        emotion_input="解釋",
        visual="修修指向右側三步驟面板",
        decoration="80%",
        bg="白色教學 UI 面板",
        archetype_tags=("T-A2", "T-V1"),
        lane="Jeff Clean Tutorial",
        recipe_id=recipe_id,
        title_pairing="13 分鐘學會 80% 的長壽飲食法則",
        asset_queries=asset_queries,
        viewer_promise="快速抓到重點",
        evidence_fit="影片有研究與步驟支撐",
        trust_risk="避免醫療承諾",
    )


def test_build_thumbnail_asset_manifest_is_search_intent_only():
    manifest = build_thumbnail_asset_manifest(
        slug="longevity-test",
        ideas=[
            _idea(asset_queries=("minimal timer icon", "clean dashboard UI kit")),
            _idea(asset_queries=("warm desk stock photo",), recipe_id="ali_warm_evidence_list"),
        ],
        generated_at="2026-05-28T12:00:00+08:00",
        source={"kind": "test"},
    )

    assert manifest["schema_version"] == ASSET_MANIFEST_SCHEMA_VERSION
    assert manifest["policy"]["download_allowed"] is False
    assert manifest["policy"]["bulk_download_allowed"] is False
    assert manifest["policy"]["requires_manual_license_registration"] is True
    assert len(manifest["items"]) == 3

    first = manifest["items"][0]
    assert first["asset_need_id"] == "idea01-asset01"
    assert first["query"] == "minimal timer icon"
    assert first["need_type"] == "icon"
    assert first["preferred_providers"] == ["envato_elements", "stock"]
    assert first["candidate_limit"] == 3
    assert first["status"] == "needed"
    assert first["provenance"]["provider"] == ""
    assert first["provenance"]["license_registration"] == ""
    assert first["provenance"]["local_path"] == ""


def test_classify_asset_need_common_thumbnail_needs():
    assert classify_asset_need("minimal vector icon pack") == "icon"
    assert classify_asset_need("clean dashboard UI kit") == "ui"
    assert classify_asset_need("paper/highlighter texture") == "texture"
    assert classify_asset_need("dark green metric background") == "background"
    assert classify_asset_need("supplement jar cutout") == "object"
    assert classify_asset_need("warm desk stock photo") == "photo"
    assert classify_asset_need("something vague") == "unknown"


def test_asset_search_urls_are_bounded_and_search_only():
    links = asset_search_urls("creatine jar cutout", "object")

    assert len(links) == 3
    assert links[0]["label"] == "Envato Elements"
    assert "creatine+jar+cutout" in links[0]["url"]
    assert all(link["url"].startswith("https://") for link in links)


def test_enrich_asset_manifest_adds_ui_state_without_mutating_source():
    manifest = build_thumbnail_asset_manifest(
        slug="longevity-test",
        ideas=[_idea(asset_queries=("minimal timer icon",))],
        generated_at="2026-05-28T12:00:00+08:00",
    )

    enriched = enrich_asset_manifest_for_ui(manifest)

    assert enriched["items_count"] == 1
    assert enriched["status_counts"]["needed"] == 1
    item = enriched["items"][0]
    assert item["status_label"] == "Needed"
    assert item["search_urls"][0]["label"] == "Envato Elements"
    assert "search_urls" not in manifest["items"][0]


def test_update_asset_manifest_item_records_candidate_and_license_progression():
    manifest = build_thumbnail_asset_manifest(
        slug="longevity-test",
        ideas=[_idea(asset_queries=("minimal timer icon",))],
        generated_at="2026-05-28T12:00:00+08:00",
    )

    candidate = update_asset_manifest_item(
        manifest,
        asset_need_id="idea01-asset01",
        status="candidate_found",
        provenance_patch={
            "provider": "envato_elements",
            "asset_url": "https://elements.envato.com/minimal-timer",
            "notes": "good fit",
        },
    )

    item = candidate["items"][0]
    assert item["status"] == "candidate_found"
    assert item["provenance"]["provider"] == "envato_elements"
    assert item["provenance"]["notes"] == "good fit"
    assert manifest["items"][0]["status"] == "needed"

    licensed = update_asset_manifest_item(
        candidate,
        asset_need_id="idea01-asset01",
        status="licensed",
        provenance_patch={
            "license_registration": "Project Use: longevity-test",
            "local_path": "Attachments/assets/timer.png",
        },
    )

    assert licensed["items"][0]["status"] == "licensed"
    assert is_asset_provenance_complete(licensed["items"][0]["provenance"])


def test_update_asset_manifest_item_rejects_false_licensed_status():
    manifest = build_thumbnail_asset_manifest(
        slug="longevity-test",
        ideas=[_idea(asset_queries=("minimal timer icon",))],
        generated_at="2026-05-28T12:00:00+08:00",
    )

    try:
        update_asset_manifest_item(
            manifest,
            asset_need_id="idea01-asset01",
            status="licensed",
            provenance_patch={
                "provider": "envato_elements",
                "asset_url": "https://elements.envato.com/minimal-timer",
            },
        )
    except ValueError as exc:
        assert "licensed requires" in str(exc)
    else:  # pragma: no cover - explicit failure branch for readability
        raise AssertionError("licensed status should require license evidence and local path")


def test_merge_existing_asset_provenance_preserves_unchanged_queries():
    old_manifest = build_thumbnail_asset_manifest(
        slug="longevity-test",
        ideas=[_idea(asset_queries=("minimal timer icon", "paper texture"))],
        generated_at="2026-05-28T12:00:00+08:00",
    )
    old_manifest = update_asset_manifest_item(
        old_manifest,
        asset_need_id="idea01-asset01",
        status="candidate_found",
        provenance_patch={
            "provider": "envato_elements",
            "asset_url": "https://elements.envato.com/minimal-timer",
        },
    )
    new_manifest = build_thumbnail_asset_manifest(
        slug="longevity-test",
        ideas=[_idea(asset_queries=("minimal timer icon", "new chart icon"))],
        generated_at="2026-05-28T12:10:00+08:00",
    )

    merged = merge_existing_asset_provenance(new_manifest, old_manifest)

    assert merged["items"][0]["status"] == "candidate_found"
    assert merged["items"][0]["provenance"]["asset_url"].endswith("minimal-timer")
    assert merged["items"][1]["query"] == "new chart icon"
    assert merged["items"][1]["status"] == "needed"
