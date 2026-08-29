from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, replace
from pathlib import Path
from typing import Callable

import pytest

from agents.brook.script_video.finished_cut_production._active_store import ActiveAssetStore
from agents.brook.script_video.finished_cut_production._assets import AssetKind
from agents.brook.script_video.finished_cut_production._neutral_asset_import import (
    FfprobeNeutralMediaInspector,
    LegacyNeutralAssetImporter,
    NeutralAssetImport,
    NeutralAssetImportError,
    NeutralMediaProbe,
    NeutralProbeProcessResult,
    SubprocessNeutralProbeProcessRunner,
)


def _canonical_hash(value: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _write_legacy_receipt(
    root: Path,
    media: Path,
    *,
    episode_id: str = "episode-001",
) -> tuple[Path, str]:
    content = media.read_bytes()
    receipt: dict[str, object] = {
        "contract": "podcast-highlight-asset-acquisition-receipt-v1",
        "episode_id": episode_id,
        "cut_id": "value-L02",
        "revision_id": "revision-retired-001",
        "attempt": 1,
        "asset_id": "stock-work-pressure",
        "source_class": "licensed_stock",
        "provider": "pexels",
        "provider_item_id": "7106572",
        "source_url": "https://www.pexels.com/video/7106572/",
        "license": "Pexels license: https://www.pexels.com/license/",
        "acquired_at": "2026-08-26T01:00:00Z",
        "original_media": {
            "path": "highlights/visual-pipeline/value-L02/jobs/old/source.mp4",
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        },
    }
    receipt["content_hash"] = _canonical_hash(receipt)
    receipt_path = root / "explicit-old-acquisition-receipt.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
    forensic_ref = "forensic-sha256:" + hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    return receipt_path, forensic_ref


def _rewrite_receipt(
    receipt_path: Path,
    mutate: Callable[[dict[str, object]], None],
) -> str:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    mutate(receipt)
    receipt.pop("content_hash", None)
    receipt["content_hash"] = _canonical_hash(receipt)
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
    return "forensic-sha256:" + hashlib.sha256(receipt_path.read_bytes()).hexdigest()


def _import_request(
    receipt_path: Path,
    media: Path,
    forensic_ref: str,
    **changes,
) -> NeutralAssetImport:
    request = NeutralAssetImport(
        receipt_path=receipt_path,
        media_path=media,
        forensic_receipt_ref=forensic_ref,
        kind=AssetKind.STOCK,
        visual_summary="焦頭爛額處理工作與家庭責任的橫式實拍",
        width=1920,
        height=1080,
        duration_sec=8.4,
    )
    return replace(request, **changes)


class _Inspector:
    def __init__(self, probe: NeutralMediaProbe) -> None:
        self._probe = probe
        self.paths: list[Path] = []

    def inspect(self, path: Path) -> NeutralMediaProbe:
        self.paths.append(path)
        return self._probe


class _ProbeRunner:
    def __init__(self, result: NeutralProbeProcessResult) -> None:
        self._result = result
        self.calls: list[tuple[tuple[str, ...], float]] = []

    def run(
        self,
        argv: tuple[str, ...],
        *,
        timeout_sec: float,
    ) -> NeutralProbeProcessResult:
        self.calls.append((argv, timeout_sec))
        return self._result


def test_production_probe_adapter_reads_only_the_explicit_media_path(tmp_path: Path) -> None:
    media = tmp_path / "explicit-acquisition.mp4"
    media.write_bytes(b"opaque media")
    runner = _ProbeRunner(
        NeutralProbeProcessResult(
            returncode=0,
            stdout=json.dumps(
                {
                    "streams": [{"width": 1920, "height": 1080}],
                    "format": {"duration": "8.4"},
                }
            ),
        )
    )

    probe = FfprobeNeutralMediaInspector(runner=runner).inspect(media)

    assert probe == NeutralMediaProbe(width=1920, height=1080, duration_sec=8.4)
    assert len(runner.calls) == 1
    argv, timeout_sec = runner.calls[0]
    assert argv[0] == "ffprobe"
    assert argv[-1] == str(media.resolve())
    assert "visual-pipeline" not in " ".join(argv)
    assert timeout_sec > 0


def test_production_probe_process_is_shell_free_and_output_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def _run(argv, **kwargs):
        observed["argv"] = argv
        observed.update(kwargs)
        return NeutralProbeProcessResult(returncode=0, stdout="x" * 100_000)

    monkeypatch.setattr(
        "agents.brook.script_video.finished_cut_production._neutral_asset_import.subprocess.run",
        _run,
    )

    result = SubprocessNeutralProbeProcessRunner().run(
        ("ffprobe", "explicit.mp4"),
        timeout_sec=7.5,
    )

    assert result.returncode == 0
    assert len(result.stdout) == 65_536
    assert observed["shell"] is False
    assert observed["timeout"] == 7.5
    assert observed["stdout"] is subprocess.PIPE
    assert observed["stderr"] is subprocess.DEVNULL


def test_real_legacy_acquisition_receipt_imports_idempotently_without_old_semantics(
    tmp_path: Path,
) -> None:
    media = tmp_path / "work-pressure.mp4"
    media.write_bytes(b"native horizontal stock from exact old acquisition")
    receipt_path, forensic_ref = _write_legacy_receipt(tmp_path, media)
    store_root = tmp_path / "assets-v2"
    store = ActiveAssetStore.open(store_root, episode_id="episode-001")
    inspector = _Inspector(NeutralMediaProbe(width=1920, height=1080, duration_sec=8.4))
    importer = LegacyNeutralAssetImporter(store=store, inspector=inspector)
    request = _import_request(receipt_path, media, forensic_ref)

    first_reference = importer.import_one(request)
    second_reference = importer.import_one(request)
    reopened = ActiveAssetStore.open(store_root, episode_id="episode-001")
    catalog_item = reopened.worker_selection_catalog().item(first_reference)

    assert second_reference == first_reference
    assert asdict(catalog_item) == {
        "reference": first_reference,
        "kind": AssetKind.STOCK,
        "visual_summary": "焦頭爛額處理工作與家庭責任的橫式實拍",
        "width": 1920,
        "height": 1080,
        "duration_sec": 8.4,
    }
    compact = reopened.resolve_active_asset(first_reference).record.compact_receipt
    assert compact is not None
    assert compact.forensic_receipt_ref == forensic_ref
    assert compact.provider == "pexels"
    index_path = store_root / "index.v1.json"
    index_text = index_path.read_text(encoding="utf-8")
    assert "visual-pipeline" not in index_text
    assert "revision-retired-001" not in index_text
    assert "value-L02" not in index_text
    compact_row = json.loads(index_text)["payload"]["records"][0]["compact_receipt"]
    assert set(compact_row) == {
        "origin",
        "media_sha256",
        "media_bytes",
        "source_class",
        "provider",
        "provider_item_id",
        "source_url",
        "license",
        "acquired_at",
        "forensic_receipt_ref",
    }
    assert inspector.paths == [media.resolve(), media.resolve()]


@pytest.mark.parametrize(
    ("field", "forged_value"),
    [("sha256", "0" * 64), ("bytes", 1)],
)
def test_import_rejects_forged_media_identity_before_store_mutation(
    tmp_path: Path,
    field: str,
    forged_value: str | int,
) -> None:
    media = tmp_path / "work-pressure.mp4"
    media.write_bytes(b"native horizontal stock from exact old acquisition")
    receipt_path, _ = _write_legacy_receipt(tmp_path, media)

    def _forge(receipt: dict[str, object]) -> None:
        original_media = receipt["original_media"]
        assert isinstance(original_media, dict)
        original_media[field] = forged_value

    forensic_ref = _rewrite_receipt(receipt_path, _forge)
    store_root = tmp_path / "assets-v2"
    inspector = _Inspector(NeutralMediaProbe(width=1920, height=1080, duration_sec=8.4))
    importer = LegacyNeutralAssetImporter(
        store=ActiveAssetStore.open(store_root, episode_id="episode-001"),
        inspector=inspector,
    )

    with pytest.raises(NeutralAssetImportError, match="media identity differs"):
        importer.import_one(_import_request(receipt_path, media, forensic_ref))

    assert not store_root.exists()
    assert inspector.paths == []


@pytest.mark.parametrize("missing_field", ["provider", "source_url", "license"])
def test_import_rejects_missing_source_facts_before_store_mutation(
    tmp_path: Path,
    missing_field: str,
) -> None:
    media = tmp_path / "work-pressure.mp4"
    media.write_bytes(b"native horizontal stock from exact old acquisition")
    receipt_path, _ = _write_legacy_receipt(tmp_path, media)

    def _remove_fact(receipt: dict[str, object]) -> None:
        receipt.pop(missing_field)

    forensic_ref = _rewrite_receipt(receipt_path, _remove_fact)
    store_root = tmp_path / "assets-v2"
    inspector = _Inspector(NeutralMediaProbe(width=1920, height=1080, duration_sec=8.4))
    importer = LegacyNeutralAssetImporter(
        store=ActiveAssetStore.open(store_root, episode_id="episode-001"),
        inspector=inspector,
    )

    with pytest.raises(NeutralAssetImportError, match="receipt fields"):
        importer.import_one(_import_request(receipt_path, media, forensic_ref))

    assert not store_root.exists()
    assert inspector.paths == []


@pytest.mark.parametrize(
    ("field", "forged_value"),
    [
        ("provider", "unknown-stock-provider"),
        ("source_url", "https://www.pexels.com/video/9999999/"),
        ("license", "trust me"),
    ],
)
def test_import_rejects_forged_source_profile_before_store_mutation(
    tmp_path: Path,
    field: str,
    forged_value: str,
) -> None:
    media = tmp_path / "work-pressure.mp4"
    media.write_bytes(b"native horizontal stock from exact old acquisition")
    receipt_path, _ = _write_legacy_receipt(tmp_path, media)

    def _forge_fact(receipt: dict[str, object]) -> None:
        receipt[field] = forged_value

    forensic_ref = _rewrite_receipt(receipt_path, _forge_fact)
    store_root = tmp_path / "assets-v2"
    inspector = _Inspector(NeutralMediaProbe(width=1920, height=1080, duration_sec=8.4))
    importer = LegacyNeutralAssetImporter(
        store=ActiveAssetStore.open(store_root, episode_id="episode-001"),
        inspector=inspector,
    )

    with pytest.raises(NeutralAssetImportError, match="publication was rejected"):
        importer.import_one(_import_request(receipt_path, media, forensic_ref))

    assert not store_root.exists()


def test_import_rejects_a_forged_forensic_receipt_reference_before_media_probe(
    tmp_path: Path,
) -> None:
    media = tmp_path / "work-pressure.mp4"
    media.write_bytes(b"native horizontal stock from exact old acquisition")
    receipt_path, _ = _write_legacy_receipt(tmp_path, media)
    store_root = tmp_path / "assets-v2"
    inspector = _Inspector(NeutralMediaProbe(width=1920, height=1080, duration_sec=8.4))
    importer = LegacyNeutralAssetImporter(
        store=ActiveAssetStore.open(store_root, episode_id="episode-001"),
        inspector=inspector,
    )

    with pytest.raises(NeutralAssetImportError, match="forensic.*reference differs"):
        importer.import_one(
            _import_request(
                receipt_path,
                media,
                "forensic-sha256:" + "0" * 64,
            )
        )

    assert not store_root.exists()
    assert inspector.paths == []


@pytest.mark.parametrize(
    "old_semantic_field",
    ["director_plan", "dp_fulfillment", "title", "concept_card"],
)
def test_import_never_accepts_old_semantic_rows(
    tmp_path: Path,
    old_semantic_field: str,
) -> None:
    media = tmp_path / "work-pressure.mp4"
    media.write_bytes(b"native horizontal stock from exact old acquisition")
    receipt_path, _ = _write_legacy_receipt(tmp_path, media)

    def _inject_semantics(receipt: dict[str, object]) -> None:
        receipt[old_semantic_field] = "retired semantic authority"

    forensic_ref = _rewrite_receipt(receipt_path, _inject_semantics)
    store_root = tmp_path / "assets-v2"
    inspector = _Inspector(NeutralMediaProbe(width=1920, height=1080, duration_sec=8.4))
    importer = LegacyNeutralAssetImporter(
        store=ActiveAssetStore.open(store_root, episode_id="episode-001"),
        inspector=inspector,
    )

    with pytest.raises(NeutralAssetImportError, match="receipt fields"):
        importer.import_one(_import_request(receipt_path, media, forensic_ref))

    assert not store_root.exists()
    assert inspector.paths == []


def test_import_rejects_a_receipt_from_another_episode(tmp_path: Path) -> None:
    media = tmp_path / "work-pressure.mp4"
    media.write_bytes(b"native horizontal stock from exact old acquisition")
    receipt_path, forensic_ref = _write_legacy_receipt(
        tmp_path,
        media,
        episode_id="episode-other",
    )
    store_root = tmp_path / "assets-v2"
    inspector = _Inspector(NeutralMediaProbe(width=1920, height=1080, duration_sec=8.4))
    importer = LegacyNeutralAssetImporter(
        store=ActiveAssetStore.open(store_root, episode_id="episode-001"),
        inspector=inspector,
    )

    with pytest.raises(NeutralAssetImportError, match="another episode"):
        importer.import_one(_import_request(receipt_path, media, forensic_ref))

    assert not store_root.exists()
    assert inspector.paths == []


def test_import_never_discovers_a_neighboring_legacy_receipt(tmp_path: Path) -> None:
    media = tmp_path / "work-pressure.mp4"
    media.write_bytes(b"native horizontal stock from exact old acquisition")
    discovered_path, forensic_ref = _write_legacy_receipt(tmp_path, media)
    legacy_root = tmp_path / "highlights" / "visual-pipeline" / "nested"
    legacy_root.mkdir(parents=True)
    decoy = legacy_root / "decoy-receipt.json"
    discovered_path.replace(decoy)
    missing_explicit_path = tmp_path / "explicit-but-missing-receipt.json"
    store_root = tmp_path / "assets-v2"
    inspector = _Inspector(NeutralMediaProbe(width=1920, height=1080, duration_sec=8.4))
    importer = LegacyNeutralAssetImporter(
        store=ActiveAssetStore.open(store_root, episode_id="episode-001"),
        inspector=inspector,
    )

    with pytest.raises(NeutralAssetImportError, match="receipt is missing"):
        importer.import_one(_import_request(missing_explicit_path, media, forensic_ref))

    assert decoy.is_file()
    assert not store_root.exists()
    assert inspector.paths == []


@pytest.mark.parametrize(
    "probe",
    [
        NeutralMediaProbe(width=1280, height=1080, duration_sec=8.4),
        NeutralMediaProbe(width=1920, height=720, duration_sec=8.4),
        NeutralMediaProbe(width=1920, height=1080, duration_sec=8.5),
    ],
)
def test_import_requires_exact_inspected_dimensions_and_duration(
    tmp_path: Path,
    probe: NeutralMediaProbe,
) -> None:
    media = tmp_path / "work-pressure.mp4"
    media.write_bytes(b"native horizontal stock from exact old acquisition")
    receipt_path, forensic_ref = _write_legacy_receipt(tmp_path, media)
    store_root = tmp_path / "assets-v2"
    importer = LegacyNeutralAssetImporter(
        store=ActiveAssetStore.open(store_root, episode_id="episode-001"),
        inspector=_Inspector(probe),
    )

    with pytest.raises(NeutralAssetImportError, match="media facts differ"):
        importer.import_one(_import_request(receipt_path, media, forensic_ref))

    assert not store_root.exists()


def test_import_rejects_native_vertical_video_instead_of_reframing_it(
    tmp_path: Path,
) -> None:
    media = tmp_path / "vertical-stock.mp4"
    media.write_bytes(b"native vertical old acquisition")
    receipt_path, forensic_ref = _write_legacy_receipt(tmp_path, media)
    store_root = tmp_path / "assets-v2"
    importer = LegacyNeutralAssetImporter(
        store=ActiveAssetStore.open(store_root, episode_id="episode-001"),
        inspector=_Inspector(NeutralMediaProbe(width=1080, height=1920, duration_sec=8.4)),
    )
    request = _import_request(
        receipt_path,
        media,
        forensic_ref,
        width=1080,
        height=1920,
    )

    with pytest.raises(NeutralAssetImportError, match="not native landscape"):
        importer.import_one(request)

    assert not store_root.exists()


def test_import_rejects_same_bytes_with_conflicting_source_provenance(
    tmp_path: Path,
) -> None:
    media = tmp_path / "work-pressure.mp4"
    media.write_bytes(b"native horizontal stock from exact old acquisition")
    receipt_path, forensic_ref = _write_legacy_receipt(tmp_path, media)
    store_root = tmp_path / "assets-v2"
    importer = LegacyNeutralAssetImporter(
        store=ActiveAssetStore.open(store_root, episode_id="episode-001"),
        inspector=_Inspector(NeutralMediaProbe(width=1920, height=1080, duration_sec=8.4)),
    )
    importer.import_one(_import_request(receipt_path, media, forensic_ref))
    index_before = (store_root / "index.v1.json").read_bytes()

    def _change_source(receipt: dict[str, object]) -> None:
        receipt["provider_item_id"] = "9999999"
        receipt["source_url"] = "https://www.pexels.com/video/9999999/"

    conflicting_ref = _rewrite_receipt(receipt_path, _change_source)

    with pytest.raises(NeutralAssetImportError, match="publication was rejected"):
        importer.import_one(_import_request(receipt_path, media, conflicting_ref))

    assert (store_root / "index.v1.json").read_bytes() == index_before
