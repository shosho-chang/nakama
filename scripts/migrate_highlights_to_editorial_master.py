"""把 Editorial Master 出現**之前**開採的 highlight 候選重新錨到 master 時鐘。

    py -3.12 -m scripts.migrate_highlights_to_editorial_master "G:\\footages\\20260814 抹布"

## 為什麼需要這支

開採是對著 Stage 5 的 release SRT 做的（來源時鐘）。Editorial Master 封存之後，
成品的內容邊界與時間軸由 master.srt 定義——兩者不是同一個時鐘：抹布的 release
SRT 有 2,630 條 cue，master.srt 只有 2,566 條。候選的 `t_start`／`t_end` 直接
拿去對 master 切，會切到錯的句子（`run_short_tighten` 就是直接讀這兩個欄位）。

`run_short_tighten._load_winner` 因此要求 `candidates.json` 與 `winners.json`
都帶著**當前** Editorial Master 的 lineage，缺了就 fail closed 要人重新 shortlist。
但重開採會丟掉已經做完的盲審與 TA 審稿；正確的做法是把既有候選**重新錨定**，
內容一字不改。

林之晨（2026-08-25）做過同樣的事，但那是臨時寫的一次性遷移
（`lin-highlight-editorial-master-migration-v1`），腳本沒有留下來。這支把它變成
可重複執行的工具。

## 怎麼錨

**不用「逐條找相同文字」**——短句（「對」「嗯」「然後呢」）在一集裡會出現幾十次，
逐條找必然對錯。改用 `difflib.SequenceMatcher` 對**整份 cue 文字序列**做一次全域
對齊，取出相符區塊，據此建立「舊 cue 編號 → 新 cue 編號」的對照表。剪掉的段落
自然落在對照表的空隙裡。

邊界落在空隙裡（也就是那一句在成品裡被剪掉了）時**不猜**：直接失敗並指名是哪一
個候選的哪一端，讓人回去決定要改邊界還是放棄那一段。

## 不做的事

- 不改任何候選的文字、標題、rationale、hook、transcript
- 不重新評分、不重新選段
- 不碰已 seal 的 Editorial Master 收據
- 原始檔備份到 `highlights/migration/pre-editorial-master-<content_hash>/`，
  連同一份收據；備份存在就拒絕再跑（已經遷移過了）
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.brook.script_video.editorial_master import (  # noqa: E402
    EditorialMasterContractError,
    EditorialMasterRequest,
)

CONTRACT = "podcast-highlight-editorial-master-migration-v1"
HIGHLIGHTS = "highlights"
_TIME_RE = re.compile(r"(\d\d):(\d\d):(\d\d)[,.](\d\d\d)\s*-->\s*(\d\d):(\d\d):(\d\d)[,.](\d\d\d)")


class MigrationError(SystemExit):
    pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_srt(path: Path) -> list[dict]:
    """回傳 [{index, start, end, text}]。index 是 SRT 自己的編號，不是序位。"""
    cues: list[dict] = []
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8-sig").strip()):
        lines = [line for line in block.splitlines() if line.strip()]
        if len(lines) < 2:
            continue
        match = _TIME_RE.search(lines[1] if len(lines) > 1 else "")
        offset = 2
        if match is None:
            match = _TIME_RE.search(lines[0])
            offset = 1
        if match is None:
            continue
        h1, m1, s1, ms1, h2, m2, s2, ms2 = (int(value) for value in match.groups())
        try:
            index = int(lines[0].strip())
        except ValueError:
            index = len(cues) + 1
        cues.append(
            {
                "index": index,
                "start": h1 * 3600 + m1 * 60 + s1 + ms1 / 1000,
                "end": h2 * 3600 + m2 * 60 + s2 + ms2 / 1000,
                "text": "\n".join(lines[offset:]).strip(),
            }
        )
    return cues


def build_index_map(source: list[dict], master: list[dict]) -> dict[int, int]:
    """舊 cue 編號 → 新 cue 編號。只包含全域對齊後**確實相符**的 cue。"""
    matcher = difflib.SequenceMatcher(
        a=[cue["text"] for cue in source], b=[cue["text"] for cue in master], autojunk=False
    )
    mapping: dict[int, int] = {}
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            mapping[source[block.a + offset]["index"]] = master[block.b + offset]["index"]
    return mapping


def _resolve(
    candidate: dict, mapping: dict[int, int], master_by_index: dict[int, dict]
) -> tuple[int, int, float, float]:
    problems = []
    resolved = {}
    for side, key in (("起點", "cue_start"), ("終點", "cue_end")):
        old = candidate.get(key)
        if old is None:
            problems.append(f"{side}缺 {key}")
            continue
        new = mapping.get(int(old))
        if new is None:
            problems.append(f"{side} cue {old} 在成品裡被剪掉了")
            continue
        resolved[key] = new
    if problems:
        raise MigrationError(
            f"{candidate.get('id')} 無法重新錨定：{'；'.join(problems)}。"
            "請回去決定要改邊界還是放棄這一段——這裡不猜。"
        )
    start_cue = master_by_index[resolved["cue_start"]]
    end_cue = master_by_index[resolved["cue_end"]]
    return (
        resolved["cue_start"],
        resolved["cue_end"],
        round(start_cue["start"], 3),
        round(end_cue["end"], 3),
    )


def migrate(episode_dir: Path, *, dry_run: bool = False) -> dict:
    try:
        master = EditorialMasterRequest(episode_dir, expected_episode_id=episode_dir.name).open()
    except EditorialMasterContractError as error:
        raise MigrationError(f"Editorial Master 驗證失敗：{error}") from error
    lineage = master.identity()

    hdir = episode_dir / HIGHLIGHTS
    candidates_path = hdir / "candidates.json"
    winners_path = hdir / "winners.json"
    candidates_doc = json.loads(candidates_path.read_text(encoding="utf-8"))
    winners_doc = json.loads(winners_path.read_text(encoding="utf-8"))

    if candidates_doc.get("editorial_master_lineage") == lineage:
        raise MigrationError("candidates.json 已經錨在這一版 Editorial Master 上了")

    source_srt = Path(candidates_doc["source_srt"])
    if not source_srt.is_file():
        raise MigrationError(f"找不到開採用的 SRT：{source_srt}")
    actual = _sha256(source_srt)
    if actual != candidates_doc.get("source_srt_sha256"):
        raise MigrationError(
            "開採用的 SRT 內容與 candidates.json 記錄的雜湊不符——不能在漂移的來源上遷移"
        )

    source_cues = _parse_srt(source_srt)
    master_cues = _parse_srt(Path(master.srt_path))
    mapping = build_index_map(source_cues, master_cues)
    master_by_index = {cue["index"]: cue for cue in master_cues}

    rows = []
    for candidate in candidates_doc["candidates"]:
        cue_start, cue_end, t_start, t_end = _resolve(candidate, mapping, master_by_index)
        rows.append(
            {
                "id": candidate.get("id"),
                "old_cues": [candidate.get("cue_start"), candidate.get("cue_end")],
                "master_cues": [cue_start, cue_end],
                "old_range": [candidate.get("t_start"), candidate.get("t_end")],
                "master_range": [t_start, t_end],
            }
        )
        candidate["cue_start"], candidate["cue_end"] = cue_start, cue_end
        candidate["t_start"], candidate["t_end"] = t_start, t_end

    candidates_doc["editorial_master_lineage"] = lineage
    winners_doc["editorial_master_lineage"] = lineage

    receipt = {
        "contract": CONTRACT,
        "episode_id": episode_dir.name,
        "editorial_master_lineage": lineage,
        "candidate_count": len(rows),
        "source_srt": str(source_srt),
        "source_srt_sha256": actual,
        "source_candidates_sha256": _sha256(candidates_path),
        "source_winners_sha256": _sha256(winners_path),
        "cue_counts": {"source": len(source_cues), "master": len(master_cues)},
        "rows": rows,
    }
    if dry_run:
        return receipt

    backup = hdir / "migration" / f"pre-editorial-master-{lineage['content_hash']}"
    if backup.exists():
        raise MigrationError(f"這一版的遷移備份已存在，不重複執行：{backup}")
    backup.mkdir(parents=True)
    backup.joinpath("candidates.json").write_bytes(candidates_path.read_bytes())
    backup.joinpath("winners.json").write_bytes(winners_path.read_bytes())

    candidates_path.write_text(
        json.dumps(candidates_doc, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    winners_path.write_text(json.dumps(winners_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    receipt["migrated_candidates_sha256"] = _sha256(candidates_path)
    receipt["migrated_winners_sha256"] = _sha256(winners_path)
    backup.joinpath("migration-receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("episode", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="只算不寫檔")
    args = parser.parse_args(argv)

    receipt = migrate(args.episode.resolve(), dry_run=args.dry_run)
    moved = [row for row in receipt["rows"] if row["old_cues"] != row["master_cues"]]
    print(
        f"候選 {receipt['candidate_count']} 段；cue 數 "
        f"{receipt['cue_counts']['source']} → {receipt['cue_counts']['master']}"
    )
    print(f"重新錨定後編號有變的：{len(moved)} 段")
    for row in receipt["rows"]:
        print(
            f"  {row['id']:>4s}  cue {row['old_cues'][0]}–{row['old_cues'][1]}"
            f" → {row['master_cues'][0]}–{row['master_cues'][1]}"
            f"   {row['old_range'][0]:.1f}s → {row['master_range'][0]:.1f}s"
        )
    if args.dry_run:
        print("\n--dry-run：沒有寫任何檔案")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
