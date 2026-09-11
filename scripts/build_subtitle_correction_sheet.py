"""把 release 的未決項做成修修能在 Obsidian 勾選填寫的勘誤單。

修修 2026-09-11：「能不能做成一個可以讓我有格子可以填寫的格式？這樣你也比較好知道
我的回饋在哪裡。」

兩邊都要的東西其實是同一個：**固定的欄位**。他要的是點一下就好、不用打字；我要的是
能用程式讀回來，而不是在一份自由書寫的 markdown 裡猜哪句是他的答案。

所以格式是 Obsidian 原生的 checkbox（`- [ ]` 在 Obsidian 裡可以直接點）加上一行固定
前綴的自由欄位。解析規則只有兩條：

- `### cue <N>` 起一個區塊，區塊裡被勾起來的 `- [x]` 決定採用哪一個選項
- `✍️` 開頭那行冒號之後的文字，是他自己寫的答案（優先於勾選）

留白＝維持原文。**不填不會擋發布**——這份單子是把「機器查不到的」攤開給他看，
不是待辦清單。

    python scripts/build_subtitle_correction_sheet.py "G:/Footages/20260721 呂冠緯"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

ANSWER_PREFIX = "✍️ 我的答案："
KEEP_LABEL = "維持原文"

#: 節目本體之外的 cue 不進單子——它們會跟片頭片尾一起被剪掉。
#: 由呼叫端以毫秒指定；`None` 表示整集都算。
ProgramWindow = tuple[int, int] | None


def _parse_srt(path: Path) -> dict[int, tuple[int, str]]:
    def milliseconds(value: str) -> int:
        hours, minutes, rest = value.split(":")
        seconds, fraction = rest.split(",")
        return ((int(hours) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + int(fraction)

    cues: dict[int, tuple[int, str]] = {}
    text = path.read_text(encoding="utf-8-sig")
    for block in (item for item in text.strip().split("\n\n") if item.strip()):
        lines = block.splitlines()
        if len(lines) < 3:
            continue
        start, _, _ = lines[1].partition(" --> ")
        cues[int(lines[0])] = (milliseconds(start.strip()), " ".join(lines[2:]).strip())
    return cues


def _timecode(value: int) -> str:
    return f"{value // 3600000:02d}:{value // 60000 % 60:02d}:{value // 1000 % 60:02d}"


def _proposals(item: dict) -> list[str]:
    seen: list[str] = []
    for key in ("a_proposals", "b_proposals"):
        for value in item.get(key) or []:
            if isinstance(value, str) and value.strip() and value not in seen:
                seen.append(value)
    return seen


def _block(cue: int, timecode: str, original: str, options: list[tuple[str, str]]) -> list[str]:
    """一個 cue 一個區塊：引言是現況，選項是勾選格，最後一行永遠是自由欄位。"""
    lines = [f"### cue {cue} · `{timecode}`", "", f"> {original}", ""]
    for label, detail in options:
        lines.append(f"- [ ] **{label}**" + (f" — {detail}" if detail else ""))
    lines.append(f"- [ ] {KEEP_LABEL}")
    lines.append(f"- {ANSWER_PREFIX}")
    lines.append("")
    return lines


def build_sheet(
    episode_dir: Path,
    *,
    program_window: ProgramWindow = None,
    clusters: dict[str, list[int]] | None = None,
    verified: dict[int, tuple[str, str]] | None = None,
) -> str:
    release = episode_dir / "subtitle-release/memo-dual-audit-v1/release.srt"
    unresolved = episode_dir / "subtitle-work/memo-dual-audit-v1/unresolved-components.json"
    cues = _parse_srt(release)
    items = json.loads(unresolved.read_text(encoding="utf-8"))["items"]

    def inside(cue: int) -> bool:
        if program_window is None:
            return True
        start, end = program_window
        return start <= cues[cue][0] <= end

    rows = [
        item for item in sorted(items, key=lambda value: value["cue_numbers"][0])
        if inside(item["cue_numbers"][0])
    ]
    clustered = {cue for group in (clusters or {}).values() for cue in group}
    verified = verified or {}

    out: list[str] = [
        "---",
        f'episode: "{episode_dir.name}"',
        "kind: subtitle-correction-sheet",
        "status: 待填",
        "---",
        "",
        f"# {episode_dir.name} — 字幕勘誤單",
        "",
        f"這些句子兩份稽核都看出「不對」，但**機器無法唯一還原**。目前字幕保留原文（不猜）。",
        "",
        "**怎麼填**：點一下方框就好；方框都不對就寫在 " + f"`{ANSWER_PREFIX}` 後面。",
        f"**留白＝維持原文**，不填不擋發布。",
        "",
    ]

    if clusters:
        out += ["---", "", "## A. 一個答案解決多句", ""]
        for question, group in clusters.items():
            present = [cue for cue in group if cue in cues and inside(cue)]
            if not present:
                continue
            out += [f"### {question}", ""]
            out += ["| 時間 | cue | 目前字幕 |", "|---|---|---|"]
            for cue in present:
                start, text = cues[cue]
                out.append(f"| `{_timecode(start)}` | {cue} | {text} |")
            out += ["", f"- {ANSWER_PREFIX}", ""]

    if verified:
        out += ["---", "", "## B. 我查到答案了，請你確認", ""]
        for cue, (proposal, basis) in sorted(verified.items()):
            if cue not in cues or not inside(cue):
                continue
            start, text = cues[cue]
            out += _block(cue, _timecode(start), text, [("採用", proposal)])
            out += [f"<small>依據：{basis}</small>", ""]

    open_rows = [
        item for item in rows
        if item["cue_numbers"][0] not in clustered and item["cue_numbers"][0] not in verified
    ]
    if open_rows:
        out += ["---", "", "## C. 這些只有你知道", ""]
        for item in open_rows:
            cue = item["cue_numbers"][0]
            start, text = cues[cue]
            options = [("採用", proposal) for proposal in _proposals(item)]
            out += _block(cue, _timecode(start), text, options)
            reason = str(item.get("reason") or "").strip()
            if reason:
                out += [f"<small>稽核判定：{reason}</small>", ""]

    out += ["---", "", f"填完把 `status:` 改成 `已填`，跟我說一聲就好。", ""]
    return "\n".join(out)


def read_sheet(path: Path) -> dict:
    """把填好的勘誤單讀回結構化答案。

    只認兩種訊號，其餘一律當成沒填：被勾起來的 `- [x]`，以及 `✍️` 那行冒號之後
    的文字。**自由欄位優先於勾選**——他兩個都動了，代表勾選不夠精確。

    回傳 `{"cues": {cue: 文字}, "keep": [cue...], "free_text": {...}, "clusters": {...}}`。
    cluster 的答案是一個**詞**不是整句，所以另外回傳、由呼叫端逐句換上去再給他確認；
    這裡不自動代換——把一個詞塞進七個句子是語意判斷，不能靜默做掉。
    """
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    cues: dict[int, str] = {}
    keep: list[int] = []
    free_text: dict[int, str] = {}
    clusters: dict[str, str] = {}

    current_cue: int | None = None
    current_cluster: str | None = None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("### cue "):
            try:
                current_cue = int(stripped.removeprefix("### cue ").split("·")[0].strip())
            except ValueError:
                current_cue = None
            current_cluster = None
            continue
        if stripped.startswith("### "):
            current_cue, current_cluster = None, stripped.removeprefix("### ").strip()
            continue
        if stripped.startswith("- [x]") or stripped.startswith("- [X]"):
            body = stripped[5:].strip()
            if current_cue is None:
                continue
            if body.startswith(KEEP_LABEL):
                keep.append(current_cue)
            elif "—" in body:
                cues[current_cue] = body.split("—", 1)[1].strip()
            continue
        if ANSWER_PREFIX in stripped:
            answer = stripped.split(ANSWER_PREFIX, 1)[1].strip()
            if not answer:
                continue
            if current_cue is not None:
                free_text[current_cue] = answer
            elif current_cluster is not None:
                clusters[current_cluster] = answer
    # 自由欄位壓過勾選。
    cues.update(free_text)
    return {
        "cues": cues,
        "keep": [cue for cue in keep if cue not in cues],
        "free_text": free_text,
        "clusters": clusters,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("episode")
    parser.add_argument("--output", required=True, help="寫到哪裡（通常是 vault 的訪談資料夾）")
    parser.add_argument("--program-start-ms", type=int)
    parser.add_argument("--program-end-ms", type=int)
    parser.add_argument("--cluster", action="append", default=[], help="「問題=cue,cue,...」")
    args = parser.parse_args(argv)

    window = None
    if args.program_start_ms is not None and args.program_end_ms is not None:
        window = (args.program_start_ms, args.program_end_ms)
    clusters: dict[str, list[int]] = {}
    for raw in args.cluster:
        question, _, cue_list = raw.partition("=")
        clusters[question] = [int(value) for value in cue_list.split(",") if value.strip()]

    sheet = build_sheet(Path(args.episode), program_window=window, clusters=clusters)
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(sheet, encoding="utf-8")
    print(f"勘誤單 → {destination}（{len(sheet)} 字元）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
