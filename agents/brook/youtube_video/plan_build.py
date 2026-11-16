"""YouTube 照稿影片：建畫面規劃表（Director 的程式部分，規格 §3）。

判斷在 skill `youtube-director`（當下的 agent）；這裡只做不需要判斷的事：

- `init`：存稿的快照、把字幕變成規劃表的句子、依稿上標記自動產生列（§3.4、§3.5）——
  `##` → 章節卡（插入）、`###` → 小標題遮罩（疊加，放在該段第一句）、
  第一個「嗨大家」段落之前 → 頻道動畫＋Jingle（插入，素材修修提供）。
  稿是寫的、字幕是講的，標題對到哪一句用模糊比對；信心低的印出來，請修修在 Bridge 看。
- `add`：收 Director 寫的列（JSON），逐列檢查分類、必填欄位、字幕範圍，再寫進規劃表。
- `stats`：每分鐘畫面數、最長空白段（§3.7）。

用法：

    python -m agents.brook.youtube_video.plan_build init "G:/Footages/<ep>" --script <稿.md>
    python -m agents.brook.youtube_video.plan_build add "G:/Footages/<ep>" rows.json
    python -m agents.brook.youtube_video.plan_build stats "G:/Footages/<ep>"
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import shutil
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents.brook.youtube_video import visual_plan as vp
from agents.brook.youtube_video.categories import load_categories

SCRIPT_SNAPSHOT = "script.md"
SRT_FILE = "transcript.srt"
CHANNEL_INTRO_MARK = "嗨大家"  # 修修：「頻道自我介紹一定從『嗨大家』開始講」
NOT_SPOKEN = ("reference",)  # `## Reference`：不唸，什麼都不產生
OUTRO = ("outro",)  # `## Outro`：不插章節卡（轉場做法待查）
LOW_CONFIDENCE = 0.55
CHAPTER_SEC = 2
ROW_FIELDS = ("description", "dp_instruction", "onscreen_text", "source")


class BuildError(ValueError):
    """規劃表建不起來或 Director 的列不合規則；訊息直接給 agent／修修看。"""


# ── 稿 ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Block:
    kind: str  # h2 / h3 / p
    text: str


def parse_script(text: str) -> list[Block]:
    """稿 → 標題與段落（`## Reference` 之後不算，YAML 前言與「標題:」行略過）。"""
    if text.startswith("---"):
        text = text.split("---", 2)[2]
    blocks: list[Block] = []
    for raw in re.split(r"\n\s*\n", text):
        chunk = raw.strip()
        if not chunk:
            continue
        for line in chunk.splitlines():
            line = line.strip()
            heading = re.match(r"^(#{2,3})\s+(.+?)\s*$", line)
            if heading:
                level, title = heading.groups()
                if level == "##" and title.strip().lower() in NOT_SPOKEN:
                    return blocks
                blocks.append(Block("h2" if level == "##" else "h3", title.strip()))
                continue
        paragraph = " ".join(
            ln.strip()
            for ln in chunk.splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")
        )
        if paragraph and not re.match(r"^標題\s*[:：]", paragraph):
            blocks.append(Block("p", paragraph))
    return blocks


# ── 字幕 ───────────────────────────────────────────────────────────────


def _seconds(stamp: str) -> float:
    h, m, rest = stamp.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest.replace(",", "."))


def parse_srt(text: str) -> list[dict[str, Any]]:
    cues = []
    for block in re.split(r"\n\s*\n", text.strip().lstrip("\ufeff")):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        start, end = (_seconds(part) for part in lines[1].split("-->"))
        body = re.sub(r"<[^>]+>", "", " ".join(lines[2:])).strip()
        cues.append(
            {"id": len(cues) + 1, "start": round(start, 3), "end": round(end, 3), "text": body}
        )
    if not cues:
        raise BuildError("字幕檔沒有任何一句")
    return cues


# ── 標題對到字幕 ────────────────────────────────────────────────────────


def _norm(text: str) -> str:
    return re.sub(r"[^\w]", "", text).lower()


@dataclass(frozen=True)
class Match:
    cue_index: int
    score: float


def _similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def find_cue(cues: Sequence[Mapping[str, Any]], paragraph: str, start: int) -> Match:
    """段落開頭最像哪一句字幕（從 start 往後找）。

    分數＝一半看「從這句起往後幾句」像不像段落開頭，一半看「這句本身」像不像段落開頭——
    只看前者會被前一段講完順口多說的尾巴拖走（例：「以終為始嗎／書中說到」）。
    """
    target = _norm(paragraph)[:18]
    if not target:
        return Match(start, 0.0)
    best = Match(start, -1.0)
    for i in range(start, len(cues)):
        window = ""
        j = i
        while len(window) < len(target) + 6 and j < len(cues):
            window += _norm(cues[j]["text"])
            j += 1
        first = _norm(cues[i]["text"])
        score = 0.5 * _similar(target, window[: len(target) + 6]) + 0.5 * _similar(
            target[: len(first)], first
        )
        if score > best.score + 1e-9:
            best = Match(i, score)
    return best


def spoken_heading(cues: Sequence[Mapping[str, Any]], heading: str, at: int, lo: int) -> int:
    """標題有被唸出來（例：「策略四 建立屬於自己的事業」）就錨在唸標題那一句：往前看 3 句。"""
    title = _norm(heading)
    for i in range(max(lo, at - 3), at):
        text = _norm(cues[i]["text"])
        if not text:
            continue
        m = difflib.SequenceMatcher(None, text, title).find_longest_match(
            0, len(text), 0, len(title)
        )
        if m.size >= 4 and m.size >= 0.5 * len(text):
            return i
    return at


@dataclass(frozen=True)
class Anchor:
    kind: str  # chapter / section / channel_intro
    title: str
    cue_index: int
    score: float
    first_line: str


def anchors(blocks: Sequence[Block], cues: Sequence[Mapping[str, Any]]) -> list[Anchor]:
    """每個標題（與「嗨大家」段落）在字幕的哪一句開始；照稿的順序，只往後找。"""
    found: list[Anchor] = []
    pos = 0
    intro_done = False
    for k, block in enumerate(blocks):
        if block.kind == "p" and not intro_done and block.text.startswith(CHANNEL_INTRO_MARK):
            m = find_cue(cues, block.text, pos)
            found.append(
                Anchor("channel_intro", "頻道動畫＋Jingle", m.cue_index, m.score, block.text[:24])
            )
            pos, intro_done = m.cue_index, True
            continue
        if block.kind not in ("h2", "h3"):
            continue
        nxt = next((b for b in blocks[k + 1 :] if b.kind == "p"), None)
        if nxt is None:
            continue
        m = find_cue(cues, nxt.text, pos)
        at = spoken_heading(cues, block.text, m.cue_index, pos)
        kind = "chapter" if block.kind == "h2" else "section"
        found.append(Anchor(kind, block.text, at, m.score, nxt.text[:24]))
        pos = m.cue_index
    return found


# ── 列 ─────────────────────────────────────────────────────────────────


def _blank_row(**fields: Any) -> dict[str, Any]:
    row = {
        "id": vp.new_row_id(),
        "origin": "auto",
        "kind": "overlay",
        "category": None,
        "start_cue": None,
        "end_cue": None,
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
    }
    row.update(fields)
    row["original"] = vp.snapshot(row)
    return row


def auto_rows(found: Sequence[Anchor], cues: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for a in found:
        cue_id = cues[a.cue_index]["id"]
        note = (
            ""
            if a.score >= LOW_CONFIDENCE
            else f"標題對到字幕的信心低（{a.score:.2f}），請確認位置"
        )
        if a.kind == "chapter":
            if a.title.strip().lower() in OUTRO:
                continue
            rows.append(
                _blank_row(kind="insert", category="chapter_card", before_cue=cue_id,
                           insert_sec=CHAPTER_SEC, layout="full", onscreen_text=a.title, note=note)
            )  # fmt: skip
        elif a.kind == "section":
            rows.append(
                _blank_row(category="section_title", start_cue=cue_id, end_cue=cue_id,
                           layout="full", onscreen_text=a.title, note=note)
            )  # fmt: skip
        else:
            rows.append(
                _blank_row(kind="insert", category="channel_intro", before_cue=cue_id,
                           layout="full", description="頻道動畫＋Jingle（修修提供）", note=note)
            )  # fmt: skip
    return rows


def director_row(plan: Mapping[str, Any], raw: Mapping[str, Any]) -> dict[str, Any]:
    """Director 寫的一列 → 規劃表的一列；不合規則就丟 BuildError（說哪一列、哪裡不對）。"""
    categories = load_categories()
    label = raw.get("ref") or raw.get("description") or raw.get("category")
    category = categories.get(str(raw.get("category") or ""))
    if category is None:
        raise BuildError(f"{label}: 不認得的分類 {raw.get('category')!r}")
    if category.auto:
        raise BuildError(f"{label}: {category.name} 是自動產生的，Director 不能手選")
    fields = {f: str(raw.get(f) or "").strip() for f in ROW_FIELDS}
    row = _blank_row(origin="director", kind=category.kind, category=category.id, **fields)
    row["note"] = str(raw.get("note") or "").strip()
    cue_ids = {cue["id"] for cue in plan["cues"]}
    if category.kind == "overlay":
        start, end = raw.get("start_cue"), raw.get("end_cue", raw.get("start_cue"))
        if start not in cue_ids or end not in cue_ids:
            raise BuildError(f"{label}: 字幕 {start}–{end} 不存在")
        if vp.cue_range(plan, start, end) == []:
            raise BuildError(f"{label}: 起點要在終點之前")
        row.update(start_cue=start, end_cue=end)
    else:
        before = raw.get("before_cue")
        if before not in cue_ids:
            raise BuildError(f"{label}: 插入點字幕 {before!r} 不存在")
        row.update(before_cue=before, insert_sec=raw.get("insert_sec"))
    if category.fixed_layout:
        row["layout"] = None if category.fixed_layout == "none" else category.fixed_layout
    else:
        layout = raw.get("layout")
        if layout not in ("full", "overlay"):
            raise BuildError(
                f"{label}: {category.name} 要選版面 full（滿版）或 overlay（疊在畫面上）"
            )
        row["layout"] = layout
    missing = vp.missing_required(row)
    if missing:
        raise BuildError(f"{label}: {category.name} 必填 {', '.join(missing)} 是空的")
    row["original"] = vp.snapshot(row)
    return row


# ── 指令 ───────────────────────────────────────────────────────────────


def init(
    episode: Path, script: Path, srt: Path | None = None
) -> tuple[dict[str, Any], list[Anchor]]:
    path = vp.plan_path(episode)
    if path.exists():
        raise BuildError(f"{path} 已經存在，不覆蓋（要重來請先把它改名）")
    srt = srt or episode / SRT_FILE
    cues = parse_srt(srt.read_text(encoding="utf-8-sig"))
    snapshot = episode / SCRIPT_SNAPSHOT
    shutil.copyfile(script, snapshot)
    blocks = parse_script(snapshot.read_text(encoding="utf-8-sig"))
    found = anchors(blocks, cues)
    plan = {
        "schema": vp.SCHEMA,
        "episode": episode.name,
        "cues": cues,
        "rows": auto_rows(found, cues),
    }
    vp.save_plan(path, plan)
    return plan, found


def add(episode: Path, rows_file: Path) -> list[dict[str, Any]]:
    path = vp.plan_path(episode)
    raw_rows = json.loads(rows_file.read_text(encoding="utf-8"))
    with vp.plan_lock(path):
        plan = vp.load_plan(path)
        new = [director_row(plan, raw) for raw in raw_rows]  # 全部合格才寫
        plan["rows"].extend(new)
        vp.save_plan(path, plan)
    return new


def describe_stats(plan: Mapping[str, Any]) -> str:
    s = vp.stats(plan)
    gap = s["longest_gap"]
    longest = (
        f"{gap.seconds:.0f} 秒（{vp.fmt_time(gap.start)}–{vp.fmt_time(gap.end)}）" if gap else "無"
    )
    return f"{s['total']} 列；每分鐘 {s['per_minute']} 個畫面（參考 4.5–5.5）；最長空白 {longest}"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="建 YouTube 照稿影片的畫面規劃表")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_init = sub.add_parser("init", help="存稿的快照、字幕變句子、自動產生列")
    p_init.add_argument("episode", type=Path)
    p_init.add_argument("--script", type=Path, required=True)
    p_init.add_argument("--srt", type=Path)
    p_add = sub.add_parser("add", help="加 Director 寫的列（JSON 陣列）")
    p_add.add_argument("episode", type=Path)
    p_add.add_argument("rows", type=Path)
    p_stats = sub.add_parser("stats", help="畫面密度與最長空白")
    p_stats.add_argument("episode", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.cmd == "init":
            plan, found = init(args.episode, args.script, args.srt)
            for a in found:
                cue = plan["cues"][a.cue_index]
                flag = "" if a.score >= LOW_CONFIDENCE else "  ← 信心低"
                print(
                    f"{a.kind:13} 第 {cue['id']:>3} 句 {a.score:.2f} {a.title}｜{cue['text']}{flag}"
                )
            print(f"{len(plan['cues'])} 句字幕；自動產生 {len(plan['rows'])} 列")
        elif args.cmd == "add":
            new = add(args.episode, args.rows)
            print(f"加了 {len(new)} 列")
            print(describe_stats(vp.load_plan(vp.plan_path(args.episode))))
        else:
            print(describe_stats(vp.load_plan(vp.plan_path(args.episode))))
    except BuildError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
