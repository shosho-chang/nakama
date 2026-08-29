#!/usr/bin/env python3
"""把 long3 的 accepted stage 對齊成品規格——補做 2026-08-29 抑制修訂漏掉的那一半。

`suppress_l04_supporting_titles` 把五個 supporting title 從成品裡拿掉，Release
因此把那五個事件記成 `intentional_aroll`（沒有畫面元件）。但它沒有同步 Release
所引用的 accepted stage，那三份紀錄裡仍寫著 `supporting_title`。

平時沒有影響——成品就是成品。但 targeted revision 是**從 accepted stage 重播**
的：它照著紀錄要去投影一張 supporting title，而該 lane 已隨那次修訂退役，於是
`_mint_projected_component` 直接拒絕，任何對 long3 的修訂都走不到新的 preview。

這支只搬運既有事實：目標值全部照 Release 已封存的內容抄，不新增判斷。

範圍是這一集 store 裡的**每一個** accepted stage，不只 Release 具名的那三個——
修訂推進時讀的是它自己那份重播來的紀錄，只對齊三個等於沒對齊到真正被讀的地方。
除了事件本身，也要移除這五個事件殘留的 component 與 built asset：intentional
aroll 不畫任何東西，而 visual checkpoint 會把兩邊的集合做精確比對，只要有一筆
成品資產活著就整個拒絕投影。

    python agents/brook/script_video/finished_cut_production/amendments/
        operations/align_long3_supporting_title_acceptances.py --apply
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[6]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

EPISODE_ID = "20260805 林之晨"
EPISODES_ROOT = Path(r"G:\Footages")
RELEASE_ID = "release-af65a1d7a2ac611eb78be493"
TARGET_EVENT_IDS = frozenset(
    {
        "evt_k_shape_prices_inflation",
        "evt_agency_autonomy_title",
        "evt_future_values_deliberation",
        "evt_human_agency_definition",
        "evt_generalist_closing_title",
    }
)

EPISODE_ROOT = EPISODES_ROOT / EPISODE_ID
AUTHORITY = (
    EPISODE_ROOT
    / "highlights"
    / "finished-cut-production-v1"
    / "runtime"
    / "episodes"
    / EPISODE_ID
    / "runs"
    / "authority.json"
)
RELEASE = EPISODE_ROOT / "highlights" / "releases" / "v1" / f"{RELEASE_ID}.json"


def _release_truth() -> dict[str, dict]:
    """The sealed shape each event must end up with, taken from the Release itself."""
    release = json.loads(RELEASE.read_text(encoding="utf-8"))["release"]
    truth = {
        event["event_id"]: event
        for event in release["events"]
        if event["event_id"] in TARGET_EVENT_IDS
    }
    missing = TARGET_EVENT_IDS - set(truth)
    if missing:
        raise SystemExit(f"Release 沒有這些事件，拒絕動手：{sorted(missing)}")
    for event_id, event in truth.items():
        if not event.get("intentional_aroll") or event.get("lane") is not None:
            raise SystemExit(f"Release 對 {event_id} 的記載不是 intentional_aroll，拒絕動手")
    return truth


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="實際寫入；不給就只報告")
    args = parser.parse_args(argv)

    truth = _release_truth()

    # Every accepted stage in the episode's store, not only the three the Release
    # names.  A revision advances against *its own* replayed stages, so aligning
    # just the Release's three left the projection reading the same stale
    # supporting_title from the revision run and failing exactly as before.
    payload = json.loads(AUTHORITY.read_text(encoding="utf-8"))
    changed = 0
    dropped = 0
    touched: set[str] = set()
    for row in payload["runs"].values():
        view = row.get("view") or {}
        for key in ("accepted_stages", "accepted_stage_history"):
            for stage in view.get(key) or []:
                for event in stage.get("events", []):
                    event_id = event.get("event_id")
                    if event_id not in TARGET_EVENT_IDS:
                        continue
                    if event.get("semantic_kind") != "supporting_title":
                        continue
                    sealed = truth[event_id]
                    event["semantic_kind"] = sealed["semantic_kind"]
                    event["implementation_kind"] = sealed["implementation_kind"]
                    event["lane"] = sealed["lane"]
                    event["intentional_aroll"] = True
                    changed += 1
                    touched.add(stage["acceptance_id"])
                # An intentional-aroll event carries no on-screen component, so it
                # must carry no built asset either.  The visual checkpoint compares
                # the two sets exactly and refuses the whole projection when a
                # built asset survives for an event that no longer draws anything.
                for field in ("components", "built_components"):
                    rows = stage.get(field)
                    if not rows:
                        continue
                    keep = [row_ for row_ in rows if row_.get("event_id") not in TARGET_EVENT_IDS]
                    if len(keep) != len(rows):
                        dropped += len(rows) - len(keep)
                        stage[field] = keep
                        touched.add(stage["acceptance_id"])

    print(f"要對齊的 acceptance：{len(touched)} 份")
    for acceptance_id in sorted(touched):
        print(f"   {acceptance_id}")
    print(f"要改寫的事件列：{changed}")
    print(f"要移除的 component／built asset 列：{dropped}")
    if not (changed or dropped):
        print("（已對齊，無事可做）")
        return 0
    if not args.apply:
        print("（未寫入；加 --apply 才會動）")
        return 0
    AUTHORITY.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    print("已寫入")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
