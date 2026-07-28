"""transcript-prose：校正後 transcript.srt → 去時間戳、分講者的人讀逐字稿。

修修 2026-07-28：字幕檔是給眼睛追畫面用的（短 cue、無標點、帶時間碼），
不是給人讀的。本 script 把它洗成一份**完整訪談稿**——一問一答各成一段、
段首標講者名、時間碼全部拿掉：

    **張修修**：今天是講什麼書？

    **謝伯讓**：今天是講那個 AI 對大腦的影響……

講者來自 ``shared/speaker_assign``（分軌 mic 能量 + Viterbi，零模型零 API），
與 ``run_speaker_split`` 同一套判定——差別是那支拿來切 cue，本支拿來合併成
「一個人連續講的話」。**軌序即 speaker index**（``detect_mic_tracks`` 依檔名
排序），沿用 ``run_short_director.py`` 的既有慣例 speaker0=主持人、
speaker1=來賓；不對就下 ``--swap``，不要用猜的。

標點怎麼來（全部由**實測停頓**推導，不新增也不改動任何一個字）：

| 訊號 | 來源 | 產出 |
|---|---|---|
| cue 內半形空格 | subtitle-gen house style（停頓 ≥0.3s）| 「，」|
| cue 間停頓 ≥0.6s | `cue_builder.est_gap` 實測 | 「。」|
| cue 間停頓 0.3–0.6s | 同上 | 「，」|
| cue 間停頓 <0.3s | 同上（純字數上限切開）| 直接相連 |
| 段末 8 字內含疑問詞 | 嗎／呢／什麼／怎麼… | 「？」|

**標點是停頓的翻譯，不是語意判斷**——長句中間的換氣會變成逗號，段落中間的
問句一律落成句號（只有段末才驗疑問詞）。要更準只能加一層語意 pass（本版刻意
不做：那會讓機器有機會動到校正過的文字）。不想要標點就下 ``--keep-spaces``。

講者邊界沿用 ``run_speaker_split`` 的結果，**它切錯的地方這裡也會錯**（典型：
句尾一兩個字被歸給下一位）。那是 Viterbi 判定層的事，本 script 不做二次猜測。

用法：
    python scripts/run_transcript_prose.py "G:/footages/20260723 謝伯讓" --guest 謝伯讓
    python scripts/run_transcript_prose.py <episode> --guest X --swap      # 軌序相反
    python scripts/run_transcript_prose.py <episode> --guest X --no-vault  # 只寫 episode 內
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger("transcript_prose")

SRT_NAME = "transcript.srt"
WORDS_NAME = "subs/words.json"
OUT_NAME = "transcript_prose.md"
VAULT_DIR = "KB/Raw/Podcasts"

_PAD = 0.05  # cue 邊界與詞時間戳的容差（秒）
_TS_RE = re.compile(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})")

# 停頓 → 標點門檻。與 shared/cue_builder 的 PAUSE_SPACE / PAUSE_FORCE_BREAK
# 對齊，才不會兩邊各自訂一套（cue 是照那兩個值切的）。
COMMA_GAP = 0.3
PERIOD_GAP = 0.6
# 同一人連講很久時的段落再分：停頓夠長 + 段落已夠厚才分，避免碎段。
PARA_GAP = 1.2
PARA_MIN_CHARS = 400

_TRAILING_PUNCT = "，。？！、；：…"
# 段末疑問偵測：訪談的主持人段落多半是問句，但語音沒有問號。只看**段落最後
# 幾個字**是否含疑問詞——放寬到整段會把「我不知道他在說什麼的那個東西」誤判。
_QUESTION_WORDS = ("嗎", "呢", "什麼", "怎麼", "怎樣", "為什麼", "哪裡", "哪個", "哪些", "多少")
_QUESTION_WINDOW = 8


def _parse_srt(text: str) -> list[tuple[float, float, str]]:
    cues: list[tuple[float, float, str]] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        m = _TS_RE.search(lines[1])
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        cues.append((start, end, " ".join(lines[2:]).strip()))
    return cues


def _cue_word_ranges(cues: list[tuple[float, float, str]], words: list[dict]) -> list[list[int]]:
    """每個 cue 涵蓋的詞 index（時間包含判定，與 run_speaker_split 同規則）。

    只靠時間、不比對文字——校正後的文字與 raw 詞不一定逐字對得上，時間軸則
    是共用的（校正只換字不動時間）。
    """
    out: list[list[int]] = []
    cursor = 0
    for start, end, _text in cues:
        while cursor > 0 and words[cursor - 1].get("end", 0) > start - _PAD:
            cursor -= 1  # 回退到可能落在本 cue 的第一個詞（cue 可能重疊）
        idx: list[int] = []
        i = cursor
        while i < len(words):
            w = words[i]
            if w.get("start") is None or w.get("end") is None:
                i += 1
                continue
            if w["start"] > end + _PAD:
                break
            if w["start"] >= start - _PAD and w["end"] <= end + _PAD:
                idx.append(i)
            i += 1
        if idx:
            cursor = idx[-1] + 1
        out.append(idx)
    return out


def _cue_speaker(idx: list[int], speakers: list[int | None], words: list[dict]) -> int | None:
    """cue 的講者 = 該 cue 內詞的**時長加權**多數決。

    speaker-split 跑過之後每個 cue 本來就只有一個人（混人的已被切開），
    加權多數決只是對「切不乾淨而保留原樣」的少數 cue 的保險。
    """
    weights: dict[int, float] = {}
    for i in idx:
        spk = speakers[i]
        if spk is None:
            continue
        w = words[i]
        dur = max(float(w["end"]) - float(w["start"]), 0.05)
        weights[spk] = weights.get(spk, 0.0) + dur
    if not weights:
        return None
    return max(weights.items(), key=lambda kv: kv[1])[0]


def _gap_between(prev_word: dict | None, next_word: dict | None) -> float:
    """兩詞間的實測停頓；任一端缺席回 0（不知道就不加標點）。"""
    if not prev_word or not next_word:
        return 0.0
    from shared.cue_builder import est_gap

    return est_gap(
        (prev_word["word"], float(prev_word["start"]), float(prev_word["end"])),
        (next_word["word"], float(next_word["start"]), float(next_word["end"])),
    )


def _spaces_to_commas(text: str) -> str:
    """CJK 之間的半形空格（subtitle-gen 的停頓標記）→ 「，」；英文空格保留。"""

    def repl(m: re.Match) -> str:
        return m.group(1) + "，" + m.group(2)

    # 兩側都是非 ASCII 才換（英文詞距、中英交界的空格都留著）
    return re.sub(r"([^\x00-\x7F])\s+([^\x00-\x7F])", repl, text)


def _append(buf: str, piece: str, sep: str) -> str:
    """接一段文字，順手把重複標點壓平（sep 為 '' 代表直接相連）。"""
    piece = piece.strip().lstrip(_TRAILING_PUNCT)
    if not piece:
        return buf
    if not buf:
        return piece
    base = buf.rstrip()
    if sep:
        base = base.rstrip(_TRAILING_PUNCT) + sep
    return base + piece


def _finish(text: str) -> str:
    """段落收尾：去掉尾端逗號類標點，補句號；末 8 字含疑問詞則補問號。"""
    text = text.rstrip().rstrip(_TRAILING_PUNCT)
    if not text:
        return ""
    tail = text[-_QUESTION_WINDOW:]
    if any(q in tail for q in _QUESTION_WORDS):
        return text + "？"
    return text + "。"


def build_paragraphs(
    cues: list[tuple[float, float, str]],
    cue_speakers: list[int | None],
    cue_ranges: list[list[int]],
    words: list[dict],
    *,
    keep_spaces: bool = False,
) -> list[tuple[int, str]]:
    """cue 序列 → [(speaker_index, 段落文字)]。純函式（不碰音檔），可直接測。

    同一講者連續的 cue 併成一段；講者換人一定換段；同一人講太久（停頓夠長
    且段落已 ≥PARA_MIN_CHARS）再分段。
    """
    paragraphs: list[tuple[int, str]] = []
    cur_spk: int | None = None
    buf = ""
    prev_word: dict | None = None

    for (_t0, _t1, raw_text), spk, idx in zip(cues, cue_speakers, cue_ranges):
        text = raw_text.strip()
        if not text:
            continue
        if not keep_spaces:
            text = _spaces_to_commas(text)
        spk = spk if spk is not None else cur_spk
        if spk is None:
            continue  # 開頭就無證據（純環境音）——沒有講者可掛，跳過

        first_word = words[idx[0]] if idx else None
        last_word = words[idx[-1]] if idx else prev_word
        gap = _gap_between(prev_word, first_word)

        if spk != cur_spk:
            if buf:
                paragraphs.append((cur_spk, _finish(buf)))
            cur_spk, buf = spk, text
        elif gap >= PARA_GAP and len(buf) >= PARA_MIN_CHARS:
            paragraphs.append((cur_spk, _finish(buf)))
            buf = text
        else:
            sep = "。" if gap >= PERIOD_GAP else ("，" if gap >= COMMA_GAP else "")
            buf = _append(buf, text, sep)

        prev_word = last_word or prev_word

    if buf and cur_spk is not None:
        paragraphs.append((cur_spk, _finish(buf)))
    return [(s, t) for s, t in paragraphs if t]


def render_markdown(paragraphs: list[tuple[int, str]], names: dict[int, str]) -> str:
    return "\n\n".join(f"**{names.get(spk, f'講者{spk}')}**：{text}" for spk, text in paragraphs)


def _load_speakers(episode_dir: Path, words: list[dict]) -> list[int | None]:
    from shared.speaker_assign import assign_word_speakers, detect_mic_tracks, load_envelopes

    audio_dir = next(
        (d for d in episode_dir.iterdir() if d.is_dir() and d.name.lower() == "audio"), None
    )
    if audio_dir is None:
        raise FileNotFoundError(f"{episode_dir} 沒有 Audio/ 分軌資料夾——無法判定講者")
    mics = detect_mic_tracks(audio_dir)
    if len(mics) < 2:
        raise SystemExit(
            "分軌 mic 不足兩軌，無法判定講者。"
            "（單人錄音沒有「分講者」可言；混音單軌請先取得分軌檔）"
        )
    logger.info(f"mic 軌序（= speaker index）: {[p.name for p in mics]}")
    reference = episode_dir / "normalized.wav"
    return assign_word_speakers(
        words, load_envelopes(mics, reference=reference if reference.exists() else None)
    )


def run(
    episode_dir: Path,
    *,
    host: str,
    guest: str,
    swap: bool = False,
    keep_spaces: bool = False,
    write_vault: bool = True,
    slug: str | None = None,
    dry_run: bool = False,
) -> dict:
    srt_path = episode_dir / SRT_NAME
    words_path = episode_dir / WORDS_NAME
    if not srt_path.exists():
        raise FileNotFoundError(f"找不到 {srt_path}（先跑 subtitle-correct）")
    if not words_path.exists():
        raise FileNotFoundError(f"找不到 {words_path}（先跑 subtitle-gen）")

    words = json.loads(words_path.read_text(encoding="utf-8"))["words"]
    cues = _parse_srt(srt_path.read_text(encoding="utf-8"))
    speakers = _load_speakers(episode_dir, words)

    ranges = _cue_word_ranges(cues, words)
    cue_speakers = [_cue_speaker(idx, speakers, words) for idx in ranges]
    paragraphs = build_paragraphs(cues, cue_speakers, ranges, words, keep_spaces=keep_spaces)
    if not paragraphs:
        raise SystemExit("洗不出任何段落——檢查 transcript.srt 與 words.json 是否同一集")

    names = {0: guest, 1: host} if swap else {0: host, 1: guest}
    body = render_markdown(paragraphs, names)

    out_path = episode_dir / OUT_NAME
    vault_rel: str | None = None
    if not dry_run:
        out_path.write_text(body + "\n", encoding="utf-8", newline="\n")
        if write_vault:
            vault_rel = _write_vault(
                body, episode_dir=episode_dir, host=host, guest=guest, slug=slug
            )

    counts: dict[str, int] = {}
    for spk, text in paragraphs:
        counts[names.get(spk, str(spk))] = counts.get(names.get(spk, str(spk)), 0) + len(text)
    return {
        "status": "dry-run" if dry_run else "written",
        "cues": len(cues),
        "paragraphs": len(paragraphs),
        "chars_by_speaker": counts,
        "out": None if dry_run else str(out_path),
        "vault": vault_rel,
        # 軌序對不對只有修修看得出來——把兩位的第一句印出來供一眼驗收
        "first_line": {
            names.get(spk, str(spk)): text[:40]
            for spk, text in _first_per_speaker(paragraphs).items()
        },
    }


def _first_per_speaker(paragraphs: list[tuple[int, str]]) -> dict[int, str]:
    seen: dict[int, str] = {}
    for spk, text in paragraphs:
        seen.setdefault(spk, text)
    return seen


def _write_vault(
    body: str, *, episode_dir: Path, host: str, guest: str, slug: str | None
) -> str | None:
    from datetime import date

    from shared.config import get_vault_path
    from shared.obsidian_writer import write_page

    # vault root 不存在就**停**，不要讓 write_page 的 mkdir(parents=True) 沿著
    # 一條錯路徑生出影子目錄。典型成因：在 sibling worktree 跑（.env 只在主倉庫，
    # VAULT_PATH 讀不到）→ 退回 config.yaml 的 VPS 路徑 /home/... → Windows 上
    # 變成 E:\home\...。2026-06-13 已經被這條坑過一次。
    vault_root = get_vault_path()
    if not vault_root.is_dir():
        raise SystemExit(
            f"vault 路徑不存在：{vault_root}\n"
            "  在 sibling worktree 跑的話 .env 讀不到（VAULT_PATH 只在主倉庫）——\n"
            "  補 .env、設 VAULT_PATH 環境變數，或加 --no-vault 只寫 episode 內。"
        )

    name = slug or re.sub(r"\s+", "-", episode_dir.name.strip())
    rel = f"{VAULT_DIR}/{name}.md"
    write_page(
        rel,
        frontmatter={
            "title": episode_dir.name,
            "type": "podcast_transcript",
            "source_type": "podcast",
            "host": host,
            "guest": guest,
            "source": str(episode_dir / SRT_NAME),
            "generated": str(date.today()),
            "generated_by": "run_transcript_prose",
        },
        body=body,
    )
    logger.info(f"vault：已寫入 {rel}")
    return rel


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="校正後 SRT → 分講者的人讀逐字稿")
    parser.add_argument("episode", help="episode 資料夾")
    parser.add_argument("--host", default="張修修", help="主持人姓名（預設 張修修）")
    parser.add_argument("--guest", required=True, help="來賓姓名（不猜，一定要給）")
    parser.add_argument("--swap", action="store_true", help="軌序相反（mic 第一軌是來賓時用）")
    parser.add_argument("--keep-spaces", action="store_true", help="保留停頓空格，不轉成標點")
    parser.add_argument("--no-vault", action="store_true", help="只寫 episode 內，不寫 vault")
    parser.add_argument("--slug", default=None, help=f"vault 檔名（預設 = 資料夾名）→ {VAULT_DIR}/")
    parser.add_argument("--dry-run", action="store_true", help="只報告不寫檔")
    args = parser.parse_args(argv)

    episode_dir = Path(args.episode)
    if not episode_dir.is_dir():
        logger.error(f"episode 資料夾不存在: {episode_dir}")
        return 1
    started = time.time()
    result = run(
        episode_dir,
        host=args.host,
        guest=args.guest,
        swap=args.swap,
        keep_spaces=args.keep_spaces,
        write_vault=not args.no_vault,
        slug=args.slug,
        dry_run=args.dry_run,
    )
    result["elapsed_sec"] = round(time.time() - started, 1)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
