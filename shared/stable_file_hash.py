"""Process-local SHA-256 reuse for files whose filesystem identity is unchanged."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path


class StableFileChangedError(RuntimeError):
    """Raised when a file changes around a cached identity validation."""


@lru_cache(maxsize=8192)
def _sha256_snapshot(path_text: str, size: int, mtime_ns: int) -> str:
    path = Path(path_text)
    before = path.stat()
    if before.st_size != size or before.st_mtime_ns != mtime_ns:
        raise StableFileChangedError("file changed before identity validation")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    after = path.stat()
    if after.st_size != size or after.st_mtime_ns != mtime_ns:
        raise StableFileChangedError("file changed during identity validation")
    return digest.hexdigest()


def stable_sha256(path: Path) -> str:
    """Hash once per resolved path/size/mtime tuple in the current process."""

    stat = path.stat()
    return _sha256_snapshot(str(path.resolve()), stat.st_size, stat.st_mtime_ns)


__all__ = ["StableFileChangedError", "stable_sha256"]
