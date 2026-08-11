"""speaker-split：校正後 transcript.srt 在「說話者變更處」切開（事後處理）。

修修 2026-07-25：不同人說的話塞在同一個 cue 相當難閱讀。本 script 用
分軌 mic 能量（shared/speaker_assign）判定每個詞是誰說的，把混人 cue
切開——**不動任何校正後的文字內容**，只切 cue、時間用詞級真實時間戳。

設計為 subtitle-correct `--apply` 之後的冪等步驟：QC 裁決 → --apply
重產 transcript.srt → 再跑本 script → --refresh-subtitles。已切乾淨的
cue（單一說話者）不受影響，重跑無害。

用法：
    python scripts/run_speaker_split.py "G:/footages/20260723 謝伯讓"
    python scripts/run_speaker_split.py <episode> --dry-run   # 只報告不寫檔
"""

from __future__ import annotations

import argparse
import difflib
import json
import logging
import re
import shutil
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger("speaker_split")

SRT_NAME = "transcript.srt"
WORDS_NAME = "subs/words.json"
BACKUP_NAME = "subs/transcript_pre_speaker_split.srt"
_PAD = 0.05  # cue 邊界與詞時間戳的容差（秒）
# 切出來的碎片下限：短於 1 frame（30fps ≈ 0.033s）的 cue 會被 Resolve 靜默丟棄
# ——整句字幕就此消失。安吉集實例：「…真實最重要」在說話者變更處切開，
# 尾巴「重要」只有 0.02s，timeline item 數比 SRT 少 1。切不出合格碎片就不切。
_MIN_PIECE_SEC = 0.12
_TS_RE = re.compile(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})")
_OPEN, _CLOSE = "《「", "》」"


def _parse_srt(text: str) -> list[tuple[float, float, str]]:
    cues = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        m = _TS_RE.search(lines[1] if len(lines) > 1 else "")
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        cues.append((start, end, "\n".join(lines[2:])))
    return cues


def _ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _join_words(ws: list[dict]) -> str:
    """raw 詞串接（與 cue_builder._join_tokens 同規則：CJK 相連、ASCII 加空格）。"""
    parts: list[str] = []
    for w in ws:
        text = w["word"]
        if parts and (text[0].isascii() or parts[-1][-1].isascii()):
            prev = parts[-1]
            if not (len(prev) == 1 and prev.isascii() and len(text) == 1 and text.isascii()):
                parts.append(" ")
        parts.append(text)
    return "".join(parts)


def _map_position(raw: str, corrected: str, raw_pos: int) -> int:
    """raw 文字的 char 位置 → 校正後文字的對應位置（difflib opcode 映射）。"""
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, raw, corrected).get_opcodes():
        if raw_pos < i2 or (raw_pos == i2 and tag == "equal"):
            if tag == "equal":
                return j1 + (raw_pos - i1)
            return j1 if raw_pos <= (i1 + i2) // 2 else j2
    return len(corrected)


def _snap_outside_brackets(text: str, pos: int) -> int:
    """切點若落在《…》「…」內部，移到括號邊界（就近側）。"""
    depth = 0
    spans: list[tuple[int, int]] = []
    open_at = -1
    for i, ch in enumerate(text):
        if ch in _OPEN:
            if depth == 0:
                open_at = i
            depth += 1
        elif ch in _CLOSE:
            depth = max(0, depth - 1)
            if depth == 0 and open_at >= 0:
                spans.append((open_at, i + 1))
                open_at = -1
    for a, b in spans:
        if a < pos < b:
            return a if pos - a <= b - pos else b
    return pos


def _jieba_boundaries(text: str) -> set[int]:
    from shared.cue_builder import jieba_boundaries  # 與 run_transcript_prose 共用同一份

    return jieba_boundaries(text)


def _snap_word_boundary(pos: int, bounds: set[int]) -> int:
    """切點移到最近的 jieba 詞邊界（絕不切在詞中間；等距取左）。"""
    if pos in bounds:
        return pos
    left = max((b for b in bounds if b < pos), default=0)
    right = min((b for b in bounds if b > pos), default=pos)
    return left if pos - left <= right - pos else right


def _runs(speakers: list[int | None]) -> list[tuple[int, int]]:
    """詞級 speaker 序列 → 連續同人區段 [i, j)（None 併入前一段）。"""
    runs: list[tuple[int, int]] = []
    cur_spk: int | None = None
    start = 0
    for i, s in enumerate(speakers):
        if s is None or s == cur_spk:
            continue
        if cur_spk is not None:
            runs.append((start, i))
            start = i
        cur_spk = s
    runs.append((start, len(speakers)))
    return runs


def split_episode(episode_dir: Path, *, dry_run: bool = False) -> dict:
    from shared.speaker_assign import assign_word_speakers, detect_mic_tracks, load_envelopes

    srt_path = episode_dir / SRT_NAME
    words_path = episode_dir / WORDS_NAME
    if not srt_path.exists():
        raise FileNotFoundError(f"找不到 {srt_path}（先跑 subtitle-correct）")
    if not words_path.exists():
        raise FileNotFoundError(f"找不到 {words_path}（先跑 subtitle-gen）")

    audio_dir = next(
        (d for d in episode_dir.iterdir() if d.is_dir() and d.name.lower() == "audio"), None
    )
    if audio_dir is None:
        raise FileNotFoundError(f"{episode_dir} 沒有 Audio/ 分軌資料夾")
    mics = detect_mic_tracks(audio_dir)
    if len(mics) < 2:
        raise SystemExit("分軌 mic 不足兩軌，無法做說話者切分")

    meta = json.loads(words_path.read_text(encoding="utf-8"))
    words = meta["words"]
    # 詞時間戳的參考音檔（stem 偏移補償基準）
    reference = episode_dir / "normalized.wav"
    if not reference.exists():
        reference = Path(meta.get("audio", "")) if meta.get("audio") else None
        reference = reference if reference and reference.exists() else None
    speakers = assign_word_speakers(words, load_envelopes(mics, reference=reference))

    cues = _parse_srt(srt_path.read_text(encoding="utf-8"))
    out: list[tuple[float, float, str]] = []
    split_count = 0
    for start, end, text in cues:
        idx = [
            i for i, w in enumerate(words) if w["start"] >= start - _PAD and w["end"] <= end + _PAD
        ]
        cue_speakers = [speakers[i] for i in idx]
        runs = _runs(cue_speakers)
        if len(runs) <= 1 or not text.strip():
            out.append((start, end, text))
            continue

        raw = _join_words([words[i] for i in idx])
        char_of_word: list[int] = []  # 每個詞在 raw 內的起始 char 位置
        pos = 0
        for k, i in enumerate(idx):
            if k > 0:
                probe = _join_words([words[j] for j in idx[: k + 1]])
                pos = len(probe) - len(words[i]["word"])
            char_of_word.append(pos)

        bounds = _jieba_boundaries(text)
        pieces: list[tuple[float, float, str]] = []
        prev_cut = 0
        ok = True
        for a, b in runs:
            if b < len(idx):
                cut = _map_position(raw, text, char_of_word[b])
                cut = _snap_word_boundary(cut, bounds)
                cut = _snap_outside_brackets(text, cut)
            else:
                cut = len(text)
            piece = text[prev_cut:cut].strip()
            if not piece:
                ok = False
                break
            p_start, p_end = words[idx[a]]["start"], words[idx[b - 1]]["end"]
            if p_end - p_start < _MIN_PIECE_SEC:
                ok = False  # 碎片過短 → Resolve 會丟掉整句，保留原 cue
                break
            pieces.append((p_start, p_end, piece))
            prev_cut = cut
        if not ok or len(pieces) < 2:
            out.append((start, end, text))  # 切不乾淨就保留原樣（保守）
            continue
        out.extend(pieces)
        split_count += 1

    if not dry_run:
        backup = episode_dir / BACKUP_NAME
        shutil.copy2(srt_path, backup)
        lines = [
            f"{seq}\n{_ts(s)} --> {_ts(e)}\n{t}\n" for seq, (s, e, t) in enumerate(out, start=1)
        ]
        srt_path.write_text("\n".join(lines), encoding="utf-8")
    return {
        "status": "dry-run" if dry_run else "split",
        "cues_in": len(cues),
        "cues_out": len(out),
        "cues_split": split_count,
        "mics": [p.name for p in mics],
    }


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="校正後 SRT 依說話者切分")
    parser.add_argument("episode", help="episode 資料夾")
    parser.add_argument("--dry-run", action="store_true", help="只報告不寫檔")
    args = parser.parse_args(argv)
    episode_dir = Path(args.episode)
    if not episode_dir.is_dir():
        logger.error(f"episode 資料夾不存在: {episode_dir}")
        return 1
    started = time.time()
    result = split_episode(episode_dir, dry_run=args.dry_run)
    result["elapsed_sec"] = round(time.time() - started, 1)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
