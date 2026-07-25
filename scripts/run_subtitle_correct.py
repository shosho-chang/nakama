"""subtitle-correct pipeline：subs/raw.srt + refs/ → transcript.srt + transcript.qc.md。

模式自動選擇：
- refs/ 內有 script.md / script.txt / 逐字稿*.md|txt（完整稿）→ scripted 對稿模式
- 否則（訪綱、報告等零散參考）→ llm 模式（Opus chunked 校正 + 可選 Gemini 仲裁）
- --mode / --script 可覆寫

用法：
    python scripts/run_subtitle_correct.py "G:/footages/20260723 謝伯讓"
    python scripts/run_subtitle_correct.py <episode> --mode llm --no-arbitration
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import find_dotenv, load_dotenv  # noqa: E402

load_dotenv()
load_dotenv(find_dotenv(usecwd=True))

from shared.subtitle_correct import (  # noqa: E402
    build_correction_report,
    correct_srt_llm,
    correct_srt_scripted,
    discover_ref_files,
)

logger = logging.getLogger("subtitle_correct")

REFS_DIR = "refs"
OUTPUT_SRT = "transcript.srt"
OUTPUT_QC = "transcript.qc.md"
MANIFEST_NAME = "correct_manifest.json"

_SCRIPT_STEMS = ("script", "逐字稿", "腳本")


def find_script_file(refs: list[Path]) -> Path | None:
    for p in refs:
        stem = p.stem.lower()
        if any(stem.startswith(s) for s in _SCRIPT_STEMS):
            return p
    return None


def run_correct(
    episode_dir: Path,
    *,
    srt: Path | None = None,
    mode: str = "auto",
    script: Path | None = None,
    model: str = "claude-opus-4-7",
    host_name: str = "",
    show_name: str = "",
    use_arbitration: bool = True,
) -> Path:
    srt_path = srt if srt else episode_dir / "subs" / "raw.srt"
    if not srt_path.exists():
        raise FileNotFoundError(f"找不到字幕檔 {srt_path}（先跑 subtitle-gen，或用 --srt 指定）")
    srt_content = srt_path.read_text(encoding="utf-8")

    refs = discover_ref_files(episode_dir)

    script_path = script or find_script_file(refs)
    if mode == "auto":
        mode = "scripted" if script_path else "llm"
    logger.info(f"校正模式: {mode}（SRT: {srt_path.name}, refs: {[p.name for p in refs]}）")

    if mode == "scripted":
        if not script_path:
            raise ValueError("scripted 模式需要完整稿（refs/script.* 或 --script）")
        script_text = script_path.read_text(encoding="utf-8", errors="replace")
        corrected, qc_items, stats = correct_srt_scripted(srt_content, script_text)
        stats["script"] = script_path.name
    else:
        audio_path = episode_dir / "normalized.wav"
        ref_files = [p for p in refs if p != script_path]
        corrected, qc_items, stats = correct_srt_llm(
            srt_content,
            ref_files=ref_files,
            model=model,
            host_name=host_name,
            show_name=show_name,
            audio_path=audio_path if audio_path.exists() else None,
            use_arbitration=use_arbitration,
        )

    out_srt = episode_dir / OUTPUT_SRT
    out_srt.write_text(corrected, encoding="utf-8")
    out_qc = episode_dir / OUTPUT_QC
    out_qc.write_text(build_correction_report(stats, qc_items), encoding="utf-8")

    manifest = {
        "stage": "correct",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "input_srt": str(srt_path),
        "refs": [p.name for p in refs],
        **stats,
    }
    (episode_dir / "subs" / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(f"完成: {out_srt}（{stats.get('corrections', 0)} 行修正）報告 → {out_qc.name}")
    return out_srt


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="subtitle-correct：SRT 校正（對稿 / LLM 雙模式）")
    parser.add_argument("episode", help="episode 資料夾")
    parser.add_argument("--srt", help="直接指定要校正的 SRT（預設 subs/raw.srt）")
    parser.add_argument("--mode", choices=["auto", "scripted", "llm"], default="auto")
    parser.add_argument("--script", help="完整稿路徑（覆寫 refs/ 自動偵測）")
    parser.add_argument("--model", default="claude-opus-4-7")
    parser.add_argument("--host-name", default="")
    parser.add_argument("--show-name", default="")
    parser.add_argument("--no-arbitration", action="store_true", help="跳過 Gemini 多模態仲裁")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    args = _parse_args(argv)
    episode_dir = Path(args.episode)
    if not episode_dir.is_dir():
        logger.error(f"episode 資料夾不存在: {episode_dir}")
        return 1
    run_correct(
        episode_dir,
        srt=Path(args.srt) if args.srt else None,
        mode=args.mode,
        script=Path(args.script) if args.script else None,
        model=args.model,
        host_name=args.host_name,
        show_name=args.show_name,
        use_arbitration=not args.no_arbitration,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
