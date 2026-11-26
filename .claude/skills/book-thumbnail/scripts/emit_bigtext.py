#!/usr/bin/env python3
"""emit_bigtext.py — 驗證書籍封面大字候選並落檔（book-thumbnail Step 0b）。

用法:
    python emit_bigtext.py <packaging_dir> < input.json

前置：同一個 packaging_dir 已由 title-brainstorm 的 emit_packages.py 寫好 packages.json
（visual_recipe=youtube_book）。大字一定掛在某一條已定案的標題上——封面大字與標題是
兩個鉤子，配對才有意義（title-brainstorm〈封面大字與標題的分工〉）。

輸入 JSON (stdin):
    {
      "episode_slug": "20261027-wealth-ladder",
      "cut_id": "full",
      "options": [
        {
          "title_rank": 1,
          "lead": "上行小字（可空）",
          "main": "主標大字",
          "hl": "主標裡的橘框詞",
          "angle": "好奇缺口",
          "why": "這組大字補了標題沒講的哪一件事",
          "cite": "transcript.txt 原句或段落",
          "panel": {"hook": 15, "aversion": 2, "notes": ["persona A：…"]}
        }
      ],
      "trace": { ... 完整發想與 panel 紀錄 ... }
    }

輸出:
    <packaging_dir>/<cut_id>/bigtext.json（＋ VAULT_PATH 有設時複製到
    vault Attachments/packaging/<episode_slug>/<cut_id>/）
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

LEAD_MAX = 10  # 上行小字：一眼讀完
MAIN_MIN, MAIN_MAX = 2, 7  # 主標：版型一行放得下、格線尺寸讀得到
PER_TITLE_MAX = 4
REQUIRED_RANKS = (1, 2, 3)
EMOJI = re.compile("[\U0001f000-\U0001faff☀-➿️]")
PUNCT = re.compile(r"[？！?!。，、…\s]")


def _len(s: str) -> int:
    """字數不算標點與空白（「你在第幾階？」算 5 字）。"""
    return len(PUNCT.sub("", s))


def validate(data: dict, titles: dict[int, str]) -> list[dict]:
    errors: list[str] = []
    out: list[dict] = []
    per_rank: dict[int, int] = {}
    for i, o in enumerate(data.get("options", []), 1):
        tag = f"option {i}"
        rank = o.get("title_rank")
        lead = (o.get("lead") or "").strip()
        main = (o.get("main") or "").strip()
        hl = (o.get("hl") or "").strip()
        if rank not in titles:
            errors.append(
                f"{tag}: title_rank={rank!r} 不在 packages.json 的標題裡（有 {sorted(titles)}）"
            )
            continue
        if not (MAIN_MIN <= _len(main) <= MAIN_MAX):
            errors.append(f"{tag}: 主標「{main}」{_len(main)} 字，要 {MAIN_MIN}–{MAIN_MAX} 字")
        if _len(lead) > LEAD_MAX:
            errors.append(f"{tag}: 上行「{lead}」{_len(lead)} 字，上限 {LEAD_MAX}")
        if not hl or hl not in main:
            errors.append(f"{tag}: 橘框詞「{hl}」必須是主標「{main}」裡的連續字")
        for field, val in (("lead", lead), ("main", main)):
            if EMOJI.search(val):
                errors.append(f"{tag}: {field} 有 emoji")
        title = titles[rank]
        core = PUNCT.sub("", main)
        if core and core in PUNCT.sub("", title):
            errors.append(
                f"{tag}: 主標「{main}」整句出現在 rank {rank} 標題裡——"
                "大字跟標題講同一句話，第二個鉤子白放"
            )
        for field in ("why", "cite"):
            if not (o.get(field) or "").strip():
                errors.append(f"{tag}: 缺 {field}")
        per_rank[rank] = per_rank.get(rank, 0) + 1
        out.append(
            {
                "id": f"r{rank}-{per_rank[rank]}",
                "title_rank": rank,
                "lead": lead,
                "main": main,
                "hl": hl,
                "angle": o.get("angle", ""),
                "why": o["why"].strip() if o.get("why") else "",
                "cite": (o.get("cite") or "").strip(),
                "panel": o.get("panel") or {},
            }
        )
    for rank, n in per_rank.items():
        if n > PER_TITLE_MAX:
            errors.append(
                f"rank {rank} 有 {n} 組大字，上限 {PER_TITLE_MAX}（gate 上是挑選題，不是清單）"
            )
    for rank in REQUIRED_RANKS:
        if rank in titles and rank not in per_rank:
            errors.append(f"rank {rank} 標題沒有任何大字候選")
    if errors:
        raise ValueError("\n  ".join(["大字候選沒過驗證："] + errors))
    return out


def main() -> int:
    if len(sys.argv) < 2:
        sys.stderr.write("Usage: python emit_bigtext.py <packaging_dir> < input.json\n")
        return 2
    pdir = Path(sys.argv[1])
    data = json.load(sys.stdin)
    cut_id = data.get("cut_id", "full")
    pk_path = pdir / "packages.json"
    if not pk_path.is_file():
        sys.stderr.write(f"找不到 {pk_path}——先跑 title-brainstorm --batch（emit_packages.py）\n")
        return 1
    packages = json.loads(pk_path.read_text(encoding="utf-8"))
    cut = next((c for c in packages.get("cuts", []) if c.get("cut_id") == cut_id), None)
    if cut is None:
        sys.stderr.write(f"packages.json 裡沒有 cut_id={cut_id}\n")
        return 1
    titles = {t["rank"]: t["text"] for t in cut.get("titles", [])}
    try:
        options = validate(data, titles)
    except ValueError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    out_dir = pdir / cut_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out = {
        "episode": packages.get("episode"),
        "cut_id": cut_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "options": options,
        "trace": data.get("trace", {}),
    }
    path = out_dir / "bigtext.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"OK — {len(options)} 組大字已驗證（{len(titles)} 條標題）")
    print(f"  → {path}")
    vault = os.environ.get("VAULT_PATH")
    slug = data.get("episode_slug")
    if vault and slug:
        dst = Path(vault) / "Attachments" / "packaging" / slug / cut_id
        dst.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dst / path.name)
        print(f"  → vault: {dst / path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
