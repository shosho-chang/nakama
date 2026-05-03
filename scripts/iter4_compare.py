"""讀 iter3 / iter4 / Memo 三份 SRT，產出量化 + 質性對比 markdown。

輸出：tests/files/out/whisperx-iter4/iter4-vs-iter3-vs-memo.md
"""

# ruff: noqa: E402  # sys.stdout.reconfigure must run before non-stdlib imports (Windows UTF-8)

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
SRTS = {
    "iter3": ROOT / "tests" / "files" / "out" / "whisperx-iter3" / "20260415.srt",
    "iter4": ROOT / "tests" / "files" / "out" / "whisperx-iter4" / "20260415.srt",
    "iter4.1": ROOT / "tests" / "files" / "out" / "whisperx-iter4_1" / "20260415.srt",
    "iter4.1-refined": ROOT
    / "tests"
    / "files"
    / "out"
    / "whisperx-iter4_1"
    / "20260415.result.refined.srt",
    "iter4.2": ROOT / "tests" / "files" / "out" / "whisperx-iter4_2" / "20260415.srt",
    "Memo": ROOT / "tests" / "files" / "20260415-memo.srt",
}
OUT = ROOT / "tests" / "files" / "out" / "whisperx-iter4_2" / "compare-all.md"

ACCEPTANCE = [
    ("數位遊牧", "podcast 主題詞"),
    ("心酸血淚", "情感字眼，心 vs 辛"),
    ("Paul", "code-switch 英文人名"),
    ("Traveling Village", "英文社群名"),
    ("Hell Yes", "英文短語"),
    ("花蓮", "在地名詞"),
    ("本尊", "口語表達"),
]

ASR_BUGS = [
    ("好深羨慕", "iter3 錯成「好生羨慕」"),
    ("哥大", "iter3 部分錯成「格大」"),
    ("格大", "iter3 ASR 錯誤"),
    ("好生羨慕", "iter3 ASR 錯誤"),
    ("哥哥大", "ASR 連寫 bug"),
]

BACKCHANNELS = [
    "可以齁",
    "可以喔",
    "對對對",
    "對對",
    "好",
    "嗯哼",
    "嗯",
    "哈哈",
    "謝謝",
]


def parse_srt(path: Path) -> list[tuple[float, float, str]]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    out = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.split("\n")
        if len(lines) < 3:
            continue
        m = re.match(
            r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3}) --> (\d{2}):(\d{2}):(\d{2})[,.](\d{3})",
            lines[1],
        )
        if not m:
            continue
        s = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) + int(m[4]) / 1000
        e = int(m[5]) * 3600 + int(m[6]) * 60 + int(m[7]) + int(m[8]) / 1000
        text_line = " ".join(lines[2:]).strip()
        out.append((s, e, text_line))
    return out


def stats(cues: list[tuple[float, float, str]]) -> dict:
    if not cues:
        return {}
    durs = [e - s for s, e, _ in cues]
    chars = [len(t) for _, _, t in cues]
    starts = [s for s, _, _ in cues]
    ends = [e for _, e, _ in cues]
    gaps = [starts[i] - ends[i - 1] for i in range(1, len(cues))]
    return {
        "cues": len(cues),
        "first_start": starts[0],
        "last_end": ends[-1],
        "duration_avg": sum(durs) / len(durs),
        "duration_min": min(durs),
        "duration_max": max(durs),
        "duration_lt_05": sum(1 for d in durs if d < 0.5),
        "duration_lt_02": sum(1 for d in durs if d < 0.2),
        "chars_avg": sum(chars) / len(chars),
        "chars_max": max(chars),
        "chars_le10": sum(1 for c in chars if c <= 10),
        "chars_ge18": sum(1 for c in chars if c >= 18),
        "gap_zero": sum(1 for g in gaps if abs(g) < 0.001),
        "gap_positive": sum(1 for g in gaps if g > 0.05),
        "gap_avg": sum(gaps) / len(gaps) if gaps else 0,
    }


def keyword_hits(cues: list[tuple[float, float, str]], kw: str) -> int:
    return sum(t.count(kw) for _, _, t in cues)


def short_cue_cases(cues: list[tuple[float, float, str]], min_dur=0.5) -> list:
    """找 duration < min_dur 的 cue，artifact suspects。"""
    out = []
    for s, e, t in cues:
        if e - s < min_dur:
            out.append((s, e, e - s, t))
    return out


def backchannel_count(cues: list[tuple[float, float, str]]) -> int:
    """獨立 cue 是 backchannel 詞的數量。"""
    n = 0
    for _, _, t in cues:
        t_clean = t.strip()
        if t_clean in BACKCHANNELS or len(t_clean) <= 4 and any(b in t_clean for b in BACKCHANNELS):
            n += 1
    return n


def head_cues(cues, n=10) -> str:
    out = []
    for i, (s, e, t) in enumerate(cues[:n], 1):
        out.append(f"{i}. {fmt_ts(s)} → {fmt_ts(e)} | {t}")
    return "\n".join(out)


def tail_cues(cues, n=10) -> str:
    out = []
    for i, (s, e, t) in enumerate(cues[-n:], len(cues) - n + 1):
        out.append(f"{i}. {fmt_ts(s)} → {fmt_ts(e)} | {t}")
    return "\n".join(out)


def fmt_ts(s: float) -> str:
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = s % 60
    return f"{h:02d}:{m:02d}:{sec:06.3f}"


def main() -> None:
    cues = {name: parse_srt(p) for name, p in SRTS.items()}
    available = [n for n, c in cues.items() if c]
    print(f"Available SRTs: {', '.join(available)}")
    if "iter4" not in available and "iter4.1" not in available:
        print("ERROR: need at least iter4 or iter4.1")
        return

    s = {name: stats(c) for name, c in cues.items() if c}

    md = []
    md.append("# Transcribe Algorithm Comparison Report\n")
    md.append(
        "iter3 (WhisperX force_break) → iter4 (stable-ts) → "
        "iter4.1 (vad 0.15 + 拆 merge) → iter4.1-refined (refine pass) vs Memo baseline\n"
    )
    md.append(f"SRTs included: {', '.join(available)}\n")

    # === 量化指標 ===
    md.append("## 1. 量化指標\n")
    headers = ["指標"] + available
    md.append("| " + " | ".join(headers) + " |")
    md.append("|" + "|".join(["---"] * len(headers)) + "|")

    def row(label: str, key: str, fmt: str = "{}"):
        cells = [label]
        for n in available:
            v = s.get(n, {}).get(key, "-")
            cells.append(fmt.format(v) if v != "-" else "-")
        md.append("| " + " | ".join(cells) + " |")

    row("Cue 數", "cues")
    row("總時長 (s)", "last_end", "{:.1f}")
    row("第一 cue 起點 (s)", "first_start", "{:.3f}")
    row("Cue duration avg (s)", "duration_avg", "{:.2f}")
    row("Cue duration min (s)", "duration_min", "{:.2f}")
    row("<0.5s artifact cues", "duration_lt_05")
    row("<0.2s artifact cues", "duration_lt_02")
    row("Chars avg", "chars_avg", "{:.1f}")
    row("Chars max", "chars_max")
    row("≤10 字 cue（細粒度）", "chars_le10")
    row("≥18 字 cue（過長）", "chars_ge18")

    # gap=0 row
    cells = ["**gap=0 (連續無停頓 %)**"]
    for n in available:
        cells.append(f"**{pct(s.get(n, {}))}**")
    md.append("| " + " | ".join(cells) + " |")

    cells = ["gap>0.05s (有自然停頓)"]
    for n in available:
        cells.append(str(s.get(n, {}).get("gap_positive", "-")))
    md.append("| " + " | ".join(cells) + " |")
    md.append("")

    # === Acceptance set ===
    md.append("## 2. 7 點 acceptance set\n")
    headers = ["Keyword"] + available
    md.append("| " + " | ".join(headers) + " |")
    md.append("|" + "|".join(["---"] * len(headers)) + "|")
    for kw, desc in ACCEPTANCE:
        cells = [f"{kw} ({desc})"]
        for n in available:
            h = keyword_hits(cues[n], kw)
            cells.append(f"{'✅' if h > 0 else '❌'} {h}")
        md.append("| " + " | ".join(cells) + " |")
    md.append("")

    # === ASR Bug 監控 ===
    md.append("## 3. ASR 錯誤詞監控\n")
    md.append(
        "注：「好生」「好深」均為合法用法（修修 2026-05-01 指正），不算錯字。"
        "下表只追蹤明確的 ASR 錯誤。\n"
    )
    headers = ["詞", "屬性"] + available
    md.append("| " + " | ".join(headers) + " |")
    md.append("|" + "|".join(["---"] * len(headers)) + "|")
    for kw, desc in ASR_BUGS:
        cells = [kw, desc]
        for n in available:
            cells.append(str(keyword_hits(cues[n], kw)))
        md.append("| " + " | ".join(cells) + " |")
    md.append("")

    # === Backchannel 抓取 ===
    md.append("## 4. Backchannel 獨立 cue 抓取\n")
    for n in available:
        bc = backchannel_count(cues[n])
        md.append(f"- {n}：{bc} 個獨立 backchannel cue")
    md.append("")

    # === 開頭末尾覆蓋 ===
    md.append("## 5. 開頭/末尾時間覆蓋\n")
    for n in available:
        ss = s.get(n, {})
        md.append(f"- {n}：{ss.get('first_start', 0):.3f}s → {ss.get('last_end', 0):.3f}s")
    md.append("")

    # === 取樣對照 ===
    md.append("## 6. 取樣對照 — 開頭前 10 cue\n")
    for n in available:
        md.append(f"**{n}:**\n```")
        md.append(head_cues(cues[n]))
        md.append("```\n")

    md.append("## 7. 取樣對照 — 末尾最後 10 cue\n")
    for n in available:
        md.append(f"**{n}:**\n```")
        md.append(tail_cues(cues[n]))
        md.append("```\n")

    # === 短 cue artifact ===
    md.append("## 8. <0.5s 短 cue 數量\n")
    for n in available:
        short = short_cue_cases(cues[n])
        md.append(f"- {n}：{len(short)} 個")
    md.append("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(md), encoding="utf-8")
    print(f"Report: {OUT}")
    for n in available:
        print(f"  {n}: {len(cues[n])} cues")


def pct(s: dict) -> str:
    if not s or "cues" not in s:
        return "-"
    z = s.get("gap_zero", 0)
    n = s["cues"] - 1
    if n <= 0:
        return "-"
    return f"{z}/{n} ({z / n * 100:.1f}%)"


if __name__ == "__main__":
    main()
