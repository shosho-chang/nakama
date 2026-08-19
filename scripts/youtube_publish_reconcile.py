"""Read-only YouTube platform reconciliation for persisted release targets."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from publish_upload import _load_yt  # noqa: E402

from agents.usopp.youtube_publish import reconcile_and_persist  # noqa: E402
from shared.release_store import get_release, list_releases, update_target  # noqa: E402


def _targets(episode: str | None, cut: str | None):
    for release in list_releases(episode):
        if cut and release["cut_id"] != cut:
            continue
        full = get_release(release["episode"], release["cut_id"])
        for target in full["targets"]:
            if target["platform"] == "youtube" and target.get("video_id"):
                yield full, target


def reconcile_targets(service, *, episode: str | None = None, cut: str | None = None) -> list[dict]:
    results = []
    for release, target in _targets(episode, cut):
        try:
            result = reconcile_and_persist(service, target)
        except Exception as exc:  # noqa: BLE001 - persist per-target query failure
            from datetime import datetime, timezone

            error = f"YouTube reconciliation failed: {str(exc)[:400]}"
            update_target(
                target["id"],
                reconciliation_error=error,
                last_reconciled_at=datetime.now(timezone.utc).isoformat(),
            )
            results.append({"cut_id": release["cut_id"], "error": error})
            continue
        results.append({"cut_id": release["cut_id"], **result.target_fields()})
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reconcile YouTube video + zh-TW CC state")
    parser.add_argument("--episode")
    parser.add_argument("--cut")
    args = parser.parse_args(argv)
    if args.cut and not args.episode:
        parser.error("--cut requires --episode")
    service = _load_yt()
    results = reconcile_targets(service, episode=args.episode, cut=args.cut)
    print(json.dumps({"reconciled": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
