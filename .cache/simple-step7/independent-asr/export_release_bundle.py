from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worktree", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()

    worktree = args.worktree.resolve(strict=True)
    destination = args.destination.resolve()
    cache = worktree / ".cache" / "simple-step7"
    explicit = {
        cache / "release-v1-corrected.srt": Path("release/release-v1-corrected.srt"),
        cache / "release-v1-ledger.json": Path("release/release-v1-ledger.json"),
        cache / "audio-final-v1-ledger.json": Path("release/audio-final-v1-ledger.json"),
        cache / "final-v2-corrected.srt": Path("base/final-v2-corrected.srt"),
        cache / "final-v2-ledger.json": Path("base/final-v2-ledger.json"),
        cache / "final-v2-unresolved.json": Path("base/final-v2-unresolved.json"),
    }
    script_root = cache / "independent-asr"
    for name in (
        "run_v2_adapter.py",
        "finalize_priority_audio.py",
        "prepare_remaining_major.py",
        "finalize_remaining_major.py",
    ):
        explicit[script_root / name] = Path("operator") / name
    for source_root, target_root in (
        (script_root / "clips", Path("evidence/priority-v1/clips")),
        (script_root / "priority-v1", Path("evidence/priority-v1/recognition")),
        (
            script_root / "remaining-major-v1",
            Path("evidence/remaining-major-v1"),
        ),
    ):
        for source in sorted(path for path in source_root.rglob("*") if path.is_file()):
            explicit[source] = target_root / source.relative_to(source_root)

    entries: list[dict[str, object]] = []
    payloads: list[tuple[Path, bytes]] = []
    for source, relative in sorted(explicit.items(), key=lambda item: str(item[1])):
        raw = source.read_bytes()
        target = destination / relative
        if target.exists() and target.read_bytes() != raw:
            raise ValueError(f"destination differs before export: {target}")
        payloads.append((target, raw))
        entries.append(
            {
                "path": relative.as_posix(),
                "size_bytes": len(raw),
                "sha256": _sha256(raw),
            }
        )

    manifest = {
        "schema_version": 1,
        "contract": "podcast-subtitle-v2-degraded-audio-release-export-v1",
        "episode_id": "20260814-moboo",
        "provenance_status": "degraded_dual_asr_major_complete_not_full_v2_checkpoint",
        "canonical_release_srt": "release/release-v1-corrected.srt",
        "canonical_release_srt_sha256": (
            "8cf28558050e9c5d7cf4fbbcfa430fda9ba534acf20297ac7f4a0b49a674681c"
        ),
        "file_count": len(entries),
        "files": entries,
    }
    manifest_raw = _canonical(manifest)
    manifest_path = destination / "EXPORT-MANIFEST.json"
    if manifest_path.exists() and manifest_path.read_bytes() != manifest_raw:
        raise ValueError(f"destination manifest differs before export: {manifest_path}")

    for target, raw in payloads:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(raw)
    destination.mkdir(parents=True, exist_ok=True)
    if not manifest_path.exists():
        manifest_path.write_bytes(manifest_raw)
    for target, raw in payloads:
        if target.read_bytes() != raw:
            raise ValueError(f"export verification failed: {target}")
    if manifest_path.read_bytes() != manifest_raw:
        raise ValueError("export manifest verification failed")
    print(
        f"files={len(entries)} manifest_sha256={_sha256(manifest_raw)} "
        f"destination={destination}"
    )
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
