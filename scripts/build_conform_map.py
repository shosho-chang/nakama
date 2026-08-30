"""build_conform_map — 把 Editorial Master 的修剪投影成一份 conform map 收據。

    py -3.10 scripts/build_conform_map.py "G:\\footages\\20260805 林之晨"

做三件事：

1. 從**還開著的 Resolve** 讀已核准 Master timeline 的每一段（timeline 位置
   ＋ 來源起點）。sealed 收據只記 timeline 位置、沒記來源起點，所以這一步
   必須連 Resolve；不改動任何東西，純讀。
2. 量三機與 normalized 音檔對 program feed 的時間偏移（FFT 互相關，三窗
   共識——沿用 speaker_assign 那一套，不另發明）。
3. 寫 `editorial-master/v1/conform-map.v1.json`。

**不 render 任何新素材**，也**不碰已 seal 的 Master 收據**——conform map 是
收據之外的追加 artifact，Master 的 content_hash 不受影響。

為什麼要這份東西：見 `shared/editorial_conform.py` 的模組說明。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from shared.editorial_conform import build_conform_map  # noqa: E402

MASTER_DIR = Path("editorial-master") / "v1"
RECEIPT_NAME = "EDITORIAL-MASTER.json"
SNAPSHOT_NAME = "timeline-snapshot.json"
CONFORM_NAME = "conform-map.v1.json"

#: 固定機位配置。key 是 conform map 的來源鍵，值是 episode 內的相對路徑。
CAMERA_SOURCES = {
    "cam1": Path("Video") / "1_CAMERA 1.mp4",
    "cam2": Path("Video") / "2_CAMERA 2.mp4",
    "cam3": Path("Video") / "3_CAMERA 3.mp4",
}
AUDIO_SOURCE = Path("normalized.wav")


def _read_timeline_items(episode_dir: Path, timeline_name: str) -> tuple[float, list[dict]]:
    """從 Resolve 讀出 Master timeline 每一段的 timeline 位置與來源起點。"""
    from build_resolve_project import connect_resolve

    resolve = connect_resolve()
    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()
    if project is None or project.GetName() != episode_dir.name:
        project = pm.LoadProject(episode_dir.name)
    if project is None:
        raise SystemExit(f"Resolve 裡找不到 project「{episode_dir.name}」")

    timeline = None
    for i in range(1, project.GetTimelineCount() + 1):
        candidate = project.GetTimelineByIndex(i)
        if candidate and candidate.GetName() == timeline_name:
            timeline = candidate
            break
    if timeline is None:
        raise SystemExit(f"Resolve 裡找不到 timeline「{timeline_name}」——被改名或刪掉了？")

    fps = float(project.GetSetting("timelineFrameRate"))
    start = timeline.GetStartFrame()
    items: list[dict] = []
    for track in range(1, timeline.GetTrackCount("video") + 1):
        for item in timeline.GetItemListInTrack("video", track) or []:
            media = item.GetMediaPoolItem()
            items.append(
                {
                    "tl_start": item.GetStart() - start,
                    "tl_end": item.GetEnd() - start,
                    "src_left_offset": item.GetLeftOffset() if media else None,
                    "source_path": media.GetClipProperty("File Path") if media else None,
                }
            )
    return fps, items


def _pick_body_source(items: list[dict], fps: float) -> str:
    """主體 = 在 timeline 上佔最多秒數的那一支來源（Intro/Outro 一定比它短）。"""
    totals: dict[str, float] = defaultdict(float)
    for item in items:
        if item["source_path"]:
            totals[item["source_path"]] += (item["tl_end"] - item["tl_start"]) / fps
    if not totals:
        raise SystemExit("Master timeline 沒有任何帶來源的 item")
    ranked = sorted(totals.items(), key=lambda kv: -kv[1])
    for path, seconds in ranked:
        print(f"  來源 {Path(path).name}: {seconds:.1f}s")
    return ranked[0][0]


def _measure_sources(episode_dir: Path, body_path: Path, *, skip_sync: bool) -> dict[str, dict]:
    """量各素材對 program feed 的偏移；找不到檔案就略過該來源。"""
    from shared.speaker_assign import _measure_offset

    sources: dict[str, dict] = {
        "program": {"path": body_path.name, "offset_sec": 0.0},
    }
    candidates = {**CAMERA_SOURCES, "audio": AUDIO_SOURCE}
    for key, rel in candidates.items():
        path = episode_dir / rel
        if not path.is_file():
            print(f"  {key}: 找不到 {rel}——略過")
            continue
        if skip_sync:
            offset = 0.0
            print(f"  {key}: --skip-sync，偏移當 0")
        else:
            measured = _measure_offset(episode_dir / body_path.name, path)
            if measured is None:
                print(f"  {key}: 量不到可靠偏移——**不寫進 conform map**，避免用錯的值")
                continue
            offset = round(float(measured), 4)
            print(f"  {key}: offset {offset:+.4f}s")
        sources[key] = {"path": rel.as_posix(), "offset_sec": offset}
    return sources


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Editorial Master 修剪 → conform map 收據")
    parser.add_argument("episode", help="episode 資料夾")
    parser.add_argument("--body", help="主體來源檔名（預設自動選佔最多秒數的那支）")
    parser.add_argument(
        "--skip-sync", action="store_true", help="跳過偏移量測（除錯用；正式流程不要用）"
    )
    parser.add_argument("--dry-run", action="store_true", help="只印不寫檔")
    args = parser.parse_args(argv)

    episode_dir = Path(args.episode)
    master_dir = episode_dir / MASTER_DIR
    receipt_path = master_dir / RECEIPT_NAME
    if not receipt_path.is_file():
        raise SystemExit(f"找不到 Editorial Master 收據：{receipt_path}")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    snapshot = json.loads((master_dir / SNAPSHOT_NAME).read_text(encoding="utf-8"))
    timeline_name = snapshot["timeline"]["name"]

    print(f"Master timeline：{timeline_name}")
    fps, items = _read_timeline_items(episode_dir, timeline_name)
    print(f"fps {fps}，video items {len(items)}")

    body_path = Path(args.body) if args.body else Path(_pick_body_source(items, fps))
    print(f"主體來源：{body_path.name}\n")

    print("量測同步偏移：")
    sources = _measure_sources(episode_dir, body_path, skip_sync=args.skip_sync)

    lineage = {
        key: receipt[key] for key in ("contract", "episode_id", "content_hash") if key in receipt
    }
    lineage["master_media_sha256"] = receipt["artifacts"]["media"]["sha256"]
    lineage["master_srt_sha256"] = receipt["artifacts"]["subtitles"]["sha256"]

    cmap = build_conform_map(
        episode_id=episode_dir.name,
        fps=fps,
        lineage=lineage,
        timeline_items=items,
        sources=sources,
        body_source_path=str(body_path),
    )

    from shared.editorial_conform import removed_spans

    removed = removed_spans(cmap)
    print(f"\n主體區段 {len(cmap['segments'])} 段；片頭片尾 {len(cmap['unconformable'])} 段")
    print(f"修剪掉 {len(removed)} 刀，共 {sum(r['duration_sec'] for r in removed):.1f}s：")
    for row in removed:
        print(
            f"  {row['source_start_sec']:.1f} – {row['source_end_sec']:.1f}s"
            f"（{row['duration_sec']:.1f}s）"
        )

    out = master_dir / CONFORM_NAME
    if args.dry_run:
        print(f"\n--dry-run：不寫檔（本來會寫 {out}）")
        return 0
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cmap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(out)
    print(f"\n寫入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
