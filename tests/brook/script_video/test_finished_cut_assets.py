from __future__ import annotations

from dataclasses import fields

import pytest

from agents.brook.script_video.finished_cut_production._assets import (
    AssetContractError,
    AssetKind,
    AssetRecord,
    ContentAddressedAssetResolver,
    InMemoryAssetResolver,
)


def _digest(seed: str) -> str:
    return seed.encode("utf-8").hex().ljust(64, "0")[:64]


def _neutral_asset(
    seed: str,
    *,
    kind: AssetKind = AssetKind.STOCK,
    extension: str = ".mp4",
    release_ids: frozenset[str] = frozenset(),
) -> AssetRecord:
    return AssetRecord(
        digest=_digest(seed),
        extension=extension,
        kind=kind,
        visual_summary=f"Neutral acquisition view of {seed}",
        width=1920,
        height=1080,
        duration_sec=None if kind is AssetKind.PHOTO else 12.0,
        release_ids=release_ids,
    )


def test_fresh_dp_catalog_exposes_a_neutral_stock_asset() -> None:
    stock = _neutral_asset("stock")
    resolver = InMemoryAssetResolver([stock])

    assert resolver.worker_selection_catalog().items() == (
        resolver.worker_selection_catalog().item(stock.reference),
    )


def test_fresh_dp_catalog_exposes_photos_and_non_editorial_clips() -> None:
    records = (
        _neutral_asset("photo", kind=AssetKind.PHOTO, extension=".jpg"),
        _neutral_asset("clip", kind=AssetKind.NON_EDITORIAL_CLIP),
    )
    catalog = InMemoryAssetResolver(records).worker_selection_catalog()

    assert tuple(item.reference for item in catalog.items()) == tuple(
        record.reference for record in records
    )


def test_semantic_render_is_rebuildable_but_hidden_from_fresh_dp() -> None:
    title = AssetRecord(
        digest=_digest("title"),
        extension=".mov",
        kind=AssetKind.TITLE_RENDER,
        recipe_identity="recipe:title:current-v1",
        release_ids=frozenset({"release-1"}),
    )
    resolver = InMemoryAssetResolver([title])
    catalog = resolver.worker_selection_catalog()

    assert catalog.items() == ()
    with pytest.raises(AssetContractError, match="worker catalog"):
        catalog.item(title.reference)
    assert resolver.resolve_for_release("release-1", title.reference).record == title
    with pytest.raises(AssetContractError, match="not bound to this Release"):
        resolver.resolve_for_release("release-2", title.reference)


@pytest.mark.parametrize(
    "kind",
    [
        AssetKind.CHAPTER_RENDER,
        AssetKind.CONCEPT_RENDER,
        AssetKind.COMPOSITE,
    ],
)
def test_every_semantic_render_kind_is_release_only(kind: AssetKind) -> None:
    record = AssetRecord(
        digest=_digest(kind.value),
        extension=".mov",
        kind=kind,
        recipe_identity=f"recipe:{kind.value}:v1",
        release_ids=frozenset({"release-1"}),
    )
    resolver = InMemoryAssetResolver([record])

    assert resolver.worker_selection_catalog().items() == ()
    assert resolver.resolve_for_release("release-1", record.reference).record == record


@pytest.mark.parametrize(
    "raw_reference",
    [
        r"G:\Footages\episode\stock.mp4",
        "/absolute/stock.mp4",
        "highlights/visual-pipeline/value-L02/stock.mp4",
        "../sibling-route/stock.mp4",
    ],
)
def test_dp_reference_must_be_an_opaque_catalog_reference(raw_reference: str) -> None:
    stock = _neutral_asset("allowed-stock")
    catalog = InMemoryAssetResolver([stock]).worker_selection_catalog()

    assert catalog.resolve_dp_reference(stock.reference).reference == stock.reference
    with pytest.raises(AssetContractError, match="worker catalog"):
        catalog.resolve_dp_reference(raw_reference)


def test_worker_item_exposes_only_neutral_acquisition_metadata() -> None:
    stock = AssetRecord(
        digest=_digest("metadata-free-stock"),
        extension=".mp4",
        kind=AssetKind.STOCK,
        visual_summary="Busy parent working at a kitchen table",
        width=1920,
        height=1080,
        duration_sec=12.5,
    )

    item = InMemoryAssetResolver([stock]).worker_selection_catalog().items()[0]

    assert (item.visual_summary, item.width > item.height, item.duration_sec) == (
        "Busy parent working at a kitchen table",
        True,
        12.5,
    )
    assert {field.name for field in fields(item)} == {
        "reference",
        "kind",
        "visual_summary",
        "width",
        "height",
        "duration_sec",
    }


def test_worker_selectable_asset_requires_usable_acquisition_metadata() -> None:
    with pytest.raises(AssetContractError, match="acquisition metadata"):
        AssetRecord(
            digest=_digest("missing-acquisition-metadata"),
            extension=".mp4",
            kind=AssetKind.STOCK,
        )


def test_core_reuses_semantic_bytes_only_for_the_exact_current_recipe() -> None:
    previous = AssetRecord(
        digest=_digest("previous-title"),
        extension=".mov",
        kind=AssetKind.TITLE_RENDER,
        recipe_identity="recipe:title:v1",
    )
    current = AssetRecord(
        digest=_digest("current-title"),
        extension=".mov",
        kind=AssetKind.TITLE_RENDER,
        recipe_identity="recipe:title:v2",
    )
    resolver = InMemoryAssetResolver([previous, current])

    assert resolver.resolve_exact_recipe("recipe:title:v2").record == current
    with pytest.raises(AssetContractError, match="exact recipe identity"):
        resolver.resolve_exact_recipe("recipe:title")


def test_filesystem_resolver_returns_the_content_addressed_object(tmp_path) -> None:
    record = _neutral_asset("content-addressed-stock", release_ids=frozenset({"release-1"}))
    object_path = (
        tmp_path / "assets-v2" / "sha256" / record.digest[:2] / f"{record.digest}{record.extension}"
    )
    object_path.parent.mkdir(parents=True)
    object_path.write_bytes(b"media")
    resolver = ContentAddressedAssetResolver(tmp_path / "assets-v2", [record])

    resolution = resolver.resolve_for_release("release-1", record.reference)

    assert resolution.record == record
    assert resolution.path == object_path
    assert resolver.resolve_worker_asset(record.reference).path == object_path


def test_filesystem_resolver_never_exposes_a_missing_object(tmp_path) -> None:
    record = _neutral_asset("missing-stock")
    resolver = ContentAddressedAssetResolver(tmp_path / "assets-v2", [record])

    with pytest.raises(AssetContractError, match="missing or outside"):
        resolver.worker_selection_catalog()


def test_filesystem_resolver_reuses_exact_recipe_without_catalog_exposure(tmp_path) -> None:
    record = AssetRecord(
        digest=_digest("rendered-current-recipe"),
        extension=".mov",
        kind=AssetKind.CONCEPT_RENDER,
        recipe_identity="recipe:concept:current",
    )
    object_path = tmp_path / "assets-v2" / "sha256" / record.digest[:2] / f"{record.digest}.mov"
    object_path.parent.mkdir(parents=True)
    object_path.write_bytes(b"semantic render")
    resolver = ContentAddressedAssetResolver(tmp_path / "assets-v2", [record])

    assert resolver.worker_selection_catalog().items() == ()
    assert resolver.resolve_exact_recipe("recipe:concept:current").path == object_path


def test_recipe_bound_media_cannot_be_registered_as_neutral() -> None:
    with pytest.raises(AssetContractError, match="neutral acquisition"):
        AssetRecord(
            digest=_digest("forged-neutral-title"),
            extension=".mov",
            kind=AssetKind.STOCK,
            recipe_identity="recipe:title:prior-release",
        )


def test_same_bytes_cannot_be_reclassified_to_launder_a_semantic_render() -> None:
    digest = _digest("same-title-bytes")
    semantic = AssetRecord(
        digest=digest,
        extension=".mov",
        kind=AssetKind.TITLE_RENDER,
        recipe_identity="recipe:title:prior-release",
    )
    forged_neutral = AssetRecord(
        digest=digest,
        extension=".mov",
        kind=AssetKind.STOCK,
        visual_summary="Forged neutral classification",
        width=1920,
        height=1080,
        duration_sec=12.0,
    )

    with pytest.raises(AssetContractError, match="duplicate asset digest"):
        InMemoryAssetResolver([semantic, forged_neutral])


def test_exact_recipe_reuse_rejects_ambiguous_outputs() -> None:
    records = [
        AssetRecord(
            digest=_digest(f"title-output-{version}"),
            extension=".mov",
            kind=AssetKind.TITLE_RENDER,
            recipe_identity="recipe:title:current",
        )
        for version in (1, 2)
    ]

    with pytest.raises(AssetContractError, match="duplicate recipe identity"):
        InMemoryAssetResolver(records)


def test_core_resolves_only_worker_catalog_assets_before_release_sealing() -> None:
    stock = _neutral_asset("fresh-worker-stock")
    old_title = AssetRecord(
        digest=_digest("old-title"),
        extension=".mov",
        kind=AssetKind.TITLE_RENDER,
        recipe_identity="recipe:title:old",
    )
    resolver = InMemoryAssetResolver([stock, old_title])

    assert resolver.resolve_worker_asset(stock.reference).record == stock
    with pytest.raises(AssetContractError, match="worker catalog"):
        resolver.resolve_worker_asset(old_title.reference)


def test_active_asset_resolution_rejects_a_well_formed_unpublished_reference() -> None:
    resolver = InMemoryAssetResolver((_neutral_asset("published-stock"),))

    with pytest.raises(AssetContractError, match="active store"):
        resolver.resolve_active_asset("asset-sha256:" + "f" * 64)
