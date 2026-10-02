"""Resolve 字幕軌 ST1 整軌重建：Intro／Outro 字幕上軌、單句文字修正（podcast-pipeline S7E）。

為什麼只能整軌重建（2026-10-02，20260722 李海碩，DaVinci Resolve Studio 21.1.0.14 實測）：
`MediaPool.AppendToTimeline` 放 SRT 一律落在字幕軌 1 **既有內容之後**——傳 item 本身落在
ST1 最後一句的結尾、傳 clipInfo dict 落在 timeline 結尾；`recordFrame`／`trackIndex` 都被忽略；
把 ST1 鎖住則 append 回 True 卻什麼都沒放。intro 字幕因此插不到正片字幕前面。唯一精確的做法：

    快照現況 ST1（起訖幀＋文字，含修修在 Resolve 上的手改）→ 合併 → DeleteClips 清空 ST1
    → append 一份絕對時間 SRT（空軌從 frame 0 起算）→ 逐句比對起訖幀與文字

Resolve API 也改不了既有字幕 item 的文字，所以單句文字修正（`--fix`）走同一條重建路。

子命令：
    pieces     唯讀。列出 V1 上某支來源檔的每一段剪輯：tl_start／tl_end／src_start_frame／src_fps
    map-cues   純計算。裁決後的「來源秒數」字幕 → timeline 幀，產出 rebuild 吃的 cues.json
    rebuild    備份 duplicate → 確認 current timeline → 快照 → 合併（重疊檢查）→ 清空 →
               append → 逐句驗證 → SaveProject。`--scratch` 改在拋棄式副本上跑，驗完即刪

rebuild exit codes：
    0  驗證通過：ST1 每一句的起訖幀與文字都和計畫一致
    1  ST1 已被清空／改寫，但結果和計畫不符 → 從備份 timeline 還原
    2  前置條件不符而中止，ST1 沒被動過（可能已建好備份 timeline，那是無害的）

純邏輯（幀對應、SRT 時間碼、合併＋重疊檢查、修正套用、diff）不依賴 Resolve，
測試在 tests/scripts/test_resolve_subtitle_rebuild.py。碰 Resolve 的部分只能在 Resolve
開著時手動跑；Resolve scripting 是單執行緒，不要和其他上軌工作同時跑。

用法（PowerShell，repo 根目錄）：
    E:\\nakama\\.venv-v2\\Scripts\\python.exe scripts\\resolve_subtitle_rebuild.py pieces `
      --project "<project>" --timeline "<timeline>" --source-name C5497.MP4
    E:\\nakama\\.venv-v2\\Scripts\\python.exe scripts\\resolve_subtitle_rebuild.py map-cues `
      --input "<episode>\\intro-outro\\intro-outro.adjudicated.json" `
      --out "<episode>\\intro-outro\\intro-outro.cues.json"
    E:\\nakama\\.venv-v2\\Scripts\\python.exe scripts\\resolve_subtitle_rebuild.py rebuild `
      --project "<project>" --timeline "<timeline>" `
      --add-cues "<episode>\\intro-outro\\intro-outro.cues.json" `
      --out-dir "<episode>\\intro-outro" --scratch
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable, Mapping, NamedTuple, Sequence

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

EXIT_OK = 0
EXIT_MISMATCH = 1
EXIT_PRECONDITION = 2

DEFAULT_TIMELINE_FPS = Fraction(30)

# Resolve 的 clip property「FPS」回小數字串（如 "29.97"）；換回精確的 NTSC 分數
_NTSC = {
    Fraction("23.976"): Fraction(24000, 1001),
    Fraction("29.97"): Fraction(30000, 1001),
    Fraction("59.94"): Fraction(60000, 1001),
}


class RebuildError(Exception):
    """前置條件不符。呼叫端以 exit 2 結束；ST1 沒被動過。"""


class Cue(NamedTuple):
    """一句字幕：timeline 絕對幀（end 為 exclusive，同 TimelineItem.GetEnd()）＋文字。"""

    start: int
    end: int
    text: str


class Fix(NamedTuple):
    """單句文字修正：起始幀上的 live 文字必須正好是 old，才換成 new。"""

    start: int
    old: str
    new: str


@dataclass(frozen=True)
class Piece:
    """V1 上某支來源檔的一段剪輯。

    tl_start／tl_end 是 timeline 幀（TimelineItem.GetStart()／GetEnd()）；
    src_start_frame 是 TimelineItem.GetSourceStartFrame()，單位是**來源**幀。
    """

    tl_start: int
    tl_end: int
    src_start_frame: int
    src_fps: Fraction

    def to_json(self) -> dict[str, object]:
        return {
            "tl_start": self.tl_start,
            "tl_end": self.tl_end,
            "src_start_frame": self.src_start_frame,
            "src_fps": format_fps(self.src_fps),
        }

    @classmethod
    def from_json(cls, raw: object, *, label: str) -> Piece:
        if not isinstance(raw, Mapping):
            raise RebuildError(f"piece {label}: expected an object")
        try:
            piece = cls(
                tl_start=_strict_int(raw["tl_start"]),
                tl_end=_strict_int(raw["tl_end"]),
                src_start_frame=_strict_int(raw["src_start_frame"]),
                src_fps=parse_fps(raw["src_fps"]),
            )
        except KeyError as exc:
            raise RebuildError(f"piece {label}: missing {exc.args[0]!r}") from exc
        if piece.tl_end <= piece.tl_start:
            raise RebuildError(f"piece {label}: tl_end {piece.tl_end} <= tl_start {piece.tl_start}")
        return piece


# ---------------------------------------------------------------- pure logic


def _strict_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RebuildError(f"expected an integer frame, got {value!r}")
    return value


def parse_fps(value: object) -> Fraction:
    """'29.97' → 30000/1001；'30'／'30.0' → 30；'30000/1001' 原樣。"""
    text = str(value).strip()
    try:
        fps = Fraction(text)
    except (ValueError, ZeroDivisionError) as exc:
        raise RebuildError(f"unparseable frame rate: {value!r}") from exc
    if fps <= 0:
        raise RebuildError(f"non-positive frame rate: {value!r}")
    return _NTSC.get(fps, fps)


def format_fps(fps: Fraction) -> str:
    return str(fps.numerator) if fps.denominator == 1 else f"{fps.numerator}/{fps.denominator}"


def src_sec_to_tl_frame(src_sec: float | int | str | Fraction, piece: Piece) -> int:
    """來源檔秒數 → timeline 幀，夾在該段剪輯內。

        tl_frame = tl_start + round(src_sec × src_fps) − src_start_frame

    src_fps 是**來源**幀率（C5497.MP4 是 30000/1001，timeline 是 30），src_start_frame 是
    GetSourceStartFrame()（來源幀）。GetLeftOffset() 是 timeline 幀，不可混進這條式子
    （2026-10-02）。秒數經 str 轉 Fraction，避免 float 誤差影響四捨五入。
    """
    sec = src_sec if isinstance(src_sec, Fraction) else Fraction(str(src_sec))
    frame = piece.tl_start + round(sec * piece.src_fps) - piece.src_start_frame
    return max(piece.tl_start, min(piece.tl_end, frame))


def normalize_text(text: str) -> str:
    """去頭尾空白。Editorial Master 的 master.srt 也 strip，所以這不會丟資訊。"""
    return text.strip()


def validate_cues(cues: Iterable[Cue], *, limit: int | None = None) -> list[Cue]:
    """排序後檢查：正時長、不出 timeline、文字非空且不會弄壞 SRT、彼此不重疊。

    重疊規則同 editorial_master._serialize_subtitles（seal 時會用同一條擋下）：
    排序後下一句的起點不得早於上一句的終點；首尾相接（end == 下一句 start）合法。
    """
    ordered = sorted(cues)
    for cue in ordered:
        if cue.start < 0 or cue.end <= cue.start:
            raise RebuildError(f"non-positive or negative-time cue: {cue}")
        if limit is not None and cue.end > limit:
            raise RebuildError(f"cue ends after the timeline end frame {limit}: {cue}")
        if not cue.text:
            raise RebuildError(f"empty subtitle text at frame {cue.start}")
        if "\r" in cue.text or "\n\n" in cue.text:
            raise RebuildError(f"subtitle text would break SRT blocks at frame {cue.start}")
    for previous, current in zip(ordered, ordered[1:]):
        if current.start < previous.end:
            raise RebuildError(f"overlap: {previous} / {current}")
    return ordered


def merge_cues(body: Sequence[Cue], added: Sequence[Cue], *, limit: int | None = None) -> list[Cue]:
    """現況 ST1 ＋ 新增字幕 → 一份排序好、驗證過的完整 ST1 計畫。"""
    return validate_cues([*body, *added], limit=limit)


def parse_fix(spec: str) -> Fix:
    """'<start_frame>=<old>=><new>' → Fix。old／new 去頭尾空白；new 不可為空。"""
    frame_part, sep, rest = spec.partition("=")
    old, arrow, new = rest.partition("=>")
    if not sep or not arrow or not frame_part.strip().isdigit():
        raise RebuildError(f"--fix must look like <start_frame>=<old>=><new>, got {spec!r}")
    fix = Fix(int(frame_part.strip()), normalize_text(old), normalize_text(new))
    if not fix.new:
        raise RebuildError(f"--fix for frame {fix.start}: new text is empty")
    return fix


def apply_fixes(cues: Sequence[Cue], fixes: Sequence[Fix]) -> list[Cue]:
    """套文字修正。起始幀必須唯一對到一句，且 live 文字必須正好等於 old。

    old 對不上就停：代表修修已經在 Resolve 上改過那句（或修正單是照舊快照寫的），
    live 才是權威，不可蓋掉。
    """
    out = list(cues)
    index: dict[int, list[int]] = {}
    for position, cue in enumerate(out):
        index.setdefault(cue.start, []).append(position)
    seen: set[int] = set()
    for fix in fixes:
        if fix.start in seen:
            raise RebuildError(f"more than one --fix for frame {fix.start}")
        seen.add(fix.start)
        positions = index.get(fix.start, [])
        if len(positions) != 1:
            raise RebuildError(
                f"--fix frame {fix.start}: {len(positions)} cues start there (expected exactly 1)"
            )
        live = out[positions[0]]
        if live.text != fix.old:
            raise RebuildError(
                f"--fix frame {fix.start}: expected {fix.old!r}, live text is {live.text!r}"
            )
        out[positions[0]] = live._replace(text=fix.new)
    return out


def build_plan(
    body: Sequence[Cue],
    added: Sequence[Cue],
    fixes: Sequence[Fix],
    *,
    limit: int | None = None,
) -> list[Cue]:
    """live ST1 → 套修正 → 併入新增字幕 → 驗證。沒有實際變更就拒絕（不白清一次軌）。"""
    if not added and not fixes:
        raise RebuildError("nothing to do: pass --add-cues and/or --fix")
    plan = merge_cues(apply_fixes(body, fixes), added, limit=limit)
    if plan == sorted(body):
        raise RebuildError("the plan is identical to live ST1; nothing to change")
    return plan


def _round_half_up(value: Fraction) -> int:
    return math.floor(value + Fraction(1, 2))


def format_srt_timestamp(frame: int, fps: Fraction = DEFAULT_TIMELINE_FPS) -> str:
    """timeline 幀 → 'HH:MM:SS,mmm'（毫秒四捨五入，同 editorial_master._srt_timestamp）。"""
    if frame < 0:
        raise ValueError(f"negative frame: {frame}")
    ms = _round_half_up(Fraction(frame) * 1000 / fps)
    hours, ms = divmod(ms, 3_600_000)
    minutes, ms = divmod(ms, 60_000)
    seconds, ms = divmod(ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{ms:03d}"


_TIMESTAMP = re.compile(r"^(\d{2,}):(\d{2}):(\d{2})[,.](\d{3})$")


def parse_srt_timestamp(text: str, fps: Fraction = DEFAULT_TIMELINE_FPS) -> int:
    """'HH:MM:SS,mmm' → 最近的 timeline 幀。"""
    match = _TIMESTAMP.match(text.strip())
    if not match:
        raise ValueError(f"bad SRT timestamp: {text!r}")
    hours, minutes, seconds, ms = (int(group) for group in match.groups())
    total_ms = ((hours * 60 + minutes) * 60 + seconds) * 1000 + ms
    return _round_half_up(Fraction(total_ms) * fps / 1000)


def render_srt(cues: Sequence[Cue], fps: Fraction = DEFAULT_TIMELINE_FPS) -> str:
    blocks = [
        f"{number}\n{format_srt_timestamp(cue.start, fps)} --> "
        f"{format_srt_timestamp(cue.end, fps)}\n{cue.text}"
        for number, cue in enumerate(cues, 1)
    ]
    return "\n\n".join(blocks) + "\n"


def parse_srt(text: str, fps: Fraction = DEFAULT_TIMELINE_FPS) -> list[Cue]:
    cues = []
    body = text.replace("\r\n", "\n").lstrip("\ufeff").strip()
    for block in re.split(r"\n[ \t]*\n", body) if body else []:
        lines = block.split("\n")
        if len(lines) < 3 or " --> " not in lines[1]:
            raise ValueError(f"malformed SRT block: {block!r}")
        start, _, end = lines[1].partition(" --> ")
        cues.append(
            Cue(
                parse_srt_timestamp(start, fps), parse_srt_timestamp(end, fps), "\n".join(lines[2:])
            )
        )
    return cues


@dataclass(frozen=True)
class CueDiff:
    expected_count: int
    actual_count: int
    first_mismatch: int | None
    missing: tuple[Cue, ...]
    extra: tuple[Cue, ...]

    @property
    def ok(self) -> bool:
        return self.first_mismatch is None

    def describe(self, limit: int = 5) -> str:
        if self.ok:
            return f"identical: {self.expected_count} cues"
        lines = [
            f"expected {self.expected_count} cues, got {self.actual_count}; "
            f"first difference at position {self.first_mismatch}",
            f"missing ({len(self.missing)}): {list(self.missing[:limit])}",
            f"extra ({len(self.extra)}): {list(self.extra[:limit])}",
        ]
        return "\n".join(lines)


def diff_cues(expected: Sequence[Cue], actual: Sequence[Cue]) -> CueDiff:
    """逐句比對（位置＋起訖幀＋文字）。missing／extra 用 multiset 差集，方便看出漏了哪句。"""
    expected, actual = list(expected), list(actual)
    first = next(
        (position for position, (a, b) in enumerate(zip(expected, actual)) if a != b), None
    )
    if first is None and len(expected) != len(actual):
        first = min(len(expected), len(actual))
    expected_counter, actual_counter = Counter(expected), Counter(actual)
    return CueDiff(
        expected_count=len(expected),
        actual_count=len(actual),
        first_mismatch=first,
        missing=tuple(sorted((expected_counter - actual_counter).elements())),
        extra=tuple(sorted((actual_counter - expected_counter).elements())),
    )


def load_pieces(data: object) -> dict[str, Piece]:
    pieces = data.get("pieces") if isinstance(data, Mapping) else None
    if not isinstance(pieces, Mapping) or not pieces:
        raise RebuildError("input has no 'pieces' object ({name: {tl_start, ...}})")
    return {str(name): Piece.from_json(raw, label=str(name)) for name, raw in pieces.items()}


def map_cues(pieces: Mapping[str, Piece], rows: object) -> list[dict[str, Any]]:
    """裁決後的字幕（piece＋來源秒數＋文字）→ 補上 tl_start_frame／tl_end_frame。

    夾進剪輯段後變成零長度＝這句落在被剪掉的片段（例如沒用到的那個 take），直接拒絕。
    """
    if not isinstance(rows, list) or not rows:
        raise RebuildError("input has no 'cues' list")
    mapped: list[dict[str, Any]] = []
    for number, row in enumerate(rows, 1):
        if not isinstance(row, Mapping):
            raise RebuildError(f"cue {number}: expected an object")
        try:
            name, text = str(row["piece"]), normalize_text(str(row["text"]))
            src_start, src_end = row["src_start_sec"], row["src_end_sec"]
        except KeyError as exc:
            raise RebuildError(f"cue {number}: missing {exc.args[0]!r}") from exc
        if name not in pieces:
            raise RebuildError(f"cue {number}: unknown piece {name!r}")
        start = src_sec_to_tl_frame(src_start, pieces[name])
        end = src_sec_to_tl_frame(src_end, pieces[name])
        if end <= start:
            raise RebuildError(
                f"cue {number} {text!r}: {start}->{end} after clamping to piece {name} "
                "(outside the edited piece?)"
            )
        mapped.append({**row, "text": text, "tl_start_frame": start, "tl_end_frame": end})
    validate_cues(Cue(r["tl_start_frame"], r["tl_end_frame"], r["text"]) for r in mapped)
    return mapped


def load_add_cues(path: Path) -> list[Cue]:
    """讀 map-cues 產出的 cues.json（只用 tl_start_frame／tl_end_frame／text）。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RebuildError(f"cannot read {path}: {exc}") from exc
    rows = data.get("cues") if isinstance(data, Mapping) else None
    if not isinstance(rows, list) or not rows:
        raise RebuildError(f"{path}: no 'cues' list")
    cues = []
    for number, row in enumerate(rows, 1):
        if not isinstance(row, Mapping) or not {"tl_start_frame", "tl_end_frame", "text"} <= set(
            row
        ):
            raise RebuildError(f"{path}: cue {number} needs tl_start_frame, tl_end_frame, text")
        cues.append(
            Cue(
                _strict_int(row["tl_start_frame"]),
                _strict_int(row["tl_end_frame"]),
                normalize_text(str(row["text"])),
            )
        )
    return cues


_UNSAFE_NAME = re.compile(r'[<>:"/\\|?*\s]+')


def artifact_paths(out_dir: Path, timeline_name: str) -> tuple[Path, Path]:
    """下一個沒用過的 `st1-rebuild.<timeline>.rNNN.srt`＋`.pre-snapshot.json`。

    每次都換新路徑：Resolve 依路徑快取已匯入的媒體，同路徑重匯會拿到舊內容
    （見 build_resolve_project.RESOLVE_SUBS_DIR 的註解）。
    """
    stem = f"st1-rebuild.{_UNSAFE_NAME.sub('_', timeline_name.strip())}"
    revision = 1
    while True:
        srt = out_dir / f"{stem}.r{revision:03d}.srt"
        snapshot = out_dir / f"{stem}.r{revision:03d}.pre-snapshot.json"
        if not srt.exists() and not snapshot.exists():
            return srt, snapshot
        revision += 1


# ------------------------------------------------------------ Resolve access


def _call(obj: Any, method: str, *args: object) -> Any:
    """唯讀查詢用：方法不存在或丟例外都回 None（例如 V1 上的轉場 item）。"""
    function = getattr(obj, method, None)
    if not callable(function):
        return None
    try:
        return function(*args)
    except Exception:
        return None


def _connect() -> Any:
    from scripts.build_resolve_project import connect_resolve

    try:
        return connect_resolve()
    except SystemExit as exc:
        raise RebuildError(f"cannot connect to DaVinci Resolve: {exc}") from exc


def _current_project(resolve: Any, name: str) -> tuple[Any, Any]:
    """只接受「Resolve 現在開著的就是這個 project」。本腳本不切 project。"""
    manager = _call(resolve, "GetProjectManager")
    project = _call(manager, "GetCurrentProject")
    current = _call(project, "GetName")
    if current != name:
        raise RebuildError(
            f"current Resolve project is {current!r}, expected {name!r}; open it in Resolve first"
        )
    return manager, project


def _timeline_by_name(project: Any, name: str) -> Any:
    count = int(_call(project, "GetTimelineCount") or 0)
    matches = [
        timeline
        for index in range(1, count + 1)
        if (timeline := _call(project, "GetTimelineByIndex", index)) is not None
        and _call(timeline, "GetName") == name
    ]
    if len(matches) != 1:
        raise RebuildError(f"{len(matches)} timelines named {name!r} (expected exactly 1)")
    return matches[0]


def _uid(timeline: Any) -> str:
    uid = _call(timeline, "GetUniqueId")
    if not uid:
        raise RebuildError(f"timeline {_call(timeline, 'GetName')!r} has no unique id")
    return str(uid)


def _timeline_fps(timeline: Any) -> Fraction:
    raw = _call(timeline, "GetSetting", "timelineFrameRate")
    if raw in (None, ""):
        raise RebuildError("timeline has no timelineFrameRate setting")
    return parse_fps(raw)


def _timeline_limit(timeline: Any) -> int:
    """前置檢查 timeline 佈局，回傳 timeline 終點幀（字幕不得超出）。"""
    tracks = int(_call(timeline, "GetTrackCount", "subtitle") or 0)
    if tracks != 1:
        raise RebuildError(
            f"timeline has {tracks} subtitle tracks, expected exactly 1 "
            "(SRT append only lands on ST1; seal merges every subtitle track)"
        )
    start = int(_call(timeline, "GetStartFrame") or 0)
    if start != 0:
        raise RebuildError(
            f"timeline start frame is {start}, not 0; absolute-time SRT placement was only "
            "verified on start frame 0 (2026-10-02)"
        )
    end = int(_call(timeline, "GetEndFrame") or 0)
    if end <= 0:
        raise RebuildError("timeline has no positive end frame")
    return end


def _snapshot_st1(timeline: Any) -> list[Cue]:
    items = timeline.GetItemListInTrack("subtitle", 1) or []
    return sorted(
        Cue(int(item.GetStart()), int(item.GetEnd()), normalize_text(item.GetName() or ""))
        for item in items
    )


def _source_names(item: Any, media_pool_item: Any) -> set[str]:
    names = {_call(item, "GetName"), _call(media_pool_item, "GetName")}
    path = _call(media_pool_item, "GetClipProperty", "File Path")
    if isinstance(path, str) and path:
        names.add(re.split(r"[\\/]", path)[-1])
    return {name.casefold() for name in names if isinstance(name, str) and name}


def _v1_pieces(timeline: Any, source_name: str) -> list[Piece]:
    pieces = []
    for item in _call(timeline, "GetItemListInTrack", "video", 1) or []:
        media_pool_item = _call(item, "GetMediaPoolItem")
        if media_pool_item is None:  # 轉場（如 Cross Dissolve）也會出現在 V1 item list
            continue
        if source_name.casefold() not in _source_names(item, media_pool_item):
            continue
        fps = _call(media_pool_item, "GetClipProperty", "FPS")
        source_start = _call(item, "GetSourceStartFrame")
        if not fps or source_start is None:
            raise RebuildError(
                f"{source_name}: no FPS clip property or GetSourceStartFrame() on a V1 item"
            )
        pieces.append(
            Piece(
                tl_start=int(item.GetStart()),
                tl_end=int(item.GetEnd()),
                src_start_frame=int(source_start),
                src_fps=parse_fps(fps),
            )
        )
    return sorted(pieces, key=lambda piece: piece.tl_start)


# ------------------------------------------------------------------ commands


def cmd_pieces(args: argparse.Namespace) -> int:
    try:
        resolve = _connect()
        _, project = _current_project(resolve, args.project)
        timeline = _timeline_by_name(project, args.timeline)
        pieces = _v1_pieces(timeline, args.source_name)
        if not pieces:
            raise RebuildError(f"no V1 item of {args.source_name!r} on {args.timeline!r}")
    except RebuildError as exc:
        print(f"ABORT: {exc}", file=sys.stderr)
        return EXIT_PRECONDITION
    print(json.dumps([piece.to_json() for piece in pieces], ensure_ascii=False, indent=1))
    return EXIT_OK


def cmd_map_cues(args: argparse.Namespace) -> int:
    try:
        try:
            data = json.loads(args.input.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RebuildError(f"cannot read {args.input}: {exc}") from exc
        pieces = load_pieces(data)
        mapped = map_cues(pieces, data.get("cues"))
    except RebuildError as exc:
        print(f"ABORT: {exc}", file=sys.stderr)
        return EXIT_PRECONDITION
    output = {
        **data,
        "pieces": {name: piece.to_json() for name, piece in pieces.items()},
        "cues": mapped,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for row in mapped:
        print(
            f"{row['piece']:>4} {row['tl_start_frame']:>7}-{row['tl_end_frame']:>7} "
            f"{format_srt_timestamp(row['tl_start_frame'])} {row['text']}"
        )
    print(f"{len(mapped)} cues -> {args.out}")
    return EXIT_OK


def _restore_hint(backup_name: str | None, work_name: str) -> str:
    if backup_name:
        return f"restore from backup timeline {backup_name!r}"
    return f"scratch timeline {work_name!r} left for inspection; the real timeline was not touched"


def cmd_rebuild(args: argparse.Namespace) -> int:
    # 1. 規劃：只讀，任何問題都在動到 Resolve 之前擋下
    try:
        fixes = [parse_fix(spec) for spec in args.fix]
        added = load_add_cues(args.add_cues) if args.add_cues else []
        resolve = _connect()
        manager, project = _current_project(resolve, args.project)
        original = _timeline_by_name(project, args.timeline)
        fps = _timeline_fps(original)
        limit = _timeline_limit(original)
        body = _snapshot_st1(original)
        plan = build_plan(body, added, fixes, limit=limit)
        srt_text = render_srt(plan, fps)
        if parse_srt(srt_text, fps) != plan:
            raise RebuildError("rendered SRT does not parse back to the plan (frame rounding?)")
    except RebuildError as exc:
        print(f"ABORT (nothing changed): {exc}", file=sys.stderr)
        return EXIT_PRECONDITION
    print(
        f"plan: live ST1 {len(body)} + added {len(added)} = {len(plan)} cues; "
        f"{len(fixes)} text fix(es); fps {format_fps(fps)}"
    )

    # 2. 備份／拋棄式副本。DuplicateTimeline 後 Resolve 可能把 current 換成副本
    stamp = datetime.now().strftime("%m%d-%H%M%S")
    backup_name: str | None = None
    if args.scratch:
        work = original.DuplicateTimeline(f"zz_scratch ST1重建 {stamp} 可刪")
        work_label = "scratch"
    else:
        backup_name = f"{args.timeline} 備份 ST1重建前 {stamp}"
        backup = original.DuplicateTimeline(backup_name)
        if not backup or _snapshot_st1(backup) != body:
            print(
                f"ABORT (nothing changed): backup {backup_name!r} failed or differs",
                file=sys.stderr,
            )
            return EXIT_PRECONDITION
        print(f"backup timeline: {backup_name!r}")
        work = original
        work_label = "target"
    if not work:
        print("ABORT (nothing changed): DuplicateTimeline failed", file=sys.stderr)
        return EXIT_PRECONDITION
    work_name = work.GetName()
    if args.scratch:
        print(f"scratch timeline: {work_name!r}")

    # 3. 確認 current timeline 真的是要動的那條——SetCurrentTimeline 在 UI 忙或有對話框時
    #    會靜默回 None，DeleteClips 在非 current timeline 上也會靜默失敗
    try:
        project.SetCurrentTimeline(work)
        current = project.GetCurrentTimeline()
        if current is None or _uid(current) != _uid(work):
            raise RebuildError(
                f"current timeline is {_call(current, 'GetName')!r}, not {work_name!r}; "
                "close any Resolve dialog, click the timeline, then rerun"
            )
        live = _snapshot_st1(work)
        if live != body:
            raise RebuildError(f"{work_label} ST1 differs from the planning snapshot; rerun")
    except RebuildError as exc:
        print(f"ABORT (ST1 not touched): {exc}", file=sys.stderr)
        if args.scratch:
            print(f"delete the scratch timeline {work_name!r} by hand", file=sys.stderr)
        return EXIT_PRECONDITION

    # 4. 寫 SRT 與重建前快照（審計紀錄，不是下次重建的來源——下次一律重讀 live）
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    srt_path, snapshot_path = artifact_paths(out_dir, work_name)
    srt_path.write_text(srt_text, encoding="utf-8")
    snapshot_path.write_text(
        json.dumps(
            {
                "project": args.project,
                "timeline": work_name,
                "timeline_uid": _uid(work),
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "fps": format_fps(fps),
                "backup_timeline": backup_name,
                "added_cues": len(added),
                "fixes": [fix._asdict() for fix in fixes],
                "plan_srt": srt_path.name,
                "items": [list(cue) for cue in live],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"wrote {srt_path.name} + {snapshot_path.name}")

    # 5. 清空 ST1（保留軌——字幕樣式掛在軌上）
    from shared.resolve_append import delete_checked

    restore = _restore_hint(backup_name, work_name)
    items = work.GetItemListInTrack("subtitle", 1) or []
    try:
        if items:
            delete_checked(project, work, items, "ST1 清空")
    except SystemExit as exc:
        if _snapshot_st1(work) == body:
            print(f"ABORT (ST1 not touched): {exc}", file=sys.stderr)
            return EXIT_PRECONDITION
        print(f"FAILED mid-delete: {exc}; {restore}", file=sys.stderr)
        return EXIT_MISMATCH
    if work.GetItemListInTrack("subtitle", 1):
        print(f"FAILED: ST1 not empty after delete; {restore}", file=sys.stderr)
        return EXIT_MISMATCH

    # 6. append 絕對時間 SRT：傳 item 本身（不是 clipInfo dict），空軌從 frame 0 起算。
    #    只送一次、不重試（不用 append_checked）：SRT 的 append 若其實放了一部分，重送會疊在
    #    ST1 最後一句之後。成敗一律交給下一步的逐句驗證判定。
    media_pool = project.GetMediaPool()
    media_pool.SetCurrentFolder(media_pool.GetRootFolder())
    imported = media_pool.ImportMedia([str(srt_path)])
    if not imported or not media_pool.AppendToTimeline(imported):
        print(f"FAILED: SRT import/append failed after ST1 was cleared; {restore}", file=sys.stderr)
        return EXIT_MISMATCH

    # 7. 逐句驗證（append 回 True 不代表有放上去：ST1 鎖住時就是回 True 卻什麼都沒放）
    diff = diff_cues(plan, _snapshot_st1(work))
    if not diff.ok:
        print(f"MISMATCH on {work_name!r}:\n{diff.describe()}\n{restore}", file=sys.stderr)
        return EXIT_MISMATCH

    if args.scratch:
        project.SetCurrentTimeline(original)
        deleted = media_pool.DeleteTimelines([work])
        print(f"VERIFIED on scratch: {diff.describe()}")
        if not deleted:
            print(f"WARNING: delete the scratch timeline {work_name!r} by hand", file=sys.stderr)
        return EXIT_OK

    saved = manager.SaveProject()
    print(f"VERIFIED: {diff.describe()} on {work_name!r}; backup {backup_name!r}")
    if not saved:
        print("WARNING: SaveProject returned False; save in Resolve (Ctrl+S)", file=sys.stderr)
    return EXIT_OK


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="resolve_subtitle_rebuild",
        description="Rebuild Resolve subtitle track 1 exactly (S7E intro/outro subtitles).",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    pieces = commands.add_parser("pieces", help="read-only: V1 pieces of one source file")
    pieces.add_argument("--project", required=True)
    pieces.add_argument("--timeline", required=True)
    pieces.add_argument("--source-name", required=True, help="e.g. C5497.MP4")
    pieces.set_defaults(handler=cmd_pieces)

    mapper = commands.add_parser("map-cues", help="pure: source seconds -> timeline frames")
    mapper.add_argument("--input", required=True, type=Path, help="adjudicated pieces + cues")
    mapper.add_argument("--out", required=True, type=Path, help="cues.json for rebuild --add-cues")
    mapper.set_defaults(handler=cmd_map_cues)

    rebuild = commands.add_parser("rebuild", help="snapshot, merge, clear and re-append ST1")
    rebuild.add_argument("--project", required=True)
    rebuild.add_argument("--timeline", required=True)
    rebuild.add_argument("--add-cues", type=Path, help="cues.json from map-cues")
    rebuild.add_argument(
        "--fix",
        action="append",
        default=[],
        metavar="FRAME=OLD=>NEW",
        help="replace the text of the live cue starting at FRAME (repeatable)",
    )
    rebuild.add_argument("--out-dir", required=True, type=Path)
    rebuild.add_argument(
        "--scratch",
        action="store_true",
        help="run on a throwaway duplicate and delete it afterwards (dry run)",
    )
    rebuild.set_defaults(handler=cmd_rebuild)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = _parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
