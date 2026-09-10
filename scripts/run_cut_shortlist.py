"""選段 gate：盲審結果彙整成候選表給修修挑，挑完才寫 winners.json。

    python scripts/run_cut_shortlist.py <episode> [--format long]        # 出表
    python scripts/run_cut_shortlist.py <episode> --pick SL4,SL3,SL7     # 寫 winners

修修 2026-08-11 裁決（安吉集 review 後）：**panel 排完不要自動 top-3 進製作**。
panel 是讀逐字稿評分的，評的是素材強度，不是成片吸引力、也不是修修的品味——
安吉集三支做完他才說「其中一兩個主題好像不是特別吸引人」，那時候製作與
packaging 的成本已經付掉了。他自己的比較：「做 5 支挑 3 支」要多付兩支的
製作＋packaging（packaging 100% 線性、是 LLM 用量最大的一塊），而把 HITL
移到**排完之後、製作之前**幾乎零成本——那張表的料在 panel 跑完時就已經齊了。

輸入（highlight-cut Step 1/2 的產物）：
    highlights/candidates.json      — id/format/variant_group/hook/rationale/時長
    highlights/review_<persona>.json — 三位評分 persona（scores[].total）
    highlights/lens_brand.json       — 品牌 lens（severity: veto/caution）
    highlights/lens_renee.json       — 留存／邊界 lens（長片必須完整覆蓋）

**盲審檔可以分格式**：`review_<persona>.<fmt>.json` / `lens_*.<fmt>.json` 存在時優先
使用，並改綁「該格式候選」的 digest。一份 persona 檔服務不了兩種格式——gate 要求
review 的 id 集合與該格式的候選**完全相等**，覆蓋長片的那份對短片來說就是「38 支
全缺」。分格式之後，長片邊界打磨也不會再把短片的盤子打翻（ADR-067 的分家精神）。

輸出（依 --format 分流，不互相覆蓋）：
    highlights/選段候選表[.short].md — 貼給修修的表（群組、中位數、hook、警示）
    highlights/winners.json          — long；短片寫 winners.short.json
                                       只有 --pick 才寫（schema 由本 script 保證）

**同時寫一份長短片合併的報告進 Vault**：
    <VAULT>/AgentOutputs/interviews/<訪談日-來賓>/0N-選段報告.md

理由跟 title-brainstorm 的報告一樣（修修 2026-09-04 裁決）：被砍掉的候選比留下
的更有教育意義，而 `highlights/` 是 footage 磁碟上的工作目錄，下一季開工時沒有
人會去翻它。報告合併長短片，因為挑選的時候是一起看的——哪一段被長片用掉了，
短片就不該再挑同一段。Vault 對不到資料夾時印警告繼續跑，本地那張表才是主產物。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.highlight_shortlist import (
    SCORERS,
    HighlightDataError,
    winners_path,
)
from shared.highlight_shortlist import (
    collect as _collect,
)
from shared.highlight_shortlist import (
    write_winners as _write_winners,
)
from shared.vault_interviews import (
    VaultInterviewError,
    interview_dir,
    next_numbered_name,
)

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

HIGHLIGHTS = "highlights"
NEWLINE = chr(10)


def collect(hl_dir: Path, fmt: str) -> list[dict]:
    """Compatibility wrapper for the shared ranking implementation."""
    try:
        return _collect(hl_dir, fmt)
    except HighlightDataError as exc:
        raise SystemExit(str(exc)) from exc


def render_table(rows: list[dict], fmt: str) -> str:
    out = [
        f"# 選段候選表（{fmt}）— 等修修挑",
        "",
        "panel 評的是**素材強度**，不是成片吸引力，也不是你的品味。挑幾支都可以",
        "（預設 3 支），指定 id 給我，我才寫 winners.json 進製作。",
        "",
        "| 排名 | id | 群組 | 中位數 | 阿哲/凱文/淑芬 | 長度 | 主題 | 品牌 lens |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        s = r["scores"]
        trio = "/".join(str(s.get(w) if s.get(w) is not None else "-") for w in SCORERS)
        flag = {"veto": "⛔ 否決", "caution": "⚠️ 注意"}.get(r["brand_severity"], "")
        rank = str(r["rank"]) if r["rank"] else "（同群組落選）"
        mins = f"{int(r['duration_sec'] // 60)}:{int(r['duration_sec'] % 60):02d}"
        out.append(
            f"| {rank} | **{r['id']}** | {r['group']} | {r['median']:.0f} | {trio} | "
            f"{mins} | {r['title']} | {flag} |"
        )
    out += ["", "## 各支 hook 與品牌 lens 細節", ""]
    for r in rows:
        out.append(f"### {r['id']} — {r['title']}（中位數 {r['median']:.0f}）")
        if r["hook"]:
            out.append(f"- **hook**：{r['hook']}")
        if r["brand_severity"]:
            out.append(f"- **品牌 lens {r['brand_severity']}**：{r['brand_issue']}")
            if r["brand_mitigation"]:
                out.append(f"  - 對策：{r['brand_mitigation']}")
        out.append("")
    return "\n".join(out) + "\n"


def _winner_ids(hl_dir: Path, fmt: str) -> list[str]:
    """已經挑定的 id（沒挑就是空的）。報告要記「當時挑了哪幾支」。"""
    path = winners_path(hl_dir, fmt)
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    winners = payload.get("winners")
    if not isinstance(winners, list):
        return []
    return [
        row["id"] for row in winners if isinstance(row, dict) and isinstance(row.get("id"), str)
    ]


def collect_for_report(hl_dir: Path, fmt: str) -> tuple[list[dict], str]:
    """報告用的收集：綁定過期不是拒印的理由，是要印在報告上的事實。

    gate 拒收過期綁定是對的——動過邊界的段落不該照舊分數送進製作。但報告是
    **唯讀的歷史紀錄**，「因為 hash 動了所以整份不印」正是修修 2026-09-10 講的
    那種過嚴。所以這裡降級重讀一次，並把過期原因原文寫進那一節。
    """
    try:
        return _collect(hl_dir, fmt), ""
    except HighlightDataError as strict_error:
        try:
            rows = _collect(hl_dir, fmt, verify_binding=False)
        except HighlightDataError as exc:
            return [], f"讀不到：{exc}"
        if not rows:
            return [], f"讀不到：{strict_error}"
        return rows, f"panel 綁定已過期（{strict_error}）——分數是**當時那一版候選**的分數"


def render_vault_report(
    episode_id: str,
    hl_dir: Path,
    per_format: dict[str, list[dict]],
    notes: dict[str, str] | None = None,
) -> str:
    """長短片合併的選段報告。落選的候選連分數一起留著，那才是下一季的參考值。"""
    out = [
        f"# 選段報告 — {episode_id}",
        "",
        "panel 評的是**素材強度**（讀逐字稿評分），不是成片吸引力，也不是修修的品味。",
        "這份報告把長片與短片兩張候選表放在一起，因為挑選時本來就要一起看——",
        "一段被長片用掉了，短片就不該再挑同一段。**落選的候選連分數一起留著**：",
        "下一季要參考的是「什麼樣的段落會被打槍」，那只有落選名單答得出來。",
        "",
    ]
    notes = notes or {}
    for fmt in ("long", "short"):
        rows = per_format.get(fmt) or []
        label = "長精華" if fmt == "long" else "短影片"
        out += [f"## {label}（format={fmt}）", ""]
        if notes.get(fmt):
            out += [f"> ⚠️ {notes[fmt]}", ""]
        if not rows:
            out += [f"這一節沒有內容：candidates.json 裡沒有 format={fmt} 的候選。", ""]
            continue
        picked = _winner_ids(hl_dir, fmt)
        out += [
            f"候選 {len(rows)} 支；已挑定：{'、'.join(picked) if picked else '（尚未挑）'}",
            "",
            # render_table 的前四行是它自己的標題與說明，這裡已經有一段了。
            # 標題各降一級，讓細節掛在該格式底下而不是跟它平輩。
            NEWLINE.join(
                ("#" + line if line.startswith("##") else line)
                for line in render_table(rows, fmt).splitlines()[4:]
            ),
            "",
        ]
    return NEWLINE.join(out).rstrip() + NEWLINE


def write_vault_report(episode_dir: Path) -> Path | None:
    """寫進 Vault 的 interview 專案資料夾。對不到就印警告回 None，不擋本地產出。"""
    episode_id = episode_dir.name
    hl_dir = episode_dir / HIGHLIGHTS
    per_format: dict[str, list[dict]] = {}
    notes: dict[str, str] = {}
    for fmt in ("long", "short"):
        per_format[fmt], notes[fmt] = collect_for_report(hl_dir, fmt)
        if notes[fmt]:
            print(f"⚠️ 選段報告 format={fmt}：{notes[fmt]}", file=sys.stderr)
    try:
        target_dir = interview_dir(episode_id)
    except VaultInterviewError as exc:
        print(f"⚠️ 選段報告沒寫進 Vault：{exc}", file=sys.stderr)
        return None
    target = target_dir / next_numbered_name(target_dir, "選段報告")
    target.write_text(render_vault_report(episode_id, hl_dir, per_format, notes), encoding="utf-8")
    return target


def write_winners(hl_dir: Path, rows: list[dict], picks: list[str], fmt: str = "long") -> Path:
    """Compatibility wrapper preserving the CLI's SystemExit error contract."""
    try:
        picked_veto = [
            p for p in picks if any(r["id"] == p and r["brand_severity"] == "veto" for r in rows)
        ]
        if picked_veto:
            print(f"⚠️ 注意：{picked_veto} 是 brand-lens 否決段，仍照你的指定寫入", file=sys.stderr)
        return _write_winners(hl_dir, rows, picks, fmt=fmt)
    except HighlightDataError as exc:
        raise SystemExit(str(exc)) from exc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="選段 gate：候選表 → 修修挑 → winners.json")
    ap.add_argument("episode", help="episode 資料夾")
    ap.add_argument("--format", default="long", choices=("long", "short"))
    ap.add_argument("--pick", help="修修挑定的 id（逗號分隔，順序＝rank）")
    ap.add_argument(
        "--no-vault-report",
        action="store_true",
        help="不要寫 Vault 的選段報告（CI，或手上沒有掛載 Vault 時）",
    )
    args = ap.parse_args(argv)

    hl_dir = Path(args.episode) / HIGHLIGHTS
    rows = collect(hl_dir, args.format)
    if not rows:
        raise SystemExit(f"candidates.json 裡沒有 format={args.format} 的候選")

    if args.pick:
        picks = [x.strip() for x in args.pick.split(",") if x.strip()]
        out = write_winners(hl_dir, rows, picks, args.format)
        print(f"{out.name} 已寫入（{len(picks)} 支）→ {out}")
        return 0

    table = render_table(rows, args.format)
    # 一個檔名餵兩種格式，跑短片的表就會蓋掉長片那張。
    out = hl_dir / ("選段候選表.md" if args.format == "long" else f"選段候選表.{args.format}.md")
    out.write_text(table, encoding="utf-8")
    print(table)
    print(f"→ {out}")

    if not args.no_vault_report:
        # 報告永遠是長短片合併的：另一種格式還沒跑過就是空的，那本身也是資訊。
        report = write_vault_report(Path(args.episode))
        if report is not None:
            print(f"→ {report}")
    print("\n把表貼給修修，等他指定 id 後跑 --pick 才進製作（不要自己選 top 3）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
