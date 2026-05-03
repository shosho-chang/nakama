"""SRT 時間軸精修工具：input audio + (SRT 或 stable-ts JSON) → refined SRT + diff 報告。

設計目的（修修 2026-05-01）：
製作影片時時間軸精準度是強需求。任何 SRT（不限 stable-ts 產出）+ 對應 audio
餵進來，跑 stable-ts 的精修流程拿到 ±100ms 級時間軸，輸出精修版 SRT
+ 每 cue 邊界偏移 diff 報告。引用「品質 > 速度」原則。

兩種 input mode（auto-detect 副檔名）：

(A) JSON mode — input 是 stable-ts result.json
    走 model.refine()：對每個 word 的 timestamp 用「mute audio + monitor token
    probability」二次精修。快、保留原 segment 結構。
    用途：對 iter4 / iter4.1 等已用 stable-ts 跑出的結果做最後精修。

(B) SRT mode — input 是任何 SRT（人類做的、iter3 產的、Memo、YouTube 等）
    走 model.align(audio, full_text)：把 SRT 文字當已知 ground truth，
    跑 forced alignment 拿到全新 word-level timestamp，再依原 SRT 的斷句
    結構重組。慢、但能修任意外部 SRT 的時間軸。
    用途：把任何 SRT 的時間軸校準到本地 audio。

用法：
    python scripts/srt_refine.py <audio> <input.{json,srt}> [-o out.srt]
        [-r report.md] [--model large-v3] [--device cuda] [--language zh]
        [--precision 0.05]
"""

# ruff: noqa: E402  # sys.stdout.reconfigure must run before non-stdlib imports (Windows UTF-8)

from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

import stable_whisper
import torch


@dataclass
class CueTime:
    idx: int
    start: float
    end: float
    text: str


_TS_RE = re.compile(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3}) --> (\d{2}):(\d{2}):(\d{2})[,.](\d{3})")


def parse_srt(path: Path) -> list[CueTime]:
    text = path.read_text(encoding="utf-8")
    out: list[CueTime] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.split("\n")
        if len(lines) < 3:
            continue
        m = _TS_RE.match(lines[1])
        if not m:
            continue
        s = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) + int(m[4]) / 1000
        e = int(m[5]) * 3600 + int(m[6]) * 60 + int(m[7]) + int(m[8]) / 1000
        try:
            idx = int(lines[0].strip())
        except ValueError:
            idx = len(out) + 1
        out.append(CueTime(idx=idx, start=s, end=e, text=" ".join(lines[2:]).strip()))
    return out


def fmt_ts(s: float) -> str:
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = s % 60
    return f"{h:02d}:{m:02d}:{sec:06.3f}"


def write_srt_from_cues(cues: list[CueTime], path: Path) -> None:
    """從 CueTime list 寫回 SRT。"""
    lines = []
    for c in cues:
        lines.append(str(c.idx))
        sh, sm, ss = int(c.start // 3600), int((c.start % 3600) // 60), c.start % 60
        eh, em, es = int(c.end // 3600), int((c.end % 3600) // 60), c.end % 60
        lines.append(
            f"{sh:02d}:{sm:02d}:{int(ss):02d},{int((ss % 1) * 1000):03d} --> "
            f"{eh:02d}:{em:02d}:{int(es):02d},{int((es % 1) * 1000):03d}"
        )
        lines.append(c.text)
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def refine_json_mode(
    audio: Path, json_in: Path, out_srt: Path, model_size: str, device: str, precision: float
) -> tuple[list[CueTime], list[CueTime]]:
    """JSON mode：load stable-ts result + refine() + 寫 refined SRT。"""
    print("Mode: JSON (stable-ts result → refine())\n")

    # Parse 原 SRT 對位用（從 result reload + write 一次拿 baseline）
    pre = stable_whisper.WhisperResult(str(json_in))
    pre_srt_path = out_srt.with_suffix(".pre.srt")
    pre.to_srt_vtt(str(pre_srt_path), segment_level=True, word_level=False)
    before = parse_srt(pre_srt_path)
    pre_srt_path.unlink()  # 清理暫存

    # Load model
    print(f"Loading {model_size} on {device}...")
    t0 = time.time()
    model = stable_whisper.load_model(model_size, device=device)
    print(f"  loaded in {time.time() - t0:.1f}s\n")

    # Reload (refine 會 mutate)
    result = stable_whisper.WhisperResult(str(json_in))

    # Refine
    print(f"Refining timestamps (precision={precision}s)...")
    print("  逐 word mute audio 重跑 forward pass，預計 wall clock ~原 ASR 時間 × 2-5\n")
    t1 = time.time()
    refined = model.refine(str(audio), result, precision=precision, verbose=False)
    print(f"  refined in {time.time() - t1:.1f}s = {(time.time() - t1) / 60:.2f} min\n")

    # Write
    refined.to_srt_vtt(str(out_srt), segment_level=True, word_level=False)
    after = parse_srt(out_srt)
    return before, after


def align_srt_mode(
    audio: Path, srt_in: Path, out_srt: Path, model_size: str, device: str, language: str
) -> tuple[list[CueTime], list[CueTime]]:
    """SRT mode：parse SRT → align(audio, full_text) → 用原 cue 結構重組。"""
    print("Mode: SRT (align() forced alignment to original cue structure)\n")

    before = parse_srt(srt_in)
    print(f"Loaded {len(before)} cues from input SRT\n")

    # 把每個 cue 的文字串接成完整 text，用 newline 分隔
    # （stable-ts align 對長文本比較友善，且 newline 會自然斷段）
    full_text = "\n".join(c.text for c in before)

    # Load model
    print(f"Loading {model_size} on {device}...")
    t0 = time.time()
    model = stable_whisper.load_model(model_size, device=device)
    print(f"  loaded in {time.time() - t0:.1f}s\n")

    # Align
    print(f"Aligning text to audio (language={language})...")
    t1 = time.time()
    aligned = model.align(str(audio), full_text, language=language, verbose=False)
    print(f"  aligned in {time.time() - t1:.1f}s = {(time.time() - t1) / 60:.2f} min\n")

    # 把 align 結果按原 cue 文字重組 timestamp
    # aligned.segments 結構：每 segment 對應 newline-split 的一塊文字
    if len(aligned.segments) != len(before):
        print(
            f"  ⚠️ aligned segments {len(aligned.segments)} != cues {len(before)}, "
            f"falling back to word-level remap"
        )
        # Fallback：把 align 出的 word 連起來按原 cue 文字 char-by-char 對齊
        all_words = [w for seg in aligned.segments for w in seg.words]
        after_cues = remap_by_chars(before, all_words)
    else:
        after_cues = []
        for orig, seg in zip(before, aligned.segments):
            after_cues.append(
                CueTime(
                    idx=orig.idx,
                    start=seg.start,
                    end=seg.end,
                    text=orig.text,  # 保留原文字
                )
            )

    write_srt_from_cues(after_cues, out_srt)
    return before, after_cues


def remap_by_chars(cues: list[CueTime], words: list) -> list[CueTime]:
    """Fallback：把 word list 的 timestamp 按 cue 文字 char count 對齊。"""
    out = []
    word_idx = 0
    word_char_pos = 0  # 當前 word 已耗用 char 數
    for c in cues:
        target_chars = len(c.text.replace(" ", ""))
        consumed = 0
        first_word_idx = word_idx
        last_word_idx = word_idx
        while consumed < target_chars and word_idx < len(words):
            w = words[word_idx]
            wlen = len(w.word.strip().replace(" ", ""))
            remaining_in_word = wlen - word_char_pos
            need = target_chars - consumed
            if need >= remaining_in_word:
                consumed += remaining_in_word
                last_word_idx = word_idx
                word_idx += 1
                word_char_pos = 0
            else:
                consumed += need
                last_word_idx = word_idx
                word_char_pos += need
        if first_word_idx < len(words):
            start = words[first_word_idx].start
            end = words[min(last_word_idx, len(words) - 1)].end
        else:
            start = c.start
            end = c.end
        out.append(CueTime(idx=c.idx, start=start, end=end, text=c.text))
    return out


def diff_report(before: list[CueTime], after: list[CueTime], mode: str) -> str:
    md = []
    md.append("# SRT Timestamp Refine Diff Report\n")
    md.append(f"Mode: {mode}, cues: {len(before)} → {len(after)}\n")

    if len(before) != len(after):
        md.append("⚠️ cue 數變化（structural change），逐 cue diff 不適用，僅出時長變化")
        return "\n".join(md)

    starts = [(a.start - b.start) for a, b in zip(after, before)]
    ends = [(a.end - b.end) for a, b in zip(after, before)]
    abs_starts = [abs(x) for x in starts]
    abs_ends = [abs(x) for x in ends]

    def stat_line(name: str, vals: list[float]) -> str:
        if not vals:
            return f"- {name}: (empty)"
        avg = sum(vals) / len(vals)
        return (
            f"- {name}: avg {avg:.3f}s, max {max(vals):.3f}s, "
            f">0.5s {sum(1 for v in vals if v > 0.5)}, "
            f">0.2s {sum(1 for v in vals if v > 0.2)}, "
            f">0.1s {sum(1 for v in vals if v > 0.1)}, "
            f">0.05s {sum(1 for v in vals if v > 0.05)}"
        )

    md.append("## 偏移統計（abs）\n")
    md.append(stat_line("Cue start 邊界偏移", abs_starts))
    md.append(stat_line("Cue end 邊界偏移", abs_ends))
    md.append("")

    md.append("## 移動方向（signed）\n")
    md.append(f"- Start 平均 shift: {sum(starts) / len(starts):+.3f}s")
    md.append(f"- End 平均 shift: {sum(ends) / len(ends):+.3f}s")
    md.append(
        f"- Start 推遲: {sum(1 for x in starts if x > 0.05)}, "
        f"提前: {sum(1 for x in starts if x < -0.05)}, "
        f"幾乎不變: {sum(1 for x in starts if abs(x) <= 0.05)}"
    )
    md.append(
        f"- End 推遲: {sum(1 for x in ends if x > 0.05)}, "
        f"提前: {sum(1 for x in ends if x < -0.05)}, "
        f"幾乎不變: {sum(1 for x in ends if abs(x) <= 0.05)}\n"
    )

    big = [
        (b, a, ds, de)
        for b, a, ds, de in zip(before, after, starts, ends)
        if abs(ds) > 0.3 or abs(de) > 0.3
    ]
    md.append(f"## 大幅修正 cue（start 或 end 偏移 >0.3s），共 {len(big)} 條\n")
    if big:
        md.append("| # | Before | After | Δstart | Δend | Text (前30字) |")
        md.append("|---|---|---|---|---|---|")
        for b, a, ds, de in big[:50]:
            md.append(
                f"| {b.idx} | {fmt_ts(b.start)}→{fmt_ts(b.end)} | "
                f"{fmt_ts(a.start)}→{fmt_ts(a.end)} | "
                f"{ds:+.3f}s | {de:+.3f}s | {b.text[:30]} |"
            )
        if len(big) > 50:
            md.append(f"\n（… 共 {len(big)} 條，截前 50）")

    return "\n".join(md)


def main() -> None:
    ap = argparse.ArgumentParser(description="SRT 時間軸精修工具")
    ap.add_argument("audio", help="audio file (wav/mp3/m4a)")
    ap.add_argument("input", help="input file (.json from stable-ts, or .srt)")
    ap.add_argument("-o", "--output", help="output SRT (default: <input>.refined.srt)")
    ap.add_argument(
        "-r", "--report", help="diff report markdown (default: <input>.refine-report.md)"
    )
    ap.add_argument("--model", default="large-v3", help="Whisper model (default large-v3)")
    ap.add_argument("--device", default="cuda", help="cuda / cpu")
    ap.add_argument("--language", default="zh", help="audio language code (for SRT mode align)")
    ap.add_argument("--precision", type=float, default=0.05, help="JSON-mode refine precision (s)")
    args = ap.parse_args()

    audio = Path(args.audio).resolve()
    inp = Path(args.input).resolve()
    if not audio.exists():
        sys.exit(f"audio not found: {audio}")
    if not inp.exists():
        sys.exit(f"input not found: {inp}")

    out_srt = Path(args.output) if args.output else inp.with_suffix(".refined.srt")
    out_report = Path(args.report) if args.report else inp.with_suffix(".refine-report.md")

    print(f"torch {torch.__version__}, CUDA {torch.cuda.is_available()}")
    print(f"stable-ts {stable_whisper.__version__}\n")
    print(f"audio:  {audio}")
    print(f"input:  {inp}")
    print(f"out:    {out_srt}")
    print(f"report: {out_report}\n")

    is_json = inp.suffix.lower() == ".json"
    t_start = time.time()
    if is_json:
        before, after = refine_json_mode(
            audio, inp, out_srt, args.model, args.device, args.precision
        )
        mode = "JSON refine()"
    else:
        before, after = align_srt_mode(audio, inp, out_srt, args.model, args.device, args.language)
        mode = "SRT align()"

    print(f"[OUT] {out_srt}")

    md = diff_report(before, after, mode)
    out_report.write_text(md, encoding="utf-8")
    print(f"[OUT] {out_report}")
    print(f"\nDONE in {time.time() - t_start:.1f}s total")


if __name__ == "__main__":
    main()
