"""SRT 字幕時間對齊 CLI — 把跑掉的時間戳重新對上音檔。

用法：

  # 1) 固定位移：字幕比聲音早 2.5 秒，就往後推 2.5 秒
  python scripts/align_srt.py input.srt --shift 2.5

  # 2) 線性變換（自行指定 a, b）：t_new = a * t_old + b
  python scripts/align_srt.py input.srt --scale 1.001 --shift 1.2

  # 3) 自動對齊：吃音檔 + 舊 SRT，跑 ASR 後用文字匹配解 (a, b)
  python scripts/align_srt.py input.srt --audio talk.wav --auto

  # dry-run：只印偏移/擬合結果，不寫檔
  python scripts/align_srt.py input.srt --audio talk.wav --auto --dry-run

輸出：預設寫到 <input>.aligned.srt，可用 --output 指定。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from shared.srt_align import (  # noqa: E402
    apply_linear,
    detect_transform,
    format_srt,
    parse_srt,
    retime_cues_from_asr,
    run_asr_segments,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SRT 字幕時間戳對齊工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("srt_path", type=Path, help="原始 SRT 檔（時間跑掉的那份）")
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="輸出路徑（預設：<input>.aligned.srt）",
    )
    parser.add_argument(
        "--shift",
        type=float,
        default=None,
        help="固定位移秒數。正值=字幕延後，負值=字幕提前。字幕比聲音早就填正值。",
    )
    parser.add_argument(
        "--scale",
        type=float,
        default=None,
        help="線性變換的 slope a（預設 1.0，即純位移）。1.001 代表每秒慢 1ms。",
    )
    parser.add_argument(
        "--audio",
        type=Path,
        default=None,
        help="音檔路徑；搭配 --auto 使用",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="自動偵測全局線性變換 (a, b)：跑 WhisperX 比對 SRT 文字後最小平方擬合",
    )
    parser.add_argument(
        "--retime",
        action="store_true",
        help="逐 cue 轉移 ASR 時間戳：適合非線性漂移或局部抖動；未匹配 cue 用鄰居線性內插",
    )
    parser.add_argument(
        "--ratio-threshold",
        type=float,
        default=0.7,
        help="auto：文字匹配最低相似度（預設 0.7；字幕 typo 多就調低）",
    )
    parser.add_argument(
        "--window",
        type=float,
        default=45.0,
        help="auto：搜尋視窗半徑秒（預設 45）",
    )
    parser.add_argument(
        "--asr-model",
        default="large-v3",
        help="auto：WhisperX 模型 ID（faster-whisper backend）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只印參數與統計，不寫輸出檔",
    )
    return parser.parse_args()


def _resolve_transform(args: argparse.Namespace) -> tuple[float, float, str]:
    """決定 (a, b) 與說明字串。優先順序：--auto > 手動 scale/shift。"""
    if args.auto:
        if args.audio is None:
            raise SystemExit("--auto 需要同時給 --audio <path>")
        if not args.audio.exists():
            raise SystemExit(f"音檔不存在：{args.audio}")

        print(f"[auto] 音檔: {args.audio}")
        print(f"[auto] SRT:  {args.srt_path}")
        print(f"[auto] 跑 ASR（模型 {args.asr_model}）並比對文字...")
        print("-" * 60)

        fit, matches = detect_transform(
            args.srt_path,
            args.audio,
            asr_model=args.asr_model,
            ratio_threshold=args.ratio_threshold,
            window_s=args.window,
        )

        print(
            f"[auto] 擬合結果：n={fit.n}  R²={fit.r_squared:.4f}  "
            f"residual σ={fit.residual_std_s * 1000:.0f}ms"
        )
        print(f"[auto] t_new = {fit.a:.6f} * t_old + {fit.b:+.3f}s")
        if fit.is_pure_shift:
            print(f"[auto] slope ≈ 1，判定為純位移：offset = {fit.b:+.3f}s")

        if matches:
            sample = matches[: min(3, len(matches))]
            print("[auto] 抽樣匹配（SRT 時間 → ASR 時間，相似度）：")
            for m in sample:
                print(
                    f"  - {m.cue.start_s:8.2f}s → {m.asr.start_s:8.2f}s  "
                    f"[{m.ratio:.2f}]  {m.cue.text.splitlines()[0][:30]}"
                )

        if fit.r_squared < 0.9:
            print("\n[auto] ⚠ R² 偏低，擬合可能不可靠。試試降 --ratio-threshold 或增大 --window。")

        return fit.a, fit.b, "auto"

    # 手動模式
    a = args.scale if args.scale is not None else 1.0
    b = args.shift if args.shift is not None else 0.0
    if a == 1.0 and b == 0.0:
        raise SystemExit("沒有任何變換參數。請用 --shift / --scale / --auto 其中之一。")
    return a, b, "manual"


def _run_retime(args: argparse.Namespace):
    """--retime 模式：逐 cue 從 ASR 轉移時間戳。"""
    if args.audio is None:
        raise SystemExit("--retime 需要 --audio <path>")
    if not args.audio.exists():
        raise SystemExit(f"音檔不存在：{args.audio}")

    srt_content = args.srt_path.read_text(encoding="utf-8")
    cues = parse_srt(srt_content)
    if not cues:
        raise SystemExit(f"SRT 解析結果為空：{args.srt_path}")

    print(f"[retime] 音檔: {args.audio}")
    print(f"[retime] SRT:  {args.srt_path}（{len(cues)} cues）")
    print(f"[retime] 跑 ASR（模型 {args.asr_model}）...")
    print("-" * 60)

    asr_segs = run_asr_segments(args.audio, asr_model=args.asr_model)

    new_cues, matches, stats = retime_cues_from_asr(
        cues,
        asr_segs,
        ratio_threshold=args.ratio_threshold,
        window_s=args.window,
    )

    coverage = stats.matched / stats.total if stats.total else 0.0
    print(f"[retime] 總 cue：{stats.total}")
    print(f"[retime] 匹配：{stats.matched}  ({coverage:.1%})")
    print(f"[retime] 內插：{stats.interpolated}")
    print(
        f"[retime] 位移分布：min={stats.offset_min_s:+.2f}s  "
        f"median={stats.offset_median_s:+.2f}s  max={stats.offset_max_s:+.2f}s"
    )

    if matches:
        print("[retime] 抽樣匹配（cue → asr，|ratio|）：")
        sample = matches[:: max(1, len(matches) // 6)][:6]
        for m in sample:
            off = m.asr.start_s - m.cue.start_s
            preview = m.cue.text.splitlines()[0][:30]
            print(
                f"  {m.cue.start_s:8.2f}s → {m.asr.start_s:8.2f}s  "
                f"(off={off:+.2f}s ratio={m.ratio:.2f})  {preview}"
            )

    if coverage < 0.3:
        print(
            f"\n[retime] ⚠ 匹配率 {coverage:.1%} 偏低，結果主要靠內插，可能不可靠。"
            f"試試降 --ratio-threshold 或增大 --window。"
        )

    if args.dry_run:
        print("[dry-run] 未寫檔")
        return

    output = args.output or args.srt_path.with_suffix(".aligned.srt")
    output.write_text(format_srt(new_cues), encoding="utf-8")
    print(f"\n已輸出：{output}")


def main() -> None:
    args = _parse_args()

    if not args.srt_path.exists():
        raise SystemExit(f"SRT 不存在：{args.srt_path}")

    if args.retime:
        _run_retime(args)
        return

    a, b, mode = _resolve_transform(args)

    srt_content = args.srt_path.read_text(encoding="utf-8")
    cues = parse_srt(srt_content)
    if not cues:
        raise SystemExit(f"SRT 解析結果為空：{args.srt_path}")

    new_cues = apply_linear(cues, a, b)

    print("-" * 60)
    print(f"cue 數量: {len(cues)}")
    print(f"變換：t_new = {a:.6f} * t_old + {b:+.3f}s  ({mode})")
    print(f"首 cue: {cues[0].start_s:.2f}s → {new_cues[0].start_s:.2f}s")
    print(f"末 cue: {cues[-1].start_s:.2f}s → {new_cues[-1].start_s:.2f}s")

    if args.dry_run:
        print("[dry-run] 未寫檔")
        return

    output = args.output or args.srt_path.with_suffix(".aligned.srt")
    output.write_text(format_srt(new_cues), encoding="utf-8")
    print(f"\n已輸出：{output}")


if __name__ == "__main__":
    main()
