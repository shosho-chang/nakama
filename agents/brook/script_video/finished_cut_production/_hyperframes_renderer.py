"""Private HyperFrames/ffmpeg Adapter for Resolve-compatible Long visual media."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ._long_visual_renderer import (
    BrowserRenderResult,
    LongVisualRecipe,
)

_HYPERFRAMES_VERSION = "0.7.72"
_HYPERFRAMES_PACKAGE = "hyperframes" + "@" + _HYPERFRAMES_VERSION
_RUNTIME_CONTRACT = "podcast-highlight-hyperframes-runtime-acquisition-v1"
_PINNED_RECEIPT_CONTENT_HASH = "59037c5dfd0c6769e2f6c43e5f31894913d7b6a3a7d5847d265da1a5a5a3938d"
_PINNED_NODE_IDENTITY = (
    91_694_408,
    "3331e1ffe19874215472217c5e94f5a0c6d8e18c4ac7111d3937aa0ad5e9b4a5",
)
_ACQUISITION_ARGV = (
    "npm",
    "install",
    "--prefix",
    "<runtime>",
    "--no-audit",
    "--no-fund",
    "--ignore-scripts=false",
)
_PROBE_ENTRIES = "stream=codec_name,pix_fmt,width,height:format=duration"


class HyperFramesRenderError(ValueError):
    """A generated visual did not satisfy the Resolve media contract."""


@dataclass(frozen=True, slots=True)
class PinnedHyperFramesRuntime:
    """Verified local execution authority for one exact HyperFrames acquisition."""

    root: Path
    node_executable: Path
    cli: Path
    receipt_content_hash: str

    @classmethod
    def verify(
        cls,
        *,
        runtime_root: str | Path,
        node_executable: str | Path,
        expected_receipt_content_hash: str = _PINNED_RECEIPT_CONTENT_HASH,
        expected_node_identity: tuple[int, str] = _PINNED_NODE_IDENTITY,
    ) -> PinnedHyperFramesRuntime:
        configured_root = Path(runtime_root)
        if configured_root.is_symlink():
            raise HyperFramesRenderError("HyperFrames runtime root cannot be a symlink")
        try:
            root = configured_root.resolve(strict=True)
        except OSError as exc:
            raise HyperFramesRenderError("pinned HyperFrames runtime is missing") from exc
        if not root.is_dir() or root.name != _HYPERFRAMES_VERSION:
            raise HyperFramesRenderError("pinned HyperFrames runtime version is invalid")

        configured_node = Path(node_executable)
        if configured_node.is_symlink():
            raise HyperFramesRenderError("pinned Node executable cannot be a symlink")
        try:
            node = configured_node.resolve(strict=True)
        except OSError as exc:
            raise HyperFramesRenderError("pinned Node executable is missing") from exc
        if not node.is_file() or _host_identity(node) != expected_node_identity:
            raise HyperFramesRenderError("pinned Node executable identity drift")

        package_json = root / "package.json"
        package_lock = root / "package-lock.json"
        manifest_path = root / "node_modules" / "hyperframes" / "package.json"
        receipt_path = root / "NPM-ACQUISITION.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HyperFramesRenderError("pinned HyperFrames receipt is unreadable") from exc
        if not isinstance(manifest, dict):
            raise HyperFramesRenderError("pinned HyperFrames manifest is invalid")
        raw_bin = manifest.get("bin")
        relative_cli = raw_bin.get("hyperframes") if isinstance(raw_bin, dict) else raw_bin
        if (
            manifest.get("name") != "hyperframes"
            or manifest.get("version") != _HYPERFRAMES_VERSION
            or not isinstance(relative_cli, str)
            or not relative_cli
        ):
            raise HyperFramesRenderError("pinned HyperFrames package identity drift")
        cli_candidate = manifest_path.parent / relative_cli
        if cli_candidate.is_symlink():
            raise HyperFramesRenderError("pinned HyperFrames CLI cannot be a symlink")
        try:
            cli = cli_candidate.resolve(strict=True)
        except OSError as exc:
            raise HyperFramesRenderError("pinned HyperFrames CLI is missing") from exc
        if not cli.is_file() or not cli.is_relative_to(manifest_path.parent.resolve()):
            raise HyperFramesRenderError("pinned HyperFrames CLI escaped package root")

        expected_keys = {
            "contract",
            "package",
            "npm_argv",
            "package_json",
            "package_lock",
            "package_manifest",
            "cli",
            "node_modules_tree",
            "content_hash",
        }
        if not isinstance(receipt, dict) or set(receipt) != expected_keys:
            raise HyperFramesRenderError("pinned HyperFrames receipt schema is invalid")
        claimed_hash = receipt["content_hash"]
        unhashed = {key: value for key, value in receipt.items() if key != "content_hash"}
        npm_argv = receipt["npm_argv"]
        if (
            claimed_hash != _content_hash(unhashed)
            or claimed_hash != expected_receipt_content_hash
            or receipt["contract"] != _RUNTIME_CONTRACT
            or receipt["package"] != _HYPERFRAMES_PACKAGE
            or not isinstance(npm_argv, list)
            or tuple(npm_argv) != _ACQUISITION_ARGV
            or receipt["package_json"] != _runtime_file_identity(root, package_json)
            or receipt["package_lock"] != _runtime_file_identity(root, package_lock)
            or receipt["package_manifest"] != _runtime_file_identity(root, manifest_path)
            or receipt["cli"] != _runtime_file_identity(root, cli)
            or receipt["node_modules_tree"] != _runtime_tree_identity(root / "node_modules")
        ):
            raise HyperFramesRenderError("pinned HyperFrames acquisition identity drift")
        return cls(
            root=root,
            node_executable=node,
            cli=cli,
            receipt_content_hash=claimed_hash,
        )


@dataclass(frozen=True, slots=True)
class RenderProcessResult:
    returncode: int
    stdout: str
    stderr: str


class RenderProcessRunner(Protocol):
    """Only external process seam used by HyperFrames, ffmpeg and ffprobe."""

    def run(
        self,
        arguments: tuple[str, ...],
        *,
        cwd: Path | None,
        timeout_sec: float,
    ) -> RenderProcessResult: ...


class SubprocessRenderProcessRunner:
    """Production shell-free process Adapter."""

    def run(
        self,
        arguments: tuple[str, ...],
        *,
        cwd: Path | None,
        timeout_sec: float,
    ) -> RenderProcessResult:
        try:
            process = subprocess.run(
                arguments,
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_sec,
                check=False,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HyperFramesRenderError("render process could not complete") from exc
        return RenderProcessResult(
            returncode=process.returncode,
            stdout=process.stdout,
            stderr=process.stderr,
        )


@dataclass(frozen=True, slots=True)
class GeneratedMediaProbe:
    codec_name: str
    pixel_format: str
    width: int
    height: int
    duration_sec: float
    has_alpha: bool


class GeneratedMediaProbePort(Protocol):
    """Read-only ffprobe seam for generated visual media."""

    def inspect(self, path: Path) -> GeneratedMediaProbe: ...


class FfprobeGeneratedMediaProbe:
    """Probe one video stream without trusting filename or renderer metadata."""

    def __init__(self, *, runner: RenderProcessRunner, executable: str = "ffprobe") -> None:
        self._runner = runner
        self._executable = executable

    def inspect(self, path: Path) -> GeneratedMediaProbe:
        media = Path(path).resolve(strict=True)
        result = self._runner.run(
            (
                self._executable,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                _PROBE_ENTRIES,
                "-of",
                "json",
                str(media),
            ),
            cwd=None,
            timeout_sec=30.0,
        )
        if result.returncode != 0:
            raise HyperFramesRenderError("ffprobe rejected generated visual media")
        try:
            payload = json.loads(result.stdout)
            streams = payload["streams"]
            stream = streams[0] if len(streams) == 1 else None
            format_row = payload["format"]
            codec_name = stream["codec_name"]
            pixel_format = stream["pix_fmt"]
            width = stream["width"]
            height = stream["height"]
            duration_sec = float(format_row["duration"])
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise HyperFramesRenderError("ffprobe output is not an exact video contract") from exc
        if (
            not isinstance(codec_name, str)
            or not codec_name
            or not isinstance(pixel_format, str)
            or not pixel_format
            or type(width) is not int
            or width <= 0
            or type(height) is not int
            or height <= 0
            or not math.isfinite(duration_sec)
            or duration_sec <= 0
        ):
            raise HyperFramesRenderError("ffprobe output contains invalid media facts")
        return GeneratedMediaProbe(
            codec_name=codec_name,
            pixel_format=pixel_format,
            width=width,
            height=height,
            duration_sec=duration_sec,
            has_alpha=pixel_format.startswith("yuva")
            or pixel_format in {"argb", "rgba", "abgr", "bgra"},
        )


class HyperFramesBrowserRenderer:
    """Hide workspace, render, transcode, probe and publication behind one Interface."""

    def __init__(
        self,
        *,
        workspace_root: str | Path,
        output_root: str | Path,
        runtime: PinnedHyperFramesRuntime,
        runner: RenderProcessRunner,
        probe: GeneratedMediaProbePort,
        ffmpeg_executable: str = "ffmpeg",
    ) -> None:
        self._workspace_root = Path(workspace_root).resolve()
        self._output_root = Path(output_root).resolve()
        self._runtime = runtime
        self._runner = runner
        self._probe = probe
        self._ffmpeg_executable = ffmpeg_executable

    def render(self, recipe: LongVisualRecipe) -> BrowserRenderResult:
        self._workspace_root.mkdir(parents=True, exist_ok=True)
        browser_html = self._browser_html(recipe)
        with tempfile.TemporaryDirectory(
            prefix="long-visual-",
            dir=self._workspace_root,
        ) as workspace_text:
            workspace = Path(workspace_text)
            composition_dir = workspace / "compositions"
            composition_dir.mkdir(parents=True)
            (composition_dir / "long_visual.html").write_text(
                browser_html,
                encoding="utf-8",
            )
            # Only an alpha-bearing overlay needs the ProRes 4444 intermediate.  A
            # full-frame card rendered to MOV costs ~118 MB for three seconds and is
            # transcoded straight to H.264 anyway.
            browser_format = "mov" if recipe.has_alpha else "mp4"
            browser_output = workspace / f"hyperframes.{browser_format}"
            encoded_output = workspace / f"encoded{recipe.extension}"
            self._run_checked(
                (
                    str(self._runtime.node_executable),
                    str(self._runtime.cli),
                    "render",
                    ".",
                    "-c",
                    "compositions/long_visual.html",
                    "-o",
                    str(browser_output),
                    "--format",
                    browser_format,
                    "-q",
                    "standard",
                    "--quiet",
                    "--no-browser-gpu",
                ),
                cwd=workspace,
                timeout_sec=max(90.0, recipe.duration_sec * 30.0),
                expected_output=browser_output,
                label="HyperFrames",
            )
            self._run_checked(
                self._encode_arguments(recipe, browser_output, encoded_output),
                cwd=None,
                timeout_sec=max(60.0, recipe.duration_sec * 20.0),
                expected_output=encoded_output,
                label="ffmpeg",
            )
            probe = self._probe.inspect(encoded_output)
            self._validate_probe(recipe, probe)
            durable_output = self._publish_output(encoded_output, workspace.name)
            return BrowserRenderResult(
                path=durable_output,
                width=probe.width,
                height=probe.height,
                duration_sec=probe.duration_sec,
                has_alpha=probe.has_alpha,
                codec_name=probe.codec_name,
                pixel_format=probe.pixel_format,
            )

    def _encode_arguments(
        self,
        recipe: LongVisualRecipe,
        source: Path,
        output: Path,
    ) -> tuple[str, ...]:
        # The browser now renders the recipe's full duration, so nothing is padded
        # or composited here: an alpha overlay is transcoded as-is, and an opaque
        # card already carries its own background from the composition.
        if recipe.has_alpha:
            codec_arguments = (
                "-c:v",
                "prores_ks",
                "-profile:v",
                "4",
                "-pix_fmt",
                "yuva444p12le",
            )
        else:
            codec_arguments = ("-c:v", "libx264", "-pix_fmt", "yuv420p")
        filter_arguments: tuple[str, ...] = ()
        return (
            self._ffmpeg_executable,
            "-y",
            "-i",
            str(source),
            "-an",
            *filter_arguments,
            "-t",
            f"{recipe.duration_sec:.6f}",
            *codec_arguments,
            "-movflags",
            "+faststart",
            str(output),
        )

    @staticmethod
    def _browser_html(recipe: LongVisualRecipe) -> str:
        marker = f'data-duration="{recipe.duration_sec:.6f}"'
        if recipe.html_document.count(marker) != 1:
            raise HyperFramesRenderError("visual HTML duration marker is invalid")
        return recipe.html_document

    def _run_checked(
        self,
        arguments: tuple[str, ...],
        *,
        cwd: Path | None,
        timeout_sec: float,
        expected_output: Path,
        label: str,
    ) -> None:
        try:
            result = self._runner.run(arguments, cwd=cwd, timeout_sec=timeout_sec)
        except Exception as exc:
            raise HyperFramesRenderError(f"{label} process failed") from exc
        if result.returncode != 0:
            raise HyperFramesRenderError(f"{label} process returned nonzero")
        if not expected_output.is_file():
            raise HyperFramesRenderError(f"{label} process did not create output")

    @staticmethod
    def _validate_probe(recipe: LongVisualRecipe, probe: GeneratedMediaProbe) -> None:
        if (
            probe.codec_name != recipe.codec_name
            or probe.pixel_format != recipe.pixel_format
            or probe.width != recipe.canvas_width
            or probe.height != recipe.canvas_height
            or probe.has_alpha is not recipe.has_alpha
            or not math.isclose(
                probe.duration_sec,
                recipe.duration_sec,
                rel_tol=0,
                abs_tol=(1 / 24) + 1e-6,
            )
        ):
            raise HyperFramesRenderError("generated media probe differs from visual recipe")

    def _publish_output(self, source: Path, workspace_name: str) -> Path:
        digest = _file_digest(source)
        self._output_root.mkdir(parents=True, exist_ok=True)
        target = self._output_root / f"{digest}{source.suffix.lower()}"
        if target.is_file() and _file_digest(target) == digest:
            return target
        staging = self._output_root / f".{target.name}.{workspace_name}.staging"
        try:
            shutil.copyfile(source, staging)
            if _file_digest(staging) != digest:
                raise HyperFramesRenderError("durable render copy differs from probed media")
            os.replace(staging, target)
        except OSError as exc:
            raise HyperFramesRenderError("durable render publication failed") from exc
        finally:
            staging.unlink(missing_ok=True)
        return target


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _host_identity(path: Path) -> tuple[int, str]:
    return path.stat().st_size, _file_digest(path)


def _runtime_file_identity(root: Path, path: Path) -> dict[str, object]:
    if path.is_symlink():
        raise HyperFramesRenderError("HyperFrames runtime file cannot be a symlink")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise HyperFramesRenderError("HyperFrames runtime file is missing") from exc
    if not resolved.is_file() or not resolved.is_relative_to(root):
        raise HyperFramesRenderError("HyperFrames runtime file escaped acquisition root")
    return {
        "path": resolved.relative_to(root).as_posix(),
        "bytes": resolved.stat().st_size,
        "sha256": _file_digest(resolved),
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
    if root.is_symlink() or not root.is_dir():
        raise HyperFramesRenderError("HyperFrames node_modules tree is missing or unsafe")
    resolved_root = root.resolve()
    rows: list[dict[str, object]] = []
    total_bytes = 0
    for path in sorted(root.rglob("*"), key=lambda value: value.as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            target = path.resolve()
            if not target.is_relative_to(resolved_root):
                raise HyperFramesRenderError("HyperFrames runtime symlink escaped acquisition root")
            rows.append({"path": relative, "symlink": os.readlink(path)})
        elif path.is_file():
            size = path.stat().st_size
            total_bytes += size
            rows.append({"path": relative, "bytes": size, "sha256": _file_digest(path)})
    if not rows:
        raise HyperFramesRenderError("HyperFrames node_modules tree is empty")
    return {
        "files": len(rows),
        "bytes": total_bytes,
        "content_hash": _content_hash(rows),
    }
