"""Measured orchestration runtime identity for audio-capable Adapters."""

from __future__ import annotations

import importlib.metadata
import inspect
import marshal
import platform
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from ..hashing import hash_file, hash_object, sha256_bytes

AudioExecutionMode = Literal["fixture", "local", "paid_api", "subscription", "other"]


def _package_version(distribution: str, *, required: bool) -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError as exc:
        if required:
            raise RuntimeError(
                f"audio Adapter execution mode requires installed {distribution!r} runtime"
            ) from exc
        return "not-installed"


def _callable_identity(function: Callable[..., object]) -> dict[str, str]:
    target = getattr(function, "__func__", function)
    source = inspect.getsourcefile(target)
    code_hash: str
    if source is not None and Path(source).is_file():
        code_hash = hash_file(Path(source))
    else:
        code = getattr(target, "__code__", None)
        code_hash = (
            sha256_bytes(marshal.dumps(code))
            if code is not None
            else hash_object(
                {
                    "module": type(target).__module__,
                    "qualname": type(target).__qualname__,
                }
            )
        )
    return {
        "module": str(getattr(target, "__module__", type(target).__module__)),
        "qualname": str(getattr(target, "__qualname__", type(target).__qualname__)),
        "code_hash": code_hash,
    }


def measure_audio_runtime_hash(
    *,
    execution_mode: AudioExecutionMode,
    runner: Callable[..., object] | None,
    clipper: Callable[..., object],
) -> str:
    """Measure the runtime the orchestrator can actually attest.

    Subscription workers execute outside this Python process.  Their runtime
    cannot honestly be claimed here, so the identity records that limitation;
    exact request/response/clip bytes remain the replay boundary.
    """

    payload: dict[str, object] = {
        "schema": "podcast-subtitle-v2-audio-runtime-1",
        "execution_mode": execution_mode,
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "python_cache_tag": sys.implementation.cache_tag,
        "byteorder": sys.byteorder,
        "pydantic": _package_version("pydantic", required=True),
        "identity_helper_code_hash": hash_file(Path(__file__)),
        "clipper": _callable_identity(clipper),
    }
    if runner is not None:
        payload["runner"] = _callable_identity(runner)
    elif execution_mode == "paid_api":
        shared_llm = Path(__file__).resolve().parents[4] / "shared" / "llm.py"
        if not shared_llm.is_file():
            raise RuntimeError("paid audio Adapter cannot locate shared.llm executable")
        payload["runner"] = {
            "kind": "shared.llm.ask_with_audio",
            "google_genai": _package_version("google-genai", required=True),
            "shared_llm_code_hash": hash_file(shared_llm),
        }
    elif execution_mode == "subscription":
        payload["runner"] = {
            "kind": "external_subscription_worker",
            "runtime_measurement": "not_available_to_orchestrator",
            "proof_boundary": "exact_request_response_clip_bytes",
        }
    else:
        payload["runner"] = {"kind": "unconfigured"}
    return hash_object(payload)


__all__ = ["AudioExecutionMode", "measure_audio_runtime_hash"]
