"""gap-fill：偵測 transcript.srt 的無字幕區段，重聽補回漏掉的對話。

修修 2026-07-25：「忽然有一個地方、一大段時間沒有字幕的，你可以偵測出來，
然後再去重聽嗎？」典型成因：幻覺 cue 被刪後底下的真實對話沒補回（如
42:13 的 15.9s 洞 = initial_prompt 汙染段 993–996 刪除後的遺留）。

流程：掃 cue 間隙（> 門檻秒數）→ 裁 normalized.wav 片段 → WhisperX 無
prompt 重辨識（避免再次汙染）→ align 字級時間戳 → cue_builder 斷句
（含說話者切分）→ house style 後處理 → 插回 transcript.srt 重編號。

- 重辨識結果快取在 subs/gap_fill.json（--apply 重生 transcript 後重跑
  gap-fill 免 GPU）；--relisten 強制重聽
- 內容太少的洞（開頭氣音等）自動跳過
- 冪等：補過的洞不再是洞，重跑無害

用法：
    python scripts/run_gap_fill.py "G:/footages/20260723 謝伯讓"
    python scripts/run_gap_fill.py <episode> --dry-run   # 只列洞不補
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

logger = logging.getLogger("gap_fill")

SRT_NAME = "transcript.srt"
CACHE_NAME = "subs/gap_fill.json"
BACKUP_NAME = "subs/transcript_pre_gap_fill.srt"
MIN_GAP_SEC = 3.0  # 小於此間隙視為正常語流
MIN_CONTENT_CHARS = 4  # 重聽出的字數低於此 → 洞裡沒實質內容，跳過
_TS = re.compile(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})")


def _parse_srt(text: str) -> list[tuple[float, float, str]]:
    cues = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [x for x in block.splitlines() if x.strip()]
        if len(lines) < 2:
            continue
        m = _TS.search(lines[1])
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        cues.append(
            (
                g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000,
                g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000,
                "\n".join(lines[2:]),
            )
        )
    return cues


def _ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _audio_duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return float((r.stdout or "0").strip() or 0)


def detect_gaps(cues: list[tuple[float, float, str]], total: float) -> list[tuple[float, float]]:
    """cue 覆蓋掃描 → (start, end) 無字幕區段清單（含頭尾）。"""
    gaps = []
    prev_end = 0.0
    for s, _e, _t in cues:
        if s - prev_end > MIN_GAP_SEC:
            gaps.append((prev_end, s))
        prev_end = max(prev_end, _e)
    if total > 0 and total - prev_end > MIN_GAP_SEC:
        gaps.append((prev_end, total))
    return gaps


def _relisten_gap(episode_dir: Path, start: float, end: float, model, align_bundle) -> list[dict]:
    """裁片段 → ASR（無 prompt）→ align → 回傳詞級 words（絕對時間）。"""
    import whisperx

    audio_path = episode_dir / "normalized.wav"
    with tempfile.TemporaryDirectory() as td:
        clip = Path(td) / "gap.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "quiet",
                "-ss",
                f"{start:.3f}",
                "-t",
                f"{end - start:.3f}",
                "-i",
                str(audio_path),
                "-ac",
                "1",
                "-ar",
                "16000",
                str(clip),
            ],
            check=True,
        )
        audio = whisperx.load_audio(str(clip))
        out = model.transcribe(audio, batch_size=4, language="zh")
        segs = out.get("segments") or []
        if not segs:
            return []
        align_model, align_meta = align_bundle
        aligned = whisperx.align(
            segs, align_model, align_meta, audio, "cuda", return_char_alignments=False
        )
        words = []
        for seg in aligned["segments"]:
            for w in seg.get("words", []):
                if w.get("start") is None or w.get("end") is None:
                    continue
                words.append(
                    {
                        "word": (w.get("word") or "").strip(),
                        "start": round(start + float(w["start"]), 3),
                        "end": round(start + float(w["end"]), 3),
                    }
                )
        return words


def fill_gaps(episode_dir: Path, *, dry_run: bool = False, relisten: bool = False) -> dict:
    from run_subtitle_gen import _speaker_fn

    from shared.transcriber import _process_srt_line

    srt_path = episode_dir / SRT_NAME
    if not srt_path.exists():
        raise FileNotFoundError(f"找不到 {srt_path}")
    cues = _parse_srt(srt_path.read_text(encoding="utf-8"))
    total = _audio_duration(episode_dir / "normalized.wav")
    gaps = detect_gaps(cues, total)
    report = {"status": "dry-run" if dry_run else "filled", "gaps": []}
    if not gaps:
        report["status"] = "no-gaps"
        return report
    if dry_run:
        report["gaps"] = [{"start": _ts(s), "end": _ts(e), "sec": round(e - s, 1)} for s, e in gaps]
        return report

    cache_path = episode_dir / CACHE_NAME
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}

    model = None
    align_bundle = None
    new_cues: list[tuple[float, float, str]] = []
    for s, e in gaps:
        key = f"{s:.2f}-{e:.2f}"
        if relisten or key not in cache:
            if model is None:
                import whisperx

                from shared.transcriber import _get_align_model

                model = whisperx.load_model(
                    "large-v3", "cuda", compute_type="float16", language="zh"
                )
                align_bundle = _get_align_model("zh")
            cache[key] = _relisten_gap(episode_dir, s, e, model, align_bundle)
            cache_path.parent.mkdir(exist_ok=True)
            cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
        words = cache[key]
        content_len = sum(len(w["word"]) for w in words)
        if content_len < MIN_CONTENT_CHARS:
            report["gaps"].append(
                {
                    "start": _ts(s),
                    "end": _ts(e),
                    "sec": round(e - s, 1),
                    "action": "skip（無實質內容）",
                }
            )
            continue

        from shared.cue_builder import segment_to_cues

        speaker_fn = _speaker_fn(episode_dir)
        speakers = speaker_fn(words) if speaker_fn else None
        gap_cues = segment_to_cues(words, speakers=speakers)
        gap_cues = [(cs, ce, _process_srt_line(text)) for cs, ce, text in gap_cues if text.strip()]
        new_cues.extend(gap_cues)
        report["gaps"].append(
            {
                "start": _ts(s),
                "end": _ts(e),
                "sec": round(e - s, 1),
                "action": f"filled +{len(gap_cues)} cues",
                "text": " ".join(t for _, _, t in gap_cues),
            }
        )

    if new_cues:
        shutil.copy2(srt_path, episode_dir / BACKUP_NAME)
        merged = sorted(cues + new_cues, key=lambda c: c[0])
        lines = [
            f"{seq}\n{_ts(cs)} --> {_ts(ce)}\n{t}\n"
            for seq, (cs, ce, t) in enumerate(merged, start=1)
        ]
        srt_path.write_text("\n".join(lines), encoding="utf-8")
    report["cues_added"] = len(new_cues)
    report["cues_total"] = len(cues) + len(new_cues)
    return report


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="無字幕區段偵測 + 重聽補洞")
    parser.add_argument("episode", help="episode 資料夾")
    parser.add_argument("--dry-run", action="store_true", help="只列出洞，不重聽不寫檔")
    parser.add_argument("--relisten", action="store_true", help="忽略快取強制重聽")
    args = parser.parse_args(argv)
    episode_dir = Path(args.episode)
    if not episode_dir.is_dir():
        logger.error(f"episode 資料夾不存在: {episode_dir}")
        return 1
    started = time.time()
    result = fill_gaps(episode_dir, dry_run=args.dry_run, relisten=args.relisten)
    result["elapsed_sec"] = round(time.time() - started, 1)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
