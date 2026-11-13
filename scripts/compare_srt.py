"""並排比對兩個 SRT 檔（用於 ASR 引擎對照測試）。

用法：
    python scripts/compare_srt.py <a.srt> <b.srt> --label-a FunASR --label-b MemoAI \\
        --output docs/research/<date>-comparison.md --bucket-seconds 20

輸出：markdown 報告，含
- 兩家整體統計（cue 數、字數、平均 cue 長）
- 固定時間桶（預設 20 秒）並排，桶內列出兩家各自所有 cue 文字
- 字數差異 / cue 數差異 highlight 給人眼快速掃描
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

_SRT_TS = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
)


@dataclass
class Cue:
    seq: int
    start_ms: int
    end_ms: int
    text: str

    @property
    def mid_ms(self) -> int:
        return (self.start_ms + self.end_ms) // 2

    @property
    def char_count(self) -> int:
        return len(re.sub(r"\s+", "", self.text))


def _ts_to_ms(h: str, m: str, s: str, ms: str) -> int:
    return (int(h) * 3600 + int(m) * 60 + int(s)) * 1000 + int(ms)


def _ms_to_ts(ms: int) -> str:
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def parse_srt(path: Path) -> list[Cue]:
    cues: list[Cue] = []
    raw = path.read_text(encoding="utf-8", errors="replace")
    blocks = re.split(r"\r?\n\r?\n+", raw.strip())
    for block in blocks:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        try:
            seq = int(lines[0])
        except ValueError:
            continue
        ts_match = _SRT_TS.search(lines[1])
        if not ts_match:
            continue
        start_ms = _ts_to_ms(*ts_match.group(1, 2, 3, 4))
        end_ms = _ts_to_ms(*ts_match.group(5, 6, 7, 8))
        text = " ".join(lines[2:]) if len(lines) > 2 else ""
        cues.append(Cue(seq, start_ms, end_ms, text))
    return cues


def stats(cues: list[Cue]) -> dict:
    if not cues:
        return {"cue_count": 0, "char_count": 0, "duration_s": 0.0, "avg_cue_chars": 0.0}
    char_count = sum(c.char_count for c in cues)
    duration_s = max(c.end_ms for c in cues) / 1000.0
    return {
        "cue_count": len(cues),
        "char_count": char_count,
        "duration_s": duration_s,
        "avg_cue_chars": char_count / len(cues),
    }


def bucket(cues: list[Cue], bucket_s: int) -> dict[int, list[Cue]]:
    buckets: dict[int, list[Cue]] = {}
    bucket_ms = bucket_s * 1000
    for c in cues:
        bid = c.mid_ms // bucket_ms
        buckets.setdefault(bid, []).append(c)
    return buckets


def render(
    cues_a: list[Cue],
    cues_b: list[Cue],
    label_a: str,
    label_b: str,
    bucket_s: int,
    audio_path: str,
    bucket_filter: range | None = None,
) -> str:
    out: list[str] = []
    out.append(f"# {label_a} vs {label_b} — SRT 並排對照")
    out.append("")
    out.append(f"- 音檔：`{audio_path}`")
    out.append(f"- 時間桶大小：{bucket_s} 秒")
    out.append("")

    # Stats
    sa = stats(cues_a)
    sb = stats(cues_b)
    out.append("## 整體統計")
    out.append("")
    out.append(f"| 維度 | {label_a} | {label_b} | 差異 |")
    out.append("|---|---|---|---|")
    out.append(
        f"| Cue 數 | {sa['cue_count']} | {sb['cue_count']} | "
        f"{sa['cue_count'] - sb['cue_count']:+d} |"
    )
    out.append(
        f"| 字數（去空白）| {sa['char_count']} | {sb['char_count']} | "
        f"{sa['char_count'] - sb['char_count']:+d} |"
    )
    out.append(
        f"| 音檔長度（s）| {sa['duration_s']:.1f} | {sb['duration_s']:.1f} | "
        f"{sa['duration_s'] - sb['duration_s']:+.1f} |"
    )
    out.append(
        f"| 平均 cue 字數 | {sa['avg_cue_chars']:.1f} | {sb['avg_cue_chars']:.1f} | "
        f"{sa['avg_cue_chars'] - sb['avg_cue_chars']:+.1f} |"
    )
    out.append("")

    # Buckets
    ba = bucket(cues_a, bucket_s)
    bb = bucket(cues_b, bucket_s)
    all_bids = sorted(set(ba.keys()) | set(bb.keys()))
    if bucket_filter is not None:
        all_bids = [bid for bid in all_bids if bid in bucket_filter]

    out.append(f"## 時間桶並排（每桶 {bucket_s}s）")
    out.append("")

    bucket_ms = bucket_s * 1000
    for bid in all_bids:
        start_ms = bid * bucket_ms
        end_ms = start_ms + bucket_ms
        out.append(f"### [{_ms_to_ts(start_ms)} - {_ms_to_ts(end_ms)}]")
        out.append("")

        ca_list = ba.get(bid, [])
        cb_list = bb.get(bid, [])
        out.append(f"**{label_a}** ({len(ca_list)} cue, {sum(c.char_count for c in ca_list)} 字)：")
        for c in ca_list:
            out.append(f"- `{_ms_to_ts(c.start_ms)} → {_ms_to_ts(c.end_ms)}` {c.text}")
        if not ca_list:
            out.append("- _(無)_")
        out.append("")

        out.append(f"**{label_b}** ({len(cb_list)} cue, {sum(c.char_count for c in cb_list)} 字)：")
        for c in cb_list:
            out.append(f"- `{_ms_to_ts(c.start_ms)} → {_ms_to_ts(c.end_ms)}` {c.text}")
        if not cb_list:
            out.append("- _(無)_")
        out.append("")

        # Eval slot
        out.append("**評分**（修修填）：⬜ ✓ / ⬜ ✗ / ⬜ 局部錯  Note: ___")
        out.append("")

    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description="SRT 並排對照")
    parser.add_argument("srt_a", type=Path)
    parser.add_argument("srt_b", type=Path)
    parser.add_argument("--label-a", default="A")
    parser.add_argument("--label-b", default="B")
    parser.add_argument("--audio-path", default="")
    parser.add_argument("--bucket-seconds", type=int, default=20)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--max-buckets",
        type=int,
        default=0,
        help="只輸出前 N 桶（0 = 全部）",
    )
    args = parser.parse_args()

    cues_a = parse_srt(args.srt_a)
    cues_b = parse_srt(args.srt_b)

    print(f"{args.label_a}: {len(cues_a)} cues from {args.srt_a}")
    print(f"{args.label_b}: {len(cues_b)} cues from {args.srt_b}")

    bucket_filter = range(args.max_buckets) if args.max_buckets else None

    md = render(
        cues_a,
        cues_b,
        args.label_a,
        args.label_b,
        args.bucket_seconds,
        args.audio_path or "（未指定）",
        bucket_filter=bucket_filter,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(md, encoding="utf-8")
    print(f"輸出：{args.output}")


if __name__ == "__main__":
    main()
