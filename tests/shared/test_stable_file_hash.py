from __future__ import annotations

from shared.stable_file_hash import _sha256_snapshot, stable_sha256


def test_stable_sha256_reuses_unchanged_file_and_invalidates_mutation(tmp_path):
    path = tmp_path / "media.mov"
    path.write_bytes(b"first-media")
    _sha256_snapshot.cache_clear()

    first = stable_sha256(path)
    assert stable_sha256(path) == first
    assert _sha256_snapshot.cache_info().hits == 1
    assert _sha256_snapshot.cache_info().misses == 1

    path.write_bytes(b"other-media")
    assert stable_sha256(path) != first
    assert _sha256_snapshot.cache_info().misses == 2
