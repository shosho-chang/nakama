"""mistake-removal 一鍵產線（cleanup v2）：footage 資料夾 → Resolve timeline。

修修拍攝工作流：照稿講、講錯拍手、重講到對為止。本產線輸出「文字跟
逐字稿對得上、無重複 take、無停頓」的 jump-cut timeline（2026-07-30 DoD）。

Stages（冪等，中間產物存在即跳過）：

1. stage   — footage 資料夾 → data/script_video/<name>/（episode.yaml、
             script.txt、aroll-audio.wav；4GB 主影片不複製，指向原檔）
2. words   — WhisperX 字級 timestamps（GPU，subprocess 到 Python310）
3. claps   — 拍手物理偵測 + NG marker 合併（clap_impulse）
4. plan    — script-coverage 選 take + ad-lib 雕刻 + 停頓收斂 + 驗證
             （script_coverage；episode 目錄有 adjudications.json 則作為
             manual_cuts 重放）。有待裁決項 → 寫 out/pending_adjudications
             .json 並以 exit code 3 停下 — 裁決層（Claude/人工）補
             adjudications.json 後重跑
5. emit    — transcript.srt + out/clean_segments.json + out/cleanup_qc.md
6. resolve — build_cleanup_timeline.py（subprocess 到 Python310）

用法：
    python scripts/run_mistake_removal.py "G:/Footages/20260730 頻道復出"
    python scripts/run_mistake_removal.py "data/script_video/<ep>" --no-resolve
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import wave
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

logger = logging.getLogger("mistake_removal")

_DATA_ROOT = _REPO_ROOT / "data" / "script_video"
_DEFAULT_GPU_PYTHON = r"C:\Users\Shosho\AppData\Local\Programs\Python\Python310\python.exe"


def _gpu_python() -> str:
    return os.environ.get("NAKAMA_GPU_PYTHON", _DEFAULT_GPU_PYTHON)


def stage_episode(input_dir: Path) -> tuple[Path, Path]:
    """footage 或 episode 目錄 → (episode_dir, main_video)。"""
    if (input_dir / "episode.yaml").exists():
        ep_dir = input_dir
    else:
        ep_dir = _DATA_ROOT / input_dir.name
        ep_dir.mkdir(parents=True, exist_ok=True)
        if not (ep_dir / "episode.yaml").exists():
            (ep_dir / "episode.yaml").write_text(
                f'id: "{input_dir.name}"\ntitle: "{input_dir.name}"\n', encoding="utf-8"
            )
    (ep_dir / "out").mkdir(exist_ok=True)

    # 逐字稿：episode 內 script.txt/md，否則從 footage 資料夾撿 *.txt
    if not any((ep_dir / n).exists() for n in ("script.txt", "script.md")):
        txts = sorted(input_dir.glob("*.txt"))
        if not txts:
            raise SystemExit(f"找不到逐字稿（{input_dir} 無 *.txt）— script-coverage 必需")
        (ep_dir / "script.txt").write_text(
            txts[0].read_text(encoding="utf-8"), encoding="utf-8"
        )
        logger.info("staged script.txt ← %s", txts[0].name)

    # 主影片：不複製，記路徑（episode 目錄或 footage 目錄裡最大的 mp4）
    def find_video(d: Path) -> Path | None:
        vids = sorted(
            [p for p in d.iterdir() if p.suffix.lower() == ".mp4"],
            key=lambda p: p.stat().st_size,
            reverse=True,
        )
        return vids[0] if vids else None

    video = find_video(ep_dir) or find_video(input_dir)
    if video is None:
        raise SystemExit(f"找不到主影片（{input_dir} 無 *.mp4）")
    (ep_dir / "out" / "main_video_path.txt").write_text(str(video), encoding="utf-8")

    wav = ep_dir / "aroll-audio.wav"
    if not wav.exists():
        logger.info("抽 PCM 音訊: %s → %s", video.name, wav.name)
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-vn",
             "-acodec", "pcm_s16le", str(wav)],
            check=True,
        )
    return ep_dir, video


def stage_words(ep_dir: Path) -> Path:
    words = ep_dir / "words.json"
    if words.exists():
        return words
    logger.info("words.json 不存在 → WhisperX（GPU，Python310）")
    subprocess.run(
        [_gpu_python(), str(_REPO_ROOT / "scripts" / "run_whisperx_words.py"),
         str(ep_dir / "aroll-audio.wav"), "--output", str(words)],
        check=True,
    )
    return words


def _fmt_ts(t: float) -> str:
    m, s = divmod(t, 60)
    return f"{int(m):02d}:{s:05.2f}"


def write_qc_report(ep_dir: Path, plan, report: dict, markers, decisions: list[dict]) -> Path:
    lines = [
        f"# mistake-removal QC — {ep_dir.name}",
        "",
        "## 驗證（DoD：保留文字≈逐字稿、每段稿只覆蓋一次、無停頓）",
        "",
        "```json",
        json.dumps(report, ensure_ascii=False, indent=1),
        "```",
        "",
        f"## NG markers（{len(markers)}）與區塊分類",
        "",
    ]
    for b in plan.blocks:
        if b.classification == "noise":
            continue
        lines.append(
            f"- [{b.classification} 稿解釋比例={b.explain_frac}] "
            f"{_fmt_ts(b.t0)}–{_fmt_ts(b.t1)}：{b.text[:60]}"
        )
    lines += ["", "## 裁決（adjudications.json 重放）", ""]
    for d in decisions:
        lines.append(f"- clap @ {d['clap']}s → cut {d['cut']}：{d['reason']}")
    if plan.adjudications:
        lines += ["", "## ⚠️ 未裁決項（本輪已中止）", ""]
        for a in plan.adjudications:
            lines.append(f"- {json.dumps(a, ensure_ascii=False)}")
    lines += ["", "## Warnings", ""]
    for w in plan.warnings:
        lines.append(f"- {w}")
    lines += ["", f"## 保留段（{len(plan.kept_segments)}）", ""]
    for i, (s, e) in enumerate(plan.kept_segments, 1):
        lines.append(f"- [{i:02d}] {_fmt_ts(s)} → {_fmt_ts(e)}（{e - s:.2f}s）")
    out = ep_dir / "out" / "cleanup_qc.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="mistake-removal 一鍵產線（cleanup v2）")
    parser.add_argument("input", help="footage 資料夾或 episode 目錄")
    parser.add_argument("--no-resolve", action="store_true", help="不建 Resolve timeline")
    parser.add_argument("--tail-policy", default="script-end", choices=["script-end", "keep-all"])
    parser.add_argument("--max-gap", type=float, default=0.5, help="停頓收斂閾值（秒）")
    args = parser.parse_args(argv)

    from agents.brook.script_video.cleanup.clap_impulse import detect_claps, merge_ng_markers
    from agents.brook.script_video.cleanup.cuts import CutPoint
    from agents.brook.script_video.cleanup.script_align import load_words
    from agents.brook.script_video.cleanup import script_coverage as sc

    ep_dir, video = stage_episode(Path(args.input))
    stage_words(ep_dir)

    words = load_words(ep_dir / "words.json")
    script_path = next(
        p for n in ("script.txt", "script.md") if (p := ep_dir / n).exists()
    )
    script = script_path.read_text(encoding="utf-8")
    wav = ep_dir / "aroll-audio.wav"
    with wave.open(str(wav), "rb") as wf:
        total = wf.getnframes() / wf.getframerate()

    claps = detect_claps(wav)
    markers = merge_ng_markers(wav, claps)
    (ep_dir / "out" / "claps_v2.json").write_text(
        json.dumps(
            {"claps": [c.time_sec for c in claps],
             "markers": [list(m.clap_times) for m in markers]},
            indent=1,
        ),
        encoding="utf-8",
    )

    decisions: list[dict] = []
    adj_path = ep_dir / "adjudications.json"
    if adj_path.exists():
        decisions = json.loads(adj_path.read_text(encoding="utf-8"))["decisions"]
    manual = [
        CutPoint(type="ripple-delete", start_sec=d["cut"][0], end_sec=d["cut"][1],
                 reason="adjudicated", confidence=1.0)
        for d in decisions
    ]

    plan = sc.build_clean_plan(
        words, script,
        total_duration_sec=total,
        ng_markers=markers,
        manual_cuts=manual,
        max_gap_sec=args.max_gap,
        tail_policy=args.tail_policy,
    )
    report = sc.verify_plan(plan, words, markers)
    qc = write_qc_report(ep_dir, plan, report, markers, decisions)

    if plan.adjudications:
        pending = ep_dir / "out" / "pending_adjudications.json"
        pending.write_text(
            json.dumps({"pending": plan.adjudications}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        logger.error(
            "有 %d 個待裁決拍手（%s）— 請補 adjudications.json 後重跑",
            len(plan.adjudications), pending,
        )
        return 3

    (ep_dir / "transcript.srt").write_text(sc.build_srt(plan, words), encoding="utf-8")
    (ep_dir / "out" / "clean_segments.json").write_text(
        json.dumps(
            {"fps": None, "total": total,
             "segments": [[round(s, 4), round(e, 4)] for s, e in plan.kept_segments]},
            indent=1,
        ),
        encoding="utf-8",
    )

    summary = {
        "episode": ep_dir.name,
        "verify": report,
        "qc_report": str(qc),
        "warnings": len(plan.warnings),
    }

    if not args.no_resolve:
        r = subprocess.run(
            [_gpu_python(), str(_REPO_ROOT / "scripts" / "build_cleanup_timeline.py"),
             str(ep_dir), "--video", str(video), "--rebuild"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        print(r.stdout)
        if r.returncode != 0:
            logger.error("Resolve timeline 建立失敗:\n%s", r.stderr[-2000:])
            summary["resolve"] = "FAILED"
        else:
            summary["resolve"] = "ok"

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
