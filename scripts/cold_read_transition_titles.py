"""跑轉場卡冷讀回收測試：只看卡片，能不能知道那一節在講什麼。

標準由修修 2026-09-08 訂下：「只要看這個 transition 的字卡，就可以知道這個小節大概
在講什麼。」機制與為什麼要兩次隔離呼叫，見
`agents/brook/script_video/transition_cold_read.py`。

註冊前用 miner 產出的 candidate 檔跑（此時修卡片最便宜）：

    python scripts/cold_read_transition_titles.py --sections path/to/candidate.json

已註冊的 cut 直接讀 run store：

    python scripts/cold_read_transition_titles.py \
        --runtime-root E:/nakama/data/finished-cut-runtime \
        --episode-id "20260901 蘇予昕" --cut-id punch-L04

有任何一張沒通過就 exit 1。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.brook.script_video.transition_cold_read import (  # noqa: E402
    ColdReadSection,
    blind_system_prompt,
    cold_read_sections,
    recovery_system_prompt,
    report_as_json,
)
from shared.llm_router import get_model  # noqa: E402


def _sections_from_payload(payload: object, cut_id: str | None = None) -> list[ColdReadSection]:
    """接受 miner candidate、`{"sections": [...]}`、或裸 section 陣列。

    一份 candidates.json 通常裝著好幾支 cut，section_id 又在各支之間重複，全部混在
    一次盲讀裡送出去既慢又讀不出東西。給 `cut_id` 就只跑那一支。
    """
    if isinstance(payload, dict):
        rows = payload.get("sections")
        if rows is None:
            candidates = payload.get("candidates")
            if isinstance(candidates, list):
                if cut_id is not None:
                    candidates = [c for c in candidates if c.get("id") == cut_id]
                    if not candidates:
                        raise SystemExit(f"這份檔案裡沒有 cut {cut_id!r}")
                elif len(candidates) > 1:
                    ids = [str(c.get("id")) for c in candidates]
                    raise SystemExit(f"這份檔案有多支 cut，請用 --cut-id 指定其中一支：{ids}")
                rows = [
                    section
                    for candidate in candidates
                    for section in (candidate.get("sections") or [])
                ]
    else:
        rows = payload
    if not isinstance(rows, list):
        raise SystemExit("找不到 sections 陣列")
    return [
        ColdReadSection(
            section_id=str(row.get("section_id") or ""),
            summary=str(row.get("summary") or ""),
            transition_title=str(row.get("transition_title") or ""),
        )
        for row in rows
        if isinstance(row, dict) and row.get("transition_title")
    ]


def _sections_from_run_store(runtime_root: Path, episode_id: str, cut_id: str | None):
    authority = runtime_root / "episodes" / episode_id / "runs" / "authority.json"
    if not authority.is_file():
        raise SystemExit(f"找不到 run store：{authority}")
    runs = json.loads(authority.read_text(encoding="utf-8"))["runs"]
    for command_id in reversed(list(runs)):
        context = (runs[command_id].get("view") or {}).get("editorial_context") or {}
        if not context.get("sections"):
            continue
        if cut_id is not None and context.get("cut_id") != cut_id:
            continue
        return _sections_from_payload(context)
    raise SystemExit("這一集沒有任何 run 帶 canonical section map")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sections", type=Path, help="miner candidate 或含 sections 的 JSON")
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--episode-id")
    parser.add_argument("--cut-id")
    parser.add_argument("--seed", type=int, default=None, help="固定盲讀的洗牌順序（重現用）")
    parser.add_argument("--json", action="store_true", help="輸出機器可讀的報告")
    args = parser.parse_args()

    if args.sections is not None:
        sections = _sections_from_payload(
            json.loads(args.sections.read_text(encoding="utf-8")), args.cut_id
        )
    elif args.runtime_root is not None and args.episode_id:
        sections = _sections_from_run_store(args.runtime_root, args.episode_id, args.cut_id)
    else:
        raise SystemExit("要嘛給 --sections，要嘛給 --runtime-root 加 --episode-id")

    if not sections:
        print("這支 cut 沒有轉場卡，不需要冷讀。")
        return 0
    missing_summary = [section.section_id for section in sections if not section.summary]
    if missing_summary:
        raise SystemExit(
            f"這些 section 沒有 summary，無從判定回收：{missing_summary}。"
            "summary 是上游 miner 寫的「這一段完成的論點」，缺了就先補上游。"
        )

    from shared.claude_cli_client import ask_via_cli

    model = get_model("brook", "transition_cold_read")
    calls = {"n": 0}

    def ask(prompt: str) -> str:
        # 盲讀與回收判定必須是兩個互不知情的 context，所以每次都是全新 subprocess，
        # 而且第二次用不同的 system prompt。
        calls["n"] += 1
        system = blind_system_prompt() if calls["n"] == 1 else recovery_system_prompt()
        return ask_via_cli(prompt, system=system, model=model)

    report = cold_read_sections(sections, ask, seed=args.seed)

    if args.json:
        print(report_as_json(report))
        return 0 if report.passed else 1

    for card in report.cards:
        mark = "OK " if card.passed else "✗  "
        print(f"{mark}{card.section_id}  「{card.transition_title}」")
        print(f"      冷讀者以為：{card.guess}")
        if not card.self_contained:
            print(f"      讀不完整，缺：{card.missing}")
        if not card.recovered:
            print(f"      實際論點：{card.summary}")
            print(f"      落空原因：{card.why}")
    print()
    if report.passed:
        print(f"{len(report.cards)} 張卡全部通過冷讀回收。")
        return 0
    print(f"{len(report.failures)}/{len(report.cards)} 張卡沒通過——只看卡片讀不出這節在講什麼。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
