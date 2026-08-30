"""Editorial Master conform map — 把成片的修剪投影到每一支原始素材。

修修 2026-08-30 裁決：**Editorial Master 不是「唯一能用的那個檔案」，而是
「一組修剪操作」**。同一組修剪可以套用到所有 raw file（機器切換的 program
feed、三台 CAM、normalized 音檔），之後全部在成片時間軸上工作，愛用哪一機
就用哪一機。

## 為什麼這條路是安全的

ADR-064 禁止衍生內容碰原始素材，理由是「原始素材裡還有你剪掉的東西」——
20260805 的 `value-L01` 曾經把已經剪掉的重複、咳嗽、道歉剪回精華片裡
（ADR-064 Context）。

conform map 讓那個理由消失：**同一刀也套用到三機與音檔**，被移除的段落在
conform 之後的攝影機素材裡同樣不存在。安全性靠的仍然是「拿不到」，只是
「拿不到」的粒度從整支檔案細化到逐段。

## 這不是新的媒體檔

conform map 是一份**時間映射**，不 render 任何新素材。下游要 CAM2 的畫面
時，用 :func:`project_master_range` 把成片時間換算成 CAM2 的來源時間，直接
從原檔拉那一段。三台 CAM 與 program feed 逐格對齊（20260805 實測偏移
0.0000s），所以換算只是「找到所屬區段 + 加上該段的來源起點」。

## 邊界：片頭片尾沒有機位

Intro/Outro 是另外錄的完整節目素材，三機沒有對應畫面。落在那裡的成片時間
會被歸進 ``unconformable``，:func:`project_master_range` 會明確拒絕，不會
靜默給出錯的畫面。

Tests：tests/shared/test_editorial_conform.py。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CONTRACT = "podcast-editorial-conform-map-v1"
SCHEMA_VERSION = 1

#: 成片主體的來源鍵。同一個 master span 可由這裡任何一支素材提供畫面／聲音。
BODY_ORIGIN = "program"

_EPS = 1e-6


class ConformMapError(ValueError):
    """conform map 結構或查詢失敗——一律 fail closed，不猜。"""


def _round(value: float) -> float:
    """統一到毫秒，避免 float 尾數讓收據每次重建都不一樣。"""
    return round(float(value), 3)


def build_conform_map(
    *,
    episode_id: str,
    fps: float,
    lineage: dict[str, Any],
    timeline_items: list[dict[str, Any]],
    sources: dict[str, dict[str, Any]],
    body_source_path: str,
) -> dict[str, Any]:
    """從 Editorial Master timeline 的 item 清單建出 conform map。

    ``timeline_items`` 每項需要 ``tl_start`` / ``tl_end``（timeline frame，已
    扣掉 timeline start）、``src_left_offset``（來源 frame）與 ``source_path``。
    轉場之類沒有來源的 item（``source_path`` 為 None）直接略過——它們不改變
    時間軸長度。

    ``body_source_path`` 指出哪一支是主體（program feed）；其餘來源路徑視為
    Intro/Outro，歸進 ``unconformable``。
    """
    if fps <= 0:
        raise ConformMapError("fps 必須為正數")
    body_key = Path(body_source_path).name.lower()

    segments: list[dict[str, Any]] = []
    unconformable: list[dict[str, Any]] = []
    for item in sorted(timeline_items, key=lambda x: float(x["tl_start"])):
        source_path = item.get("source_path")
        if not source_path:
            continue  # 轉場：沒有來源媒體
        master_start = _round(float(item["tl_start"]) / fps)
        master_end = _round(float(item["tl_end"]) / fps)
        if master_end <= master_start:
            raise ConformMapError(f"item 時間範圍不合法：{master_start}–{master_end}")
        row = {
            "master_start_sec": master_start,
            "master_end_sec": master_end,
            "source_path": str(source_path),
        }
        if Path(source_path).name.lower() == body_key:
            left = item.get("src_left_offset")
            if left is None:
                raise ConformMapError(f"主體 item 缺 src_left_offset：{master_start}")
            row["origin"] = BODY_ORIGIN
            row["source_start_sec"] = _round(float(left) / fps)
            segments.append(row)
        else:
            row["origin"] = "intro_outro"
            unconformable.append(row)

    if not segments:
        raise ConformMapError("找不到任何主體區段——body_source_path 對不上任何 item")

    for earlier, later in zip(segments, segments[1:]):
        if later["master_start_sec"] + _EPS < earlier["master_end_sec"]:
            raise ConformMapError(
                f"主體區段重疊：{earlier['master_end_sec']} > {later['master_start_sec']}"
            )

    return {
        "contract": CONTRACT,
        "schema_version": SCHEMA_VERSION,
        "episode_id": episode_id,
        "fps": float(fps),
        "editorial_master_lineage": lineage,
        "sources": sources,
        "segments": segments,
        "unconformable": unconformable,
    }


#: conform map 在 episode 內的相對路徑（`build_conform_map.py` 寫在這裡）。
RELATIVE_PATH = Path("editorial-master") / "v1" / "conform-map.v1.json"


def conform_source_paths(episode_dir: Path) -> list[Path] | None:
    """這一集的合法畫面來源＝conform map 列出的素材（ADR-067）。

    短片的影片軌是機位，不是 Master——`highlight_materialization` 的 live 檢查
    要用這份清單來取代「每個 item 都必須是 Master 檔案」。沒有 conform map 就
    回 None，讓呼叫端維持長片的原判。
    """
    path = episode_dir / RELATIVE_PATH
    if not path.is_file():
        return None
    cmap = load_conform_map(path)
    return [episode_dir / str(entry["path"]) for entry in cmap["sources"].values()]


def load_conform_map(path: Path) -> dict[str, Any]:
    """讀取並驗證 conform map；契約不符一律拒絕。"""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConformMapError(f"conform map 讀取失敗：{exc}") from exc
    if not isinstance(payload, dict) or payload.get("contract") != CONTRACT:
        raise ConformMapError(f"conform map contract 不符（需要 {CONTRACT}）")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ConformMapError("conform map schema_version 不符")
    for key in ("fps", "sources", "segments"):
        if key not in payload:
            raise ConformMapError(f"conform map 缺欄位 {key}")
    return payload


def source_entry(cmap: dict[str, Any], source_key: str) -> dict[str, Any]:
    """取出某一支素材的登錄資料（路徑與同步偏移）。"""
    sources = cmap.get("sources") or {}
    entry = sources.get(source_key)
    if not isinstance(entry, dict):
        raise ConformMapError(f"conform map 沒有來源「{source_key}」（有：{sorted(sources)}）")
    return entry


def project_master_range(
    cmap: dict[str, Any],
    master_start_sec: float,
    master_end_sec: float,
    *,
    source_key: str,
) -> list[dict[str, Any]]:
    """成片時間區間 → 指定素材上的來源區間清單。

    成片的一段可能橫跨數個修剪區段（每一刀就是一個接縫），所以回傳的是
    **清單**，逐段給出 ``source_start_sec`` / ``source_end_sec``。呼叫端照
    順序接起來，就是與成片內容完全一致的畫面。

    落在 Intro/Outro（三機沒有畫面）或任何未覆蓋的空隙時 fail closed。
    """
    if master_end_sec <= master_start_sec:
        raise ConformMapError(f"時間區間不合法：{master_start_sec}–{master_end_sec}")
    entry = source_entry(cmap, source_key)
    offset = float(entry.get("offset_sec") or 0.0)

    for hole in cmap.get("unconformable") or []:
        if (
            master_start_sec < float(hole["master_end_sec"]) - _EPS
            and master_end_sec > float(hole["master_start_sec"]) + _EPS
        ):
            raise ConformMapError(
                f"{master_start_sec:.3f}–{master_end_sec:.3f} 落在片頭片尾"
                f"（{hole['master_start_sec']}–{hole['master_end_sec']}）——三機沒有對應畫面"
            )

    pieces: list[dict[str, Any]] = []
    covered = 0.0
    for seg in cmap["segments"]:
        seg_start = float(seg["master_start_sec"])
        seg_end = float(seg["master_end_sec"])
        lo = max(master_start_sec, seg_start)
        hi = min(master_end_sec, seg_end)
        if hi - lo <= _EPS:
            continue
        src_base = float(seg["source_start_sec"])
        pieces.append(
            {
                "master_start_sec": _round(lo),
                "master_end_sec": _round(hi),
                "source_path": entry["path"],
                "source_start_sec": _round(src_base + (lo - seg_start) - offset),
                "source_end_sec": _round(src_base + (hi - seg_start) - offset),
            }
        )
        covered += hi - lo

    if not pieces:
        raise ConformMapError(f"{master_start_sec:.3f}–{master_end_sec:.3f} 不在任何主體區段內")
    if abs(covered - (master_end_sec - master_start_sec)) > 1e-3:
        raise ConformMapError(
            f"{master_start_sec:.3f}–{master_end_sec:.3f} 只覆蓋到 {covered:.3f}s"
            "——成片區段之間有空隙，不可靜默補齊"
        )
    return pieces


def master_to_source_sec(cmap: dict[str, Any], master_sec: float, *, source_key: str) -> float:
    """單一時間點的換算（給抽樣張、對點用）。"""
    entry = source_entry(cmap, source_key)
    offset = float(entry.get("offset_sec") or 0.0)
    # 半開區間 [start, end)：剛好落在刀口的時間點屬於**後**一段。
    # 否則接縫那一格會被算回被剪掉的內容前面，差一格就是差一整刀。
    for seg in cmap["segments"]:
        seg_start = float(seg["master_start_sec"])
        seg_end = float(seg["master_end_sec"])
        if seg_start - _EPS <= master_sec < seg_end - _EPS:
            return _round(float(seg["source_start_sec"]) + (master_sec - seg_start) - offset)
    last = cmap["segments"][-1]
    if abs(master_sec - float(last["master_end_sec"])) <= _EPS:  # 主體最末端點
        span = float(last["master_end_sec"]) - float(last["master_start_sec"])
        return _round(float(last["source_start_sec"]) + span - offset)
    raise ConformMapError(f"成片時間 {master_sec:.3f}s 不在任何主體區段內")


def source_to_master_sec(
    cmap: dict[str, Any], source_sec: float, *, source_key: str
) -> float | None:
    """逆向換算：素材時間 → 成片時間。

    落在被剪掉的區間回傳 ``None``——那正是安全性：被修修剪掉的
    內容（咳嗽、道歉、重複）在成片時間軸上沒有位置，呼叫端應該丟掉它。
    """
    entry = source_entry(cmap, source_key)
    offset = float(entry.get("offset_sec") or 0.0)
    body_sec = source_sec + offset  # 先回到 program feed 的時鐘
    for seg in cmap["segments"]:
        src_start = float(seg["source_start_sec"])
        span = float(seg["master_end_sec"]) - float(seg["master_start_sec"])
        if src_start - _EPS <= body_sec < src_start + span - _EPS:
            return _round(float(seg["master_start_sec"]) + (body_sec - src_start))
    return None


def removed_spans(cmap: dict[str, Any]) -> list[dict[str, float]]:
    """被修剪掉的原始區間——安全性的證據：這些內容在 conform 後拿不到。"""
    out: list[dict[str, float]] = []
    for earlier, later in zip(cmap["segments"], cmap["segments"][1:]):
        gap_start = float(earlier["source_start_sec"]) + (
            float(earlier["master_end_sec"]) - float(earlier["master_start_sec"])
        )
        gap_end = float(later["source_start_sec"])
        if gap_end - gap_start > _EPS:
            out.append(
                {
                    "source_start_sec": _round(gap_start),
                    "source_end_sec": _round(gap_end),
                    "duration_sec": _round(gap_end - gap_start),
                }
            )
    return out
