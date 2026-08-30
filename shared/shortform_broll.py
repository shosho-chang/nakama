"""短片素材層的把關（ADR-067）——短片線自己的 stock gate，不走 ADR-065 收據鏈。

## 為什麼短片要自己一條

長片的素材由 ADR-065 的 Director → DP → 語意稽核收據鏈授權。短片今天走不了那條：

- SKILL Step 3.2 已把那條鏈標為「已停用（ADR-066）… 不要執行」
- `build_authoritative_broll_receipt` 沒有 `title_items` 就會自己去讀
  `<cid>_titles.json`，要求**每一張字卡**都帶 DP 的 `visual_materialization`
  ——短片的字卡走的是逐字稿保證（`run_shortform_titles`），一接上就全毀
- ADR-066 的 `ShortPolicy` 還是個殼（只驗片長與「≤2 張 title-like 卡」，
  跟 mode B 的逐子句字卡直接衝突），`_materialization` 裡沒有 short 分支

## 這條 gate 換掉什麼、沒換掉什麼

**沒換掉的是授權。** 每一支素材都必須有 acquisition receipt，而且檔案 bytes 的
SHA-256 要對得上收據——這條跟長片一樣嚴，一步都不能省。

**換掉的是「獨立 DP agent 覆核語意」**，改成兩件機械可驗的事：

1. **對齊哪句話**：每個 item 宣告 `source_cues`，落點必須包在那幾句的時間裡。
   這跟字卡的逐字稿保證同一個精神——不能證明「這支素材好」，但能證明
   「這支素材對的是哪句話」，剩下的由人看一眼。
2. **直式**：短片素材必須是直式（修修 2026-08-30）。橫的素材裁進 9:16 只會
   剩中間一條，跟短片那個「硬把橫的切成直的」是同一種病。

另外兩條版面紀律直接照 SKILL Step 9 寫死：素材不可壓到開場上下分割，
也不可蓋掉 punch zoom（「punch zoom 與具象比喻衝突時，縮短 punch 讓位 footage」
——所以這裡是叫你改 zoom 企劃，不是默默讓 B-roll 蓋上去）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from agents.brook.script_video.highlight_broll import probe_stock_video

CONTRACT = "shortform-broll-receipt-v1"
#: acquisition receipt 的契約名——與長片線同一份（授權證據不分長短片）。
ACQUISITION_CONTRACT = "podcast-highlight-asset-acquisition-receipt-v1"

#: 落點與 source_cues 的容許誤差（秒）。與字卡的 trigger 對齊容差同量級。
CUE_TOLERANCE_SEC = 0.35
MIN_SPAN_SEC = 0.8
#: 這一版只收 stock 影片／照片。貼紙、概念卡、icon 動畫還在長片線的收據鏈上，
#: 短片要用得先各自定 gate——不要因為「反正都是 track 4」就一起放行。
SUPPORTED_KINDS = ("video", "photo")
STRUCTURAL_KINDS = ("badge",)


class ShortformBrollError(Exception):
    """短片素材層契約違反。"""


def _canonical_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _asset_paths(episode_dir: Path, slug: str) -> tuple[Path, Path]:
    assets = episode_dir / "assets" / "broll"
    media = sorted(p for p in assets.glob(f"{slug}.*") if p.suffix != ".json")
    if len(media) != 1:
        raise ShortformBrollError(
            f"assets/broll/{slug}.* 必須恰好一個素材檔（找到 {len(media)} 個）"
        )
    receipt = assets / f"{slug}.acquisition.json"
    if not receipt.is_file():
        raise ShortformBrollError(
            f"assets/broll/{slug}.acquisition.json 不存在——素材沒有授權收據就不能上片"
        )
    return media[0], receipt


def _load_acquisition(path: Path, media: Path) -> dict[str, Any]:
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ShortformBrollError(f"{path.name} 不是合法 JSON") from exc
    if receipt.get("contract") != ACQUISITION_CONTRACT:
        raise ShortformBrollError(f"{path.name} 的 contract 不是 {ACQUISITION_CONTRACT}")
    for field in ("provider", "source_url", "license", "source_class", "original_media"):
        if not receipt.get(field):
            raise ShortformBrollError(f"{path.name} 缺 {field}")
    digest = hashlib.sha256(media.read_bytes()).hexdigest()
    if digest != str(receipt["original_media"].get("sha256")):
        raise ShortformBrollError(
            f"{media.name} 的 SHA-256 與 {path.name} 不符——素材被換過或收據不是這支的"
        )
    return receipt


def _cue_span(cues: dict[int, dict], ids: list[int], *, where: str) -> tuple[float, float, str]:
    if not ids:
        raise ShortformBrollError(f"{where} 缺 source_cues——素材必須說清楚它對的是哪幾句")
    if ids != list(range(ids[0], ids[-1] + 1)):
        raise ShortformBrollError(f"{where} 的 source_cues 必須連續：{ids}")
    missing = [n for n in ids if n not in cues]
    if missing:
        raise ShortformBrollError(f"{where} 的 source_cues 在最新 SRT 不存在：{missing}")
    return (
        cues[ids[0]]["t0"],
        cues[ids[-1]]["t1"],
        "".join(cues[n]["text"] for n in ids),
    )


def verify_shortform_broll(
    episode_dir: Path,
    cid: str,
    items: list[dict[str, Any]],
    *,
    editorial_master_lineage: dict[str, Any],
    cues: list[dict[str, Any]],
    punches: list[dict[str, Any]] | None = None,
    opener_sec: float = 0.0,
) -> dict[str, Any]:
    """驗短片素材企劃，並在每個 item 上蓋出 materializer 要吃的 projection。

    蓋出來的 `visual_materialization` 形狀與長片線相同——下游的物化程式碼因此
    一行都不用改，但**授權它的是這條 gate**，不是 DP 稽核鏈。
    """
    if not isinstance(items, list):
        raise ShortformBrollError("B-roll plan items 必須是 array")
    by_n = {int(c["n"]): c for c in cues}
    windows = [
        (float(p["t0"]), float(p["t1"]), f"punch@{float(p['t0']):.2f}s") for p in (punches or [])
    ]
    if opener_sec > 0:
        windows.append((0.0, float(opener_sec), "開場上下分割"))

    stock: list[dict[str, Any]] = []
    slugs: set[str] = set()
    placed: list[tuple[float, float, str]] = []
    for index, item in enumerate(items):
        kind = str(item.get("kind") or "")
        where = f"item {index}（{item.get('slug')}）"
        if kind in STRUCTURAL_KINDS:
            continue
        if kind not in SUPPORTED_KINDS:
            raise ShortformBrollError(
                f"{where} kind={kind!r} 還不在短片素材層裡（目前只有 "
                f"{'/'.join(SUPPORTED_KINDS)}）——貼紙／概念卡／icon 要各自定 gate"
            )
        slug = str(item.get("slug") or "").strip()
        if not slug or slug in slugs:
            raise ShortformBrollError(f"{where} 的 slug 必須非空且唯一")
        slugs.add(slug)

        t0, t1 = float(item["t0"]), float(item["t1"])
        if t1 - t0 < MIN_SPAN_SEC:
            raise ShortformBrollError(f"{where} 只有 {t1 - t0:.2f}s——B-roll 至少 {MIN_SPAN_SEC}s")
        for other_t0, other_t1, label in placed:
            if t0 < other_t1 and other_t0 < t1:
                raise ShortformBrollError(f"{where} 與 {label} 重疊")
        placed.append((t0, t1, where))
        for w0, w1, label in windows:
            if t0 < w1 and w0 < t1:
                raise ShortformBrollError(
                    f"{where} {t0:.2f}–{t1:.2f}s 壓到{label} {w0:.2f}–{w1:.2f}s。"
                    "SKILL Step 9：punch zoom 與具象比喻衝突時縮短 punch 讓位 footage"
                    "——改 zoom 企劃或挪素材，不要疊上去"
                )

        cue_ids = [int(n) for n in (item.get("source_cues") or [])]
        cue_t0, cue_t1, quote = _cue_span(by_n, cue_ids, where=where)
        if t0 < cue_t0 - CUE_TOLERANCE_SEC or t1 > cue_t1 + CUE_TOLERANCE_SEC:
            raise ShortformBrollError(
                f"{where} 的落點 {t0:.2f}–{t1:.2f}s 沒有包在 source_cues "
                f"{cue_ids[0]}–{cue_ids[-1]}（{cue_t0:.2f}–{cue_t1:.2f}s）裡"
                "——素材必須對齊當下那句話，不是對齊整支的大主題"
            )

        media, receipt_path = _asset_paths(episode_dir, slug)
        acquisition = _load_acquisition(receipt_path, media)
        probe = probe_stock_video(media)
        stream = probe["video_streams"][0]
        if stream["height"] <= stream["width"]:
            raise ShortformBrollError(
                f"{where} 的素材是 {stream['width']}×{stream['height']} 橫式——"
                "短片素材必須直式（修修 2026-08-30）。橫的裁進 9:16 只會剩中間一條"
            )
        src_in = float(item.get("src_in", 0.0))
        if src_in < 0 or src_in + (t1 - t0) > probe["duration_seconds"] + 0.05:
            raise ShortformBrollError(
                f"{where} 的 src_in={src_in}s ＋ 片長 {t1 - t0:.2f}s 超過素材長度 "
                f"{probe['duration_seconds']:.2f}s"
            )

        relative = media.resolve().relative_to(episode_dir.resolve()).as_posix()
        provenance = {
            "kind": "stock_source",
            "provider": acquisition["provider"],
            "source_url": acquisition["source_url"],
            "license": acquisition["license"],
            "receipt": {
                "bytes": receipt_path.stat().st_size,
                "path": receipt_path.resolve().relative_to(episode_dir.resolve()).as_posix(),
                "sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
            },
        }
        media_evidence = {
            "bytes": media.stat().st_size,
            "path": relative,
            "sha256": str(acquisition["original_media"]["sha256"]),
        }
        item["visual_materialization"] = {
            "materialization_id": f"{cid}-{slug}",
            "event_id": slug,
            "authority": CONTRACT,
            "target_lane": "broll_track2",
            "implementation_kind": "stock_video" if kind == "video" else "stock_photo",
            "mode": "stock",
            "cue_ids": cue_ids,
            "quote": quote,
            "t0": t0,
            "t1": t1,
            "source_range": {"start_sec": src_in, "end_sec": round(src_in + (t1 - t0), 3)},
            "on_screen_text": None,
            "media": media_evidence,
            "provenance": provenance,
            "render_spec": None,
        }
        if kind == "video":
            stock.append(
                {
                    "category": "stock_video",
                    "kind": kind,
                    "slug": slug,
                    "materialization_id": f"{cid}-{slug}",
                    "t0": t0,
                    "t1": t1,
                    "source_cues": cue_ids,
                    "quote": quote,
                    "asset": {**media_evidence, "media": probe},
                    "provenance": provenance,
                }
            )

    core = {
        "contract": CONTRACT,
        "cut_id": cid,
        "format": "short",
        "editorial_master_lineage": editorial_master_lineage,
        "plan_sha256": _canonical_hash(items),
        "stock_video_count": len(stock),
        "stock_videos": stock,
    }
    return {**core, "content_hash": _canonical_hash(core)}


def write_shortform_broll_receipt(episode_dir: Path, cid: str, receipt: dict[str, Any]) -> Path:
    """物化成功之後才落收據（先寫暫存再 replace，中斷不會留半份）。"""
    from agents.brook.script_video.highlight_broll import receipt_path

    path = receipt_path(episode_dir, cid)
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_suffix(path.suffix + ".tmp")
    staging.write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    staging.replace(path)
    return path
