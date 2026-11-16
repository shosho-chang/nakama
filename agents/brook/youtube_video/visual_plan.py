"""畫面規劃表：資料格式、Bridge 上的修改規則、統計。

規格 §4（`docs/plans/2026-11-15-youtube-script-video-rough-cut-and-director.md`）。
一集一份 `visual_plan.json`，放在 `<PODCAST_EPISODES_ROOT>/<episode>/`
（跟 podcast 同一個根目錄，本機是 `G:/Footages`）。
Director 寫入、修修在 Bridge 修改、agent 重排時只改標了「重排」的列。

修修定下的規則（原話在規格裡）：
- 狀態只有「核准／重排」單選，都沒選＝待審；重排必填修正意見。
- 直接改分類、版面、畫面描述、畫面上的字 → 改完即核准該列。
- 時間只能從字幕重選起訖，不能直接打數字。
- 刪整列 → 併入前後空白段；「復原」回到 Director 原版。
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from filelock import FileLock

from agents.brook.youtube_video.categories import Category, load_categories

SCHEMA = "nakama.youtube_visual_plan.v1"
PLAN_FILE = "visual_plan.json"

STATUSES = (None, "approved", "rearrange")
ORIGINS = frozenset({"director", "auto", "manual"})
# 程式自動顯示的標示（規格 §4.2）；「待製作」「等你提供素材」由分類推得，不存。
PROGRAM_STATES = (None, "replanning", "awaiting_material", "on_timeline")
TEXT_FIELDS = ("description", "dp_instruction", "onscreen_text", "source")
# 修修：直接改這四個欄位＝改完即核准。
APPROVE_ON_EDIT = frozenset({"category", "layout", "description", "onscreen_text"})
EDITABLE = frozenset(
    {
        "category",
        "layout",
        *TEXT_FIELDS,
        "status",
        "note",
        "start_cue",
        "end_cue",
        "before_cue",
    }
)
MAX_TEXT = 4000


class PlanError(ValueError):
    """A requested change breaks a plan rule; the message is shown to 修修."""


# ── 讀寫 ────────────────────────────────────────────────────────────────


def plan_path(episode_dir: Path) -> Path:
    return episode_dir / PLAN_FILE


def load_plan(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    validate_plan(payload)
    return payload


def save_plan(path: Path, plan: Mapping[str, Any]) -> None:
    """Atomic replace so a crash mid-write never leaves a half-written plan."""

    validate_plan(plan)
    data = dict(plan)
    data["updated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    fd, tmp = tempfile.mkstemp(prefix=".visual_plan-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def plan_lock(path: Path) -> FileLock:
    return FileLock(str(path) + ".lock", timeout=10)


# ── 驗證 ────────────────────────────────────────────────────────────────


def validate_plan(plan: Mapping[str, Any]) -> None:
    if plan.get("schema") != SCHEMA:
        raise PlanError(f"schema must be {SCHEMA}")
    cues = plan.get("cues")
    rows = plan.get("rows")
    if not isinstance(cues, list) or not isinstance(rows, list):
        raise PlanError("plan needs `cues` and `rows` lists")
    cue_ids = set()
    for cue in cues:
        if not {"id", "start", "end", "text"} <= set(cue):
            raise PlanError(f"cue missing fields: {cue!r}")
        if cue["end"] < cue["start"]:
            raise PlanError(f"cue {cue['id']} ends before it starts")
        cue_ids.add(cue["id"])
    categories = load_categories()
    row_ids = set()
    for row in rows:
        row_id = row.get("id")
        if not row_id or row_id in row_ids:
            raise PlanError(f"row id missing or duplicated: {row_id!r}")
        row_ids.add(row_id)
        if row.get("origin") not in ORIGINS:
            raise PlanError(f"{row_id}: unknown origin {row.get('origin')!r}")
        if row.get("kind") not in ("overlay", "insert"):
            raise PlanError(f"{row_id}: unknown kind {row.get('kind')!r}")
        category = row.get("category")
        if category is not None:
            if category not in categories:
                raise PlanError(f"{row_id}: unknown category {category!r}")
            if categories[category].kind != row["kind"]:
                raise PlanError(f"{row_id}: category {category} is not {row['kind']}")
        if row["kind"] == "overlay":
            start, end = row.get("start_cue"), row.get("end_cue")
            if (
                start not in cue_ids
                or end not in cue_ids
                or _cue_index(cues, start) > _cue_index(cues, end)
            ):
                raise PlanError(f"{row_id}: invalid subtitle range")
        elif row.get("before_cue") not in cue_ids:
            raise PlanError(f"{row_id}: invalid insert point")
        if row.get("status") not in STATUSES:
            raise PlanError(f"{row_id}: unknown status {row.get('status')!r}")
        if row.get("status") == "rearrange" and not str(row.get("note") or "").strip():
            raise PlanError("選「重排」要寫修正意見")
        if row.get("program_state") not in PROGRAM_STATES:
            raise PlanError(f"{row_id}: unknown program_state {row.get('program_state')!r}")


def _cue_index(cues: list[Mapping[str, Any]], cue_id: object) -> int:
    for index, cue in enumerate(cues):
        if cue["id"] == cue_id:
            return index
    raise PlanError(f"unknown subtitle {cue_id!r}")


def _find_row(plan: Mapping[str, Any], row_id: str) -> dict[str, Any]:
    for row in plan["rows"]:
        if row["id"] == row_id:
            return row
    raise PlanError("找不到這一列")


def new_row_id() -> str:
    return "r" + uuid.uuid4().hex[:10]


def snapshot(row: Mapping[str, Any]) -> dict[str, Any]:
    """The Director version that 「復原」 returns to."""

    keys = ("category", "layout", *TEXT_FIELDS, "start_cue", "end_cue", "before_cue", "insert_sec")
    return {key: row.get(key) for key in keys}


# ── Bridge 上的修改 ─────────────────────────────────────────────────────


def update_row(plan: dict[str, Any], row_id: str, changes: Mapping[str, Any]) -> dict[str, Any]:
    """Apply one autosave from Bridge and return the updated row."""

    unknown = set(changes) - EDITABLE
    if unknown:
        raise PlanError(f"不能修改的欄位：{', '.join(sorted(unknown))}")
    row = _find_row(plan, row_id)
    categories = load_categories()
    updated = dict(row)

    if "category" in changes and changes["category"] != row.get("category"):
        if row["origin"] == "auto":
            raise PlanError("自動產生的列不能改分類")
        category = categories.get(str(changes["category"]))
        if category is None or category.auto or category.kind != row["kind"]:
            raise PlanError("這個分類不能用在這一列")
        updated["category"] = category.id
        if category.fixed_layout:
            updated["layout"] = None if category.fixed_layout == "none" else category.fixed_layout

    if "layout" in changes:
        updated["layout"] = _checked_layout(categories, updated, changes["layout"])

    for field in (*TEXT_FIELDS, "note"):
        if field in changes:
            value = str(changes[field] or "")
            if len(value) > MAX_TEXT:
                raise PlanError("文字太長")
            updated[field] = value

    if "start_cue" in changes or "end_cue" in changes:
        if row["kind"] != "overlay":
            raise PlanError("插入類只能改插入點")
        updated["start_cue"] = changes.get("start_cue", row["start_cue"])
        updated["end_cue"] = changes.get("end_cue", row["end_cue"])
        if _cue_index(plan["cues"], updated["start_cue"]) > _cue_index(
            plan["cues"], updated["end_cue"]
        ):
            raise PlanError("起點要在終點之前")
    if "before_cue" in changes:
        if row["kind"] != "insert":
            raise PlanError("疊加類要選起訖字幕")
        _cue_index(plan["cues"], changes["before_cue"])
        updated["before_cue"] = changes["before_cue"]

    edited = any(updated.get(key) != row.get(key) for key in APPROVE_ON_EDIT)
    if "status" in changes:
        status = changes["status"] or None
        if status not in STATUSES:
            raise PlanError("狀態只有核准或重排")
        updated["status"] = status
    elif edited:
        updated["status"] = "approved"
    if updated["status"] == "rearrange" and not str(updated.get("note") or "").strip():
        raise PlanError("選「重排」要寫修正意見")

    row.clear()
    row.update(updated)
    return row


def _checked_layout(categories: Mapping[str, Category], row: Mapping[str, Any], value: object):
    category = categories.get(row.get("category") or "")
    if category and category.fixed_layout:
        fixed = None if category.fixed_layout == "none" else category.fixed_layout
        if value not in (fixed, None, ""):
            raise PlanError("這個分類的版面是固定的")
        return fixed
    if value in (None, ""):
        return None
    if value not in ("full", "overlay"):
        raise PlanError("版面只有滿版或疊在畫面上")
    return value


def add_row(plan: dict[str, Any], start_cue: object, end_cue: object) -> dict[str, Any]:
    """「加畫面」：從空白段選的字幕建一列新的疊加畫面，等 Director 補指示。"""

    if _cue_index(plan["cues"], start_cue) > _cue_index(plan["cues"], end_cue):
        raise PlanError("起點要在終點之前")
    row = {
        "id": new_row_id(),
        "origin": "manual",
        "kind": "overlay",
        "category": None,
        "start_cue": start_cue,
        "end_cue": end_cue,
        "before_cue": None,
        "insert_sec": None,
        "layout": None,
        "description": "",
        "dp_instruction": "",
        "onscreen_text": "",
        "source": "",
        "status": None,
        "note": "",
        "program_state": None,
        "original": None,
    }
    plan["rows"].append(row)
    return row


def delete_row(plan: dict[str, Any], row_id: str) -> None:
    row = _find_row(plan, row_id)
    if row["origin"] == "auto":
        raise PlanError("自動產生的列不能刪除")
    plan["rows"].remove(row)


def restore_row(plan: dict[str, Any], row_id: str) -> dict[str, Any]:
    row = _find_row(plan, row_id)
    original = row.get("original")
    if not original:
        raise PlanError("這一列沒有 Director 原版")
    row.update(original)
    row["status"] = None
    row["note"] = ""
    return row


# ── 顯示用的推導 ────────────────────────────────────────────────────────


def fmt_time(seconds: float) -> str:
    whole = int(round(seconds))
    return (
        f"{whole // 3600}:{whole % 3600 // 60:02d}:{whole % 60:02d}"
        if whole >= 3600
        else (f"{whole // 60}:{whole % 60:02d}")
    )


@dataclass(frozen=True)
class Gap:
    cue_ids: tuple[object, ...]
    start: float
    end: float

    @property
    def seconds(self) -> float:
        return max(0.0, self.end - self.start)


def _row_anchor(plan: Mapping[str, Any], row: Mapping[str, Any]) -> tuple[int, int]:
    """Sort key: inserts sit just before their cue, overlays at their first cue."""

    if row["kind"] == "insert":
        return (_cue_index(plan["cues"], row["before_cue"]), 0)
    return (_cue_index(plan["cues"], row["start_cue"]), 1)


def _covers(row: Mapping[str, Any], categories: Mapping[str, Category]) -> bool:
    if row["kind"] != "overlay":
        return False
    category = categories.get(row.get("category") or "")
    return category.covers if category else True


def covered_cue_ids(plan: Mapping[str, Any]) -> set[object]:
    categories = load_categories()
    covered: set[object] = set()
    cues = plan["cues"]
    for row in plan["rows"]:
        if not _covers(row, categories):
            continue
        first, last = _cue_index(cues, row["start_cue"]), _cue_index(cues, row["end_cue"])
        covered.update(cue["id"] for cue in cues[first : last + 1])
    return covered


def gaps(plan: Mapping[str, Any]) -> list[Gap]:
    """Runs of subtitles with nothing on screen; an insert (e.g. 章節卡) ends a run."""

    covered = covered_cue_ids(plan)
    insert_points = {row["before_cue"] for row in plan["rows"] if row["kind"] == "insert"}
    result: list[Gap] = []
    run: list[Mapping[str, Any]] = []

    def flush() -> None:
        if run:
            result.append(Gap(tuple(c["id"] for c in run), run[0]["start"], run[-1]["end"]))
            run.clear()

    for cue in plan["cues"]:
        if cue["id"] in insert_points:
            flush()
        if cue["id"] in covered:
            flush()
        else:
            run.append(cue)
    flush()
    return result


def timeline(plan: Mapping[str, Any]) -> list[tuple[str, Any]]:
    """Rows in time order with ("gap", Gap) entries where nothing is on screen."""

    entries: list[tuple[tuple[int, int, int], tuple[str, Any]]] = []
    for order, row in enumerate(plan["rows"]):
        entries.append(((*_row_anchor(plan, row), order), ("row", row)))
    for gap in gaps(plan):
        entries.append(((_cue_index(plan["cues"], gap.cue_ids[0]), 2, 0), ("gap", gap)))
    return [entry for _, entry in sorted(entries, key=lambda item: item[0])]


def missing_required(row: Mapping[str, Any]) -> list[str]:
    category = load_categories().get(row.get("category") or "")
    if category is None:
        return ["category"]
    return [field for field in category.required if not str(row.get(field) or "").strip()]


def row_flags(row: Mapping[str, Any]) -> list[str]:
    """程式自動顯示的標示（規格 §4.2）。"""

    flags: list[str] = []
    if row.get("program_state"):
        flags.append(row["program_state"])
    category = load_categories().get(row.get("category") or "")
    if category is not None and row.get("program_state") != "on_timeline":
        if category.readiness == "user" and "awaiting_material" not in flags:
            flags.append("awaiting_material")
        elif category.readiness in ("unverified", "future"):
            flags.append("pending_build")
    return flags


def stats(plan: Mapping[str, Any]) -> dict[str, Any]:
    rows = plan["rows"]
    categories = load_categories()
    cues = plan["cues"]
    duration = (cues[-1]["end"] - cues[0]["start"]) if cues else 0.0
    counted = [
        row
        for row in rows
        if row.get("category") in categories and categories[row["category"]].density
    ]
    plan_gaps = gaps(plan)
    longest = max(plan_gaps, key=lambda gap: gap.seconds, default=None)
    return {
        "total": len(rows),
        "approved": sum(1 for row in rows if row.get("status") == "approved"),
        "rearrange": sum(1 for row in rows if row.get("status") == "rearrange"),
        "pending": sum(1 for row in rows if row.get("status") is None),
        "per_minute": round(len(counted) / (duration / 60), 1) if duration > 0 else 0.0,
        "longest_gap": longest,
        "duration": duration,
    }


def rows_for_agent(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    """「重排好了」之後 agent 要處理的列：標了重排的，加上指示還沒補的新增列。"""

    return [
        row
        for row in plan["rows"]
        if row.get("status") == "rearrange"
        or (row["origin"] == "manual" and not str(row.get("dp_instruction") or "").strip())
    ]


def cue_lookup(plan: Mapping[str, Any]) -> dict[object, Mapping[str, Any]]:
    return {cue["id"]: cue for cue in plan["cues"]}


def cue_range(plan: Mapping[str, Any], start: object, end: object) -> list[Mapping[str, Any]]:
    cues = plan["cues"]
    return cues[_cue_index(cues, start) : _cue_index(cues, end) + 1]


def ensure_ids(rows: Iterable[dict[str, Any]]) -> None:
    for row in rows:
        row.setdefault("id", new_row_id())
