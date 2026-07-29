"""subtitle-correct pipeline：subs/raw.srt + refs/ → transcript.srt + transcript.qc.md。

模式：
- refs/ 有完整稿（script.* / 逐字稿*）→ scripted 對稿（零 LLM，直接跑）
- 否則 → **cowork subagent 流程**（subscription quota，預設）：
    ① `--emit-chunks` 切 chunk + 指示檔到 subs/correct_work/
    ② skill 派 Opus/其他 subagent 逐 chunk 校正、合併成 corrections.json
    ③ `--apply corrections.json` 機械套用（越界過濾 + 過度刪減防護 + Pass 2 + 報告）
- 付費 API 路徑（Anthropic Opus ± Gemini 仲裁）需明確 `--api`（仲裁再加 `--arbitrate`）

用法：
    python scripts/run_subtitle_correct.py <episode>                    # scripted（有完整稿時）
    python scripts/run_subtitle_correct.py <episode> --emit-chunks      # cowork ①
    python scripts/run_subtitle_correct.py <episode> --apply subs/correct_work/corrections.json
    python scripts/run_subtitle_correct.py <episode> --api --arbitrate  # 付費路徑（opt-in）
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
    WORKDIR_NAME,
    apply_corrections,
    build_correction_report,
    correct_srt_llm,
    correct_srt_scripted,
    discover_ref_files,
    emit_correction_workspace,
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


def _load_srt(episode_dir: Path, srt: Path | None) -> tuple[Path, str]:
    srt_path = srt if srt else episode_dir / "subs" / "raw.srt"
    if not srt_path.exists():
        raise FileNotFoundError(f"找不到字幕檔 {srt_path}（先跑 subtitle-gen，或用 --srt 指定）")
    return srt_path, srt_path.read_text(encoding="utf-8")


def run_emit_chunks(
    episode_dir: Path,
    *,
    srt: Path | None = None,
    host_name: str = "",
    show_name: str = "",
) -> Path:
    """cowork subagent 流程第一段：切 chunk + 指示檔到 subs/correct_work/。"""
    _, srt_content = _load_srt(episode_dir, srt)
    refs = discover_ref_files(episode_dir)
    script_path = find_script_file(refs)
    ref_files = [p for p in refs if p != script_path]
    workdir = episode_dir / "subs" / WORKDIR_NAME
    meta = emit_correction_workspace(
        srt_content,
        workdir,
        ref_files=ref_files,
        host_name=host_name,
        show_name=show_name,
    )
    print(json.dumps({"workdir": str(workdir), **meta}, ensure_ascii=False, indent=2))
    return workdir


def run_apply(
    episode_dir: Path,
    corrections_json: Path,
    *,
    srt: Path | None = None,
) -> Path:
    """cowork subagent 流程第二段：套用 merged corrections JSON → 產出 + 報告。"""
    srt_path, srt_content = _load_srt(episode_dir, srt)
    payload = json.loads(corrections_json.read_text(encoding="utf-8"))
    corrected, qc_items, stats = apply_corrections(srt_content, payload)
    refs = discover_ref_files(episode_dir)
    return _write_outputs(episode_dir, srt_path, refs, corrected, qc_items, stats)


def run_correct(
    episode_dir: Path,
    *,
    srt: Path | None = None,
    mode: str = "auto",
    script: Path | None = None,
    model: str = "claude-opus-4-7",
    host_name: str = "",
    show_name: str = "",
    use_arbitration: bool = False,
) -> Path:
    """scripted 對稿（零 LLM）或 API 路徑（明確 opt-in，花 API 錢）。"""
    srt_path, srt_content = _load_srt(episode_dir, srt)

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

    return _write_outputs(episode_dir, srt_path, refs, corrected, qc_items, stats)


def _write_outputs(
    episode_dir: Path,
    srt_path: Path,
    refs: list[Path],
    corrected: str,
    qc_items: list[dict],
    stats: dict,
) -> Path:
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
    (episode_dir / "subs").mkdir(exist_ok=True)
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
    parser.add_argument(
        "--emit-chunks",
        action="store_true",
        help="cowork 流程①：切 chunk + 指示檔到 subs/correct_work/，供 subagent 認領",
    )
    parser.add_argument(
        "--apply",
        metavar="JSON",
        help="cowork 流程②：套用 merged corrections JSON（Pass 2 / 防護 / 報告）",
    )
    parser.add_argument(
        "--api",
        action="store_true",
        help="改走付費 API 路徑（Anthropic Opus）；未指定時 llm 模式只接受 cowork 流程",
    )
    parser.add_argument(
        "--arbitrate",
        action="store_true",
        help="開 Gemini 多模態仲裁（花 API 錢；僅 --api 路徑有效，預設關）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    args = _parse_args(argv)
    episode_dir = Path(args.episode)
    if not episode_dir.is_dir():
        logger.error(f"episode 資料夾不存在: {episode_dir}")
        return 1

    srt = Path(args.srt) if args.srt else None

    if args.emit_chunks:
        run_emit_chunks(episode_dir, srt=srt, host_name=args.host_name, show_name=args.show_name)
        return 0

    if args.apply:
        run_apply(episode_dir, Path(args.apply), srt=srt)
        return 0

    script = Path(args.script) if args.script else None
    # llm 模式（無 --api）擋下：避免不小心燒 API 錢。scripted 是零 LLM 不受限
    if not args.api:
        refs = discover_ref_files(episode_dir)
        will_be_scripted = args.mode == "scripted" or (
            args.mode == "auto" and (script or find_script_file(refs))
        )
        if not will_be_scripted:
            logger.error(
                "llm 校正預設走 cowork subagent 流程（subscription quota）："
                "--emit-chunks → 派 subagent 校正 → --apply corrections.json。"
                "真的要用付費 API 路徑請加 --api（仲裁另加 --arbitrate）"
            )
            return 2

    run_correct(
        episode_dir,
        srt=srt,
        mode=args.mode,
        script=script,
        model=args.model,
        host_name=args.host_name,
        show_name=args.show_name,
        use_arbitration=args.arbitrate,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
