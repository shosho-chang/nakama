"""他說「定稿了」之後，我第一件事跑這支。

**這不是按鈕**（修修 2026-09-10：「現在不要有按鈕可以按了，我要直接跟你講」）。
觸發是他那句話；這支只是把「這一集現在到哪裡、接下來該平行派什麼」一次算清楚，
免得我靠印象派工、或把已經做完的再做一次。

唯讀：只讀檔與 DB，不寫任何東西、不碰 Resolve。

    python scripts/episode_fanout_status.py "G:/Footages/20260901 蘇予昕"
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from shared.config import get_db_path  # noqa: E402
from shared.highlight_shortlist import winners_path  # noqa: E402

OK, PENDING, BAD = "✅", "⬜", "❌"


def _load(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _master(episode_dir: Path) -> tuple[bool, str]:
    try:
        from agents.brook.script_video.editorial_master import EditorialMasterRequest

        identity = (
            EditorialMasterRequest(episode_dir, expected_episode_id=episode_dir.name)
            .open()
            .identity()
        )
        return True, identity["content_hash"][:12]
    except Exception as error:  # noqa: BLE001 — 任何開不起來的理由都要原文回報
        return False, f"{type(error).__name__}: {error}"


def _candidates(hl: Path) -> dict[str, int]:
    doc = _load(hl / "candidates.json") or {}
    counts: dict[str, int] = {}
    for row in doc.get("candidates") or []:
        if isinstance(row, dict):
            counts[str(row.get("format"))] = counts.get(str(row.get("format")), 0) + 1
    return counts


def _panel(hl: Path, fmt: str) -> tuple[bool, str]:
    """該格式的盲審檔齊不齊、綁得對不對。"""
    from shared.highlight_shortlist import HighlightDataError, collect

    try:
        rows = collect(hl, fmt)
    except HighlightDataError as error:
        return False, str(error)
    return bool(rows), f"{len(rows)} 支候選排好了" if rows else "沒有候選"


def _winners(hl: Path, fmt: str) -> list[str]:
    doc = _load(winners_path(hl, fmt)) or {}
    return [w["id"] for w in doc.get("winners") or [] if isinstance(w, dict) and w.get("id")]


def _releases(episode_id: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    try:
        con = sqlite3.connect(get_db_path())
        rows = con.execute(
            "select r.cut_id, t.platform, t.status from releases r "
            "left join release_targets t on t.release_id = r.id where r.episode = ?",
            (episode_id,),
        ).fetchall()
        con.close()
    except sqlite3.Error as error:
        return {"__error__": [str(error)]}
    for cut_id, platform, status in rows:
        out.setdefault(cut_id, []).append(f"{platform}:{status}")
    return out


def report(episode_dir: Path) -> int:
    hl = episode_dir / "highlights"
    episode_id = episode_dir.name
    print(f"# {episode_id}\n")

    ok, detail = _master(episode_dir)
    print(f"{OK if ok else BAD} Editorial Master　{detail}")
    if not ok:
        print("\n定稿不成立——先修這個，不要往下派工（錯的 master 派出去是六支白工）。")
        return 1

    counts = _candidates(hl)
    if not counts:
        print(f"{PENDING} 候選開採　還沒跑")
        print("\n下一步【平行 ×3】miner：story / punch / value（互相隔離）")
        print("        然後 [序列] run_highlight_cut.py --merge-miners")
        return 0
    print(f"{OK} 候選開採　" + "、".join(f"{k} {v} 支" for k, v in sorted(counts.items())))

    dispatch: list[str] = []
    for fmt in ("long", "short"):
        if fmt not in counts:
            continue
        good, why = _panel(hl, fmt)
        won = _winners(hl, fmt)
        # 已經挑完的格式，盲審綁定過期是**歷史**不是待辦：段落早就做完甚至上架了，
        # 重跑 panel 只會把已經付掉的成本再付一次。標成資訊，不進派工清單。
        mark = OK if good else (f"{OK}·" if won else BAD)
        note = why if good else ("（綁定已過期，但已挑完，不用重跑）" if won else why)
        print(f"{mark} 盲審 {fmt:5s}　{note}")
        if not good and not won:
            dispatch.append(
                f"【平行 ×5】{fmt} 盲審：阿哲／凱文／淑芬／brand lens"
                + ("／Renee lens" if fmt == "long" else "（Renee 不需要）")
            )
            dispatch.append(
                f"        digest：run_cut_shortlist.py <ep> --format {fmt} --print-digest"
            )
        elif won:
            print(f"{OK} 選段 {fmt:5s}　已挑：{'、'.join(won)}")
        else:
            print(f"{PENDING} 選段 {fmt:5s}　候選表已出，等他挑")
            dispatch.append(f"■ 停點 1：把 {fmt} 候選表交給他挑（不要自動 top 3）")

    pkg = _load(episode_dir / "packaging" / "packages.json") or {}
    by_cut = {c["cut_id"]: c for c in pkg.get("cuts") or [] if isinstance(c, dict)}
    rel = _releases(episode_id)

    # 格式從 winners 來，不從 packages.json 來——還沒進 packaging 的 cut 在那邊查不到，
    # 會變成「未知格式」而算錯它該有幾條標題。
    all_winners = [(c, "long") for c in _winners(hl, "long")]
    all_winners += [(c, "short") for c in _winners(hl, "short")]
    if all_winners:
        print("\n## 每一支")
        for cut_id, fmt in all_winners:
            cut = by_cut.get(cut_id) or {}
            titles = len(cut.get("titles") or [])
            packages = len(cut.get("packages") or [])
            exported = (hl / "exports" / f"{cut_id}.mp4").is_file()
            targets = rel.get(cut_id) or []
            need_cover = fmt == "long"  # 短片不做封面（修修 2026-09-10 裁定）
            # 長片 Top 5、短片 1 條（title-brainstorm 的長短分流，D4/D13）。
            # `fmt` 在 packages.json 沒有這支時是 None——那就是「還沒進 packaging」。
            want_titles = 5 if fmt == "long" else 1
            cover = "封面 —"
            if need_cover:
                cover = "封面 ✅" if packages == 3 else f"封面 {packages}/3"
            bits = [
                "標題 ✅" if titles == want_titles else f"標題 {titles}/{want_titles}",
                cover,
                "匯出 ✅" if exported else "匯出 ⬜",
                ("／".join(targets) if targets else "未登錄"),
            ]
            print(f"  {cut_id:14s} {'  '.join(bits)}")
            if not exported:
                dispatch.append(f"[序列] publish_prep.py <ep> --cut {cut_id}（Resolve，單執行緒）")
            if titles < want_titles:
                dispatch.append(
                    f"【平行】title-brainstorm {cut_id}"
                    f"（{fmt or '未知格式'}，{want_titles} 條；回 payload，序列 emit）"
                )
            if need_cover and titles == want_titles and packages < 3:
                dispatch.append(f"[序列] thumbnail-brainstorm {cut_id}（封面 3 個包）")

    carousel = _load(episode_dir / "ig-carousel" / "current.json")
    if carousel:
        print(f"\n{OK} carousel　{carousel.get('revision')}")
    else:
        print(f"\n{PENDING} carousel　還沒做")
        dispatch.append("【平行】carousel 文案（skills/ig-cards，三個盲審 lens 到收斂）")

    print("\n## 下一步")
    if dispatch:
        for line in dict.fromkeys(dispatch):
            print(f"  {line}")
    else:
        print("  這一集沒有待派的工作了——剩下的是他在 gate 上挑，以及上傳前的明確核准。")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="「定稿了」之後：這一集到哪裡、接下來派什麼")
    ap.add_argument("episode", help="episode 資料夾")
    args = ap.parse_args(argv)
    episode_dir = Path(args.episode)
    if not episode_dir.is_dir():
        raise SystemExit(f"{episode_dir} 不存在")
    return report(episode_dir)


if __name__ == "__main__":
    raise SystemExit(main())
