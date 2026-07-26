"""line-polish：套用斷句掃描的邊界修正（subagent 掃描 → 機械套用）。

修修 2026-07-26：「一個/小時」被拆開，整個字幕到處都有——cue_builder 的
停頓斷句被 WhisperX 拉長的詞尾時間戳廢掉（gap 恆 0），退化成字數切。
本 script 吃 subagent 全片掃描產出的邊界移動清單（highlights/line_moves_*.json），
把相鄰 cue 的邊界移動 ±N 字：**不動任何文字內容**，只移邊界；時間用詞級
真實時間戳重算。

防護：
- 移動後兩句皆 ≤22 字且非空
- 切點不落在《》「」內部
- 被移動的字的說話者必須與接收句一致（不跨說話者搬字）
- 任一防護失敗 → 跳過該移動並記錄

用法：
    python scripts/run_line_polish.py <episode>            # 套用 + 報告
    python scripts/run_line_polish.py <episode> --dry-run  # 只驗證不寫檔
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

logger = logging.getLogger("line_polish")

SRT_NAME = "transcript.srt"
MOVES_GLOB = "highlights/line_moves_*.json"
BACKUP_NAME = "subs/transcript_pre_line_polish.srt"
HARD_MAX = 22
_TS = re.compile(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})")
_OPEN, _CLOSE = "《「", "》」"


def _parse_srt(text: str) -> list[list]:
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
            [
                g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000,
                g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000,
                "\n".join(lines[2:]),
            ]
        )
    return cues


def _ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _join_words(ws: list[dict]) -> str:
    parts: list[str] = []
    for w in ws:
        text = w["word"]
        if parts and (text[0].isascii() or parts[-1][-1].isascii()):
            prev = parts[-1]
            if not (len(prev) == 1 and prev.isascii() and len(text) == 1 and text.isascii()):
                parts.append(" ")
        parts.append(text)
    return "".join(parts)


def _map_to_raw(corrected: str, raw: str, pos: int) -> int:
    """校正後文字的 char 位置 → raw 詞串接文字的對應位置。"""
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, corrected, raw).get_opcodes():
        if pos < i2 or (pos == i2 and tag == "equal"):
            if tag == "equal":
                return j1 + (pos - i1)
            return j1 if pos <= (i1 + i2) // 2 else j2
    return len(raw)


def _inside_brackets(text: str, pos: int) -> bool:
    depth = 0
    for i, ch in enumerate(text[:pos]):
        if ch in _OPEN:
            depth += 1
        elif ch in _CLOSE:
            depth = max(0, depth - 1)
    return depth > 0


def polish(episode_dir: Path, *, dry_run: bool = False) -> dict:
    from shared.speaker_assign import assign_word_speakers, detect_mic_tracks, load_envelopes

    srt_path = episode_dir / SRT_NAME
    cues = _parse_srt(srt_path.read_text(encoding="utf-8"))
    moves: list[dict] = []
    for f in sorted(episode_dir.glob(MOVES_GLOB)):
        moves.extend(json.loads(f.read_text(encoding="utf-8")).get("moves", []))
    moves.sort(key=lambda m: m["after_cue"])
    if not moves:
        return {"status": "no-moves"}

    meta = json.loads((episode_dir / "subs/words.json").read_text(encoding="utf-8"))
    words = meta["words"]
    mics = detect_mic_tracks(episode_dir / "Audio") if (episode_dir / "Audio").is_dir() else []
    speakers: list = [None] * len(words)
    if len(mics) >= 2:
        reference = episode_dir / "normalized.wav"
        speakers = assign_word_speakers(
            words, load_envelopes(mics, reference=reference if reference.exists() else None)
        )

    applied = 0
    skipped: list[str] = []
    for mv in moves:
        i = mv["after_cue"] - 1
        if not (0 <= i < len(cues) - 1):
            skipped.append(f"{mv['after_cue']}: cue 序號越界")
            continue
        a, b = cues[i], cues[i + 1]
        merged = a[2] + b[2]
        pos = len(a[2]) + int(mv["delta"])
        if not (0 < pos < len(merged)):
            skipped.append(f"{mv['after_cue']}: 移動後有一側為空")
            continue
        left, right = merged[:pos], merged[pos:]
        if len(left) > HARD_MAX or len(right) > HARD_MAX:
            skipped.append(f"{mv['after_cue']}: 移動後超過 {HARD_MAX} 字")
            continue
        if _inside_brackets(merged, pos):
            skipped.append(f"{mv['after_cue']}: 切點落在括號內")
            continue

        # 詞級時間重算：pair 時間範圍內的詞 → 邊界 char → 邊界詞
        idx = [
            k for k, w in enumerate(words) if w["start"] >= a[0] - 0.05 and w["end"] <= b[1] + 0.05
        ]
        if len(idx) < 2:
            skipped.append(f"{mv['after_cue']}: 找不到對應詞")
            continue
        raw = _join_words([words[k] for k in idx])
        raw_pos = _map_to_raw(merged, raw, pos)
        char_of_word: list[int] = []
        p = 0
        for j, k in enumerate(idx):
            if j > 0:
                probe = _join_words([words[kk] for kk in idx[: j + 1]])
                p = len(probe) - len(words[k]["word"])
            char_of_word.append(p)
        bword = next((j for j in range(len(idx)) if char_of_word[j] >= raw_pos), len(idx) - 1)
        if bword == 0:
            skipped.append(f"{mv['after_cue']}: 邊界詞回捲到句首")
            continue

        # 說話者防護：被移動的詞須與接收句的多數說話者一致
        delta = int(mv["delta"])
        if delta > 0:  # B 頭幾個詞搬給 A
            moved = [speakers[idx[j]] for j in range(len(idx)) if char_of_word[j] >= len(a[2]) - 1]
            moved = moved[: abs(delta)]
            host = [speakers[k] for k in idx if words[k]["end"] <= a[1] + 0.05]
        else:  # A 尾幾個詞搬給 B
            moved = [speakers[idx[j]] for j in range(len(idx)) if char_of_word[j] >= raw_pos]
            moved = moved[: abs(delta)]
            host = [speakers[k] for k in idx if words[k]["start"] >= b[0] - 0.05]
        moved_spk = {s for s in moved if s is not None}
        host_spk = {s for s in host if s is not None}
        if moved_spk and host_spk and not (moved_spk & host_spk):
            skipped.append(f"{mv['after_cue']}: 跨說話者搬字，拒絕")
            continue

        new_a_end = words[idx[bword - 1]]["end"]
        new_b_start = words[idx[bword]]["start"]
        cues[i] = [a[0], new_a_end, left]
        cues[i + 1] = [new_b_start, b[1], right]
        applied += 1

    if not dry_run and applied:
        shutil.copy2(srt_path, episode_dir / BACKUP_NAME)
        lines = [
            f"{seq}\n{_ts(s)} --> {_ts(e)}\n{t}\n" for seq, (s, e, t) in enumerate(cues, start=1)
        ]
        srt_path.write_text("\n".join(lines), encoding="utf-8")
    return {
        "status": "dry-run" if dry_run else "polished",
        "moves_total": len(moves),
        "applied": applied,
        "skipped": skipped,
    }


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="斷句邊界修正套用")
    parser.add_argument("episode", help="episode 資料夾")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    episode_dir = Path(args.episode)
    if not episode_dir.is_dir():
        logger.error(f"episode 資料夾不存在: {episode_dir}")
        return 1
    started = time.time()
    result = polish(episode_dir, dry_run=args.dry_run)
    result["elapsed_sec"] = round(time.time() - started, 1)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
