"""Trusted HyperFrames candidate rendering must fail closed.

The DP worker is allowed to propose a component and closed variables.  It is
not allowed to render its own FFmpeg card and label that file HyperFrames.
"""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import agents.brook.script_video.highlight_candidate_renderer as candidate_renderer
from agents.brook.script_video.highlight_candidate_renderer import (
    DP_HYDRATION_CONTRACT,
    HYPERFRAMES_RENDER_CONTRACT,
    TrustedRenderError,
    dp_hydration_receipt_identity,
    hydrate_dp_hyperframes_proposal,
    hyperframes_runtime_status,
    prepare_hyperframes_runtime,
    render_hyperframes_candidate,
    verify_dp_hydration_receipt,
    verify_hyperframes_render_receipt,
)

REVISION_ID = "r-56f399026934cb0b3fef8ed5"
CUT_ID = "value-L01"
PNG_DATA_URI = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUB"
    "AScY42YAAAAASUVORK5CYII="
)


def _identity(root: Path, path: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _concept_params(*, title: str = "高壓教育不是兒童遊戲") -> dict[str, object]:
    return {
        "title": title,
        "left_label": "高壓管教",
        "right_label": "服從訓練",
        "left_src": PNG_DATA_URI,
        "right_src": PNG_DATA_URI,
        "show_sec": 3.0,
        "pos_y": 0.63,
    }


def _copy_fixture_runner(fixture: Path, calls: list[list[str]]):
    def run(argv, **_kwargs):
        normalized = [str(value) for value in argv]
        calls.append(normalized)
        output = Path(normalized[normalized.index("-o") + 1])
        shutil.copy2(fixture, output)
        return SimpleNamespace(returncode=0, stdout="rendered", stderr="")

    return run


def _dynamic_fixture(tmp_path: Path) -> Path:
    path = tmp_path / "dynamic-fixture.mp4"
    if path.exists():
        return path
    completed = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=30:duration=3",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0 and path.is_file()
    return path


def _runtime_install_runner(version: str, calls: list[list[str]] | None = None):
    def install(argv, **_kwargs):
        normalized = [str(value) for value in argv]
        if calls is not None:
            calls.append(normalized)
        prefix = Path(normalized[normalized.index("--prefix") + 1])
        package = prefix / "node_modules" / "hyperframes"
        cli = package / "dist" / "cli.js"
        cli.parent.mkdir(parents=True)
        cli.write_text("console.log('trusted hyperframes fixture')", encoding="utf-8")
        (package / "package.json").write_text(
            json.dumps(
                {
                    "name": "hyperframes",
                    "version": version,
                    "bin": {"hyperframes": "dist/cli.js"},
                }
            ),
            encoding="utf-8",
        )
        (prefix / "package-lock.json").write_text(
            json.dumps({"lockfileVersion": 3, "packages": {}}), encoding="utf-8"
        )
        dependency = prefix / "node_modules" / "renderer-transitive" / "index.js"
        dependency.parent.mkdir(parents=True)
        dependency.write_text("export const renderer = 'pinned';", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="installed", stderr="")

    return install


def _malicious_runtime_install_runner(version: str, media: Path):
    """Install a fake CLI that copies arbitrary moving media to HyperFrames output."""

    def install(argv, **_kwargs):
        normalized = [str(value) for value in argv]
        prefix = Path(normalized[normalized.index("--prefix") + 1])
        package = prefix / "node_modules" / "hyperframes"
        cli = package / "dist" / "cli.js"
        cli.parent.mkdir(parents=True)
        cli.write_text(
            "const fs=require('fs');"
            "const args=process.argv.slice(2);"
            f"fs.copyFileSync({json.dumps(str(media))},args[args.indexOf('-o')+1]);",
            encoding="utf-8",
        )
        (package / "package.json").write_text(
            json.dumps(
                {
                    "name": "hyperframes",
                    "version": version,
                    "bin": {"hyperframes": "dist/cli.js"},
                }
            ),
            encoding="utf-8",
        )
        (prefix / "package-lock.json").write_text(
            json.dumps({"lockfileVersion": 3, "packages": {}}), encoding="utf-8"
        )
        dependency = prefix / "node_modules" / "renderer-transitive" / "index.js"
        dependency.parent.mkdir(parents=True)
        dependency.write_text("export const renderer = 'forged';", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="installed forged runtime", stderr="")

    return install


def _prepare_fake_runtime(tmp_path: Path, version: str = "0.7.72") -> Path:
    runtime_root = tmp_path / "runtimes"
    candidate_renderer._prepare_hyperframes_runtime_for_test(
        f"hyperframes@{version}",
        runtime_root=runtime_root,
        npm_executable="npm-test",
        runner=_runtime_install_runner(version),
    )
    return runtime_root


def _render(tmp_path: Path, *, params: dict[str, object] | None = None):
    root = tmp_path / "episode"
    root.mkdir()
    fixture = _dynamic_fixture(tmp_path)
    calls: list[list[str]] = []
    runtime_root = _prepare_fake_runtime(tmp_path)
    render_params = params or _concept_params()
    result = candidate_renderer._render_hyperframes_candidate_for_test(
        root,
        cut_id=CUT_ID,
        revision_id=REVISION_ID,
        candidate_id="hf-candidate-01",
        component="concept_card",
        render_params=render_params,
        expected_on_screen_text=str(render_params["title"]),
        runtime_root=runtime_root,
        runtime_command=("hyperframes-test",),
        runner=_copy_fixture_runner(fixture, calls),
    )
    return root, result, calls


def _rehash_receipt(root: Path, receipt_path: Path) -> dict[str, object]:
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    payload.pop("content_hash", None)
    payload["content_hash"] = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    receipt_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return _identity(root, receipt_path)


def _asset_authority_projection(root: Path, tmp_path: Path) -> dict[str, object]:
    media = root / "trusted-acquisitions" / "pexels-school-01.mp4"
    media.parent.mkdir(parents=True)
    shutil.copy2(_dynamic_fixture(tmp_path), media)
    receipt = media.with_name("ACQUISITION.json")
    receipt.write_text('{"contract":"trusted-fixture"}', encoding="utf-8")
    return {
        "identity": {"fixture": "current-authority"},
        "authority_chain": [{"fixture": "current-authority"}],
        "attempt": 1,
        "assets": [
            {
                "asset_id": "pexels-school-01",
                "source_class": "licensed_stock",
                "provider": "Pexels",
                "provider_item_id": "12345",
                "source_url": "https://www.pexels.com/video/12345/",
                "license": "Pexels license",
                "acquired_at": "2026-08-26T00:00:00Z",
                "semantic_summary": "戰後校園高壓教育與隊列的歷史情境",
                "original_media": _identity(root, media),
                "acquisition_receipt": _identity(root, receipt),
            }
        ],
    }


def test_trusted_renderer_writes_a_fresh_verifiable_hyperframes_receipt(
    tmp_path: Path,
) -> None:
    root, result, calls = _render(tmp_path)

    assert len(calls) == 1
    assert calls[0][:3] == ["hyperframes-test", "render", "."]
    assert "drawtext" not in " ".join(calls[0])
    assert result["provenance"]["kind"] == "hyperframes_render"
    verified = candidate_renderer._verify_hyperframes_test_receipt(
        root,
        receipt_identity=result["provenance"]["receipt"],
        expected_cut_id=CUT_ID,
        expected_revision_id=REVISION_ID,
        expected_candidate_id="hf-candidate-01",
        expected_component="concept_card",
        expected_render_params=_concept_params(),
        expected_on_screen_text="高壓教育不是兒童遊戲",
        expected_media=result["preview_media"],
        runtime_root=tmp_path / "runtimes",
    )

    assert verified["contract"] != HYPERFRAMES_RENDER_CONTRACT
    assert verified["render_spec"]["render_params"] == _concept_params()
    assert {key: verified["media"][key] for key in ("path", "bytes", "sha256")} == result[
        "preview_media"
    ]
    with pytest.raises(TrustedRenderError, match="trusted-renders|contract"):
        verify_hyperframes_render_receipt(
            root,
            receipt_identity=result["provenance"]["receipt"],
            expected_cut_id=CUT_ID,
            expected_revision_id=REVISION_ID,
            expected_candidate_id="hf-candidate-01",
            expected_component="concept_card",
            expected_render_params=_concept_params(),
            expected_on_screen_text="高壓教育不是兒童遊戲",
            expected_media=result["preview_media"],
            runtime_root=tmp_path / "runtimes",
        )


@pytest.mark.parametrize(
    "hero_text",
    ["與其教故事\r\n不如動手做", "傳統道路\r\n沒有保證了"],
)
def test_hero_title_preserves_the_exact_two_line_boundary(tmp_path: Path, hero_text: str) -> None:
    root = tmp_path / "episode"
    root.mkdir()
    runtime_root = _prepare_fake_runtime(tmp_path)
    fixture = _dynamic_fixture(tmp_path)
    calls: list[list[str]] = []
    params = {
        "text": hero_text,
        "tier": 1,
        "style": "paper",
        "show_sec": 4.0,
        "pos_y": 0.6,
    }

    result = candidate_renderer._render_hyperframes_candidate_for_test(
        root,
        cut_id=CUT_ID,
        revision_id=REVISION_ID,
        candidate_id="hero-01",
        component="punch_card_wide",
        render_params=params,
        expected_on_screen_text=hero_text,
        runtime_root=runtime_root,
        runtime_command=("hyperframes-test",),
        runner=_copy_fixture_runner(fixture, calls),
    )

    receipt_path = root / str(result["provenance"]["receipt"]["path"])
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    variables_path = root / str(receipt["variables_file"]["path"])
    variables = json.loads(variables_path.read_text(encoding="utf-8"))
    assert receipt["render_spec"]["render_params"]["text"] == hero_text.replace("\r\n", "\n")
    assert [variables["line1"], variables["line2"]] == hero_text.split("\r\n")


def test_transition_title_preserves_an_exact_two_line_boundary(tmp_path: Path) -> None:
    root = tmp_path / "episode"
    root.mkdir()
    runtime_root = _prepare_fake_runtime(tmp_path, "0.6.42")
    fixture = _dynamic_fixture(tmp_path)
    title = "教育開始\n改變"
    params = {
        "kicker": "01",
        "title": title,
        "style": "paper",
        "show_sec": 4.0,
    }

    result = candidate_renderer._render_hyperframes_candidate_for_test(
        root,
        cut_id=CUT_ID,
        revision_id=REVISION_ID,
        candidate_id="transition-01",
        component="transition_title",
        render_params=params,
        expected_on_screen_text=title,
        runtime_root=runtime_root,
        runtime_command=("hyperframes-test",),
        runner=_copy_fixture_runner(fixture, []),
    )

    receipt_path = root / str(result["provenance"]["receipt"]["path"])
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    variables_path = root / str(receipt["variables_file"]["path"])
    variables = json.loads(variables_path.read_text(encoding="utf-8"))
    assert receipt["render_spec"]["render_params"]["title"] == title
    assert variables["title"] == title


def test_rev10_plain_text_self_reported_receipt_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "episode"
    receipt = (
        root
        / "highlights"
        / "visual-pipeline"
        / CUT_ID
        / "jobs"
        / REVISION_ID
        / "workers"
        / "dp-session"
        / "assets"
        / "provenance-receipt.txt"
    )
    receipt.parent.mkdir(parents=True)
    receipt.write_text(
        "All HyperFrames candidates are original deterministic local motion-card previews.",
        encoding="utf-8",
    )
    media = receipt.with_name("fake-concept-card.mp4")
    media.write_bytes(b"15KB ffmpeg color/drawtext card" * 500)

    with pytest.raises(TrustedRenderError, match="trusted-renders|JSON|receipt"):
        verify_hyperframes_render_receipt(
            root,
            receipt_identity=_identity(root, receipt),
            expected_cut_id=CUT_ID,
            expected_revision_id=REVISION_ID,
            expected_candidate_id="hf-candidate-01",
            expected_component="concept_card",
            expected_render_params=_concept_params(),
            expected_on_screen_text="高壓教育不是兒童遊戲",
            expected_media=_identity(root, media),
        )


def test_public_renderer_cannot_inject_a_fake_process_runner(tmp_path: Path) -> None:
    root = tmp_path / "episode"
    root.mkdir()
    forged_media = _dynamic_fixture(tmp_path)

    assert "runner" not in inspect.signature(render_hyperframes_candidate).parameters
    assert "runtime_command" not in inspect.signature(render_hyperframes_candidate).parameters
    assert "runner" not in inspect.signature(hydrate_dp_hyperframes_proposal).parameters
    assert "runtime_command" not in inspect.signature(
        hydrate_dp_hyperframes_proposal
    ).parameters
    assert "runner" not in inspect.signature(prepare_hyperframes_runtime).parameters
    assert "npm_executable" not in inspect.signature(prepare_hyperframes_runtime).parameters
    with pytest.raises(TypeError):
        render_hyperframes_candidate(
            root,
            cut_id=CUT_ID,
            revision_id=REVISION_ID,
            candidate_id="forged-moving-card",
            component="concept_card",
            render_params=_concept_params(),
            expected_on_screen_text="高壓教育不是兒童遊戲",
            runtime_command=("fake-hyperframes",),
            runner=_copy_fixture_runner(forged_media, []),
        )
    assert not list(root.rglob("HYPERFRAMES-RENDER.json"))


def test_test_runtime_cannot_mint_a_production_render_receipt(tmp_path: Path) -> None:
    root = tmp_path / "episode"
    root.mkdir()
    forged_media = _dynamic_fixture(tmp_path)
    runtime_root = tmp_path / "forged-runtimes"
    candidate_renderer._prepare_hyperframes_runtime_for_test(
        "hyperframes@0.7.72",
        runtime_root=runtime_root,
        npm_executable="npm-test",
        runner=_malicious_runtime_install_runner("0.7.72", forged_media),
    )

    with pytest.raises(TrustedRenderError, match="test|acquisition|contract"):
        render_hyperframes_candidate(
            root,
            cut_id=CUT_ID,
            revision_id=REVISION_ID,
            candidate_id="forged-runtime-card",
            component="concept_card",
            render_params=_concept_params(),
            expected_on_screen_text="高壓教育不是兒童遊戲",
            runtime_root=runtime_root,
        )
    assert not list(root.rglob("trusted-renders/**/HYPERFRAMES-RENDER.json"))


def test_self_signed_fake_runtime_cannot_claim_the_production_contract(tmp_path: Path) -> None:
    root = tmp_path / "episode"
    root.mkdir()
    forged_media = _dynamic_fixture(tmp_path)
    runtime_root = tmp_path / "forged-runtimes"
    candidate_renderer._prepare_hyperframes_runtime_for_test(
        "hyperframes@0.7.72",
        runtime_root=runtime_root,
        npm_executable="npm-test",
        runner=_malicious_runtime_install_runner("0.7.72", forged_media),
    )
    acquisition = runtime_root / "0.7.72" / "NPM-ACQUISITION.json"
    receipt = json.loads(acquisition.read_text(encoding="utf-8"))
    receipt["contract"] = candidate_renderer.HYPERFRAMES_RUNTIME_CONTRACT
    acquisition.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
    _rehash_receipt(runtime_root, acquisition)

    with pytest.raises(TrustedRenderError, match="pinned|official|identity"):
        render_hyperframes_candidate(
            root,
            cut_id=CUT_ID,
            revision_id=REVISION_ID,
            candidate_id="self-signed-runtime-card",
            component="concept_card",
            render_params=_concept_params(),
            expected_on_screen_text="高壓教育不是兒童遊戲",
            runtime_root=runtime_root,
        )
    assert not list(root.rglob("trusted-renders/**/HYPERFRAMES-RENDER.json"))


def test_internal_runtime_acquisition_cannot_inject_a_production_runner(
    tmp_path: Path,
) -> None:
    called = False

    def forged_runner(_argv, **_kwargs):
        nonlocal called
        called = True
        return SimpleNamespace(returncode=0, stdout="forged", stderr="")

    with pytest.raises(TrustedRenderError, match="production.*injection"):
        candidate_renderer._prepare_hyperframes_runtime(
            "hyperframes@0.7.72",
            runtime_root=tmp_path / "forged-runtimes",
            npm_executable="npm-test",
            runner=forged_runner,
        )
    assert not called


def test_production_runtime_rejects_a_path_hijacked_node(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = _prepare_fake_runtime(tmp_path)
    test_status = candidate_renderer._hyperframes_runtime_status(
        "hyperframes@0.7.72", runtime_root=runtime_root, test_mode=True
    )
    pinned_keys = {
        "package_manifest_sha256",
        "cli_sha256",
        "package_lock_sha256",
        "node_modules_content_hash",
        "acquisition_content_hash",
    }
    acquisition = runtime_root / "0.7.72" / "NPM-ACQUISITION.json"
    receipt = json.loads(acquisition.read_text(encoding="utf-8"))
    receipt["contract"] = candidate_renderer.HYPERFRAMES_RUNTIME_CONTRACT
    acquisition.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
    _rehash_receipt(runtime_root, acquisition)
    production_receipt = json.loads(acquisition.read_text(encoding="utf-8"))
    pinned = {key: test_status[key] for key in pinned_keys}
    pinned["acquisition_content_hash"] = production_receipt["content_hash"]
    monkeypatch.setitem(
        candidate_renderer._PINNED_RUNTIME_IDENTITIES,
        "hyperframes@0.7.72",
        pinned,
    )
    fake_node = tmp_path / "node.exe"
    fake_node.write_bytes(b"path-hijacked-node")
    original_which = candidate_renderer.shutil.which

    def fake_which(name: str):
        if name in {"node", "node.exe"}:
            return str(fake_node)
        return original_which(name)

    monkeypatch.setattr(candidate_renderer.shutil, "which", fake_which)
    with pytest.raises(TrustedRenderError, match="Node.*pinned|executable.*pinned"):
        hyperframes_runtime_status("hyperframes@0.7.72", runtime_root=runtime_root)


def test_private_trust_mode_never_coerces_a_stateful_object(tmp_path: Path) -> None:
    class TruthinessTrap:
        def __bool__(self) -> bool:
            raise AssertionError("trust mode was coerced before type validation")

    root = tmp_path / "episode"
    root.mkdir()
    runtime_root = _prepare_fake_runtime(tmp_path)
    with pytest.raises(TrustedRenderError, match="test_mode.*bool"):
        candidate_renderer._hyperframes_runtime_status(
            "hyperframes@0.7.72",
            runtime_root=runtime_root,
            test_mode=TruthinessTrap(),
        )
    with pytest.raises(TrustedRenderError, match="test_mode.*bool"):
        candidate_renderer._prepare_hyperframes_runtime(
            "hyperframes@0.7.72",
            runtime_root=tmp_path / "other-runtime",
            test_mode=TruthinessTrap(),
        )
    with pytest.raises(TrustedRenderError, match="test_mode.*bool"):
        candidate_renderer._render_hyperframes_candidate(
            root,
            cut_id=CUT_ID,
            revision_id=REVISION_ID,
            candidate_id="truthiness-trap",
            component="concept_card",
            render_params=_concept_params(),
            expected_on_screen_text="高壓教育不是兒童遊戲",
            test_mode=TruthinessTrap(),
        )


def test_asset_candidates_are_exact_joined_to_core_authority_projection(tmp_path: Path) -> None:
    root = tmp_path / "episode"
    root.mkdir()
    authority = _asset_authority_projection(root, tmp_path)
    proposal_path = root / "workers" / "dp" / "proposal.json"
    output_path = root / "trusted" / "hydrated.json"
    proposal_path.parent.mkdir(parents=True)
    proposal = {
        "implementations": [
            {
                "mode": "stock",
                "on_screen_text": None,
                "candidates": [
                    {
                        "candidate_id": "stock-a",
                        "visual_summary": "戰後校園高壓教育與隊列的歷史情境",
                        "authority_asset_id": "pexels-school-01",
                    }
                ],
            }
        ]
    }
    proposal_path.write_text(json.dumps(proposal, ensure_ascii=False), encoding="utf-8")

    original_loader = candidate_renderer._fresh_asset_authority_projection
    candidate_renderer._fresh_asset_authority_projection = lambda *args, **kwargs: authority
    try:
        hydrated = hydrate_dp_hyperframes_proposal(
            root,
            cut_id=CUT_ID,
            revision_id=REVISION_ID,
            attempt=1,
            proposal_path=proposal_path,
            output_path=output_path,
        )
        hydration_identity = dp_hydration_receipt_identity(root, output_path)
        verified_hydration = verify_dp_hydration_receipt(
            root,
            receipt_identity=hydration_identity,
            expected_cut_id=CUT_ID,
            expected_revision_id=REVISION_ID,
            expected_attempt=1,
            expected_raw_proposal=_identity(root, proposal_path),
            expected_hydrated_proposal=_identity(root, output_path),
        )
    finally:
        candidate_renderer._fresh_asset_authority_projection = original_loader

    candidate = hydrated["implementations"][0]["candidates"][0]
    authority_asset = authority["assets"][0]
    assert candidate["media"] == authority_asset["original_media"]
    assert candidate["provenance"] == {
        "kind": "stock_source",
        "provider": "Pexels",
        "source_url": "https://www.pexels.com/video/12345/",
        "license": "Pexels license",
        "receipt": authority_asset["acquisition_receipt"],
    }
    assert json.loads(proposal_path.read_text(encoding="utf-8")) == proposal
    assert json.loads(output_path.read_text(encoding="utf-8")) == hydrated
    assert verified_hydration["contract"] == DP_HYDRATION_CONTRACT
    assert verified_hydration["raw_proposal"] == _identity(root, proposal_path)
    assert verified_hydration["hydrated_proposal"] == _identity(root, output_path)
    assert verified_hydration["asset_authority"]["projection_content_hash"]
    assert verified_hydration["raw_proposal_document"] == proposal
    assert verified_hydration["hydrated_proposal_document"] == hydrated


def test_worker_media_cannot_bypass_asset_authority_hydration(tmp_path: Path) -> None:
    root = tmp_path / "episode"
    root.mkdir()
    authority = _asset_authority_projection(root, tmp_path)
    proposal_path = root / "workers" / "dp" / "proposal.json"
    output_path = root / "trusted" / "hydrated.json"
    proposal_path.parent.mkdir(parents=True)
    candidate = {
        "candidate_id": "stock-a",
        "visual_summary": "戰後校園高壓教育與隊列的歷史情境",
        "authority_asset_id": "pexels-school-01",
        "media": authority["assets"][0]["original_media"],
    }
    proposal_path.write_text(
        json.dumps(
            {
                "implementations": [
                    {"mode": "stock", "on_screen_text": None, "candidates": [candidate]}
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(TrustedRenderError, match="spec-only schema"):
        hydrate_dp_hyperframes_proposal(
            root,
            cut_id=CUT_ID,
            revision_id=REVISION_ID,
            attempt=1,
            proposal_path=proposal_path,
            output_path=output_path,
        )
    assert not output_path.exists()


@pytest.mark.parametrize("tamper", ["raw", "hydrated", "sidecar"])
def test_hydration_receipt_rejects_bound_file_tamper(tmp_path: Path, tamper: str) -> None:
    root = tmp_path / "episode"
    root.mkdir()
    authority = _asset_authority_projection(root, tmp_path)
    raw_path = root / "workers" / "dp" / "proposal.json"
    hydrated_path = root / "trusted" / "hydrated.json"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_text(
        json.dumps(
            {
                "implementations": [
                    {
                        "mode": "stock",
                        "on_screen_text": None,
                        "candidates": [
                            {
                                "candidate_id": "stock-a",
                                "visual_summary": "戰後校園高壓教育與隊列的歷史情境",
                                "authority_asset_id": "pexels-school-01",
                            }
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    original_loader = candidate_renderer._fresh_asset_authority_projection
    candidate_renderer._fresh_asset_authority_projection = lambda *args, **kwargs: authority
    try:
        hydrate_dp_hyperframes_proposal(
            root,
            cut_id=CUT_ID,
            revision_id=REVISION_ID,
            attempt=1,
            proposal_path=raw_path,
            output_path=hydrated_path,
        )
        raw_identity = _identity(root, raw_path)
        hydrated_identity = _identity(root, hydrated_path)
        receipt_identity = dp_hydration_receipt_identity(root, hydrated_path)
        tamper_path = {
            "raw": raw_path,
            "hydrated": hydrated_path,
            "sidecar": root / str(receipt_identity["path"]),
        }[tamper]
        tamper_path.write_bytes(tamper_path.read_bytes() + b"\n")
        with pytest.raises(TrustedRenderError, match="drift|hash|identity|byte-size"):
            verify_dp_hydration_receipt(
                root,
                receipt_identity=receipt_identity,
                expected_cut_id=CUT_ID,
                expected_revision_id=REVISION_ID,
                expected_attempt=1,
                expected_raw_proposal=raw_identity,
                expected_hydrated_proposal=hydrated_identity,
            )
    finally:
        candidate_renderer._fresh_asset_authority_projection = original_loader


def test_forged_caller_mapping_is_not_a_public_authority_input(tmp_path: Path) -> None:
    root = tmp_path / "episode"
    root.mkdir()
    forged_authority = _asset_authority_projection(root, tmp_path)
    proposal_path = root / "workers" / "dp" / "proposal.json"
    output_path = root / "trusted" / "hydrated.json"
    proposal_path.parent.mkdir(parents=True)
    proposal_path.write_text(
        json.dumps(
            {
                "implementations": [
                    {
                        "mode": "stock",
                        "on_screen_text": None,
                        "candidates": [
                            {
                                "candidate_id": "stock-a",
                                "visual_summary": "戰後校園高壓教育與隊列的歷史情境",
                                "authority_asset_id": "pexels-school-01",
                            }
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert "asset_authority" not in inspect.signature(
        hydrate_dp_hyperframes_proposal
    ).parameters
    with pytest.raises(TypeError):
        hydrate_dp_hyperframes_proposal(
            root,
            cut_id=CUT_ID,
            revision_id=REVISION_ID,
            attempt=1,
            proposal_path=proposal_path,
            output_path=output_path,
            asset_authority=forged_authority,
        )
    assert not output_path.exists()


def test_static_playable_card_cannot_pass_as_a_hyperframes_component(tmp_path: Path) -> None:
    root = tmp_path / "episode"
    root.mkdir()
    runtime_root = _prepare_fake_runtime(tmp_path)
    static_media = Path(__file__).parent / "fixtures" / "davinci_import" / "black10s.mp4"

    with pytest.raises(TrustedRenderError, match="motion|blank|transparent"):
        candidate_renderer._render_hyperframes_candidate_for_test(
            root,
            cut_id=CUT_ID,
            revision_id=REVISION_ID,
            candidate_id="static-fake-01",
            component="concept_card",
            render_params=_concept_params(),
            expected_on_screen_text="高壓教育不是兒童遊戲",
            runtime_root=runtime_root,
            runtime_command=("hyperframes-test",),
            runner=_copy_fixture_runner(static_media, []),
        )


def test_runtime_must_be_explicitly_preinstalled_with_an_acquisition_receipt(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtimes"
    with pytest.raises(TrustedRenderError, match="preinstalled|prepare-runtime"):
        hyperframes_runtime_status("hyperframes@0.7.72", runtime_root=runtime_root)
    calls: list[list[str]] = []

    status = candidate_renderer._prepare_hyperframes_runtime_for_test(
        "hyperframes@0.7.72",
        runtime_root=runtime_root,
        npm_executable="npm-test",
        runner=_runtime_install_runner("0.7.72", calls),
    )

    assert calls[0][:3] == ["npm-test", "install", "--prefix"]
    assert "--ignore-scripts=false" in calls[0]
    assert "--no-package-lock" not in calls[0]
    assert status["package"] == "hyperframes@0.7.72"
    assert len(status["acquisition_content_hash"]) == 64
    assert Path(status["runtime_root"]).name == "0.7.72"

    def should_not_reinstall(_argv, **_kwargs):
        raise AssertionError("valid preinstalled runtime was downloaded again")

    same = candidate_renderer._prepare_hyperframes_runtime_for_test(
        "hyperframes@0.7.72",
        runtime_root=runtime_root,
        npm_executable="npm-test",
        runner=should_not_reinstall,
    )
    assert same["acquisition_content_hash"] == status["acquisition_content_hash"]


def test_renderer_cli_help_exposes_explicit_runtime_gate() -> None:
    script = Path(__file__).parents[3] / "scripts" / "podcast_highlight_candidate_renderer.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert completed.returncode == 0
    assert "prepare-runtime" in completed.stdout
    assert "runtime-status" in completed.stdout
    assert "hydrate-dp" in completed.stdout
    assert "never downloads packages" in completed.stdout
    hydrate_help = subprocess.run(
        [sys.executable, str(script), "hydrate-dp", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert hydrate_help.returncode == 0
    assert "--attempt" in hydrate_help.stdout
    assert "--asset-authority" not in hydrate_help.stdout


@pytest.mark.skipif(
    os.environ.get("RUN_TRUSTED_HYPERFRAMES_SMOKE") != "1",
    reason="explicit Windows HyperFrames operational smoke",
)
def test_real_preinstalled_hyperframes_renders_playable_component_media(
    tmp_path: Path,
) -> None:
    runtime_root = os.environ.get("PODCAST_HYPERFRAMES_RUNTIME_ROOT")
    assert runtime_root, "smoke requires the explicit preinstalled runtime root"
    root = tmp_path / "episode"
    root.mkdir()

    result = render_hyperframes_candidate(
        root,
        cut_id=CUT_ID,
        revision_id=REVISION_ID,
        candidate_id="real-concept-01",
        component="concept_card",
        render_params=_concept_params(),
        expected_on_screen_text="高壓教育不是兒童遊戲",
        runtime_root=runtime_root,
    )

    receipt_path = root / str(result["provenance"]["receipt"]["path"])
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    stream = receipt["media"]["probe"]["video_streams"][0]
    assert (stream["width"], stream["height"]) == (1920, 1080)
    assert receipt["media"]["probe"]["duration_seconds"] >= 5.9
    assert receipt["media"]["bytes"] > 100_000
    frame_samples = receipt["media"]["frame_audit"]["samples"]
    assert len(frame_samples) >= 3
    assert max(sample["alpha_coverage"] for sample in frame_samples) >= 0.002
    assert len({sample["rgba_sha256"] for sample in frame_samples}) >= 2
    execution = receipt["execution"]
    assert execution["contract"] == "podcast-highlight-hyperframes-execution-v1"
    assert execution["exit_code"] == 0
    assert execution["executable"]["sha256"] == hashlib.sha256(
        Path(execution["executable"]["path"]).read_bytes()
    ).hexdigest()
    assert execution["argv"][0] == execution["executable"]["path"].replace("/", "\\")
    assert len(execution["stdout"]["sha256"]) == 64
    assert len(execution["stderr"]["sha256"]) == 64
    tampered = copy.deepcopy(receipt)
    tampered["execution"]["executable"]["sha256"] = "0" * 64
    execution_without_hash = {
        key: value for key, value in tampered["execution"].items() if key != "content_hash"
    }
    tampered["execution"]["content_hash"] = hashlib.sha256(
        json.dumps(
            execution_without_hash,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    receipt_without_hash = {key: value for key, value in tampered.items() if key != "content_hash"}
    tampered["content_hash"] = hashlib.sha256(
        json.dumps(
            receipt_without_hash,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    receipt_path.write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(TrustedRenderError, match="executable.*drift"):
        verify_hyperframes_render_receipt(
            root,
            receipt_identity=_identity(root, receipt_path),
            expected_cut_id=CUT_ID,
            expected_revision_id=REVISION_ID,
            expected_candidate_id="real-concept-01",
            expected_component="concept_card",
            expected_render_params=_concept_params(),
            expected_on_screen_text="高壓教育不是兒童遊戲",
            expected_media=result["preview_media"],
            runtime_root=runtime_root,
        )


@pytest.mark.skipif(
    os.environ.get("RUN_TRUSTED_HYPERFRAMES_SMOKE") != "1",
    reason="explicit Windows HyperFrames operational smoke",
)
def test_real_preinstalled_legacy_runtime_preserves_multiline_transition(
    tmp_path: Path,
) -> None:
    runtime_root = os.environ.get("PODCAST_HYPERFRAMES_RUNTIME_ROOT")
    assert runtime_root, "smoke requires the explicit preinstalled runtime root"
    root = tmp_path / "episode"
    root.mkdir()
    title = "教育開始\n改變"

    result = render_hyperframes_candidate(
        root,
        cut_id=CUT_ID,
        revision_id=REVISION_ID,
        candidate_id="real-transition-01",
        component="transition_title",
        render_params={
            "kicker": "01",
            "title": title,
            "style": "paper",
            "show_sec": 4.0,
        },
        expected_on_screen_text=title,
        runtime_root=runtime_root,
    )

    receipt_path = root / str(result["provenance"]["receipt"]["path"])
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    stream = receipt["media"]["probe"]["video_streams"][0]
    samples = receipt["media"]["frame_audit"]["samples"]
    assert (stream["width"], stream["height"]) == (1920, 1080)
    assert receipt["media"]["probe"]["duration_seconds"] >= 3.9
    assert max(sample["alpha_coverage"] for sample in samples) >= 0.002
    assert len({sample["rgba_sha256"] for sample in samples}) >= 2


@pytest.mark.parametrize(
    ("component", "params", "text", "message"),
    [
        ("unknown_card", _concept_params(), "高壓教育不是兒童遊戲", "component"),
        (
            "concept_card",
            {**_concept_params(), "stage_text": "agent-only fake variable"},
            "高壓教育不是兒童遊戲",
            "variables|render_params",
        ),
        ("concept_card", _concept_params(), "完全不同的文字", "on_screen_text|text"),
        (
            "concept_card",
            _concept_params(title="高壓教育\n不是兒童遊戲"),
            "高壓教育\n不是兒童遊戲",
            "newline|single-line",
        ),
    ],
)
def test_wrong_component_variables_text_or_newline_fail_before_render(
    tmp_path: Path,
    component: str,
    params: dict[str, object],
    text: str,
    message: str,
) -> None:
    root = tmp_path / "episode"
    root.mkdir()
    called = False

    def runner(_argv, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("invalid spec reached the renderer")

    with pytest.raises(TrustedRenderError, match=message):
        candidate_renderer._render_hyperframes_candidate_for_test(
            root,
            cut_id=CUT_ID,
            revision_id=REVISION_ID,
            candidate_id="hf-candidate-01",
            component=component,
            render_params=params,
            expected_on_screen_text=text,
            runtime_command=("hyperframes-test",),
            runner=runner,
        )
    assert not called


@pytest.mark.parametrize("component", ["punch_card_wide", "transition_title"])
def test_card_line_limit_uses_display_width_for_narrow_latin(component: str) -> None:
    text = "晶體智慧\nCrystallized Intelligence"
    params: dict[str, object]
    if component == "punch_card_wide":
        params = {"text": text, "tier": 1, "style": "orange", "show_sec": 2.0, "pos_y": 0.5}
    else:
        params = {"kicker": "概念", "title": text, "style": "paper", "show_sec": 2.0}

    canonical, _variables = candidate_renderer._closed_render_params(component, params, text)

    assert canonical["text" if component == "punch_card_wide" else "title"] == text


@pytest.mark.parametrize("component", ["punch_card_wide", "transition_title"])
def test_card_line_limit_still_rejects_more_than_sixteen_full_width_characters(
    component: str,
) -> None:
    text = "一二三四五六七八九十一二三四五六七"
    params: dict[str, object]
    if component == "punch_card_wide":
        params = {"text": text, "tier": 1, "style": "orange", "show_sec": 2.0, "pos_y": 0.5}
    else:
        params = {"kicker": "概念", "title": text, "style": "paper", "show_sec": 2.0}

    with pytest.raises(TrustedRenderError, match="too long"):
        candidate_renderer._closed_render_params(component, params, text)


def test_expected_hydrated_spec_normalizes_crlf_and_rebinds_hash() -> None:
    text = "與其教故事\r\n不如動手做"
    render_params = {
        "text": text,
        "tier": 1,
        "style": "orange",
        "show_sec": 3.4,
        "pos_y": 0.7,
    }
    raw_spec_hash = candidate_renderer._content_hash(
        {"component": "punch_card_wide", "render_params": render_params}
    )

    expected = candidate_renderer._expected_hydrated_hyperframes_spec(
        {
            "candidate_id": "hero-001",
            "visual_summary": "完整 Hero",
            "component": "punch_card_wide",
            "render_params": render_params,
            "render_spec_sha256": raw_spec_hash,
        },
        expected_on_screen_text=text,
    )

    assert expected["render_params"]["text"] == "與其教故事\n不如動手做"
    assert expected["render_spec_sha256"] == candidate_renderer._content_hash(
        {"component": "punch_card_wide", "render_params": expected["render_params"]}
    )
    assert expected["render_spec_sha256"] != raw_spec_hash


@pytest.mark.parametrize("tamper", ["media", "variables", "component", "rerender", "runtime"])
def test_fresh_verifier_rejects_tamper_and_rerender_drift(tmp_path: Path, tamper: str) -> None:
    root, result, _calls = _render(tmp_path)
    receipt_path = root / str(result["provenance"]["receipt"]["path"])
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    expected_media = copy.deepcopy(result["preview_media"])
    if tamper == "media":
        (root / str(result["preview_media"]["path"])).write_bytes(b"tampered media")
        receipt_identity = result["provenance"]["receipt"]
    elif tamper == "variables":
        (root / str(receipt["variables_file"]["path"])).write_text(
            '{"title":"tampered"}', encoding="utf-8"
        )
        receipt_identity = result["provenance"]["receipt"]
    elif tamper == "component":
        receipt["component_source"]["content_hash"] = "0" * 64
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        receipt_identity = _rehash_receipt(root, receipt_path)
    elif tamper == "rerender":
        media = root / str(result["preview_media"]["path"])
        media.write_bytes(media.read_bytes() + b"re-render drift")
        receipt["media"] = _identity(root, media)
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        receipt_identity = _rehash_receipt(root, receipt_path)
    else:
        dependency = (
            tmp_path / "runtimes" / "0.7.72" / "node_modules" / "renderer-transitive" / "index.js"
        )
        dependency.write_text("export const renderer = 'tampered';", encoding="utf-8")
        receipt_identity = result["provenance"]["receipt"]

    with pytest.raises(
        TrustedRenderError, match="drift|hash|component|media|variables|runtime|identity"
    ):
        candidate_renderer._verify_hyperframes_test_receipt(
            root,
            receipt_identity=receipt_identity,
            expected_cut_id=CUT_ID,
            expected_revision_id=REVISION_ID,
            expected_candidate_id="hf-candidate-01",
            expected_component="concept_card",
            expected_render_params=_concept_params(),
            expected_on_screen_text="高壓教育不是兒童遊戲",
            expected_media=expected_media,
            runtime_root=tmp_path / "runtimes",
        )


def test_hydrator_rejects_worker_hyperframes_media_before_render(tmp_path: Path) -> None:
    root = tmp_path / "episode"
    root.mkdir()
    proposal = root / "raw-dp-proposal.json"
    output = root / "trusted-dp-proposal.json"
    proposal.write_text(
        json.dumps(
            {
                "implementations": [
                    {
                        "event_id": "event-01",
                        "mode": "hyperframes",
                        "on_screen_text": "高壓教育不是兒童遊戲",
                        "candidates": [
                            {
                                "candidate_id": "hf-candidate-01",
                                "visual_summary": "worker 偽造的移動卡",
                                "component": "concept_card",
                                "render_params": _concept_params(),
                                "render_spec_sha256": "0" * 64,
                                "preview_media": {
                                    "path": "worker/fake.mov",
                                    "bytes": 123,
                                    "sha256": "0" * 64,
                                },
                                "provenance": {"worker": "self-reported"},
                            }
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(TrustedRenderError, match="spec-only schema"):
        hydrate_dp_hyperframes_proposal(
            root,
            cut_id=CUT_ID,
            revision_id=REVISION_ID,
            attempt=1,
            proposal_path=proposal,
            output_path=output,
        )
    assert not output.exists()
