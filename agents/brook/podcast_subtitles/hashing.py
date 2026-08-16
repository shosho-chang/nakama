"""Canonical serialisation and content identities for Subtitle V2.

Hashes in this module are identities, not checksums added after the fact.  The
same logical value therefore has the same digest regardless of dict insertion
order or pretty-printing, and non-finite floats are rejected.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import math
import os
import stat
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping

from pydantic import BaseModel


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _jsonable(value.model_dump(mode="json", exclude_none=False))
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _jsonable(getattr(value, field.name))
            for field in dataclasses.fields(value)
            if field.metadata.get("canonical_json", True)
        }
    if isinstance(value, Enum):
        return _jsonable(value.value)
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return {"$bytes": base64.b64encode(value).decode("ascii")}
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite floats cannot be content-addressed")
        return value
    if isinstance(value, Mapping):
        non_string_keys = [key for key in value if not isinstance(key, str)]
        if non_string_keys:
            raise TypeError(
                "canonical JSON mappings require string keys; got "
                f"{type(non_string_keys[0]).__name__}"
            )
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (set, frozenset)):
        converted = [_jsonable(item) for item in value]
        return sorted(converted, key=lambda item: canonical_json_bytes(item))
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    """Return deterministic UTF-8 JSON bytes for a supported value."""

    return json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_object(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def hash_file(path: str | Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def measure_regular_file(
    path: str | Path,
    *,
    chunk_size: int = 1024 * 1024,
) -> tuple[str, int]:
    """Hash one non-reparse regular file with stable pre/post file identity.

    This is the narrow input-binding primitive for security-sensitive artifacts.
    It rejects symlinks/reparse points and detects replacement or mutation while
    the stream is being read instead of combining a digest and size measured
    from potentially different file versions.
    """

    candidate = Path(path)
    before_path = candidate.lstat()
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attributes = int(getattr(before_path, "st_file_attributes", 0))
    if stat.S_ISLNK(before_path.st_mode) or attributes & reparse_flag:
        raise ValueError("content identity requires a non-reparse regular file")
    if not stat.S_ISREG(before_path.st_mode):
        raise ValueError("content identity requires a regular file")

    digest = hashlib.sha256()
    with candidate.open("rb") as stream:
        before_stream = os.fstat(stream.fileno())
        if not stat.S_ISREG(before_stream.st_mode):
            raise ValueError("opened content identity source is not a regular file")
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
        after_stream = os.fstat(stream.fileno())
    after_path = candidate.lstat()

    after_attributes = int(getattr(after_path, "st_file_attributes", 0))
    if (
        stat.S_ISLNK(after_path.st_mode)
        or after_attributes & reparse_flag
        or not stat.S_ISREG(after_path.st_mode)
    ):
        raise ValueError("regular file path changed to a reparse or non-regular object")

    def path_identity(value: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
        return (
            value.st_dev,
            value.st_ino,
            value.st_mode,
            int(getattr(value, "st_file_attributes", 0)),
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )

    def stream_identity(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
        return (
            value.st_dev,
            value.st_ino,
            value.st_mode,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )

    def core_identity(value: os.stat_result) -> tuple[int, int, int]:
        return (value.st_dev, value.st_ino, value.st_size)

    if (
        path_identity(before_path) != path_identity(after_path)
        or stream_identity(before_stream) != stream_identity(after_stream)
        or core_identity(before_path) != core_identity(before_stream)
        or core_identity(after_path) != core_identity(after_stream)
    ):
        raise ValueError("regular file changed while its content identity was measured")
    return digest.hexdigest(), after_stream.st_size


def stage_cache_key(
    *,
    stage: str,
    upstream_hashes: Iterable[str],
    policy_hash: str,
    code_hash: str,
    config_hash: str,
) -> str:
    """Identify a deterministic stage from every input that can affect it."""

    payload = {
        "stage": stage,
        "upstream_hashes": list(upstream_hashes),
        "policy_hash": policy_hash,
        "code_hash": code_hash,
        "config_hash": config_hash,
    }
    if not stage or any(not value for value in payload["upstream_hashes"]):
        raise ValueError("stage and upstream hashes must be non-empty")
    if not all((policy_hash, code_hash, config_hash)):
        raise ValueError("policy, code, and config hashes must be non-empty")
    return hash_object(payload)


def canonical_token_hash(tokens: Iterable[Any]) -> str:
    """Hash ordered ``(token_id, lexeme)`` truth without cue/timing metadata."""

    pairs: list[list[str]] = []
    for token in tokens:
        if isinstance(token, BaseModel):
            data = token.model_dump(mode="json", exclude_none=False)
        elif dataclasses.is_dataclass(token) and not isinstance(token, type):
            data = dataclasses.asdict(token)
        elif isinstance(token, Mapping):
            data = dict(token)
        else:
            raise TypeError(f"unsupported canonical token: {type(token).__name__}")
        token_id = data.get("id", data.get("token_id"))
        lexeme = data.get("text", data.get("lexeme"))
        if not isinstance(token_id, str) or not token_id:
            raise ValueError("canonical token ID must be a non-empty string")
        if not isinstance(lexeme, str) or not lexeme:
            raise ValueError("canonical token lexeme must be a non-empty string")
        pairs.append([token_id, lexeme])
    return sha256_bytes(
        json.dumps(pairs, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


def target_fingerprint(*, audio_span_id: str, token_ids: Iterable[str], lexemes: str) -> str:
    """Stable review fingerprint used to reject decisions made against stale text."""

    return hash_object(
        {
            "audio_span_id": audio_span_id,
            "token_ids": list(token_ids),
            "lexemes": lexemes,
        }
    )


__all__ = [
    "canonical_json_bytes",
    "canonical_token_hash",
    "hash_file",
    "hash_object",
    "measure_regular_file",
    "sha256_bytes",
    "stage_cache_key",
    "target_fingerprint",
]
