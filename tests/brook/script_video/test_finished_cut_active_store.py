from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from agents.brook.script_video.finished_cut_production._active_store import (
    ActiveAssetPublication,
    ActiveAssetStore,
    ActiveAssetStoreError,
)
from agents.brook.script_video.finished_cut_production._assets import (
    AssetContractError,
    AssetKind,
    CompactAssetReceipt,
)


def _neutral_receipt(content: bytes) -> CompactAssetReceipt:
    return CompactAssetReceipt(
        origin="neutral_acquisition",
        media_sha256=hashlib.sha256(content).hexdigest(),
        media_bytes=len(content),
        source_class="licensed_stock",
        provider="pexels",
        provider_item_id="7106572",
        source_url="https://www.pexels.com/video/7106572/",
        license="Pexels license: https://www.pexels.com/license/",
        acquired_at="2026-08-26T01:00:00Z",
        forensic_receipt_ref="forensic-sha256:" + "f" * 64,
    )


@pytest.mark.parametrize(
    ("field", "forged_value"),
    [
        ("provider", "unknown-stock-provider"),
        ("source_url", "https://www.pexels.com/video/9999999/"),
        ("license", "trust me"),
    ],
)
def test_compact_receipt_rejects_a_forged_licensed_source_profile(
    field: str,
    forged_value: str,
) -> None:
    receipt = _neutral_receipt(b"native horizontal stock")

    with pytest.raises(AssetContractError, match="source profile"):
        replace(receipt, **{field: forged_value})


def test_compact_receipt_rejects_an_old_semantic_class_as_acquisition_origin() -> None:
    receipt = _neutral_receipt(b"native horizontal stock")

    with pytest.raises(AssetContractError, match="source class"):
        replace(receipt, source_class="title_render")


def test_compact_receipt_rejects_an_impossible_acquisition_timestamp() -> None:
    receipt = _neutral_receipt(b"native horizontal stock")

    with pytest.raises(AssetContractError, match="acquisition time"):
        replace(receipt, acquired_at="2026-99-99T99:99:99Z")


def test_generated_asset_gets_a_current_generated_compact_receipt_on_reopen(
    tmp_path: Path,
) -> None:
    source = tmp_path / "hero.mov"
    content = b"current generated hero"
    source.write_bytes(content)
    store_root = tmp_path / "assets-v2"

    published = ActiveAssetStore.open(store_root, episode_id="episode-001").publish(
        ActiveAssetPublication(
            source_path=source,
            kind=AssetKind.TITLE_RENDER,
            recipe_identity="recipe:hero:current",
        )
    )
    reopened = ActiveAssetStore.open(store_root, episode_id="episode-001")

    assert reopened.resolve_active_asset(published.record.reference).record.compact_receipt == (
        CompactAssetReceipt(
            origin="current_generated",
            media_sha256=published.record.digest,
            media_bytes=len(content),
        )
    )


def test_byte_identical_publish_is_idempotent_across_store_reopen(tmp_path: Path) -> None:
    source = tmp_path / "hero.webm"
    source.write_bytes(b"deterministic hero media")
    store_root = tmp_path / "assets-v2"
    publication = ActiveAssetPublication(
        source_path=source,
        kind=AssetKind.TITLE_RENDER,
        recipe_identity="recipe:hero:current",
    )

    first = ActiveAssetStore.open(store_root, episode_id="episode-001").publish(publication)
    second = ActiveAssetStore.open(store_root, episode_id="episode-001").publish(publication)
    reopened = ActiveAssetStore.open(store_root, episode_id="episode-001")

    assert second == first
    assert reopened.resolve_exact_recipe("recipe:hero:current") == first
    assert first.path is not None
    assert first.path.read_bytes() == b"deterministic hero media"

    reopened.bind_release(first.record.reference, release_id="release-001")
    after_release_bind = ActiveAssetStore.open(
        store_root,
        episode_id="episode-001",
    ).publish(publication)
    assert after_release_bind.record.release_ids == frozenset({"release-001"})


def test_semantic_asset_is_hidden_from_fresh_dp_but_rebuilds_bound_release(
    tmp_path: Path,
) -> None:
    source = tmp_path / "chapter.mp4"
    source.write_bytes(b"chapter media")
    store_root = tmp_path / "assets-v2"
    store = ActiveAssetStore.open(store_root, episode_id="episode-001")
    published = store.publish(
        ActiveAssetPublication(
            source_path=source,
            kind=AssetKind.CHAPTER_RENDER,
            recipe_identity="recipe:chapter:current",
        )
    )

    assert store.worker_selection_catalog().items() == ()
    with pytest.raises(ActiveAssetStoreError, match="Release"):
        store.resolve_for_release("release-001", published.record.reference)

    store.bind_release(published.record.reference, release_id="release-001")
    reopened = ActiveAssetStore.open(store_root, episode_id="episode-001")

    assert reopened.resolve_for_release("release-001", published.record.reference).path == (
        published.path
    )
    assert reopened.worker_selection_catalog().items() == ()


def test_neutral_acquisition_metadata_survives_index_reopen(tmp_path: Path) -> None:
    source = tmp_path / "work-pressure.mp4"
    content = b"native horizontal stock"
    source.write_bytes(content)
    store_root = tmp_path / "assets-v2"
    published = ActiveAssetStore.open(store_root, episode_id="episode-001").publish(
        ActiveAssetPublication(
            source_path=source,
            kind=AssetKind.STOCK,
            visual_summary="焦頭爛額處理工作與家庭責任的橫式實拍",
            width=1920,
            height=1080,
            duration_sec=8.4,
            compact_receipt=_neutral_receipt(content),
        )
    )

    reopened = ActiveAssetStore.open(store_root, episode_id="episode-001")
    catalog_item = reopened.worker_selection_catalog().item(published.record.reference)

    assert catalog_item.visual_summary == "焦頭爛額處理工作與家庭責任的橫式實拍"
    assert catalog_item.width == 1920
    assert catalog_item.height == 1080
    assert catalog_item.duration_sec == 8.4
    assert reopened.resolve_worker_asset(catalog_item.reference) == published
    assert reopened.resolve_active_asset(published.record.reference).record.compact_receipt == (
        _neutral_receipt(content)
    )


@pytest.mark.parametrize("receipt_state", ["missing", "wrong_digest", "wrong_bytes"])
def test_neutral_publication_rejects_unproven_content_before_store_mutation(
    tmp_path: Path,
    receipt_state: str,
) -> None:
    content = b"native horizontal stock"
    source = tmp_path / "work-pressure.mp4"
    source.write_bytes(content)
    receipt = _neutral_receipt(content)
    if receipt_state == "missing":
        receipt = None
    elif receipt_state == "wrong_digest":
        receipt = replace(receipt, media_sha256="0" * 64)
    else:
        receipt = replace(receipt, media_bytes=len(content) + 1)
    store_root = tmp_path / "assets-v2"

    with pytest.raises(ActiveAssetStoreError, match="provenance"):
        ActiveAssetStore.open(store_root, episode_id="episode-001").publish(
            ActiveAssetPublication(
                source_path=source,
                kind=AssetKind.STOCK,
                visual_summary="焦頭爛額處理工作與家庭責任的橫式實拍",
                width=1920,
                height=1080,
                duration_sec=8.4,
                compact_receipt=receipt,
            )
        )

    assert not store_root.exists()


def test_same_content_with_conflicting_compact_provenance_is_rejected(
    tmp_path: Path,
) -> None:
    content = b"native horizontal stock"
    source = tmp_path / "work-pressure.mp4"
    source.write_bytes(content)
    store_root = tmp_path / "assets-v2"
    store = ActiveAssetStore.open(store_root, episode_id="episode-001")
    publication = ActiveAssetPublication(
        source_path=source,
        kind=AssetKind.STOCK,
        visual_summary="焦頭爛額處理工作與家庭責任的橫式實拍",
        width=1920,
        height=1080,
        duration_sec=8.4,
        compact_receipt=_neutral_receipt(content),
    )
    store.publish(publication)
    index_before = (store_root / "index.v1.json").read_bytes()
    conflicting_receipt = replace(
        _neutral_receipt(content),
        provider_item_id="9999999",
        source_url="https://www.pexels.com/video/9999999/",
    )

    with pytest.raises(ActiveAssetStoreError, match="conflicting asset metadata"):
        store.publish(replace(publication, compact_receipt=conflicting_receipt))

    assert (store_root / "index.v1.json").read_bytes() == index_before


def test_conflicting_bytes_cannot_replace_an_existing_recipe(tmp_path: Path) -> None:
    first_path = tmp_path / "hero-a.webm"
    first_path.write_bytes(b"hero version a")
    second_path = tmp_path / "hero-b.webm"
    second_path.write_bytes(b"hero version b")
    store = ActiveAssetStore.open(tmp_path / "assets-v2", episode_id="episode-001")
    store.publish(
        ActiveAssetPublication(
            source_path=first_path,
            kind=AssetKind.TITLE_RENDER,
            recipe_identity="recipe:hero:current",
        )
    )

    with pytest.raises(ActiveAssetStoreError, match="recipe identity"):
        store.publish(
            ActiveAssetPublication(
                source_path=second_path,
                kind=AssetKind.TITLE_RENDER,
                recipe_identity="recipe:hero:current",
            )
        )

    assert store.resolve_exact_recipe("recipe:hero:current").path is not None


def test_corrupt_content_or_index_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "hero.webm"
    source.write_bytes(b"valid media")
    store_root = tmp_path / "assets-v2"
    store = ActiveAssetStore.open(store_root, episode_id="episode-001")
    published = store.publish(
        ActiveAssetPublication(
            source_path=source,
            kind=AssetKind.TITLE_RENDER,
            recipe_identity="recipe:hero:current",
        )
    )
    assert published.path is not None
    published.path.write_bytes(b"corrupt media")

    with pytest.raises(ActiveAssetStoreError, match="corrupt"):
        store.resolve_active_asset(published.record.reference)

    (store_root / "index.v1.json").write_bytes(b'{"schema":"tampered"}')
    with pytest.raises(ActiveAssetStoreError, match="schema"):
        ActiveAssetStore.open(store_root, episode_id="episode-001")


def test_reopen_fails_closed_when_an_index_staging_write_remains(tmp_path: Path) -> None:
    source = tmp_path / "hero.webm"
    source.write_bytes(b"valid media")
    store_root = tmp_path / "assets-v2"
    ActiveAssetStore.open(store_root, episode_id="episode-001").publish(
        ActiveAssetPublication(
            source_path=source,
            kind=AssetKind.TITLE_RENDER,
            recipe_identity="recipe:hero:current",
        )
    )
    (store_root / ".index.v1.json.staging").write_bytes(b"partial replacement")

    with pytest.raises(ActiveAssetStoreError, match="incomplete"):
        ActiveAssetStore.open(store_root, episode_id="episode-001")


def test_reopen_rejects_a_path_field_even_with_a_recomputed_index_checksum(
    tmp_path: Path,
) -> None:
    source = tmp_path / "hero.webm"
    source.write_bytes(b"valid media")
    store_root = tmp_path / "assets-v2"
    ActiveAssetStore.open(store_root, episode_id="episode-001").publish(
        ActiveAssetPublication(
            source_path=source,
            kind=AssetKind.TITLE_RENDER,
            recipe_identity="recipe:hero:current",
        )
    )
    index_path = store_root / "index.v1.json"
    envelope = json.loads(index_path.read_text(encoding="utf-8"))
    payload = envelope["payload"]
    payload["records"][0]["source_path"] = "G:/episode/highlights/visual-pipeline/old.webm"
    encoded_payload = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    envelope["payload_sha256"] = hashlib.sha256(encoded_payload).hexdigest()
    index_path.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ActiveAssetStoreError, match="record fields"):
        ActiveAssetStore.open(store_root, episode_id="episode-001")
