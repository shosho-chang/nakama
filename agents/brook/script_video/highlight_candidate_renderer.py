"""Trusted HyperFrames candidate renderer for Podcast Highlight visuals.

Creative DP workers propose a registered component and its closed variables.
Only this trusted boundary invokes HyperFrames and writes the canonical render
receipt outside the worker sandbox.  A playable file plus a self-written text
receipt is deliberately insufficient proof of a HyperFrames render.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

from agents.brook.script_video.highlight_broll import BrollContractError, probe_stock_video

HYPERFRAMES_RENDER_CONTRACT = "podcast-highlight-hyperframes-render-v1"
DP_HYDRATION_CONTRACT = "podcast-highlight-dp-hydration-v1"
_HYPERFRAMES_TEST_RENDER_CONTRACT = "podcast-highlight-hyperframes-test-render-v1"
_HYPERFRAMES_EXECUTION_CONTRACT = "podcast-highlight-hyperframes-execution-v1"
_HYPERFRAMES_TEST_EXECUTION_CONTRACT = "podcast-highlight-hyperframes-test-execution-v1"
HYPERFRAMES_RUNTIME_CONTRACT = "podcast-highlight-hyperframes-runtime-acquisition-v1"
_HYPERFRAMES_TEST_RUNTIME_CONTRACT = (
    "podcast-highlight-hyperframes-test-runtime-acquisition-v1"
)
_PINNED_RUNTIME_IDENTITIES = {
    "hyperframes@0.7.72": {
        "package_manifest_sha256": (
            "d224e50bdc2bbd6fc4f50651f872c4186efd960bea2fbcff48673f95dc65f63b"
        ),
        "cli_sha256": "d566bbc6149f07f76d2cef62ab687401f5245557581c4043403278ce20d6deb0",
        "package_lock_sha256": "60075e9018db235ba0207e3bee5121c91b605ee63601f5addc2702bf860adcf6",
        "node_modules_content_hash": (
            "66d6cd9b2ca80e58b2d6914d43c0cff0011bc5d4f62721dbb6adbf377ff39b8a"
        ),
        "acquisition_content_hash": (
            "b76b325ee6ef7971ae0217c282a0eed16ed835150e21debc306a2ac9a3a61839"
        ),
    },
    "hyperframes@0.6.42": {
        "package_manifest_sha256": (
            "4ef366323219ecb4f6380a8ecfd0f804f0149bd69ffb9e9e9ca45cba587684ac"
        ),
        "cli_sha256": "3694b5f47fbf870886fc3b38ba490ce5d534c179338860cf8b38120ba2ab3368",
        "package_lock_sha256": "509c8c44079d24a5d600adb83f8864831a0b34b3146aa463fcfe270649d9e96a",
        "node_modules_content_hash": (
            "40d4da8adf151902868dc354b8e7096e9f8546536ed7aa1c5a1c95cb1dc4f908"
        ),
        "acquisition_content_hash": (
            "c7308807ccfb291bfed72abe9cf8a396cb58745ffb8a64fcb9c5652661ebd2bf"
        ),
    },
}
_PINNED_NODE_EXECUTABLES = frozenset(
    {
        (
            91_694_408,
            "3331e1ffe19874215472217c5e94f5a0c6d8e18c4ac7111d3937aa0ad5e9b4a5",
        )
    }
)
HYPERFRAMES_PROVIDER = "Nakama trusted HyperFrames renderer"
HYPERFRAMES_LICENSE = "Nakama original composition render"
TRUSTED_RENDER_DIR = "trusted-renders"
_TEST_RENDER_DIR = "test-renders"

_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SAFE_REVISION = re.compile(r"^r-[0-9a-f]{24}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DATA_IMAGE = re.compile(r"^data:image/(?P<type>png|jpeg|webp);base64,(?P<data>[A-Za-z0-9+/=]+)$")
_REPO_ROOT = Path(__file__).resolve().parents[3]
_COMPOSITION_ROOT = _REPO_ROOT / "video" / "compositions"
_IDENTITY_KEYS = {"path", "bytes", "sha256"}
_RECEIPT_KEYS = {
    "contract",
    "episode_id",
    "cut_id",
    "revision_id",
    "candidate_id",
    "engine",
    "execution",
    "render_spec",
    "component_source",
    "variables_file",
    "media",
    "content_hash",
}
_EXECUTION_KEYS = {
    "contract",
    "executable",
    "argv",
    "cwd",
    "exit_code",
    "stdout",
    "stderr",
    "content_hash",
}
_HYDRATION_KEYS = {
    "contract",
    "episode_id",
    "cut_id",
    "revision_id",
    "attempt",
    "raw_proposal",
    "hydrated_proposal",
    "asset_authority",
    "hyperframes",
    "content_hash",
}


class TrustedRenderError(RuntimeError):
    """A candidate render or its trusted receipt is invalid."""


@dataclass(frozen=True, slots=True)
class _Component:
    directory: str
    composition: str
    package: str
    logical_keys: frozenset[str]
    duration_seconds: float


_COMPONENTS = {
    "concept_card": _Component(
        "concept_card",
        "concept_card_wide.html",
        "hyperframes@0.7.72",
        frozenset(
            {
                "title",
                "left_label",
                "right_label",
                "left_src",
                "right_src",
                "show_sec",
                "pos_y",
            }
        ),
        6.0,
    ),
    "punch_card": _Component(
        "punch_card",
        "punch_card_wide.html",
        "hyperframes@0.7.72",
        frozenset({"text", "tier", "style", "show_sec", "pos_y"}),
        4.0,
    ),
    "punch_card_wide": _Component(
        "punch_card",
        "punch_card_wide.html",
        "hyperframes@0.7.72",
        frozenset({"text", "tier", "style", "show_sec", "pos_y"}),
        4.0,
    ),
    "quote_card": _Component(
        "quote_card",
        "quote_card.html",
        "hyperframes@0.6.42",
        frozenset({"quote", "attribution", "source"}),
        5.0,
    ),
    "transition_title": _Component(
        "transition_title",
        "transition_title_wide.html",
        "hyperframes@0.6.42",
        frozenset({"kicker", "title", "style", "show_sec"}),
        4.0,
    ),
    "book_cover": _Component(
        "book_cover",
        "book_cover.html",
        "hyperframes@0.6.42",
        frozenset({"cover_src", "title_zh", "title_en", "author"}),
        4.5,
    ),
}


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _content_hash(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_token(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_TOKEN.fullmatch(value):
        raise TrustedRenderError(f"{label} is unsafe")
    return value


def _safe_revision(value: object) -> str:
    if not isinstance(value, str) or not _SAFE_REVISION.fullmatch(value):
        raise TrustedRenderError("revision_id is unsafe")
    return value


def _strict_test_mode(value: object) -> bool:
    if type(value) is not bool:
        raise TrustedRenderError("test_mode must be an actual bool")
    return value


def _exact_dict(value: object, keys: set[str] | frozenset[str], label: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != set(keys):
        raise TrustedRenderError(f"{label} must use exact keys {sorted(keys)}")
    return dict(value)


def _episode_path(root: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise TrustedRenderError(f"{label}.path must be episode-relative")
    path = (root / value).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.is_symlink():
        raise TrustedRenderError(f"{label}.path escapes episode or is missing")
    return path


def _identity(root: Path, path: Path) -> dict[str, object]:
    resolved = path.resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file() or path.is_symlink():
        raise TrustedRenderError("trusted render file escaped episode root")
    return {
        "path": resolved.relative_to(root).as_posix(),
        "bytes": resolved.stat().st_size,
        "sha256": _sha256_file(resolved),
    }


def _verify_identity(root: Path, raw: object, label: str) -> tuple[Path, dict[str, object]]:
    identity = _exact_dict(raw, _IDENTITY_KEYS, label)
    path = _episode_path(root, identity["path"], label)
    size = identity["bytes"]
    digest = identity["sha256"]
    if (
        not isinstance(size, int)
        or isinstance(size, bool)
        or size <= 0
        or path.stat().st_size != size
    ):
        raise TrustedRenderError(f"{label} byte-size drift")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest) or _sha256_file(path) != digest:
        raise TrustedRenderError(f"{label} hash drift")
    return path, identity


def _read_json_identity_snapshot(
    root: Path, raw: object, label: str
) -> tuple[Path, dict[str, object], dict[str, object]]:
    """Read bytes once, then derive both identity verification and JSON from them."""

    identity = _exact_dict(raw, _IDENTITY_KEYS, label)
    path = _episode_path(root, identity["path"], label)
    try:
        encoded = path.read_bytes()
    except OSError as error:
        raise TrustedRenderError(f"{label} is unreadable") from error
    size = identity["bytes"]
    digest = identity["sha256"]
    if (
        not isinstance(size, int)
        or isinstance(size, bool)
        or size <= 0
        or len(encoded) != size
        or not isinstance(digest, str)
        or not _SHA256.fullmatch(digest)
        or hashlib.sha256(encoded).hexdigest() != digest
    ):
        raise TrustedRenderError(f"{label} identity drift")
    try:
        document = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TrustedRenderError(f"{label} is unreadable JSON") from error
    if not isinstance(document, dict):
        raise TrustedRenderError(f"{label} must be a JSON object")
    return path, identity, document


def _single_line(value: object, label: str, *, maximum: int = 80) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TrustedRenderError(f"{label} must be non-empty text")
    if "\n" in value or "\r" in value:
        raise TrustedRenderError(f"{label} must be single-line; newline is not allowed")
    if len(value) > maximum or any(ord(character) < 32 for character in value):
        raise TrustedRenderError(f"{label} text is invalid")
    return value


def _optional_single_line(value: object, label: str, *, maximum: int = 120) -> str:
    if not isinstance(value, str):
        raise TrustedRenderError(f"{label} must be text")
    if not value:
        return ""
    return _single_line(value, label, maximum=maximum)


def _line_display_units(value: str) -> int:
    return sum(
        0
        if unicodedata.combining(character)
        else 2
        if unicodedata.east_asian_width(character) in {"W", "F", "A"}
        else 1
        for character in value
    )


def _number(value: object, label: str, minimum: float, maximum: float) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TrustedRenderError(f"{label} must be numeric")
    normalized = float(value)
    if normalized < minimum or normalized > maximum:
        raise TrustedRenderError(f"{label} is outside [{minimum}, {maximum}]")
    return normalized


def _normalized_multiline(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise TrustedRenderError(f"{label} must be non-empty text")
    normalized = value.replace("\r\n", "\n")
    if "\r" in normalized:
        raise TrustedRenderError(f"{label} contains an unsupported newline")
    return normalized


def _image_data_uri(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise TrustedRenderError(f"{label} must be a data image URI")
    match = _DATA_IMAGE.fullmatch(value)
    if match is None:
        raise TrustedRenderError(f"{label} must be a closed data:image URI")
    try:
        decoded = base64.b64decode(match.group("data"), validate=True)
    except (binascii.Error, ValueError) as error:
        raise TrustedRenderError(f"{label} base64 payload is invalid") from error
    if not decoded or len(decoded) > 15 * 1024 * 1024:
        raise TrustedRenderError(f"{label} image size is invalid")
    return value


def _closed_render_params(
    component: str,
    raw: object,
    expected_on_screen_text: object,
) -> tuple[dict[str, object], dict[str, object]]:
    spec = _COMPONENTS.get(component)
    if spec is None:
        raise TrustedRenderError(
            f"component is not registered for trusted HyperFrames: {component}"
        )
    params = _exact_dict(raw, spec.logical_keys, "render_params variables")
    expected_text = _normalized_multiline(expected_on_screen_text, "expected_on_screen_text")
    variables: dict[str, object]
    if component == "concept_card":
        title = _single_line(params["title"], "render_params.title")
        if title != expected_text:
            raise TrustedRenderError("render_params title differs from exact on_screen_text")
        variables = {
            "title": title,
            "left_label": _single_line(
                params["left_label"], "render_params.left_label", maximum=30
            ),
            "right_label": _single_line(
                params["right_label"], "render_params.right_label", maximum=30
            ),
            "left_src": _image_data_uri(params["left_src"], "render_params.left_src"),
            "right_src": _image_data_uri(params["right_src"], "render_params.right_src"),
            "show_sec": _number(params["show_sec"], "render_params.show_sec", 1.0, 5.7),
            "pos_y": _number(params["pos_y"], "render_params.pos_y", 0.35, 0.82),
        }
        return variables, variables
    if component in {"punch_card", "punch_card_wide"}:
        text = _normalized_multiline(params["text"], "render_params.text")
        if text != expected_text:
            raise TrustedRenderError("render_params text differs from exact on_screen_text")
        lines = text.split("\n")
        if len(lines) not in {1, 2} or any(not line.strip() for line in lines):
            raise TrustedRenderError("punch_card text must preserve exactly one or two lines")
        if any(_line_display_units(line) > 32 for line in lines):
            raise TrustedRenderError("punch_card line is too long")
        tier = params["tier"]
        if not isinstance(tier, int) or isinstance(tier, bool) or tier not in {1, 2}:
            raise TrustedRenderError("render_params.tier must be 1 or 2")
        style = params["style"]
        if style not in {"orange", "paper", "ink"}:
            raise TrustedRenderError("render_params.style is not supported")
        canonical_params = {
            "text": text,
            "tier": tier,
            "style": style,
            "show_sec": _number(params["show_sec"], "render_params.show_sec", 0.6, 4.0),
            "pos_y": _number(params["pos_y"], "render_params.pos_y", 0.35, 0.82),
        }
        variables = {
            "line1": lines[0],
            "line2": lines[1] if len(lines) == 2 else "",
            "show_sec": canonical_params["show_sec"],
            "pos_y": canonical_params["pos_y"],
            "tier": tier,
            "style": style,
        }
        return canonical_params, variables
    if component == "quote_card":
        quote = _single_line(params["quote"], "render_params.quote", maximum=100)
        if quote != expected_text:
            raise TrustedRenderError("render_params quote differs from exact on_screen_text")
        variables = {
            "quote": quote,
            "attribution": _single_line(
                params["attribution"], "render_params.attribution", maximum=60
            ),
            "source": _optional_single_line(params["source"], "render_params.source"),
        }
        return variables, variables
    if component == "transition_title":
        title = _normalized_multiline(params["title"], "render_params.title")
        title_lines = title.split("\n")
        if len(title_lines) not in {1, 2} or any(not line.strip() for line in title_lines):
            raise TrustedRenderError(
                "transition_title must preserve exactly one or two non-empty lines"
            )
        if any(_line_display_units(line) > 32 for line in title_lines):
            raise TrustedRenderError("transition_title line is too long")
        if title != expected_text:
            raise TrustedRenderError("render_params title differs from exact on_screen_text")
        style = params["style"]
        if style not in {"paper", "paper_hand", "scrim"}:
            raise TrustedRenderError("render_params.style is not supported")
        variables = {
            "kicker": _single_line(params["kicker"], "render_params.kicker", maximum=12),
            "title": title,
            "style": style,
            "show_sec": _number(params["show_sec"], "render_params.show_sec", 1.2, 4.0),
        }
        return variables, variables
    if component == "book_cover":
        title = _single_line(params["title_zh"], "render_params.title_zh")
        if title != expected_text:
            raise TrustedRenderError("render_params title differs from exact on_screen_text")
        variables = {
            "cover_src": _image_data_uri(params["cover_src"], "render_params.cover_src"),
            "title_zh": title,
            "title_en": _optional_single_line(params["title_en"], "render_params.title_en"),
            "author": _optional_single_line(params["author"], "render_params.author", maximum=60),
        }
        return variables, variables
    raise TrustedRenderError(f"component has no closed variable adapter: {component}")


def _component_source(component: str) -> dict[str, object]:
    spec = _COMPONENTS[component]
    root = (_COMPOSITION_ROOT / spec.directory).resolve()
    composition = root / "compositions" / spec.composition
    if not root.is_dir() or not composition.is_file():
        raise TrustedRenderError(f"registered component source is missing: {component}")
    files: list[dict[str, object]] = []
    for path in sorted(root.rglob("*"), key=lambda value: value.as_posix()):
        if not path.is_file():
            continue
        relative_parts = path.relative_to(root).parts
        if any(part in {"node_modules", ".cache", ".git"} for part in relative_parts):
            continue
        if path.is_symlink():
            raise TrustedRenderError(f"component source contains a symlink: {path.name}")
        files.append(
            {
                "path": path.relative_to(_REPO_ROOT).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    if not files or composition.relative_to(_REPO_ROOT).as_posix() not in {
        row["path"] for row in files
    }:
        raise TrustedRenderError(
            f"component composition is absent from source identity: {component}"
        )
    package = json.loads((root / "package.json").read_text(encoding="utf-8"))
    render_script = str(package.get("scripts", {}).get("render", ""))
    if spec.package not in render_script:
        raise TrustedRenderError(f"component HyperFrames package version drift: {component}")
    return {"files": files, "content_hash": _content_hash(files)}


def _normalized_argv(
    spec: _Component,
    *,
    media_path: str,
    variables_path: str,
) -> list[str]:
    return [
        "hyperframes",
        "render",
        ".",
        "-c",
        f"compositions/{spec.composition}",
        "-o",
        media_path,
        "--format",
        "mov",
        "-q",
        "standard",
        "--quiet",
        "--no-browser-gpu",
        "--strict-variables",
        "--strict",
        "--no-best-effort",
        "--variables-file",
        variables_path,
    ]


def _frame_audit(
    media_path: Path, spec: _Component, params: Mapping[str, object]
) -> dict[str, object]:
    """Inspect visible alpha and motion before any candidate can reach audit/core."""

    ffmpeg = shutil.which("ffmpeg.exe") or shutil.which("ffmpeg")
    if not ffmpeg:
        raise TrustedRenderError("ffmpeg is unavailable for trusted render frame audit")
    raw_show_sec = params.get("show_sec", spec.duration_seconds)
    show_sec = min(float(raw_show_sec), spec.duration_seconds)
    sample_times = sorted({0.2, min(0.9, show_sec * 0.45), min(2.0, show_sec - 0.25)})
    rows: list[dict[str, object]] = []
    for sample_time in sample_times:
        if sample_time < 0:
            continue
        completed = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(media_path),
                "-ss",
                f"{sample_time:.3f}",
                "-frames:v",
                "1",
                "-vf",
                "scale=480:-2:flags=area,format=rgba",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgba",
                "-",
            ],
            capture_output=True,
            check=False,
        )
        rgba = completed.stdout
        if completed.returncode != 0 or not rgba or len(rgba) % 4:
            raise TrustedRenderError("trusted HyperFrames frame audit could not decode RGBA")
        alpha = rgba[3::4]
        coverage = sum(value > 16 for value in alpha) / len(alpha)
        rows.append(
            {
                "time_sec": round(sample_time, 3),
                "rgba_sha256": hashlib.sha256(rgba).hexdigest(),
                "alpha_coverage": round(coverage, 6),
            }
        )
    if not rows or max(float(row["alpha_coverage"]) for row in rows) < 0.002:
        raise TrustedRenderError("trusted HyperFrames output is visually blank/transparent")
    if len({str(row["rgba_sha256"]) for row in rows}) < 2:
        raise TrustedRenderError("trusted HyperFrames output has no inspectable component motion")
    return {"samples": rows, "content_hash": _content_hash(rows)}


def _actual_argv(
    runtime_command: Sequence[str],
    spec: _Component,
    *,
    media_path: Path,
    variables_path: Path,
) -> list[str]:
    normalized = _normalized_argv(
        spec,
        media_path=str(media_path),
        variables_path=str(variables_path),
    )
    return [*runtime_command, *normalized[1:]]


def _runtime_root(configured: str | Path | None = None) -> Path:
    if configured is not None:
        return Path(configured).resolve()
    configured = os.environ.get("PODCAST_HYPERFRAMES_RUNTIME_ROOT")
    if configured:
        return Path(configured).resolve()
    return (_REPO_ROOT / "video" / "node_modules" / ".nakama-hyperframes").resolve()


def _runtime_version(package: str) -> str:
    prefix = "hyperframes@"
    if not package.startswith(prefix):
        raise TrustedRenderError(f"unsupported HyperFrames package identity: {package}")
    version = package[len(prefix) :]
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise TrustedRenderError(f"unsafe HyperFrames version: {version}")
    return version


def _external_identity(base: Path, path: Path) -> dict[str, object]:
    resolved = path.resolve()
    if not resolved.is_relative_to(base) or not resolved.is_file() or path.is_symlink():
        raise TrustedRenderError("HyperFrames runtime file escaped its acquisition root")
    return {
        "path": resolved.relative_to(base).as_posix(),
        "bytes": resolved.stat().st_size,
        "sha256": _sha256_file(resolved),
    }


def _host_file_identity(path: Path) -> dict[str, object]:
    resolved = path.resolve()
    if not resolved.is_file() or path.is_symlink():
        raise TrustedRenderError("trusted renderer executable is missing or unsafe")
    return {
        "path": resolved.as_posix(),
        "bytes": resolved.stat().st_size,
        "sha256": _sha256_file(resolved),
    }


def _process_text_identity(value: object) -> dict[str, object]:
    text = str(value or "")
    encoded = text.encode("utf-8")
    return {"bytes": len(encoded), "sha256": hashlib.sha256(encoded).hexdigest()}


def _execution_receipt(
    *,
    command: Sequence[str],
    cwd: Path,
    completed: object,
    test_mode: bool,
) -> dict[str, object]:
    test_mode = _strict_test_mode(test_mode)
    executable: object = None
    if not test_mode:
        executable = _host_file_identity(Path(command[0]))
    receipt: dict[str, object] = {
        "contract": (
            _HYPERFRAMES_TEST_EXECUTION_CONTRACT
            if test_mode
            else _HYPERFRAMES_EXECUTION_CONTRACT
        ),
        "executable": executable,
        "argv": list(command),
        "cwd": cwd.resolve().as_posix(),
        "exit_code": getattr(completed, "returncode", None),
        "stdout": _process_text_identity(getattr(completed, "stdout", "")),
        "stderr": _process_text_identity(getattr(completed, "stderr", "")),
    }
    receipt["content_hash"] = _content_hash(receipt)
    return receipt


def _tree_identity(root: Path) -> dict[str, object]:
    """Hash every runtime file and safe symlink target, including transitive deps."""

    if not root.is_dir() or root.is_symlink():
        raise TrustedRenderError("HyperFrames node_modules tree is missing or unsafe")
    rows: list[dict[str, object]] = []
    total_bytes = 0
    for path in sorted(root.rglob("*"), key=lambda value: value.as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            resolved = path.resolve()
            if not resolved.is_relative_to(root.resolve()):
                raise TrustedRenderError("HyperFrames node_modules symlink escaped runtime")
            rows.append({"path": relative, "symlink": os.readlink(path)})
        elif path.is_file():
            size = path.stat().st_size
            total_bytes += size
            rows.append({"path": relative, "bytes": size, "sha256": _sha256_file(path)})
    if not rows:
        raise TrustedRenderError("HyperFrames node_modules tree is empty")
    return {
        "files": len(rows),
        "bytes": total_bytes,
        "content_hash": _content_hash(rows),
    }


def _inspect_runtime(runtime: Path, package: str) -> tuple[Path, Path, str]:
    version = _runtime_version(package)
    package_root = runtime / "node_modules" / "hyperframes"
    manifest_path = package_root / "package.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise TrustedRenderError(
            f"HyperFrames {version} is not preinstalled; run the explicit prepare-runtime gate"
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TrustedRenderError("preinstalled HyperFrames package manifest is invalid") from error
    if manifest.get("name") != "hyperframes" or manifest.get("version") != version:
        raise TrustedRenderError("preinstalled HyperFrames package version drift")
    raw_bin = manifest.get("bin")
    if isinstance(raw_bin, str):
        relative_cli = raw_bin
    elif isinstance(raw_bin, dict) and isinstance(raw_bin.get("hyperframes"), str):
        relative_cli = raw_bin["hyperframes"]
    else:
        raise TrustedRenderError("preinstalled HyperFrames package has no CLI entrypoint")
    cli = (package_root / relative_cli).resolve()
    if not cli.is_relative_to(package_root.resolve()) or not cli.is_file() or cli.is_symlink():
        raise TrustedRenderError("preinstalled HyperFrames CLI entrypoint is unsafe")
    node = shutil.which("node.exe") or shutil.which("node")
    if not node:
        raise TrustedRenderError("Node.js is unavailable for preinstalled HyperFrames runtime")
    return manifest_path, cli, node


def _hyperframes_runtime_status(
    package: str,
    *,
    runtime_root: str | Path | None = None,
    test_mode: bool,
) -> dict[str, object]:
    """Freshly inspect one acquisition contract without crossing trust modes."""

    test_mode = _strict_test_mode(test_mode)
    version = _runtime_version(package)
    root = _runtime_root(runtime_root)
    runtime = (root / version).resolve()
    if not runtime.is_relative_to(root):
        raise TrustedRenderError("HyperFrames runtime path escaped its configured root")
    manifest_path, cli, node = _inspect_runtime(runtime, package)
    package_json_path = runtime / "package.json"
    package_lock_path = runtime / "package-lock.json"
    if not package_json_path.is_file() or not package_lock_path.is_file():
        raise TrustedRenderError("HyperFrames runtime package lock is missing")
    receipt_path = runtime / "NPM-ACQUISITION.json"
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TrustedRenderError(
            "preinstalled HyperFrames runtime lacks acquisition receipt"
        ) from error
    receipt = _exact_dict(
        receipt,
        {
            "contract",
            "package",
            "npm_argv",
            "package_json",
            "package_lock",
            "package_manifest",
            "cli",
            "node_modules_tree",
            "content_hash",
        },
        "HyperFrames runtime acquisition receipt",
    )
    claimed_hash = receipt.pop("content_hash")
    if claimed_hash != _content_hash(receipt):
        raise TrustedRenderError("HyperFrames runtime acquisition receipt hash drift")
    expected_manifest = _external_identity(runtime, manifest_path)
    expected_cli = _external_identity(runtime, cli)
    expected_package_json = _external_identity(runtime, package_json_path)
    expected_package_lock = _external_identity(runtime, package_lock_path)
    expected_tree = _tree_identity(runtime / "node_modules")
    expected_contract = (
        _HYPERFRAMES_TEST_RUNTIME_CONTRACT if test_mode else HYPERFRAMES_RUNTIME_CONTRACT
    )
    if (
        receipt["contract"] != expected_contract
        or receipt["package"] != package
        or receipt["package_json"] != expected_package_json
        or receipt["package_lock"] != expected_package_lock
        or receipt["package_manifest"] != expected_manifest
        or receipt["cli"] != expected_cli
        or receipt["node_modules_tree"] != expected_tree
    ):
        raise TrustedRenderError("HyperFrames runtime acquisition identity drift")
    npm_argv = receipt["npm_argv"]
    if not isinstance(npm_argv, list) or npm_argv != [
        "npm",
        "install",
        "--prefix",
        "<runtime>",
        "--no-audit",
        "--no-fund",
        "--ignore-scripts=false",
    ]:
        raise TrustedRenderError("HyperFrames runtime acquisition argv drift")
    status = {
        "package": package,
        "runtime_root": runtime.as_posix(),
        "package_manifest_sha256": expected_manifest["sha256"],
        "cli_sha256": expected_cli["sha256"],
        "package_lock_sha256": expected_package_lock["sha256"],
        "node_modules_content_hash": expected_tree["content_hash"],
        "acquisition_content_hash": claimed_hash,
        "command": [node, str(cli)],
    }
    if not test_mode:
        node_identity = _host_file_identity(Path(node))
        if (
            int(node_identity["bytes"]),
            str(node_identity["sha256"]),
        ) not in _PINNED_NODE_EXECUTABLES:
            raise TrustedRenderError(
                "production HyperFrames Node executable differs from the pinned binary"
            )
        pinned = _PINNED_RUNTIME_IDENTITIES.get(package)
        actual = {key: status[key] for key in pinned} if pinned is not None else None
        if pinned is None or actual != pinned:
            raise TrustedRenderError(
                "production HyperFrames runtime differs from the pinned official acquisition"
            )
    return status


def hyperframes_runtime_status(
    package: str, *, runtime_root: str | Path | None = None
) -> dict[str, object]:
    """Freshly inspect one production runtime acquired by the closed npm gate."""

    return _hyperframes_runtime_status(package, runtime_root=runtime_root, test_mode=False)


def _prepare_hyperframes_runtime(
    package: str,
    *,
    runtime_root: str | Path | None = None,
    npm_executable: str | None = None,
    runner: Callable[..., object] | None = None,
    timeout_seconds: int = 900,
    test_mode: bool = False,
) -> dict[str, object]:
    """Explicitly acquire a pinned runtime; rendering itself never downloads."""

    test_mode = _strict_test_mode(test_mode)
    if not test_mode and (npm_executable is not None or runner is not None):
        raise TrustedRenderError("production runtime acquisition forbids process injection")
    version = _runtime_version(package)
    root = _runtime_root(runtime_root)
    root.mkdir(parents=True, exist_ok=True)
    destination = root / version
    if destination.exists():
        return _hyperframes_runtime_status(
            package, runtime_root=root, test_mode=test_mode
        )
    staging = root / f".{version}-{uuid.uuid4().hex}"
    staging.mkdir()
    _write_json(
        staging / "package.json",
        {
            "name": f"nakama-hyperframes-runtime-{version}",
            "private": True,
            "dependencies": {"hyperframes": version},
        },
    )
    npm = npm_executable or shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        raise TrustedRenderError("npm is unavailable for explicit HyperFrames acquisition")
    canonical_argv = [
        "npm",
        "install",
        "--prefix",
        "<runtime>",
        "--no-audit",
        "--no-fund",
        "--ignore-scripts=false",
    ]
    command = [npm, *canonical_argv[1:3], str(staging), *canonical_argv[4:]]
    run = runner or subprocess.run
    try:
        completed = run(
            command,
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise TrustedRenderError(f"HyperFrames runtime acquisition failed: {error}") from error
    if getattr(completed, "returncode", None) != 0:
        stderr = str(getattr(completed, "stderr", "") or "")[-500:]
        raise TrustedRenderError(f"HyperFrames runtime acquisition failed: {stderr}")
    manifest_path, cli, _node = _inspect_runtime(staging, package)
    package_json_path = staging / "package.json"
    package_lock_path = staging / "package-lock.json"
    if not package_lock_path.is_file():
        raise TrustedRenderError("npm acquisition did not produce a dependency lock")
    receipt: dict[str, object] = {
        "contract": (
            _HYPERFRAMES_TEST_RUNTIME_CONTRACT
            if test_mode
            else HYPERFRAMES_RUNTIME_CONTRACT
        ),
        "package": package,
        "npm_argv": canonical_argv,
        "package_json": _external_identity(staging, package_json_path),
        "package_lock": _external_identity(staging, package_lock_path),
        "package_manifest": _external_identity(staging, manifest_path),
        "cli": _external_identity(staging, cli),
        "node_modules_tree": _tree_identity(staging / "node_modules"),
    }
    receipt["content_hash"] = _content_hash(receipt)
    _write_json(staging / "NPM-ACQUISITION.json", receipt)
    if destination.exists():
        raise TrustedRenderError("HyperFrames runtime destination appeared during acquisition")
    staging.rename(destination)
    return _hyperframes_runtime_status(package, runtime_root=root, test_mode=test_mode)


def prepare_hyperframes_runtime(
    package: str,
    *,
    runtime_root: str | Path | None = None,
    timeout_seconds: int = 900,
) -> dict[str, object]:
    """Acquire a pinned runtime through the closed trusted npm process."""

    return _prepare_hyperframes_runtime(
        package,
        runtime_root=runtime_root,
        timeout_seconds=timeout_seconds,
    )


def _prepare_hyperframes_runtime_for_test(
    package: str,
    *,
    npm_executable: str,
    runner: Callable[..., object],
    **kwargs: object,
) -> dict[str, object]:
    return _prepare_hyperframes_runtime(
        package,
        npm_executable=npm_executable,
        runner=runner,
        test_mode=True,
        **kwargs,
    )


def _receipt_path(
    root: Path,
    *,
    cut_id: str,
    revision_id: str,
    candidate_id: str,
    render_spec_sha256: str,
    render_dir: str = TRUSTED_RENDER_DIR,
) -> Path:
    return (
        root
        / "highlights"
        / "visual-pipeline"
        / cut_id
        / "jobs"
        / revision_id
        / render_dir
        / candidate_id
        / render_spec_sha256
        / "HYPERFRAMES-RENDER.json"
    )


def _read_receipt(path: Path) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TrustedRenderError("HyperFrames trusted receipt is not valid JSON") from error
    return _exact_dict(raw, _RECEIPT_KEYS, "HyperFrames trusted receipt")


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _candidate_result(
    root: Path, receipt_path: Path, receipt: Mapping[str, object]
) -> dict[str, object]:
    render_spec = receipt["render_spec"]
    media = receipt["media"]
    assert isinstance(render_spec, dict)
    assert isinstance(media, dict)
    return {
        "render_spec": dict(render_spec),
        "preview_media": {key: media[key] for key in _IDENTITY_KEYS},
        "provenance": {
            "kind": "hyperframes_render",
            "provider": HYPERFRAMES_PROVIDER,
            "source_url": None,
            "license": HYPERFRAMES_LICENSE,
            "receipt": _identity(root, receipt_path),
        },
    }


def _verify_execution_receipt(
    raw: object,
    *,
    runtime_status: Mapping[str, object],
    spec: _Component,
    test_mode: bool,
) -> dict[str, object]:
    test_mode = _strict_test_mode(test_mode)
    execution = _exact_dict(raw, _EXECUTION_KEYS, "receipt.execution")
    claimed_hash = execution.pop("content_hash")
    if not isinstance(claimed_hash, str) or claimed_hash != _content_hash(execution):
        raise TrustedRenderError("HyperFrames execution receipt hash drift")
    expected_contract = (
        _HYPERFRAMES_TEST_EXECUTION_CONTRACT
        if test_mode
        else _HYPERFRAMES_EXECUTION_CONTRACT
    )
    if execution["contract"] != expected_contract or execution["exit_code"] != 0:
        raise TrustedRenderError("HyperFrames execution contract/exit drift")
    expected_cwd = (_COMPOSITION_ROOT / spec.directory).resolve()
    if execution["cwd"] != expected_cwd.as_posix():
        raise TrustedRenderError("HyperFrames execution cwd drift")
    argv = execution["argv"]
    if not isinstance(argv, list) or not argv or any(
        not isinstance(value, str) or not value for value in argv
    ):
        raise TrustedRenderError("HyperFrames execution argv is invalid")
    for stream_name in ("stdout", "stderr"):
        stream = _exact_dict(
            execution[stream_name], {"bytes", "sha256"}, f"execution.{stream_name}"
        )
        if (
            not isinstance(stream["bytes"], int)
            or isinstance(stream["bytes"], bool)
            or stream["bytes"] < 0
            or not isinstance(stream["sha256"], str)
            or not _SHA256.fullmatch(stream["sha256"])
        ):
            raise TrustedRenderError(f"HyperFrames execution {stream_name} identity drift")
    if test_mode:
        if execution["executable"] is not None:
            raise TrustedRenderError("test execution must not claim a trusted executable")
    else:
        runtime_command = runtime_status["command"]
        if not isinstance(runtime_command, list) or len(runtime_command) < 2:
            raise TrustedRenderError("HyperFrames runtime command drift")
        if execution["executable"] != _host_file_identity(Path(str(runtime_command[0]))):
            raise TrustedRenderError("HyperFrames executable identity drift")
        if argv[: len(runtime_command)] != runtime_command:
            raise TrustedRenderError("HyperFrames actual runtime command drift")
        try:
            media_path = Path(argv[argv.index("-o") + 1]).resolve()
            variables_path = Path(argv[argv.index("--variables-file") + 1]).resolve()
        except (ValueError, IndexError) as error:
            raise TrustedRenderError("HyperFrames actual argv lacks bound IO paths") from error
        temporary_root = Path(tempfile.gettempdir()).resolve()
        if (
            media_path.parent != variables_path.parent
            or not media_path.parent.is_relative_to(temporary_root)
            or not media_path.parent.name.startswith("nakama-hf-")
            or media_path.name != "preview.mov"
            or variables_path.name != "variables.json"
        ):
            raise TrustedRenderError("HyperFrames actual argv scratch paths drift")
        expected_actual = _actual_argv(
            runtime_command,
            spec,
            media_path=media_path,
            variables_path=variables_path,
        )
        if argv != expected_actual:
            raise TrustedRenderError("HyperFrames actual argv drift")
    return {**execution, "content_hash": claimed_hash}


def _verify_hyperframes_render_receipt_bound(
    episode_root: str | Path,
    *,
    receipt_identity: object,
    expected_cut_id: str,
    expected_revision_id: str,
    expected_candidate_id: str,
    expected_component: str,
    expected_render_params: object,
    expected_on_screen_text: object,
    expected_media: object,
    runtime_root: str | Path | None = None,
    expected_contract: str,
    render_dir: str,
) -> dict[str, object]:
    """Freshly verify one canonical trusted render and every byte it binds."""

    root = Path(episode_root).resolve()
    cut_id = _safe_token(expected_cut_id, "cut_id")
    revision_id = _safe_revision(expected_revision_id)
    candidate_id = _safe_token(expected_candidate_id, "candidate_id")
    component = _safe_token(expected_component, "component")
    canonical_params, variables = _closed_render_params(
        component, expected_render_params, expected_on_screen_text
    )
    render_spec_sha256 = _content_hash({"component": component, "render_params": canonical_params})
    expected_receipt_path = _receipt_path(
        root,
        cut_id=cut_id,
        revision_id=revision_id,
        candidate_id=candidate_id,
        render_spec_sha256=render_spec_sha256,
        render_dir=render_dir,
    ).resolve()
    receipt_path, normalized_receipt_identity = _verify_identity(
        root, receipt_identity, "HyperFrames trusted receipt"
    )
    if receipt_path != expected_receipt_path or render_dir not in receipt_path.parts:
        raise TrustedRenderError("HyperFrames receipt is outside canonical trusted-renders path")
    receipt = _read_receipt(receipt_path)
    claimed_content_hash = receipt.pop("content_hash")
    if not isinstance(claimed_content_hash, str) or claimed_content_hash != _content_hash(receipt):
        raise TrustedRenderError("HyperFrames receipt content hash drift")
    if (
        receipt["contract"] != expected_contract
        or receipt["episode_id"] != root.name
        or receipt["cut_id"] != cut_id
        or receipt["revision_id"] != revision_id
        or receipt["candidate_id"] != candidate_id
    ):
        raise TrustedRenderError("HyperFrames receipt lineage drift")
    spec = _COMPONENTS.get(component)
    if spec is None:
        raise TrustedRenderError(f"component is not registered: {component}")
    render_spec = _exact_dict(
        receipt["render_spec"],
        {"component", "composition", "render_params", "render_spec_sha256"},
        "receipt.render_spec",
    )
    if render_spec != {
        "component": component,
        "composition": spec.composition,
        "render_params": canonical_params,
        "render_spec_sha256": render_spec_sha256,
    }:
        raise TrustedRenderError("HyperFrames receipt render spec drift")
    component_source = _exact_dict(
        receipt["component_source"], {"files", "content_hash"}, "receipt.component_source"
    )
    if component_source != _component_source(component):
        raise TrustedRenderError("HyperFrames component source identity drift")
    variables_path, variables_identity = _verify_identity(
        root, receipt["variables_file"], "receipt.variables_file"
    )
    if variables_path.parent != receipt_path.parent:
        raise TrustedRenderError("HyperFrames variables file escaped its render directory")
    try:
        actual_variables = json.loads(variables_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TrustedRenderError("HyperFrames variables file is invalid JSON") from error
    if actual_variables != variables:
        raise TrustedRenderError("HyperFrames variables drift from closed component schema")
    media_value = _exact_dict(
        receipt["media"], {*_IDENTITY_KEYS, "probe", "frame_audit"}, "receipt.media"
    )
    media_path, media_identity = _verify_identity(
        root,
        {key: media_value[key] for key in _IDENTITY_KEYS},
        "receipt.media",
    )
    if media_path.parent != receipt_path.parent:
        raise TrustedRenderError("HyperFrames media escaped its render directory")
    _, expected_media_identity = _verify_identity(root, expected_media, "expected_media")
    if media_identity != expected_media_identity:
        raise TrustedRenderError("HyperFrames media identity differs from selected candidate")
    try:
        fresh_probe = probe_stock_video(media_path)
    except BrollContractError as error:
        raise TrustedRenderError("HyperFrames media is not playable") from error
    if media_value["probe"] != fresh_probe:
        raise TrustedRenderError("HyperFrames media probe drift")
    fresh_frame_audit = _frame_audit(media_path, spec, canonical_params)
    if media_value["frame_audit"] != fresh_frame_audit:
        raise TrustedRenderError("HyperFrames media frame audit drift")
    runtime_status = _hyperframes_runtime_status(
        spec.package,
        runtime_root=runtime_root,
        test_mode=expected_contract == _HYPERFRAMES_TEST_RENDER_CONTRACT,
    )
    execution = _verify_execution_receipt(
        receipt["execution"],
        runtime_status=runtime_status,
        spec=spec,
        test_mode=expected_contract == _HYPERFRAMES_TEST_RENDER_CONTRACT,
    )
    runtime_binding = {
        "acquisition_content_hash": runtime_status["acquisition_content_hash"],
        "package_lock_sha256": runtime_status["package_lock_sha256"],
        "node_modules_content_hash": runtime_status["node_modules_content_hash"],
    }
    engine = _exact_dict(
        receipt["engine"], {"name", "package", "argv", "runtime"}, "receipt.engine"
    )
    expected_argv = _normalized_argv(
        spec,
        media_path=media_identity["path"],
        variables_path=variables_identity["path"],
    )
    if engine != {
        "name": "hyperframes",
        "package": spec.package,
        "argv": expected_argv,
        "runtime": runtime_binding,
    }:
        raise TrustedRenderError("HyperFrames engine invocation receipt drift")
    return {
        **receipt,
        "content_hash": claimed_content_hash,
        "execution": execution,
        "media": media_value,
    }


def verify_hyperframes_render_receipt(
    episode_root: str | Path,
    *,
    receipt_identity: object,
    expected_cut_id: str,
    expected_revision_id: str,
    expected_candidate_id: str,
    expected_component: str,
    expected_render_params: object,
    expected_on_screen_text: object,
    expected_media: object,
    runtime_root: str | Path | None = None,
) -> dict[str, object]:
    """Freshly verify a production receipt minted by the closed trusted runner."""

    return _verify_hyperframes_render_receipt_bound(
        episode_root,
        receipt_identity=receipt_identity,
        expected_cut_id=expected_cut_id,
        expected_revision_id=expected_revision_id,
        expected_candidate_id=expected_candidate_id,
        expected_component=expected_component,
        expected_render_params=expected_render_params,
        expected_on_screen_text=expected_on_screen_text,
        expected_media=expected_media,
        runtime_root=runtime_root,
        expected_contract=HYPERFRAMES_RENDER_CONTRACT,
        render_dir=TRUSTED_RENDER_DIR,
    )


def _verify_hyperframes_test_receipt(
    episode_root: str | Path,
    **kwargs: object,
) -> dict[str, object]:
    return _verify_hyperframes_render_receipt_bound(
        episode_root,
        **kwargs,
        expected_contract=_HYPERFRAMES_TEST_RENDER_CONTRACT,
        render_dir=_TEST_RENDER_DIR,
    )


def _render_hyperframes_candidate(
    episode_root: str | Path,
    *,
    cut_id: str,
    revision_id: str,
    candidate_id: str,
    component: str,
    render_params: object,
    expected_on_screen_text: object,
    runtime_root: str | Path | None = None,
    runtime_command: Sequence[str] | None = None,
    runner: Callable[..., object] | None = None,
    timeout_seconds: int = 900,
    test_mode: bool = False,
) -> dict[str, object]:
    """Render one candidate through a pinned registered HyperFrames component."""

    test_mode = _strict_test_mode(test_mode)
    root = Path(episode_root).resolve()
    if not root.is_dir():
        raise TrustedRenderError("episode_root does not exist")
    safe_cut = _safe_token(cut_id, "cut_id")
    safe_revision = _safe_revision(revision_id)
    safe_candidate = _safe_token(candidate_id, "candidate_id")
    safe_component = _safe_token(component, "component")
    canonical_params, variables = _closed_render_params(
        safe_component, render_params, expected_on_screen_text
    )
    spec = _COMPONENTS[safe_component]
    render_spec_sha256 = _content_hash(
        {"component": safe_component, "render_params": canonical_params}
    )
    receipt_path = _receipt_path(
        root,
        cut_id=safe_cut,
        revision_id=safe_revision,
        candidate_id=safe_candidate,
        render_spec_sha256=render_spec_sha256,
        render_dir=_TEST_RENDER_DIR if test_mode else TRUSTED_RENDER_DIR,
    )
    if receipt_path.exists():
        receipt = _read_receipt(receipt_path)
        result = _candidate_result(root, receipt_path, receipt)
        verifier = (
            _verify_hyperframes_test_receipt if test_mode else verify_hyperframes_render_receipt
        )
        verifier(
            root,
            receipt_identity=result["provenance"]["receipt"],
            expected_cut_id=safe_cut,
            expected_revision_id=safe_revision,
            expected_candidate_id=safe_candidate,
            expected_component=safe_component,
            expected_render_params=canonical_params,
            expected_on_screen_text=expected_on_screen_text,
            expected_media=result["preview_media"],
            runtime_root=runtime_root,
        )
        return result

    trusted_root = receipt_path.parents[2]
    trusted_root.mkdir(parents=True, exist_ok=True)
    staging = trusted_root / f".{safe_candidate}-{render_spec_sha256[:12]}-{uuid.uuid4().hex}"
    staging.mkdir()
    variables_path = staging / "variables.json"
    media_path = staging / "preview.mov"
    _write_json(variables_path, variables)
    status = _hyperframes_runtime_status(
        spec.package, runtime_root=runtime_root, test_mode=test_mode
    )
    if not test_mode and (runtime_command is not None or runner is not None):
        raise TrustedRenderError("production renderer forbids process/runtime injection")
    if runtime_command is None:
        resolved_runtime_command = status["command"]
        assert isinstance(resolved_runtime_command, list)
    else:
        if not runtime_command or any(
            not isinstance(value, str) or not value for value in runtime_command
        ):
            raise TrustedRenderError("trusted HyperFrames runtime command is invalid")
        resolved_runtime_command = list(runtime_command)
    run = runner or subprocess.run
    execution: dict[str, object]
    component_cwd = (_COMPOSITION_ROOT / spec.directory).resolve()
    with tempfile.TemporaryDirectory(prefix="nakama-hf-") as temporary_directory:
        short_root = Path(temporary_directory).resolve()
        short_variables = short_root / "variables.json"
        short_media = short_root / "preview.mov"
        _write_json(short_variables, variables)
        command = _actual_argv(
            resolved_runtime_command,
            spec,
            media_path=short_media,
            variables_path=short_variables,
        )
        try:
            completed = run(
                command,
                cwd=str(component_cwd),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise TrustedRenderError(f"trusted HyperFrames invocation failed: {error}") from error
        if getattr(completed, "returncode", None) != 0 or not short_media.is_file():
            stderr = str(getattr(completed, "stderr", "") or "")[-1600:]
            stdout = str(getattr(completed, "stdout", "") or "")[-1600:]
            raise TrustedRenderError(
                f"trusted HyperFrames render failed: {(stderr or stdout).strip()}"
            )
        execution = _execution_receipt(
            command=command,
            cwd=component_cwd,
            completed=completed,
            test_mode=test_mode,
        )
        try:
            probe = probe_stock_video(short_media)
        except BrollContractError as error:
            raise TrustedRenderError("trusted HyperFrames output is not playable") from error
        shutil.copy2(short_media, media_path)
    frame_audit = _frame_audit(media_path, spec, canonical_params)

    final_dir = receipt_path.parent
    final_variables = final_dir / variables_path.name
    final_media = final_dir / media_path.name
    final_variables_identity = {
        "path": final_variables.relative_to(root).as_posix(),
        "bytes": variables_path.stat().st_size,
        "sha256": _sha256_file(variables_path),
    }
    final_media_identity = {
        "path": final_media.relative_to(root).as_posix(),
        "bytes": media_path.stat().st_size,
        "sha256": _sha256_file(media_path),
    }
    normalized_argv = _normalized_argv(
        spec,
        media_path=final_media_identity["path"],
        variables_path=final_variables_identity["path"],
    )
    document: dict[str, object] = {
        "contract": (
            _HYPERFRAMES_TEST_RENDER_CONTRACT if test_mode else HYPERFRAMES_RENDER_CONTRACT
        ),
        "episode_id": root.name,
        "cut_id": safe_cut,
        "revision_id": safe_revision,
        "candidate_id": safe_candidate,
        "engine": {
            "name": "hyperframes",
            "package": spec.package,
            "argv": normalized_argv,
            "runtime": {
                "acquisition_content_hash": status["acquisition_content_hash"],
                "package_lock_sha256": status["package_lock_sha256"],
                "node_modules_content_hash": status["node_modules_content_hash"],
            },
        },
        "execution": execution,
        "render_spec": {
            "component": safe_component,
            "composition": spec.composition,
            "render_params": canonical_params,
            "render_spec_sha256": render_spec_sha256,
        },
        "component_source": _component_source(safe_component),
        "variables_file": final_variables_identity,
        "media": {**final_media_identity, "probe": probe, "frame_audit": frame_audit},
    }
    document["content_hash"] = _content_hash(document)
    _write_json(staging / receipt_path.name, document)
    if final_dir.exists():
        raise TrustedRenderError("trusted render destination appeared during render")
    final_dir.parent.mkdir(parents=True, exist_ok=True)
    staging.rename(final_dir)
    result = _candidate_result(root, receipt_path, document)
    verifier = _verify_hyperframes_test_receipt if test_mode else verify_hyperframes_render_receipt
    verifier(
        root,
        receipt_identity=result["provenance"]["receipt"],
        expected_cut_id=safe_cut,
        expected_revision_id=safe_revision,
        expected_candidate_id=safe_candidate,
        expected_component=safe_component,
        expected_render_params=canonical_params,
        expected_on_screen_text=expected_on_screen_text,
        expected_media=result["preview_media"],
        runtime_root=runtime_root,
    )
    return result


def render_hyperframes_candidate(
    episode_root: str | Path,
    *,
    cut_id: str,
    revision_id: str,
    candidate_id: str,
    component: str,
    render_params: object,
    expected_on_screen_text: object,
    runtime_root: str | Path | None = None,
    timeout_seconds: int = 900,
) -> dict[str, object]:
    """Render through the closed pinned runtime; process injection is impossible."""

    return _render_hyperframes_candidate(
        episode_root,
        cut_id=cut_id,
        revision_id=revision_id,
        candidate_id=candidate_id,
        component=component,
        render_params=render_params,
        expected_on_screen_text=expected_on_screen_text,
        runtime_root=runtime_root,
        timeout_seconds=timeout_seconds,
    )


def _render_hyperframes_candidate_for_test(
    episode_root: str | Path,
    *,
    runtime_command: Sequence[str],
    runner: Callable[..., object],
    **kwargs: object,
) -> dict[str, object]:
    """Private test seam; its test-only receipt is rejected by the public verifier."""

    return _render_hyperframes_candidate(
        episode_root,
        runtime_command=runtime_command,
        runner=runner,
        test_mode=True,
        **kwargs,
    )


def _fresh_asset_authority_projection(
    episode_root: Path,
    *,
    cut_id: str,
    revision_id: str,
    attempt: int,
    editorial_master: object | None,
) -> dict[str, object]:
    """Fresh-load canonical authority without creating an import-time core cycle."""

    from agents.brook.script_video.highlight_visual_pipeline import (
        load_asset_authority_projection,
    )

    return load_asset_authority_projection(
        episode_root,
        cut_id=cut_id,
        revision_id=revision_id,
        attempt=attempt,
        editorial_master=editorial_master,
    )


def _authority_assets_from_projection(
    root: Path,
    value: object,
    *,
    expected_attempt: int,
) -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    projection = _exact_dict(
        value,
        {"identity", "authority_chain", "attempt", "assets"},
        "trusted asset authority projection",
    )
    if projection["attempt"] != expected_attempt:
        raise TrustedRenderError("trusted asset authority attempt drift")
    rows = projection["assets"]
    if not isinstance(rows, list) or not rows:
        raise TrustedRenderError("trusted asset authority has no acquired assets")
    authority_by_id: dict[str, dict[str, object]] = {}
    authority_keys = {
        "asset_id",
        "source_class",
        "provider",
        "provider_item_id",
        "source_url",
        "license",
        "acquired_at",
        "semantic_summary",
        "original_media",
        "acquisition_receipt",
    }
    for asset_index, raw_asset in enumerate(rows, 1):
        asset = _exact_dict(
            raw_asset,
            authority_keys,
            f"trusted asset authority asset {asset_index}",
        )
        asset_id = _safe_token(asset["asset_id"], "authority.asset_id")
        if asset_id in authority_by_id:
            raise TrustedRenderError("trusted asset authority contains a duplicate asset_id")
        media_path, media_identity = _verify_identity(
            root, asset["original_media"], f"authority asset {asset_id}.original_media"
        )
        try:
            probe_stock_video(media_path)
        except BrollContractError as error:
            raise TrustedRenderError(f"authority asset {asset_id} is not playable media") from error
        _, receipt_identity = _verify_identity(
            root,
            asset["acquisition_receipt"],
            f"authority asset {asset_id}.acquisition_receipt",
        )
        authority_by_id[asset_id] = {
            **asset,
            "original_media": media_identity,
            "acquisition_receipt": receipt_identity,
        }
    return projection, authority_by_id


def hydrate_dp_hyperframes_proposal(
    episode_root: str | Path,
    *,
    cut_id: str,
    revision_id: str,
    attempt: int,
    proposal_path: str | Path,
    output_path: str | Path,
    editorial_master: object | None = None,
    runtime_root: str | Path | None = None,
) -> dict[str, object]:
    """Hydrate a DP spec-only proposal at the trusted media boundary.

    HyperFrames specs are rendered here.  Stock/provided specs are exact-joined
    to a projection freshly returned by ``load_asset_authority_projection``.
    Creative workers may not include media or provenance in either lane.
    """

    root = Path(episode_root).resolve()
    safe_cut = _safe_token(cut_id, "cut_id")
    safe_revision = _safe_revision(revision_id)
    if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
        raise TrustedRenderError("attempt must be a positive integer")
    raw_path = Path(proposal_path).resolve()
    destination = Path(output_path).resolve()
    if (
        not raw_path.is_relative_to(root)
        or not raw_path.is_file()
        or not destination.is_relative_to(root)
        or raw_path == destination
    ):
        raise TrustedRenderError("DP proposal paths must be distinct episode-local files")
    try:
        proposal = json.loads(raw_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TrustedRenderError("DP proposal is unreadable JSON") from error
    if not isinstance(proposal, dict):
        raise TrustedRenderError("DP proposal must be an object")
    implementations = proposal.get("implementations")
    if not isinstance(implementations, list):
        raise TrustedRenderError("DP proposal implementations must be an array")
    hydrated = dict(proposal)
    hydrated_implementations: list[object] = []
    spec_only_keys = {
        "candidate_id",
        "visual_summary",
        "component",
        "render_params",
        "render_spec_sha256",
    }
    asset_spec_only_keys = {"candidate_id", "visual_summary", "authority_asset_id"}
    authority_by_id: dict[str, dict[str, object]] | None = None
    authority_projection: dict[str, object] | None = None
    hyperframes_bindings: list[dict[str, object]] = []

    def trusted_assets() -> dict[str, dict[str, object]]:
        nonlocal authority_by_id, authority_projection
        if authority_by_id is not None:
            return authority_by_id
        authority_projection, authority_by_id = _authority_assets_from_projection(
            root,
            _fresh_asset_authority_projection(
                root,
                cut_id=safe_cut,
                revision_id=safe_revision,
                attempt=attempt,
                editorial_master=editorial_master,
            ),
            expected_attempt=attempt,
        )
        return authority_by_id

    for item_index, raw_item in enumerate(implementations, 1):
        if not isinstance(raw_item, dict):
            raise TrustedRenderError(f"DP implementation {item_index} is not an object")
        item = dict(raw_item)
        mode = item.get("mode")
        if mode in {"stock", "provided_asset"}:
            candidates = item.get("candidates")
            if not isinstance(candidates, list) or not candidates:
                raise TrustedRenderError(
                    f"DP asset implementation {item_index} has no candidates"
                )
            hydrated_candidates: list[dict[str, object]] = []
            for candidate_index, raw_candidate in enumerate(candidates, 1):
                candidate = _exact_dict(
                    raw_candidate,
                    asset_spec_only_keys,
                    f"DP asset candidate {item_index}.{candidate_index} spec-only schema",
                )
                authority_asset_id = _safe_token(
                    candidate["authority_asset_id"], "candidate.authority_asset_id"
                )
                authority_asset = trusted_assets().get(authority_asset_id)
                if authority_asset is None:
                    raise TrustedRenderError(
                        "DP candidate references an absent trusted authority asset"
                    )
                if candidate["visual_summary"] != authority_asset["semantic_summary"]:
                    raise TrustedRenderError(
                        "DP candidate visual_summary differs from trusted authority"
                    )
                source_class = str(authority_asset["source_class"])
                if mode == "stock" and source_class not in {
                    "licensed_stock",
                    "official_archive",
                    "public_domain",
                }:
                    raise TrustedRenderError(
                        "DP stock candidate does not reference stock/archive authority"
                    )
                if mode == "provided_asset" and not source_class.startswith("provided_"):
                    raise TrustedRenderError(
                        "DP provided candidate does not reference provided authority"
                    )
                hydrated_candidates.append(
                    {
                        "candidate_id": _safe_token(
                            candidate["candidate_id"], "candidate.candidate_id"
                        ),
                        "visual_summary": candidate["visual_summary"],
                        "authority_asset_id": authority_asset_id,
                        "media": authority_asset["original_media"],
                        "provenance": {
                            "kind": "stock_source" if mode == "stock" else "provided_source",
                            "provider": authority_asset["provider"],
                            "source_url": authority_asset["source_url"],
                            "license": authority_asset["license"],
                            "receipt": authority_asset["acquisition_receipt"],
                        },
                    }
                )
            item["candidates"] = hydrated_candidates
            hydrated_implementations.append(item)
            continue
        if mode != "hyperframes":
            raise TrustedRenderError(f"DP implementation {item_index} mode is not hydratable")
        on_screen_text = item.get("on_screen_text")
        candidates = item.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise TrustedRenderError(
                f"DP HyperFrames implementation {item_index} has no candidates"
            )
        hydrated_candidates: list[dict[str, object]] = []
        for candidate_index, raw_candidate in enumerate(candidates, 1):
            candidate = _exact_dict(
                raw_candidate,
                spec_only_keys,
                f"DP HyperFrames candidate {item_index}.{candidate_index} spec-only schema",
            )
            component = _safe_token(candidate["component"], "candidate.component")
            raw_spec_hash = _content_hash(
                {"component": component, "render_params": candidate["render_params"]}
            )
            if candidate["render_spec_sha256"] != raw_spec_hash:
                raise TrustedRenderError("DP candidate render spec hash drift")
            result = render_hyperframes_candidate(
                root,
                cut_id=safe_cut,
                revision_id=safe_revision,
                candidate_id=_safe_token(candidate["candidate_id"], "candidate_id"),
                component=component,
                render_params=candidate["render_params"],
                expected_on_screen_text=on_screen_text,
                runtime_root=runtime_root,
            )
            trusted_spec = result["render_spec"]
            assert isinstance(trusted_spec, dict)
            hyperframes_bindings.append(
                {
                    "event_id": _safe_token(item.get("event_id"), "implementation.event_id"),
                    "candidate_id": candidate["candidate_id"],
                    "component": component,
                    "render_spec_sha256": trusted_spec["render_spec_sha256"],
                    "preview_media": result["preview_media"],
                    "receipt": result["provenance"]["receipt"],
                }
            )
            hydrated_candidates.append(
                {
                    "candidate_id": candidate["candidate_id"],
                    "visual_summary": candidate["visual_summary"],
                    "component": component,
                    "render_params": trusted_spec["render_params"],
                    "render_spec_sha256": trusted_spec["render_spec_sha256"],
                    "preview_media": result["preview_media"],
                    "provenance": result["provenance"],
                }
            )
        item["candidates"] = hydrated_candidates
        hydrated_implementations.append(item)
    hydrated["implementations"] = hydrated_implementations
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(hydrated, ensure_ascii=False, indent=2) + "\n"
    if destination.exists():
        try:
            existing = json.loads(destination.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise TrustedRenderError("trusted DP proposal destination drift") from error
        if existing != hydrated:
            raise TrustedRenderError("trusted DP proposal destination drift")
    else:
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(encoded, encoding="utf-8")
        temporary.replace(destination)
    hydration_identity = _publish_dp_hydration_receipt(
        root,
        cut_id=safe_cut,
        revision_id=safe_revision,
        attempt=attempt,
        raw_path=raw_path,
        hydrated_path=destination,
        authority_projection=authority_projection,
        hyperframes_bindings=hyperframes_bindings,
    )
    verify_dp_hydration_receipt(
        root,
        receipt_identity=hydration_identity,
        expected_cut_id=safe_cut,
        expected_revision_id=safe_revision,
        expected_attempt=attempt,
        expected_raw_proposal=_identity(root, raw_path),
        expected_hydrated_proposal=_identity(root, destination),
        editorial_master=editorial_master,
        runtime_root=runtime_root,
    )
    return hydrated


def dp_hydration_receipt_path(hydrated_proposal_path: str | Path) -> Path:
    hydrated = Path(hydrated_proposal_path)
    return hydrated.with_name(f"{hydrated.name}.hydration.json")


def dp_hydration_receipt_identity(
    episode_root: str | Path,
    hydrated_proposal_path: str | Path,
) -> dict[str, object]:
    root = Path(episode_root).resolve()
    path = dp_hydration_receipt_path(hydrated_proposal_path).resolve()
    return _identity(root, path)


def _authority_projection_binding(
    projection: Mapping[str, object] | None,
    *,
    attempt: int,
) -> dict[str, object] | None:
    if projection is None:
        return None
    return {
        "attempt": attempt,
        "identity": projection["identity"],
        "authority_chain": projection["authority_chain"],
        "projection_content_hash": _content_hash(projection),
    }


def _publish_dp_hydration_receipt(
    root: Path,
    *,
    cut_id: str,
    revision_id: str,
    attempt: int,
    raw_path: Path,
    hydrated_path: Path,
    authority_projection: Mapping[str, object] | None,
    hyperframes_bindings: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    receipt_path = dp_hydration_receipt_path(hydrated_path).resolve()
    if not receipt_path.is_relative_to(root) or receipt_path == raw_path:
        raise TrustedRenderError("DP hydration receipt path escaped episode root")
    document: dict[str, object] = {
        "contract": DP_HYDRATION_CONTRACT,
        "episode_id": root.name,
        "cut_id": cut_id,
        "revision_id": revision_id,
        "attempt": attempt,
        "raw_proposal": _identity(root, raw_path),
        "hydrated_proposal": _identity(root, hydrated_path),
        "asset_authority": _authority_projection_binding(
            authority_projection, attempt=attempt
        ),
        "hyperframes": [dict(row) for row in hyperframes_bindings],
    }
    document["content_hash"] = _content_hash(document)
    encoded = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    if receipt_path.exists():
        try:
            existing = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise TrustedRenderError("DP hydration receipt destination drift") from error
        if existing != document:
            raise TrustedRenderError("DP hydration receipt destination drift")
    else:
        temporary = receipt_path.with_name(f".{receipt_path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(encoded, encoding="utf-8")
        temporary.replace(receipt_path)
    return _identity(root, receipt_path)


def _load_json_object(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TrustedRenderError(f"{label} is unreadable JSON") from error
    if not isinstance(value, dict):
        raise TrustedRenderError(f"{label} must be an object")
    return value


def _expected_authority_candidate(
    raw: object,
    *,
    mode: str,
    authority_by_id: Mapping[str, Mapping[str, object]],
    label: str,
) -> dict[str, object]:
    candidate = _exact_dict(
        raw,
        {"candidate_id", "visual_summary", "authority_asset_id"},
        f"{label} raw authority candidate",
    )
    candidate_id = _safe_token(candidate["candidate_id"], f"{label}.candidate_id")
    asset_id = _safe_token(candidate["authority_asset_id"], f"{label}.authority_asset_id")
    asset = authority_by_id.get(asset_id)
    if asset is None or candidate["visual_summary"] != asset["semantic_summary"]:
        raise TrustedRenderError(f"{label} differs from canonical acquisition authority")
    source_class = str(asset["source_class"])
    if mode == "stock" and source_class not in {
        "licensed_stock",
        "official_archive",
        "public_domain",
    }:
        raise TrustedRenderError(f"{label} is not canonical stock/archive authority")
    if mode == "provided_asset" and not source_class.startswith("provided_"):
        raise TrustedRenderError(f"{label} is not canonical provided authority")
    return {
        "candidate_id": candidate_id,
        "visual_summary": candidate["visual_summary"],
        "authority_asset_id": asset_id,
        "media": asset["original_media"],
        "provenance": {
            "kind": "stock_source" if mode == "stock" else "provided_source",
            "provider": asset["provider"],
            "source_url": asset["source_url"],
            "license": asset["license"],
            "receipt": asset["acquisition_receipt"],
        },
    }


def verify_dp_hydration_receipt(
    episode_root: str | Path,
    *,
    receipt_identity: object,
    expected_cut_id: str,
    expected_revision_id: str,
    expected_attempt: int,
    expected_raw_proposal: object,
    expected_hydrated_proposal: object,
    editorial_master: object | None = None,
    runtime_root: str | Path | None = None,
) -> dict[str, object]:
    """Freshly prove raw worker proposal -> trusted hydrated proposal lineage."""

    root = Path(episode_root).resolve()
    cut_id = _safe_token(expected_cut_id, "cut_id")
    revision_id = _safe_revision(expected_revision_id)
    if (
        not isinstance(expected_attempt, int)
        or isinstance(expected_attempt, bool)
        or expected_attempt < 1
    ):
        raise TrustedRenderError("expected_attempt must be a positive integer")
    receipt_path, receipt_file_identity, receipt_document = _read_json_identity_snapshot(
        root, receipt_identity, "DP hydration receipt"
    )
    raw_path, raw_identity, raw_proposal = _read_json_identity_snapshot(
        root, expected_raw_proposal, "raw DP proposal"
    )
    hydrated_path, hydrated_identity, hydrated_proposal = _read_json_identity_snapshot(
        root, expected_hydrated_proposal, "hydrated DP proposal"
    )
    if receipt_path != dp_hydration_receipt_path(hydrated_path).resolve():
        raise TrustedRenderError("DP hydration receipt is outside canonical sibling path")
    receipt = _exact_dict(
        receipt_document,
        _HYDRATION_KEYS,
        "DP hydration receipt",
    )
    claimed_hash = receipt.pop("content_hash")
    if not isinstance(claimed_hash, str) or claimed_hash != _content_hash(receipt):
        raise TrustedRenderError("DP hydration receipt content hash drift")
    if (
        receipt["contract"] != DP_HYDRATION_CONTRACT
        or receipt["episode_id"] != root.name
        or receipt["cut_id"] != cut_id
        or receipt["revision_id"] != revision_id
        or receipt["attempt"] != expected_attempt
        or receipt["raw_proposal"] != raw_identity
        or receipt["hydrated_proposal"] != hydrated_identity
    ):
        raise TrustedRenderError("DP hydration receipt lineage drift")
    if set(raw_proposal) != set(hydrated_proposal) or {
        key: value for key, value in raw_proposal.items() if key != "implementations"
    } != {key: value for key, value in hydrated_proposal.items() if key != "implementations"}:
        raise TrustedRenderError("hydrated DP proposal changed non-implementation fields")
    raw_items = raw_proposal.get("implementations")
    hydrated_items = hydrated_proposal.get("implementations")
    if (
        not isinstance(raw_items, list)
        or not isinstance(hydrated_items, list)
        or len(raw_items) != len(hydrated_items)
    ):
        raise TrustedRenderError("hydrated DP implementation coverage drift")
    authority_projection: dict[str, object] | None = None
    authority_by_id: dict[str, dict[str, object]] = {}
    expected_hyperframes: list[dict[str, object]] = []
    for item_index, (raw_item, hydrated_item) in enumerate(
        zip(raw_items, hydrated_items, strict=True), 1
    ):
        if not isinstance(raw_item, dict) or not isinstance(hydrated_item, dict):
            raise TrustedRenderError("DP hydration implementation must be an object")
        if set(raw_item) != set(hydrated_item) or {
            key: value for key, value in raw_item.items() if key != "candidates"
        } != {key: value for key, value in hydrated_item.items() if key != "candidates"}:
            raise TrustedRenderError("hydrated DP implementation changed creative intent")
        raw_candidates = raw_item.get("candidates")
        hydrated_candidates = hydrated_item.get("candidates")
        if (
            not isinstance(raw_candidates, list)
            or not isinstance(hydrated_candidates, list)
            or len(raw_candidates) != len(hydrated_candidates)
        ):
            raise TrustedRenderError("hydrated DP candidate coverage drift")
        mode = raw_item.get("mode")
        if mode in {"stock", "provided_asset"}:
            if authority_projection is None:
                authority_projection, authority_by_id = _authority_assets_from_projection(
                    root,
                    _fresh_asset_authority_projection(
                        root,
                        cut_id=cut_id,
                        revision_id=revision_id,
                        attempt=expected_attempt,
                        editorial_master=editorial_master,
                    ),
                    expected_attempt=expected_attempt,
                )
            for candidate_index, (raw_candidate, hydrated_candidate) in enumerate(
                zip(raw_candidates, hydrated_candidates, strict=True), 1
            ):
                expected_candidate = _expected_authority_candidate(
                    raw_candidate,
                    mode=str(mode),
                    authority_by_id=authority_by_id,
                    label=f"implementation {item_index} candidate {candidate_index}",
                )
                if hydrated_candidate != expected_candidate:
                    raise TrustedRenderError("hydrated authority candidate binding drift")
        elif mode == "hyperframes":
            event_id = _safe_token(raw_item.get("event_id"), "implementation.event_id")
            on_screen_text = raw_item.get("on_screen_text")
            for raw_candidate, hydrated_candidate in zip(
                raw_candidates, hydrated_candidates, strict=True
            ):
                spec_candidate = _exact_dict(
                    raw_candidate,
                    {
                        "candidate_id",
                        "visual_summary",
                        "component",
                        "render_params",
                        "render_spec_sha256",
                    },
                    "raw HyperFrames candidate",
                )
                full_candidate = _exact_dict(
                    hydrated_candidate,
                    {
                        "candidate_id",
                        "visual_summary",
                        "component",
                        "render_params",
                        "render_spec_sha256",
                        "preview_media",
                        "provenance",
                    },
                    "hydrated HyperFrames candidate",
                )
                if {key: full_candidate[key] for key in spec_candidate} != spec_candidate:
                    raise TrustedRenderError("hydrated HyperFrames spec drift")
                provenance = _exact_dict(
                    full_candidate["provenance"],
                    {"kind", "provider", "source_url", "license", "receipt"},
                    "hydrated HyperFrames provenance",
                )
                if provenance["kind"] != "hyperframes_render":
                    raise TrustedRenderError("hydrated HyperFrames provenance kind drift")
                verify_hyperframes_render_receipt(
                    root,
                    receipt_identity=provenance["receipt"],
                    expected_cut_id=cut_id,
                    expected_revision_id=revision_id,
                    expected_candidate_id=str(full_candidate["candidate_id"]),
                    expected_component=str(full_candidate["component"]),
                    expected_render_params=full_candidate["render_params"],
                    expected_on_screen_text=on_screen_text,
                    expected_media=full_candidate["preview_media"],
                    runtime_root=runtime_root,
                )
                expected_hyperframes.append(
                    {
                        "event_id": event_id,
                        "candidate_id": full_candidate["candidate_id"],
                        "component": full_candidate["component"],
                        "render_spec_sha256": full_candidate["render_spec_sha256"],
                        "preview_media": full_candidate["preview_media"],
                        "receipt": provenance["receipt"],
                    }
                )
        else:
            raise TrustedRenderError("DP hydration contains an unsupported mode")
    expected_authority = _authority_projection_binding(
        authority_projection, attempt=expected_attempt
    )
    if receipt["asset_authority"] != expected_authority:
        raise TrustedRenderError("DP hydration authority lineage drift")
    if receipt["hyperframes"] != expected_hyperframes:
        raise TrustedRenderError("DP hydration HyperFrames receipt lineage drift")
    return {
        **receipt,
        "content_hash": claimed_hash,
        "receipt_identity": receipt_file_identity,
        "raw_proposal_document": raw_proposal,
        "hydrated_proposal_document": hydrated_proposal,
    }


hydrate_dp_proposal = hydrate_dp_hyperframes_proposal


__all__ = [
    "HYPERFRAMES_LICENSE",
    "HYPERFRAMES_PROVIDER",
    "HYPERFRAMES_RENDER_CONTRACT",
    "HYPERFRAMES_RUNTIME_CONTRACT",
    "DP_HYDRATION_CONTRACT",
    "TRUSTED_RENDER_DIR",
    "TrustedRenderError",
    "hydrate_dp_proposal",
    "hydrate_dp_hyperframes_proposal",
    "hyperframes_runtime_status",
    "dp_hydration_receipt_identity",
    "dp_hydration_receipt_path",
    "prepare_hyperframes_runtime",
    "render_hyperframes_candidate",
    "verify_hyperframes_render_receipt",
    "verify_dp_hydration_receipt",
]
