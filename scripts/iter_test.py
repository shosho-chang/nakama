"""WhisperX algorithm 改進迭代測試 runner。

用法：
    python scripts/iter_test.py <iter_num> [--note "描述本輪改了什麼"]

行為：
    1. 跑 WhisperX bare ASR 在 tests/files/20260415.wav
    2. 輸出 SRT 到 tests/files/out/whisperx-iter{N}/20260415.srt
    3. 產 metrics report 到 tests/files/out/whisperx-iter{N}/iter{N}.report.md
       含：cue 分布、詞邊界 cut、within-segment 重複、關鍵字命中、跟上一版 / MemoAI 比較

設計：
    - 不改 transcriber.py（演算法改動由呼叫方 sub-agent / 我做，本 script 只跑 + 量）
    - 每輪改動透過 git diff shared/transcriber.py 自動抓進報告
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
PYTHON310 = r"C:\Users\Shosho\AppData\Local\Programs\Python\Python310\python.exe"
TEST_AUDIO = ROOT / "tests" / "files" / "20260415.wav"
MEMO_BASELINE = ROOT / "tests" / "files" / "20260415-memo.srt"
WHISPERX_V1 = ROOT / "tests" / "files" / "out" / "whisperx" / "20260415.srt"
WHISPERX_V2 = ROOT / "tests" / "files" / "out" / "whisperx-v2" / "20260415.srt"

# 7 點 acceptance set（從 ADR-013 + round-2 報告）
ACCEPTANCE_KEYWORDS = [
    ("數位遊牧", "podcast 主題詞"),
    ("心酸血淚", "情感字眼，心 vs 辛"),
    ("Paul", "code-switch 英文人名"),
    ("Traveling Village", "英文社群名"),
    ("Hell Yes", "英文短語"),
    ("花蓮", "在地名詞"),
    ("本尊", "口語表達"),
]

# within-segment 重複 bug 偵測（adjacent identical 2-4 chars）
REPETITION_PATTERNS = [
    ("花蓮花蓮", "花蓮重複"),
    ("本本尊", "本尊重複（FunASR-style）"),
    ("數位數位", "數位重複"),
]


def srt_text(path: Path) -> str:
    """從 SRT 提取所有字幕文字（concat 成單一字串）。"""
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    parts = []
    for block in text.strip().split("\n\n"):
        lines = block.split("\n")
        if len(lines) >= 3:
            parts.append(lines[2])
    return "".join(parts)


def srt_cues(path: Path) -> list[tuple[float, float, str]]:
    """讀 SRT 為 (start_sec, end_sec, text) tuple list。"""
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    out = []
    for block in text.strip().split("\n\n"):
        lines = block.split("\n")
        if len(lines) < 3:
            continue
        m = re.match(r"(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> (\d{2}):(\d{2}):(\d{2}),(\d{3})", lines[1])
        if not m:
            continue
        h1, m1, s1, ms1, h2, m2, s2, ms2 = map(int, m.groups())
        start = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000
        end = h2 * 3600 + m2 * 60 + s2 + ms2 / 1000
        out.append((start, end, "\n".join(lines[2:])))
    return out


def cue_length_dist(cues: list[tuple[float, float, str]]) -> dict:
    lens = [len(c[2]) for c in cues]
    if not lens:
        return {"n": 0}
    return {
        "n": len(lens),
        "avg": sum(lens) / len(lens),
        "min": min(lens),
        "max": max(lens),
        "exact_max": sum(1 for x in lens if x == 20),
        "le_10": sum(1 for x in lens if x <= 10),
        "le_15": sum(1 for x in lens if x <= 15),
        "ge_18": sum(1 for x in lens if x >= 18),
    }


# 常見 bigram / trigram 不該被切到 cue 邊界
COMMON_BIGRAMS = {
    "然後", "因為", "所以", "但是", "可是", "如果", "不過", "其實",
    "就是", "只是", "也是", "還是", "或是", "以及", "而且", "並且",
    "可能", "應該", "可以", "需要", "必須", "已經", "正在", "剛剛",
    "馬上", "立刻", "突然", "終於", "永遠", "一直", "覺得", "想要",
    "希望", "知道", "了解", "理解", "明白", "記得", "忘記", "看到",
    "這個", "那個", "這樣", "那樣", "這些", "那些", "什麼", "怎麼",
    "為什麼", "因此", "於是", "或者", "雖然", "困難", "簡單", "辛苦",
    "大家", "我們", "他們", "你們", "自己", "別人", "朋友", "家人",
}
COMMON_TRIGRAMS = {"那時候", "這時候", "這個人", "那個人", "為什麼", "怎麼樣"}


def boundary_cuts(cues: list[tuple[float, float, str]]) -> list[tuple[int, str]]:
    """偵測詞被切到 cue 邊界。返回 (cue_idx, joined_word) list。"""
    out = []
    for i in range(len(cues) - 1):
        end_text = cues[i][2].strip()
        start_text = cues[i + 1][2].strip()
        # 末 1 字 + 首 1 字 = bigram?
        if len(end_text) >= 1 and len(start_text) >= 1:
            bg = end_text[-1] + start_text[0]
            if bg in COMMON_BIGRAMS:
                out.append((i, bg))
                continue
        # 末 1 字 + 首 2 字 = trigram?
        if len(end_text) >= 1 and len(start_text) >= 2:
            tg = end_text[-1] + start_text[:2]
            if tg in COMMON_TRIGRAMS:
                out.append((i, tg))
                continue
        # 末 2 字 + 首 1 字 = trigram?
        if len(end_text) >= 2 and len(start_text) >= 1:
            tg = end_text[-2:] + start_text[0]
            if tg in COMMON_TRIGRAMS:
                out.append((i, tg))
                continue
    return out


def hallucinations(plain: str) -> dict:
    """偵測已知 hallucination 模式。"""
    out = {}
    for pat, desc in [
        ("主持人 張修修", "PR #274 prompt-leak"),
        ("主持人張修修", "prompt-leak no space"),
        ("不正常人類研究所", "show name leak"),
    ]:
        n = plain.count(pat)
        if n:
            out[pat] = {"count": n, "desc": desc}
    return out


def repetitions(plain: str) -> dict:
    out = {}
    for pat, desc in REPETITION_PATTERNS:
        n = plain.count(pat)
        if n:
            out[pat] = {"count": n, "desc": desc}
    return out


def keyword_hits(plain: str) -> dict:
    out = {}
    for kw, desc in ACCEPTANCE_KEYWORDS:
        n = plain.count(kw)
        out[kw] = {"count": n, "desc": desc, "ok": n > 0}
    return out


def git_diff_transcriber() -> str:
    try:
        result = subprocess.run(
            ["git", "diff", "shared/transcriber.py"],
            capture_output=True, text=True, cwd=ROOT, encoding="utf-8",
        )
        return result.stdout.strip()
    except Exception as e:
        return f"(git diff failed: {e})"


def run_whisperx(out_dir: Path) -> tuple[Path, float]:
    """跑 WhisperX bare ASR，回傳 (srt_path, elapsed_sec)。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    proc = subprocess.run(
        [
            PYTHON310,
            str(ROOT / "scripts" / "run_transcribe.py"),
            str(TEST_AUDIO),
            "--output-dir", str(out_dir),
            "--no-auphonic", "--no-llm-correction", "--no-arbitration",
        ],
        capture_output=True, text=True, encoding="utf-8", cwd=ROOT,
    )
    elapsed = time.time() - started
    if proc.returncode != 0:
        print("=== STDERR ===")
        print(proc.stderr)
        raise RuntimeError(f"run_transcribe.py exited {proc.returncode}")
    return out_dir / f"{TEST_AUDIO.stem}.srt", elapsed


def render_report(iter_num: int, note: str, srt_path: Path, elapsed: float, diff: str) -> str:
    cues = srt_cues(srt_path)
    plain = srt_text(srt_path)
    dist = cue_length_dist(cues)
    bcuts = boundary_cuts(cues)
    halluc = hallucinations(plain)
    reps = repetitions(plain)
    kw = keyword_hits(plain)

    # 對比基準
    v1_cues = srt_cues(WHISPERX_V1)
    v2_cues = srt_cues(WHISPERX_V2)
    memo_cues = srt_cues(MEMO_BASELINE)

    def short(cues):
        d = cue_length_dist(cues)
        if d.get("n", 0) == 0:
            return "—"
        return f"n={d['n']} avg={d['avg']:.1f} max={d['max']} ge18={d['ge_18']}"

    lines = []
    lines.append(f"# Iter {iter_num} 報告")
    lines.append("")
    if note:
        lines.append(f"**Note**: {note}")
        lines.append("")
    lines.append(f"- SRT: `{srt_path.relative_to(ROOT)}`")
    lines.append(f"- Wall clock: {elapsed:.1f}s ({elapsed / 60:.2f} min)")
    lines.append(f"- Total cues: {dist.get('n', 0)}")
    lines.append("")

    lines.append("## Cue 長度分布")
    lines.append("")
    lines.append("| 版本 | n | avg 字 | max | exact 20 | ≤10 | ≥18 |")
    lines.append("|---|---|---|---|---|---|---|")
    for name, c in [(f"iter{iter_num}", cues), ("v2 (PR#274)", v2_cues), ("v1 (PR#271)", v1_cues), ("MemoAI", memo_cues)]:
        d = cue_length_dist(c)
        if d.get("n", 0) == 0:
            lines.append(f"| {name} | — | — | — | — | — | — |")
        else:
            lines.append(f"| {name} | {d['n']} | {d['avg']:.1f} | {d['max']} | {d['exact_max']} | {d['le_10']} | {d['ge_18']} |")
    lines.append("")

    lines.append("## Hallucination 檢查")
    lines.append("")
    if not halluc:
        lines.append("✅ 無已知 hallucination pattern")
    else:
        for pat, info in halluc.items():
            lines.append(f"❌ `{pat}` × {info['count']} ({info['desc']})")
    lines.append("")

    lines.append("## Within-segment 重複")
    lines.append("")
    if not reps:
        lines.append("✅ 無已知重複 pattern")
    else:
        for pat, info in reps.items():
            lines.append(f"⚠️ `{pat}` × {info['count']} ({info['desc']})")
    lines.append("")

    lines.append("## 詞邊界切到 cue（bigram / trigram）")
    lines.append("")
    lines.append(f"找到 {len(bcuts)} 處：")
    lines.append("")
    for i, word in bcuts[:15]:
        c1 = cues[i][2].strip()
        c2 = cues[i + 1][2].strip()
        lines.append(f"- cue {i + 1}/{i + 2}「{word}」: `...{c1[-8:]} | {c2[:8]}...`")
    if len(bcuts) > 15:
        lines.append(f"- ... 還有 {len(bcuts) - 15} 處")
    lines.append("")

    lines.append("## 7 點 acceptance set")
    lines.append("")
    lines.append("| keyword | hit? | count | 說明 |")
    lines.append("|---|---|---|---|")
    for k, info in kw.items():
        mark = "✅" if info["ok"] else "❌"
        lines.append(f"| {k} | {mark} | {info['count']} | {info['desc']} |")
    lines.append("")

    lines.append("## 對比")
    lines.append("")
    lines.append(f"- iter{iter_num}: {short(cues)}")
    lines.append(f"- v2 (PR #274): {short(v2_cues)}")
    lines.append(f"- v1 (PR #271): {short(v1_cues)}")
    lines.append(f"- MemoAI baseline: {short(memo_cues)}")
    lines.append("")

    if diff:
        lines.append("## Algorithm change diff")
        lines.append("")
        lines.append("```diff")
        # 限制 diff 長度避免 report 過大
        if len(diff) > 8000:
            lines.append(diff[:8000])
            lines.append("... (truncated)")
        else:
            lines.append(diff)
        lines.append("```")

    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("iter_num", type=int)
    p.add_argument("--note", default="", help="本輪改動描述")
    p.add_argument("--skip-run", action="store_true", help="跳過 WhisperX 執行（重生報告用）")
    args = p.parse_args()

    out_dir = ROOT / "tests" / "files" / "out" / f"whisperx-iter{args.iter_num}"
    out_dir.mkdir(parents=True, exist_ok=True)
    srt_path = out_dir / f"{TEST_AUDIO.stem}.srt"
    elapsed = 0.0

    if not args.skip_run:
        print(f"[iter{args.iter_num}] 跑 WhisperX bare ASR …")
        srt_path, elapsed = run_whisperx(out_dir)
        print(f"[iter{args.iter_num}] 完成 ({elapsed:.1f}s) → {srt_path}")

    diff = git_diff_transcriber()
    report = render_report(args.iter_num, args.note, srt_path, elapsed, diff)

    report_path = out_dir / f"iter{args.iter_num}.report.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"[iter{args.iter_num}] 報告 → {report_path}")
    print()
    print(report[: report.find("## Algorithm change diff")] if "## Algorithm change diff" in report else report)


if __name__ == "__main__":
    main()
