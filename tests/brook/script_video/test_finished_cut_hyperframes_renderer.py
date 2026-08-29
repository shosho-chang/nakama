from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from agents.brook.script_video.finished_cut_production._hyperframes_renderer import (
    FfprobeGeneratedMediaProbe,
    HyperFramesBrowserRenderer,
    HyperFramesRenderError,
    PinnedHyperFramesRuntime,
    RenderProcessResult,
    SubprocessRenderProcessRunner,
)
from agents.brook.script_video.finished_cut_production._long_visual_renderer import (
    LongVisualRenderer,
    LongVisualRenderError,
    LongVisualRenderRequest,
)
from agents.brook.script_video.finished_cut_production._visual_assets import (
    FaceSafePlacement,
    FfmpegPersonInsetCompositor,
    PersonInsetCompositeRequest,
    build_long_visual_media_adapters,
)


class _ProcessRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], Path | None, float]] = []
        self.html_documents: list[str] = []
        self.probe_codec = "prores"
        self.probe_pixel_format = "yuva444p12le"
        self.probe_duration = 3.0

    def run(
        self,
        arguments: tuple[str, ...],
        *,
        cwd: Path | None,
        timeout_sec: float,
    ) -> RenderProcessResult:
        self.calls.append((arguments, cwd, timeout_sec))
        if len(arguments) > 1 and Path(arguments[1]).name == "hyperframes.mjs":
            assert cwd is not None
            self.html_documents.append(
                (cwd / "compositions" / "long_visual.html").read_text(encoding="utf-8")
            )
            Path(arguments[arguments.index("-o") + 1]).write_bytes(b"browser frames")
            return RenderProcessResult(returncode=0, stdout="", stderr="")
        if arguments[0] == "ffmpeg":
            Path(arguments[-1]).write_bytes(f"encoded:{arguments[-1]}".encode("utf-8"))
            return RenderProcessResult(returncode=0, stdout="", stderr="")
        if arguments[0] == "ffprobe":
            return RenderProcessResult(
                returncode=0,
                stdout=json.dumps(
                    {
                        "streams": [
                            {
                                "codec_name": self.probe_codec,
                                "pix_fmt": self.probe_pixel_format,
                                "width": 1920,
                                "height": 1080,
                            }
                        ],
                        "format": {"duration": f"{self.probe_duration:.6f}"},
                    }
                ),
                stderr="",
            )
        raise AssertionError(f"unexpected process: {arguments[0]}")


class _FailingProcessRunner(_ProcessRunner):
    def __init__(self, failure: str) -> None:
        super().__init__()
        self.failure = failure
        if failure == "probe_mismatch":
            self.probe_pixel_format = "yuv420p"

    def run(
        self,
        arguments: tuple[str, ...],
        *,
        cwd: Path | None,
        timeout_sec: float,
    ) -> RenderProcessResult:
        if (
            len(arguments) > 1
            and Path(arguments[1]).name == "hyperframes.mjs"
            and self.failure in {"timeout", "nonzero", "missing_output"}
        ):
            self.calls.append((arguments, cwd, timeout_sec))
            if self.failure == "timeout":
                raise TimeoutError("injected render timeout")
            if self.failure == "nonzero":
                return RenderProcessResult(returncode=9, stdout="", stderr="render failed")
            return RenderProcessResult(returncode=0, stdout="", stderr="")
        return super().run(arguments, cwd=cwd, timeout_sec=timeout_sec)


class _AdaptiveProcessRunner(_ProcessRunner):
    def run(
        self,
        arguments: tuple[str, ...],
        *,
        cwd: Path | None,
        timeout_sec: float,
    ) -> RenderProcessResult:
        if arguments[0] == "ffprobe" and Path(arguments[-1]).suffix == ".mp4":
            self.probe_codec = "h264"
            self.probe_pixel_format = "yuv420p"
        elif arguments[0] == "ffprobe":
            self.probe_codec = "prores"
            self.probe_pixel_format = "yuva444p12le"
        return super().run(arguments, cwd=cwd, timeout_sec=timeout_sec)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _identity(root: Path, path: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _content_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _runtime_tree_identity(root: Path) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    total_bytes = 0
    for path in sorted(root.rglob("*"), key=lambda value: value.as_posix()):
        if not path.is_file():
            continue
        size = path.stat().st_size
        total_bytes += size
        rows.append(
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": size,
                "sha256": _sha256(path),
            }
        )
    return {"files": len(rows), "bytes": total_bytes, "content_hash": _content_hash(rows)}


def _pinned_runtime(tmp_path: Path) -> PinnedHyperFramesRuntime:
    runtime = tmp_path / "runtime" / "0.7.72"
    package_root = runtime / "node_modules" / "hyperframes"
    cli = package_root / "bin" / "hyperframes.mjs"
    cli.parent.mkdir(parents=True)
    package_json = runtime / "package.json"
    package_lock = runtime / "package-lock.json"
    manifest = package_root / "package.json"
    package_json.write_text('{"dependencies":{"hyperframes":"0.7.72"}}\n', encoding="utf-8")
    package_lock.write_text('{"lockfileVersion":3}\n', encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "name": "hyperframes",
                "version": "0.7.72",
                "bin": {"hyperframes": "./bin/hyperframes.mjs"},
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    cli.write_text("import '../dist/cli.js';\n", encoding="utf-8")
    receipt = {
        "contract": "podcast-highlight-hyperframes-runtime-acquisition-v1",
        "package": "hyperframes@0.7.72",
        "npm_argv": [
            "npm",
            "install",
            "--prefix",
            "<runtime>",
            "--no-audit",
            "--no-fund",
            "--ignore-scripts=false",
        ],
        "package_json": _identity(runtime, package_json),
        "package_lock": _identity(runtime, package_lock),
        "package_manifest": _identity(runtime, manifest),
        "cli": _identity(runtime, cli),
        "node_modules_tree": _runtime_tree_identity(runtime / "node_modules"),
    }
    receipt_hash = _content_hash(receipt)
    (runtime / "NPM-ACQUISITION.json").write_text(
        json.dumps({**receipt, "content_hash": receipt_hash}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    node = tmp_path / "node.exe"
    node.write_bytes(b"pinned node fixture")
    return PinnedHyperFramesRuntime.verify(
        runtime_root=runtime,
        node_executable=node,
        expected_receipt_content_hash=receipt_hash,
        expected_node_identity=(node.stat().st_size, _sha256(node)),
    )


def test_alpha_hero_renders_as_prores_4444_mov_with_exact_process_contract(
    tmp_path: Path,
) -> None:
    runner = _ProcessRunner()
    workspaces = tmp_path / "workspaces"
    browser = HyperFramesBrowserRenderer(
        workspace_root=workspaces,
        output_root=tmp_path / "renders",
        runtime=_pinned_runtime(tmp_path),
        runner=runner,
        probe=FfprobeGeneratedMediaProbe(runner=runner),
    )
    renderer = LongVisualRenderer(browser=browser)

    output = renderer.render(
        LongVisualRenderRequest(
            recipe_identity="recipe:hero:prores-current",
            event_id="event-hero",
            role="hero_title",
            display="真正的選擇",
            duration_sec=3.0,
            target_width=1920,
            target_height=1080,
            layout_identity="hero_title:v1",
        )
    )

    assert output.media.path.suffix == ".mov"
    assert output.media.codec_name == "prores"
    assert output.media.pixel_format == "yuva444p12le"
    assert output.media.has_alpha is True
    hyperframes, ffmpeg, ffprobe = runner.calls
    assert hyperframes[0] == (
        str((tmp_path / "node.exe").resolve()),
        str(
            (
                tmp_path
                / "runtime"
                / "0.7.72"
                / "node_modules"
                / "hyperframes"
                / "bin"
                / "hyperframes.mjs"
            ).resolve()
        ),
        "render",
        ".",
        "-c",
        "compositions/long_visual.html",
        "-o",
        str(hyperframes[1] / "hyperframes.mov"),
        "--format",
        "mov",
        "-q",
        "standard",
        "--quiet",
        "--no-browser-gpu",
    )
    # The browser now renders the recipe's full duration, so ffmpeg only
    # transcodes: nothing is padded out of a truncated animation any more.
    assert ffmpeg[0] == (
        "ffmpeg",
        "-y",
        "-i",
        str(hyperframes[1] / "hyperframes.mov"),
        "-an",
        "-t",
        "3.000000",
        "-c:v",
        "prores_ks",
        "-profile:v",
        "4",
        "-pix_fmt",
        "yuva444p12le",
        "-movflags",
        "+faststart",
        str(hyperframes[1] / "encoded.mov"),
    )
    assert ffprobe[0] == (
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name,pix_fmt,width,height:format=duration",
        "-of",
        "json",
        str(hyperframes[1] / "encoded.mov"),
    )
    assert (hyperframes[2], ffmpeg[2], ffprobe[2]) == (90.0, 60.0, 30.0)
    assert 'data-composition-id="long_visual"' in runner.html_documents[0]
    assert 'data-width="1920"' in runner.html_documents[0]
    assert 'data-height="1080"' in runner.html_documents[0]
    assert 'data-duration="3.000000"' in runner.html_documents[0]
    # These cards are driven by CSS animations and register no GSAP timeline.
    # Without this marker HyperFrames waits out its full 45s sub-composition
    # readiness timeout per worker, which cost ~90s of dead wait per card.
    assert "data-no-timeline" in runner.html_documents[0]
    assert list(workspaces.iterdir()) == []


def test_pinned_runtime_rejects_malformed_acquisition_argv_as_contract_error(
    tmp_path: Path,
) -> None:
    verified = _pinned_runtime(tmp_path)
    receipt_path = verified.root / "NPM-ACQUISITION.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["npm_argv"] = None
    unhashed = {key: value for key, value in receipt.items() if key != "content_hash"}
    receipt["content_hash"] = _content_hash(unhashed)
    receipt_path.write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(HyperFramesRenderError, match="acquisition identity drift"):
        PinnedHyperFramesRuntime.verify(
            runtime_root=verified.root,
            node_executable=verified.node_executable,
            expected_receipt_content_hash=receipt["content_hash"],
            expected_node_identity=(
                verified.node_executable.stat().st_size,
                _sha256(verified.node_executable),
            ),
        )


def test_pinned_runtime_rejects_missing_or_wrong_version_directory(tmp_path: Path) -> None:
    verified = _pinned_runtime(tmp_path)
    node_identity = (
        verified.node_executable.stat().st_size,
        _sha256(verified.node_executable),
    )

    with pytest.raises(HyperFramesRenderError, match="runtime is missing"):
        PinnedHyperFramesRuntime.verify(
            runtime_root=tmp_path / "missing" / "0.7.72",
            node_executable=verified.node_executable,
            expected_node_identity=node_identity,
        )

    wrong_version = verified.root.with_name("0.7.73")
    verified.root.rename(wrong_version)
    with pytest.raises(HyperFramesRenderError, match="version is invalid"):
        PinnedHyperFramesRuntime.verify(
            runtime_root=wrong_version,
            node_executable=verified.node_executable,
            expected_receipt_content_hash=verified.receipt_content_hash,
            expected_node_identity=node_identity,
        )


def test_pinned_runtime_rejects_cli_path_that_escapes_package_root(tmp_path: Path) -> None:
    verified = _pinned_runtime(tmp_path)
    manifest_path = verified.root / "node_modules" / "hyperframes" / "package.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["bin"] = {"hyperframes": str(verified.node_executable)}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(HyperFramesRenderError, match="escaped package root"):
        PinnedHyperFramesRuntime.verify(
            runtime_root=verified.root,
            node_executable=verified.node_executable,
            expected_receipt_content_hash=verified.receipt_content_hash,
            expected_node_identity=(
                verified.node_executable.stat().st_size,
                _sha256(verified.node_executable),
            ),
        )


def test_full_frame_chapter_uses_h264_mp4_without_alpha(tmp_path: Path) -> None:
    runner = _ProcessRunner()
    runner.probe_codec = "h264"
    runner.probe_pixel_format = "yuv420p"
    browser = HyperFramesBrowserRenderer(
        workspace_root=tmp_path / "workspaces",
        output_root=tmp_path / "renders",
        runtime=_pinned_runtime(tmp_path),
        runner=runner,
        probe=FfprobeGeneratedMediaProbe(runner=runner),
    )

    output = LongVisualRenderer(browser=browser).render(
        LongVisualRenderRequest(
            recipe_identity="recipe:chapter:h264-current",
            event_id="event-chapter",
            role="chapter",
            display="第一章｜重新定義選擇",
            duration_sec=3.0,
            target_width=1920,
            target_height=1080,
            layout_identity="fullscreen_transition:v4",
        )
    )

    assert output.media.path.suffix == ".mp4"
    assert output.media.codec_name == "h264"
    assert output.media.pixel_format == "yuv420p"
    assert output.media.has_alpha is False
    hyperframes, ffmpeg = runner.calls[0][0], runner.calls[1][0]
    # An opaque full-frame card needs no alpha, so the browser writes H.264
    # directly.  The ProRes 4444 intermediate this replaces cost ~118 MB for
    # three seconds and was transcoded straight to H.264 anyway.
    assert hyperframes[hyperframes.index("--format") + 1] == "mp4"
    assert hyperframes[hyperframes.index("-o") + 1].endswith("hyperframes.mp4")
    assert ffmpeg[ffmpeg.index("-c:v") + 1] == "libx264"
    assert ffmpeg[ffmpeg.index("-pix_fmt") + 1] == "yuv420p"
    # The card paints its own background and is rendered at full duration, so
    # there is nothing left to composite or pad.
    assert "-filter_complex" not in ffmpeg
    assert "-vf" not in ffmpeg
    assert "-profile:v" not in ffmpeg
    assert "data-no-timeline" in runner.html_documents[0]


@pytest.mark.parametrize("failure", ["timeout", "nonzero", "missing_output", "probe_mismatch"])
def test_renderer_failures_publish_nothing_and_cleanup_unique_workspace(
    tmp_path: Path,
    failure: str,
) -> None:
    runner = _FailingProcessRunner(failure)
    workspaces = tmp_path / "workspaces"
    output_root = tmp_path / "renders"
    renderer = LongVisualRenderer(
        browser=HyperFramesBrowserRenderer(
            workspace_root=workspaces,
            output_root=output_root,
            runtime=_pinned_runtime(tmp_path),
            runner=runner,
            probe=FfprobeGeneratedMediaProbe(runner=runner),
        )
    )

    with pytest.raises(LongVisualRenderError, match="browser rendering failed"):
        renderer.render(
            LongVisualRenderRequest(
                recipe_identity=f"recipe:hero:{failure}",
                event_id="event-hero",
                role="hero_title",
                display="真正的選擇",
                duration_sec=3.0,
                target_width=1920,
                target_height=1080,
                layout_identity="hero_title:v1",
            )
        )

    assert list(workspaces.iterdir()) == []
    assert not output_root.exists() or list(output_root.iterdir()) == []


def test_every_generated_long_visual_role_uses_the_resolve_media_contract(
    tmp_path: Path,
) -> None:
    runner = _AdaptiveProcessRunner()
    renderer = LongVisualRenderer(
        browser=HyperFramesBrowserRenderer(
            workspace_root=tmp_path / "workspaces",
            output_root=tmp_path / "renders",
            runtime=_pinned_runtime(tmp_path),
            runner=runner,
            probe=FfprobeGeneratedMediaProbe(runner=runner),
        )
    )
    roles = (
        ("chapter", "fullscreen_transition:v4"),
        ("hero_title", "hero_title:v1"),
        ("identity_card", "identity_card:v1"),
        ("visual_effect", "visual_effect:v1"),
    )

    outputs = tuple(
        renderer.render(
            LongVisualRenderRequest(
                recipe_identity=f"recipe:{role}:resolve-current",
                event_id=f"event-{role}",
                role=role,
                display=f"顯示文字 {role}",
                duration_sec=3.0,
                target_width=1920,
                target_height=1080,
                layout_identity=layout_identity,
            )
        )
        for role, layout_identity in roles
    )

    assert tuple(
        (output.media.path.suffix, output.media.codec_name, output.media.pixel_format)
        for output in outputs
    ) == (
        (".mp4", "h264", "yuv420p"),
        (".mov", "prores", "yuva444p12le"),
        (".mov", "prores", "yuva444p12le"),
        (".mov", "prores", "yuva444p12le"),
    )
    render_workspaces = [
        cwd
        for arguments, cwd, _timeout in runner.calls
        if len(arguments) > 1 and Path(arguments[1]).name == "hyperframes.mjs"
    ]
    assert len(render_workspaces) == len(set(render_workspaces)) == len(roles)
    assert len({output.media.path for output in outputs}) == len(roles)
    assert list((tmp_path / "workspaces").iterdir()) == []


def test_private_factory_wires_title_and_person_inset_to_one_probed_process_seam(
    tmp_path: Path,
) -> None:
    runner = _AdaptiveProcessRunner()
    adapters = build_long_visual_media_adapters(
        workspace_root=tmp_path / "workspaces",
        render_output_root=tmp_path / "renders",
        inset_output_root=tmp_path / "insets",
        runtime=_pinned_runtime(tmp_path),
        runner=runner,
    )
    title = adapters.title_renderer.render(
        LongVisualRenderRequest(
            recipe_identity="recipe:hero:factory",
            event_id="event-hero",
            role="hero_title",
            display="真正的選擇",
            duration_sec=3.0,
            target_width=1920,
            target_height=1080,
            layout_identity="hero_title:v1",
        )
    )
    portrait = tmp_path / "portrait.png"
    portrait.write_bytes(b"portrait")
    inset = adapters.person_inset_compositor.composite(
        PersonInsetCompositeRequest(
            render_identity="recipe:person:factory",
            source_path=portrait,
            target_width=1920,
            target_height=1080,
            duration_sec=3.0,
            placement=FaceSafePlacement(
                x_ratio=0.75,
                y_ratio=0.24,
                width_ratio=0.20,
                height_ratio=0.42,
                avoids_faces=True,
            ),
        )
    )

    assert title.media.path.suffix == ".mov"
    assert inset.path.suffix == ".mov"
    assert inset.has_alpha is True
    assert [call[0][0] for call in runner.calls].count("ffprobe") == 2


@pytest.mark.skipif(
    os.environ.get("NAKAMA_RUN_LOCAL_RENDER_SMOKE") != "1",
    reason="explicit local HyperFrames/ffmpeg smoke",
)
def test_real_pinned_hyperframes_and_ffmpeg_render_probe_in_temp_workspace(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    node = shutil.which("node.exe") or shutil.which("node")
    assert node is not None
    runner = SubprocessRenderProcessRunner()
    runtime = PinnedHyperFramesRuntime.verify(
        runtime_root=(repo_root / "video" / "node_modules" / ".nakama-hyperframes" / "0.7.72"),
        node_executable=node,
    )
    renderer = LongVisualRenderer(
        browser=HyperFramesBrowserRenderer(
            workspace_root=tmp_path / "workspaces",
            output_root=tmp_path / "renders",
            runtime=runtime,
            runner=runner,
            probe=FfprobeGeneratedMediaProbe(runner=runner),
        )
    )

    rendered = renderer.render(
        LongVisualRenderRequest(
            recipe_identity="recipe:chapter:real-local-smoke",
            event_id="event-chapter-smoke",
            role="chapter",
            display="AI 時代的工作機會與資產，將走向 K 型發展",
            duration_sec=3.0,
            target_width=1920,
            target_height=1080,
            layout_identity="fullscreen_transition:v4",
        )
    )

    assert rendered.media.path.is_file()
    assert rendered.media.codec_name == "h264"
    assert rendered.media.pixel_format == "yuv420p"
    assert (rendered.media.width, rendered.media.height) == (1920, 1080)


@pytest.mark.skipif(
    os.environ.get("NAKAMA_RUN_LOCAL_RENDER_SMOKE") != "1",
    reason="explicit local HyperFrames/ffmpeg smoke",
)
def test_real_pinned_hyperframes_renders_prores_4444_alpha_hero(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    node = shutil.which("node.exe") or shutil.which("node")
    assert node is not None
    runner = SubprocessRenderProcessRunner()
    runtime = PinnedHyperFramesRuntime.verify(
        runtime_root=(repo_root / "video" / "node_modules" / ".nakama-hyperframes" / "0.7.72"),
        node_executable=node,
    )
    renderer = LongVisualRenderer(
        browser=HyperFramesBrowserRenderer(
            workspace_root=tmp_path / "workspaces",
            output_root=tmp_path / "renders",
            runtime=runtime,
            runner=runner,
            probe=FfprobeGeneratedMediaProbe(runner=runner),
        )
    )

    rendered = renderer.render(
        LongVisualRenderRequest(
            recipe_identity="recipe:hero:real-local-alpha-smoke",
            event_id="event-hero-smoke",
            role="hero_title",
            display="真正的選擇",
            duration_sec=0.5,
            target_width=1920,
            target_height=1080,
            layout_identity="hero_title:v1",
        )
    )

    assert rendered.media.path.suffix == ".mov"
    assert rendered.media.codec_name == "prores"
    assert rendered.media.pixel_format == "yuva444p12le"
    assert rendered.media.has_alpha is True


@pytest.mark.skipif(
    os.environ.get("NAKAMA_RUN_LOCAL_RENDER_SMOKE") != "1",
    reason="explicit local HyperFrames/ffmpeg smoke",
)
def test_real_ffmpeg_person_inset_has_alpha_animation_and_cleans_workspace(
    tmp_path: Path,
) -> None:
    from PIL import Image, ImageDraw

    portrait = tmp_path / "synthetic-person.png"
    canvas = Image.new("RGBA", (320, 480), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.ellipse((96, 24, 224, 152), fill=(238, 196, 162, 255))
    draw.rounded_rectangle((56, 142, 264, 476), radius=56, fill=(41, 64, 87, 255))
    canvas.save(portrait)
    runner = SubprocessRenderProcessRunner()
    probe = FfprobeGeneratedMediaProbe(runner=runner)
    output_root = tmp_path / "person-insets"
    result = FfmpegPersonInsetCompositor(
        output_root=output_root,
        runner=runner,
        probe=probe,
    ).composite(
        PersonInsetCompositeRequest(
            render_identity="recipe:person:real-local-alpha-smoke",
            source_path=portrait,
            target_width=1920,
            target_height=1080,
            duration_sec=0.5,
            placement=FaceSafePlacement(
                x_ratio=0.72,
                y_ratio=0.12,
                width_ratio=0.20,
                height_ratio=0.50,
                avoids_faces=True,
            ),
        )
    )

    inspected = probe.inspect(result.path)
    assert (inspected.codec_name, inspected.pixel_format, inspected.has_alpha) == (
        "prores",
        "yuva444p12le",
        True,
    )
    assert result.animated is True
    assert not any(path.name.startswith(".person-inset-") for path in output_root.iterdir())
