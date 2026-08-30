"""mode B 字卡企劃：把「論證骨架」規格展開成 `<id>_titles.json`。

短片的字卡逐子句承接**全部**逐字稿（`covers_full_transcript`）。創意判斷是
**哪幾句歸同一個 beat、哪一句要升級成 emphasis**——那寫在 `<id>_titles.plan.json`
裡，由 agent 依 shortform-director 手冊決定。機械部分（斷行、覆蓋率、強調預算、
kinetic comp 長度）在這裡，每一條違反就爆，不降級。

之所以要有這支：2026-08-30 之前企劃是用 scratchpad 的一次性腳本產的，每支短片
一份、彼此漂移，重跑一次就可能拿到舊版（實測把 punch-S02 的 20 句企劃蓋成
31 句的舊版）。規格進 episode 資料夾、工具進 repo，重跑才可重現。

plan 檔（`highlights/tighten/<id>_titles.plan.json`）：

```json
{
  "spine": "一句話寫這支的論證骨架",
  "split_opener_sec": 4.0,
  "beats": [
    {"beat": "hook", "tier": 2, "transition": "enter", "cues": [1, 2, 3],
     "roles": {"3": "emphasis"}}
  ]
}
```

- `cues` 依序不重不漏地覆蓋 tight SRT 的每一句（自檢）
- `tier` 1 = hero 位（`pos_y` 0.62），2 = 下方（0.86）；tier1 全片 1–3 段
- `roles` 把某句升級成 `emphasis`／`hero`（字數上限更嚴），預設 `caption`
- `transition` 是該 beat **第一張**的進場；其餘 caption 用 `cut`、升級的用 `promote`
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.zh_linebreak import wrap_lines  # noqa: E402

TIGHTEN_DIR = "highlights/tighten"
#: 每種版式的單行字數上限（顯示寬，兩行）。排不下不是換樣式，是上游沒拆句。
LINE_LIMIT = {"caption": 10, "emphasis": 7, "hero": 5}
#: kinetic comp 一次渲的上限；超過要拆成兩個 sequence。
MAX_SEQUENCE_SEC = 11.8
#: 強調不能到處都是——超過四分之一就沒有強調可言。
EMPHASIS_SHARE = 4
TIER1_RANGE = (1, 3)


def parse_srt(path: Path) -> list[dict]:
    rows: list[dict] = []
    for block in re.split(r"\r?\n\r?\n", path.read_bytes().decode("utf-8-sig").strip()):
        lines = [x for x in block.splitlines() if x.strip()]
        if len(lines) < 3:
            continue
        m = re.match(r"\d\d:(\d\d):(\d\d),(\d\d\d) --> \d\d:(\d\d):(\d\d),(\d\d\d)", lines[1])
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        rows.append(
            {
                "n": int(lines[0]),
                "t0": round(g[0] * 60 + g[1] + g[2] / 1000, 3),
                "t1": round(g[3] * 60 + g[4] + g[5] / 1000, 3),
                "text": " ".join(lines[2:]).replace(" ", ""),
            }
        )
    return rows


def latest_tight_srt(episode_dir: Path, cid: str) -> Path:
    found = sorted((episode_dir / "highlights/srt").glob(f"{cid}_tight_r*.srt"))
    if not found:
        raise SystemExit(f"找不到 {cid} 的 tight SRT——先跑 run_shortform_director.py")
    return found[-1]


def build(episode_dir: Path, cid: str) -> dict:
    plan_path = episode_dir / TIGHTEN_DIR / f"{cid}_titles.plan.json"
    if not plan_path.is_file():
        raise SystemExit(
            f"缺企劃規格 {plan_path}——論證骨架是創意判斷，工具不會替你想。"
            "格式見本檔 docstring 與 shortform-director SKILL"
        )
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    srt = latest_tight_srt(episode_dir, cid)
    cues = {c["n"]: c for c in parse_srt(srt)}

    planned = [n for b in plan["beats"] for n in b["cues"]]
    if planned != sorted(cues):
        missing = sorted(set(cues) - set(planned))
        extra = [n for n in planned if n not in cues]
        raise SystemExit(
            f"覆蓋不符（{srt.name} 共 {len(cues)} 句）：漏 {missing}／多 {extra}／"
            f"順序 {'亂' if sorted(planned) == sorted(cues) else '不適用'}"
        )

    titles = []
    for b in plan["beats"]:
        ns = b["cues"]
        roles = {int(k): v for k, v in (b.get("roles") or {}).items()}
        states = []
        for n in ns:
            cue = cues[n]
            role = roles.get(n, "caption")
            lines = wrap_lines(cue["text"], LINE_LIMIT[role], 2)
            if lines is None:
                raise SystemExit(
                    f"cue {n}「{cue['text']}」（{len(cue['text'])} 字）排不進 {role} 的"
                    f" {LINE_LIMIT[role]} 字兩行——這句要在導播那一步（_split_long_cues）"
                    "拆開，不是在這裡換樣式或降級成三行"
                )
            states.append(
                {
                    "at": round(cue["t0"] - cues[ns[0]]["t0"], 3),
                    "trigger_cue": n,
                    "source_cues": [n],
                    "transition": b["transition"]
                    if not states
                    else ("promote" if role != "caption" else "cut"),
                    "role": role,
                    "lines": lines,
                    "scales": [1.0] * len(lines),
                }
            )
        show = round(cues[ns[-1]]["t1"] - cues[ns[0]]["t0"], 2)
        if show > MAX_SEQUENCE_SEC:
            raise SystemExit(
                f"beat {b['beat']} {ns} 長 {show}s 超過 kinetic comp 上限"
                f" {MAX_SEQUENCE_SEC}s——拆成兩段"
            )
        titles.append(
            {
                "t0": cues[ns[0]]["t0"],
                "t1": cues[ns[-1]]["t1"],
                "source_cues": list(ns),
                "beat": b["beat"],
                "tier": b["tier"],
                "pos_y": 0.62 if b["tier"] == 1 else 0.86,
                "exit": "hard_cut",
                "states": states,
            }
        )

    all_states = [s for t in titles for s in t["states"]]
    n_emph = sum(1 for s in all_states if s["role"] != "caption")
    budget = len(all_states) // EMPHASIS_SHARE
    n_tier1 = sum(1 for t in titles if t["tier"] == 1)
    if n_emph > budget:
        raise SystemExit(f"強調預算超標：{n_emph} 張 > 上限 {budget}")
    if not TIER1_RANGE[0] <= n_tier1 <= TIER1_RANGE[1]:
        raise SystemExit(f"tier1 段數 {n_tier1} 不在 {TIER1_RANGE[0]}–{TIER1_RANGE[1]}")

    return {
        "_spine": plan["spine"],
        "_card_unit": "一個 state = 一個子句級 cue；論點句升級成 emphasis",
        "_srt": srt.name,
        "covers_full_transcript": True,
        "transition_mode": "kinetic",
        "split_opener_sec": plan.get("split_opener_sec", 4.0),
        "titles": titles,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("episode")
    ap.add_argument("--id", required=True)
    args = ap.parse_args(argv)
    episode_dir = Path(args.episode)
    doc = build(episode_dir, args.id)
    out = episode_dir / TIGHTEN_DIR / f"{args.id}_titles.json"
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    states = [s for t in doc["titles"] for s in t["states"]]
    print(f"{doc['_srt']}: {len(doc['titles'])} 段 / {len(states)} 張　逐 cue 恰好一次 ✓")
    for t in doc["titles"]:
        print(f"\n  {t['t0']:6.2f}-{t['t1']:6.2f}  tier{t['tier']} {t['beat']}")
        for s in t["states"]:
            lines = " / ".join(s["lines"])
            print(f"      +{s['at']:5.2f} {s['transition']:8s} [{s['role']:8s}] {lines}")
    print(f"\n→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
