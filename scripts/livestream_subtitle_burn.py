"""社群直播：release.srt → display.srt → 燒字幕的影片（剪掉頭尾）。

用法：
    # 1. 顯示層字幕（剪接點以原始錄影秒數表示；中場休息等靜音幻覺段用 --drop 拿掉）
    python scripts/livestream_subtitle_burn.py prepare \
        --srt "<episode>/subtitle-release/memo-dual-audit-v1/release.srt" \
        --start 373 --end 7700 --drop 5240-5537 \
        --out "<episode>/burn/display.srt"

    # 2. 先燒一小段試看（--preview-seconds），沒問題再燒整場
    python scripts/livestream_subtitle_burn.py burn \
        --video "<episode>/video1151365866.mp4" --srt "<episode>/burn/display.srt" \
        --start 373 --end 7700 --out "<episode>/burn/final.mp4"

字幕樣式＝修修 2026-11-20 看過試片的版本：LINE Seed TW 粗體、半透明深底，置中在 Zoom
畫面左側投影片範圍（右側 224px 是視訊欄）。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.livestream_subtitles import build_display_srt  # noqa: E402

#: 使用者字型資料夾（LINE Seed TW 裝在這裡，不在 C:\Windows\Fonts）
DEFAULT_FONTS_DIR = Path.home() / "AppData/Local/Microsoft/Windows/Fonts"
#: libass 對 SRT 的座標系是 384x288，數值都以這個比例換算到實際畫面
LIVESTREAM_SUB_STYLE = (
    "FontName=LINE Seed TW_TTF Bold,FontSize=15,PrimaryColour=&H00FFFFFF,"
    "BorderStyle=3,Outline=1.2,Shadow=0,BackColour=&H66000000,OutlineColour=&H66000000,"
    "MarginV=14,MarginL=0,MarginR=40,WrapStyle=2"
)


def _parse_range(text: str) -> tuple[float, float]:
    a, _, b = text.partition("-")
    lo, hi = float(a), float(b)
    if hi <= lo:
        raise argparse.ArgumentTypeError(f"範圍要前小後大：{text}")
    return lo, hi


def cmd_prepare(args: argparse.Namespace) -> int:
    src = Path(args.srt)
    text, stats = build_display_srt(
        src.read_text(encoding="utf-8"),
        start=args.start,
        end=args.end,
        drop_ranges=args.drop or [],
        max_line_chars=args.max_line_chars,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(json.dumps({"output": str(out), **stats.__dict__}, ensure_ascii=False))
    return 0


def _filter_path(path: Path) -> str:
    """ffmpeg filter 參數裡的路徑：反斜線換正斜線、磁碟機冒號跳脫。"""
    return str(path).replace("\\", "/").replace(":", "\\:")


def burn_command(
    *,
    video: Path,
    srt_name: str,
    start: float,
    end: float | None,
    out: Path,
    fonts_dir: Path,
    style: str = LIVESTREAM_SUB_STYLE,
    crf: int = 20,
    preset: str = "medium",
) -> list[str]:
    vf = f"subtitles={srt_name}:fontsdir='{_filter_path(fonts_dir)}':force_style='{style}'"
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-stats", "-y", "-ss", f"{start:.3f}"]
    if end is not None:
        cmd += ["-to", f"{end:.3f}"]
    cmd += [
        "-i",
        str(video),
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-crf",
        str(crf),
        "-preset",
        preset,
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(out),
    ]
    return cmd


def cmd_burn(args: argparse.Namespace) -> int:
    srt = Path(args.srt).resolve()
    out = Path(args.out).resolve()
    end = args.end
    if args.preview_seconds:
        end = args.start + args.preview_seconds
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = burn_command(
        video=Path(args.video).resolve(),
        srt_name=srt.name,
        start=args.start,
        end=end,
        out=out,
        fonts_dir=Path(args.fonts_dir),
        crf=args.crf,
        preset=args.preset,
    )
    if shutil.which("ffmpeg") is None:
        print("找不到 ffmpeg", file=sys.stderr)
        return 1
    # 以 srt 所在資料夾為 cwd、filter 只給檔名，避開 Windows 路徑在 filter 裡的跳脫問題
    subprocess.run(cmd, check=True, cwd=srt.parent)
    print(json.dumps({"output": str(out), "start": args.start, "end": end}, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="社群直播字幕：顯示層＋燒字幕")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("prepare", help="release.srt → display.srt")
    p.add_argument("--srt", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--start", type=float, default=0.0, help="剪接起點（原始錄影秒數）")
    p.add_argument("--end", type=float, default=None, help="剪接終點（原始錄影秒數）")
    p.add_argument("--drop", type=_parse_range, action="append", help="整段拿掉的範圍 a-b（秒）")
    p.add_argument("--max-line-chars", type=int, default=24)
    p.set_defaults(func=cmd_prepare)

    b = sub.add_parser("burn", help="剪頭尾＋燒字幕")
    b.add_argument("--video", required=True)
    b.add_argument("--srt", required=True, help="prepare 產生的 display.srt（時間已平移）")
    b.add_argument("--out", required=True)
    b.add_argument("--start", type=float, default=0.0)
    b.add_argument("--end", type=float, default=None)
    b.add_argument("--preview-seconds", type=float, default=None, help="只燒 start 起這麼多秒")
    b.add_argument("--fonts-dir", default=str(DEFAULT_FONTS_DIR))
    b.add_argument("--crf", type=int, default=20)
    b.add_argument("--preset", default="medium")
    b.set_defaults(func=cmd_burn)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
