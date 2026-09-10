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
    layered: list[list[dict]] = []
    for track in range(1, timeline.GetTrackCount("video") + 1):
        rows: list[dict] = []
        for item in timeline.GetItemListInTrack("video", track) or []:
            media = item.GetMediaPoolItem()
            rows.append(
                {
                    "tl_start": item.GetStart() - start,
                    "tl_end": item.GetEnd() - start,
                    "src_left_offset": item.GetLeftOffset() if media else None,
                    "source_path": media.GetClipProperty("File Path") if media else None,
                }
            )
        layered.append(rows)
    return fps, _flatten_tracks(layered)


def _flatten_tracks(layered: list[list[dict]]) -> list[dict]:
    """多軌壓成一條「實際看到的畫面」，高軌蓋低軌。

    以前這裡把每一軌的 item 全部倒進同一個清單。只認一支主體時看不出問題——
    上層軌的 item 來源不是主體，會被歸進 unconformable 丟掉。一旦三機都算主體
    （切鏡不是片頭片尾），V2／V3 的插入畫面就和 V1 底下那一段在成片時間上重疊，
    `build_conform_map` 直接報「主體區段重疊」（20260901 蘇予昕：V1 634 個 item、
    V2 12 個、V3 2 個）。

    Resolve 的合成語意是不透明素材由**最上層**決定畫面，所以這裡照樣做：高軌優先，
    低軌被蓋掉的部分切開，只留露出來的區間（`src_left_offset` 跟著位移）。
    """
    covered: list[tuple[int, int]] = []
    out: list[dict] = []
    for rows in reversed(layered):  # 由最高軌往下
        for item in sorted(rows, key=lambda x: int(x["tl_start"])):
            spans = [(int(item["tl_start"]), int(item["tl_end"]))]
            for lo, hi in covered:
                nxt: list[tuple[int, int]] = []
                for a, b in spans:
                    if hi <= a or lo >= b:
                        nxt.append((a, b))
                        continue
                    if a < lo:
                        nxt.append((a, lo))
                    if hi < b:
                        nxt.append((hi, b))
                spans = nxt
            for a, b in spans:
                if b <= a:
                    continue
                left = item["src_left_offset"]
                out.append(
                    {
                        "tl_start": a,
                        "tl_end": b,
                        # 切開之後來源起點要跟著往後推同樣的格數。
                        "src_left_offset": None if left is None else int(left) + (a - int(item["tl_start"])),
                        "source_path": item["source_path"],
                    }
                )
            covered.append((int(item["tl_start"]), int(item["tl_end"])))
    out.sort(key=lambda x: x["tl_start"])
    return out


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


def _resolve_body_media(episode_dir: Path, body_path: Path) -> Path:
    """主體來源的實際檔案位置。

    `_pick_body_source` 回的是 Resolve 記的**完整路徑**，而機位檔放在
    `<episode>/Video/`。原本這裡直接用 `episode_dir / body_path.name`，等於把
    `Video/` 這一層丟掉——參考檔根本不存在，於是 `_measure_offset` 對每一個來源
    都回 None，四個來源全部被「量不到可靠偏移」略過，conform map 只剩 `program`。
    下游 `run_transcript_prose` 需要 `audio` 來源，就報「conform map 沒有來源
    「audio」」——錯誤訊息離真正的原因隔了兩層（2026-09-10 蘇予昕實況）。
    """
    candidates = [body_path] if body_path.is_absolute() else []
    candidates += [
        episode_dir / body_path,
        episode_dir / "Video" / body_path.name,
        episode_dir / body_path.name,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    tried = "\n  ".join(str(candidate) for candidate in candidates)
    raise SystemExit(f"找不到主體來源檔，量不了偏移。試過：\n  {tried}")


def _measure_sources(episode_dir: Path, body_path: Path, *, skip_sync: bool) -> dict[str, dict]:
    """量各素材對 program feed 的偏移；找不到檔案就略過該來源。"""
    from shared.speaker_assign import _measure_offset

    body_media = _resolve_body_media(episode_dir, body_path)
    sources: dict[str, dict] = {
        "program": {"path": body_path.name, "offset_sec": 0.0},
    }
    candidates = {**CAMERA_SOURCES, "audio": AUDIO_SOURCE}
    for key, rel in candidates.items():
        path = episode_dir / rel
        if not path.is_file():
            # 機位檔可能放在 episode 根目錄而不是 Video/（早期集數）。
            alternative = episode_dir / rel.name
            if alternative.is_file():
                path, rel = alternative, Path(rel.name)
            else:
                print(f"  {key}: 找不到 {rel}——略過")
                continue
        if skip_sync:
            offset = 0.0
            print(f"  {key}: --skip-sync，偏移當 0")
        else:
            measured = _measure_offset(body_media, path)
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

    # 剪接台直接吃三機原檔、在 timeline 上切鏡時，每一次切鏡都是一個「來源不是
    # 主體」的 item。只認一支主體會把它們全部歸成片頭片尾——20260901 蘇予昕 有
    # 371 段（全片 47%）這樣被埋掉，短片導播走到切鏡點就報「三機沒有對應畫面」。
    # 機位同步過（`sources` 的實測偏移），共用一個 source 時鐘，可以一起當主體。
    camera_names = {rel.name.lower() for rel in CAMERA_SOURCES.values()}
    extra_bodies = sorted(
        {
            item["source_path"]
            for item in items
            if item.get("source_path")
            and Path(item["source_path"]).name.lower() in camera_names
            and Path(item["source_path"]).name.lower() != body_path.name.lower()
        }
    )
    if extra_bodies:
        print("同步機位一併當主體（切鏡不是片頭片尾）：")
        for path in extra_bodies:
            print(f"  {Path(path).name}")

    cmap = build_conform_map(
        episode_id=episode_dir.name,
        fps=fps,
        lineage=lineage,
        timeline_items=items,
        sources=sources,
        body_source_path=str(body_path),
        extra_body_source_paths=extra_bodies,
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
