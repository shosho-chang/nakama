"""把滿版轉場卡的文字攤開來人眼 review：canonical 寫了什麼、片子上實際是什麼。

滿版轉場卡的字是同一份 canonical `transition_title` 走三段路：YouTube chapter、
description timestamp、滿版卡。上游 miner 寫、Director 逐字沿用、renderer 渲出來。
2026-09-08 修修 review 蘇予昕那一集時，六張卡全部沒頭沒尾，而要查出「這行字是誰寫的」
花了整輪考古——才發現 canonical 一直沒變、是 Director 每個 run 都自己改寫一遍，還把
「粽子」改成「繩子」「筷子」。這支就是把那輪考古變成一行指令。

用法：

    python scripts/review_transition_titles.py \
        --runtime-root E:/nakama/data/finished-cut-runtime \
        --episode-id "20260901 蘇予昕"

加 `--full` 印出每節完整逐字稿（review 文案是否真的總結了整節時要）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.brook.script_video.finished_cut_production._approved_cut import (  # noqa: E402
    ApprovedCutRegistrationError,
    _validate_transition_title,
)

SAMPLE_CHARS = 90


def _timestamp(seconds: float) -> str:
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def _lint(section_id: str, title: str) -> str | None:
    try:
        _validate_transition_title(section_id, title)
    except ApprovedCutRegistrationError as error:
        return str(error)
    return None


def _load_runs(runtime_root: Path, episode_id: str) -> dict:
    authority = runtime_root / "episodes" / episode_id / "runs" / "authority.json"
    if not authority.is_file():
        raise SystemExit(f"找不到 run store：{authority}")
    return json.loads(authority.read_text(encoding="utf-8"))["runs"]


def _canonical_sections(runs: dict, cut_id: str | None) -> tuple[str, list, list]:
    """回傳最近一個 run 的 (cut_id, sections, cues)。canonical 在同一支 cut 內固定不變。"""
    for command_id in reversed(list(runs)):
        context = (runs[command_id].get("view") or {}).get("editorial_context") or {}
        if not context.get("sections"):
            continue
        if cut_id is not None and context.get("cut_id") != cut_id:
            continue
        return context["cut_id"], context["sections"], context.get("cues") or []
    raise SystemExit("這一集沒有任何 run 帶 canonical section map")


def _shipped_by_run(runs: dict, cut_id: str) -> dict[str, list[tuple[float, str]]]:
    """每個 run 的 Director 實際交出來的滿版轉場文字。"""
    shipped: dict[str, list[tuple[float, str]]] = {}
    for command_id, run in runs.items():
        view = run.get("view") or {}
        if ((view.get("editorial_context") or {}).get("cut_id")) != cut_id:
            continue
        stages = [s for s in view.get("accepted_stages") or [] if s["stage"] == "director"]
        if not stages:
            continue
        events = [
            event for event in stages[-1]["events"] if event.get("semantic_kind") == "chapter"
        ]
        if events:
            shipped[command_id] = [
                (float(e["t0"]), e.get("display") or "")
                for e in sorted(events, key=lambda e: e["t0"])
            ]
    return shipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", required=True, type=Path)
    parser.add_argument("--episode-id", required=True)
    parser.add_argument("--cut-id")
    parser.add_argument("--full", action="store_true", help="印出每節完整逐字稿")
    args = parser.parse_args()

    runs = _load_runs(args.runtime_root, args.episode_id)
    cut_id, sections, cues = _canonical_sections(runs, args.cut_id)
    print(f"# {args.episode_id} — {cut_id}\n")

    violations = 0
    for section in sections:
        section_id = section["section_id"]
        title = section.get("transition_title")
        body = "".join(c["text"] for c in cues if c["section_id"] == section_id)
        count = sum(1 for c in cues if c["section_id"] == section_id)
        head = f"{_timestamp(section['t0'])}  {section_id}  {count} 句"
        if title is None:
            print(f"{head}  （無轉場卡）")
        else:
            print(f"{head}  卡片=「{title}」")
            problem = _lint(section_id, title)
            if problem is not None:
                violations += 1
                print(f"    ✗ {problem}")
        if args.full:
            print(f"    {body}")
        elif body:
            print(f"    開頭｜{body[:SAMPLE_CHARS]}")
            print(f"    結尾｜{body[-SAMPLE_CHARS:]}")
        print()

    canonical = [
        (section["t0"], section["transition_title"])
        for section in sections
        if section.get("transition_title")
    ]
    shipped = _shipped_by_run(runs, cut_id)
    rewritten = {
        command_id: rows
        for command_id, rows in shipped.items()
        if [t for _, t in rows] != [t for _, t in canonical]
    }
    print(f"## Director 交出來的文字（{len(shipped)} 個 run）\n")
    if not rewritten:
        print("每個 run 都逐字沿用 canonical。\n")
    else:
        print(f"⚠ {len(rewritten)}/{len(shipped)} 個 run 改寫了 canonical——下游必須逐字沿用。\n")
        seen: set[tuple[str, ...]] = set()
        for command_id, rows in rewritten.items():
            key = tuple(t for _, t in rows)
            if key in seen:
                continue
            seen.add(key)
            print(f"  {command_id}")
            for (_, expected), (_, got) in zip(canonical, rows, strict=False):
                mark = "  " if expected == got else "→ "
                print(f"    {mark}canonical={expected!r}  shipped={got!r}")
            print()

    if violations:
        print(f"canonical 有 {violations} 張卡違反轉場卡文字標準。")
    return 1 if violations or rewritten else 0


if __name__ == "__main__":
    sys.exit(main())
