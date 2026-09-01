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
| 停頓 ≥0.6s | `cue_builder.est_gap` 實測 | 「。」|
| 停頓 0.3–0.6s（含 cue 內的半形空格）| 同上 | 「，」|
| 停頓 <0.3s | 同上（cue 是字數上限切的）| 直接相連 |
| 段末 8 字內含疑問詞 | 嗎／呢／什麼／怎麼… | 「？」|

**停頓不等於句讀**——這是兩輪 review 反覆撞到的根因，標點要再過三道語法閘：

1. **只下在 jieba 詞邊界**（`cue_builder.jieba_boundaries`，與 run_speaker_split
   共用）。0.3 秒的換氣常落在詞中間：「反方的意｜見」「不｜過」「認知儲｜備」，
   下了逗號讀者會卡住，「不，過」甚至被讀成否定。
2. **黏著詞組中間連逗號都不行**（`_BIND_FORWARD` / `_BIND_BACKWARD`）。jieba 認為
   「生命的｜起源」是合法邊界，於是第二版出現「對生命的，起源」「蠻高階，的
   認知能力」「還是比，認真做」。
3. **連接詞後面不給句號**（`_NO_PERIOD_AFTER`），避免「我們會因為。」；但連續
   `PERIOD_FLOOR_CHARS` 字沒有句號就強制留一個，否則整段變成逗號長串。

分段也只切在**合法的句號位置**——不然會出現「所以他覺得。」／下一段「人就是
命中註定」這種腰斬。

**標點仍然是停頓的翻譯，不是語意判斷。** 有些句子只有懂意思才斷得對，例如
「你反芻了過去｜好的事情我們要延續」——講者在「好的事情」後面換氣，機器照著
停頓斷就變成「我們要延續不好的事情」，意思剛好相反。這類要靠語意 pass，本
script 不做。不想要標點就下 ``--keep-spaces``。

講者邊界沿用 ``run_speaker_split`` 的結果，**它切錯的地方這裡也會錯**。句尾一兩
個字被歸給下一位是可接受的（修修 2026-07-28：「只要問題的本體跟回答的本體有
切出來就好」），但**整句被吞掉**就不行——搶話密集處 Viterbi 切不開。本 script
不做二次猜測，改成把可疑的 cue 標出來（`transcript_prose_suspect.json`，判準是
兩軌 mic 的能量差）。訪談結束後的收工閒聊是重災區，用 ``--outtakes-from``
另存 `transcript_outtakes.md`，不混進主稿。

用法：
    python scripts/run_transcript_prose.py "G:/footages/20260723 謝伯讓" --guest 謝伯讓
    python scripts/run_transcript_prose.py <episode> --guest X --swap      # 軌序相反
    python scripts/run_transcript_prose.py <episode> --guest X --no-vault  # 只寫 episode 內
    python scripts/run_transcript_prose.py <episode> --guest X --outtakes-from 1:01:36
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

from shared.episode_transcript import resolve_transcript_srt  # noqa: E402

logger = logging.getLogger("transcript_prose")

SRT_NAME = "transcript.srt"  # legacy 位置；實際來源見 shared.episode_transcript
WORDS_NAME = "subs/words.json"
OUT_NAME = "transcript_prose.md"
MIXED_NAME = "transcript_prose_suspect.json"
OUTTAKES_NAME = "transcript_outtakes.md"
VAULT_DIR = "KB/Raw/Podcasts"

_PAD = 0.05  # cue 邊界與詞時間戳的容差（秒）
_TS_RE = re.compile(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})")

# 停頓 → 標點門檻。與 shared/cue_builder 的 PAUSE_SPACE / PAUSE_FORCE_BREAK
# 對齊，才不會兩邊各自訂一套（cue 是照那兩個值切的）。
COMMA_GAP = 0.3
PERIOD_GAP = 0.6
# 同一人連講很久時的段落再分：停頓夠長 + 段落已夠厚才分，避免碎段。
# 第一版 1.2s/400 字太保守——174 段裡只觸發 2 次，留下 1143 / 905 / 692 字
# 的文字牆（review agent 三份報告都點名）。放寬到真正的換氣點會斷。
PARA_GAP = 0.8
PARA_MIN_CHARS = 300
HARD_PARA_CHARS = 600  # 超過這個長度，普通句末停頓（PERIOD_GAP）就分段

_TRAILING_PUNCT = "，。？！、；：…"
# 段末疑問偵測：訪談的主持人段落多半是問句，但語音沒有問號。只看**段落最後
# 幾個字**是否含疑問詞——放寬到整段會把「我不知道他在說什麼的那個東西」誤判。
_QUESTION_WORDS = ("嗎", "呢", "什麼", "怎麼", "怎樣", "為什麼", "哪裡", "哪個", "哪些", "多少")
_QUESTION_WINDOW = 8

# 連接詞後面不可能是句號（還在等下半句）——降級成逗號，避免
# 「我們會因為。」「大家都很關心但。」這種把句子腰斬的殘句。
_NO_PERIOD_AFTER = tuple(
    "因為 所以 但是 可是 不過 而且 然後 如果 雖然 還有 或是 還是 就是 只是 但 而 那".split()
)
# 這些詞與**後面的字**黏成一個詞組，中間連逗號都不能有（第二輪 review：
# jieba 認為「生命的｜起源」是合法詞邊界，於是出現「對生命的，起源」
# 「蠻高階，的認知能力」「還是比，認真做」）。只收語法上必然黏著的：
# 結構助詞、介詞／處置詞、程度副詞、繫詞——「對」這種既是介詞又是應答詞
# 的**刻意不收**（收了會把「對，我兒子喜歡騎」的逗號也吃掉）。
_BIND_FORWARD = tuple("的 地 得 在 把 被 讓 從 跟 和 或 比 是 很 太 更 最 真 蠻".split())
# 這些字黏著**前面的字**，前面不能有標點（「蠻高階，的認知能力」的另一半）。
_BIND_BACKWARD = tuple("的 地 得 了 嗎 呢 吧 們 個 過 著".split())
# 連接詞降級的保底：連續這麼多字沒有句號就強制留一個，否則整段變成
# 三百字的逗號長串（第二輪 review 抓到我上一版改過頭）。
PERIOD_FLOOR_CHARS = 80

# 講者判定可疑的 cue：兩軌 mic 的能量差太小 = 兩人同時在講，Viterbi 只能
# 挑一個。門檻 6dB 是實測定的——全片 |margin| 中位數 18.6dB，而第二輪
# review 用音檔獨立驗出的 9 個錯判 cue 全部 ≤6.4dB（門檻取 7 全含）。
MIXED_MARGIN_DB = 7.0


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


def _cue_weights(idx: list[int], speakers: list[int | None], words: list[dict]) -> dict[int, float]:
    """cue 內各講者的**發話時長**（秒）。"""
    weights: dict[int, float] = {}
    for i in idx:
        spk = speakers[i]
        if spk is None:
            continue
        w = words[i]
        dur = max(float(w["end"]) - float(w["start"]), 0.05)
        weights[spk] = weights.get(spk, 0.0) + dur
    return weights


def _cue_speaker(idx: list[int], speakers: list[int | None], words: list[dict]) -> int | None:
    """cue 的講者 = 該 cue 內詞的**時長加權**多數決。

    speaker-split 跑過之後每個 cue 本來就只有一個人（混人的已被切開），
    加權多數決只是對「切不乾淨而保留原樣」的少數 cue 的保險。
    """
    weights = _cue_weights(idx, speakers, words)
    if not weights:
        return None
    return max(weights.items(), key=lambda kv: kv[1])[0]


def _forward_fill(cue_speakers: list[int | None]) -> list[int | None]:
    """無證據的 cue 承接前一個 cue 的講者。

    **必須在切 outtakes 之前做完**：切片後每一段是獨立重跑的，若切點後的
    第一個 cue 剛好沒有講者證據，新切片裡沒有「前一位」可承接，整句會被
    丟掉（實測掉了「OK哦哦時間可以嗎」9 個字）。
    """
    out: list[int | None] = []
    last: int | None = None
    for spk in cue_speakers:
        last = spk if spk is not None else last
        out.append(last)
    return out


def cue_margins_db(cues: list[tuple[float, float, str]], envelopes) -> list[float | None]:
    """每個 cue 期間兩軌 mic 的能量差（各自扣掉自身噪音底）。|值| 越小＝越可疑。

    第一版量的是「Viterbi 判完之後詞級標籤的分歧」——那是**機器的自我懷疑**，
    不是聲音的模糊度：整個 cue 被判給錯的人時分歧為 0，正好把最嚴重的一類
    錯誤系統性排除掉（第二輪 review 用音檔獨立驗證，涵蓋率 <10%、對真正的
    錯誤 0 命中）。改成直接量兩軌能量差：全片 |margin| 中位數 18.6dB，而那
    9 個已知錯判 cue 全部落在 6.4dB 內。
    """
    import numpy as np

    from shared.speaker_assign import FRAME_SEC

    db = 20 * np.log10(envelopes + 1e-9)
    floors = np.percentile(db, 10, axis=1)
    out: list[float | None] = []
    for t0, t1, _text in cues:
        f0 = max(0, int(t0 / FRAME_SEC))
        f1 = min(db.shape[1], int(t1 / FRAME_SEC) + 1)
        if f1 <= f0:
            out.append(None)
            continue
        level = db[:, f0:f1].mean(axis=1) - floors
        out.append(float(level[0] - level[1]))
    return out


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
    """CJK 之間的半形空格（停頓標記）→ 落在詞邊界才給「，」，否則直接接起來。

    停頓不等於句讀：0.3 秒的換氣常常落在詞的中間（「反方的意｜見」「不｜過」
    「認知儲｜備」）。第一版無條件下逗號，讀者會在這些點卡住甚至讀反意思，
    所以改成先問 jieba：這個位置是不是詞邊界？不是就不下標點。
    英文詞距與中英交界的空格一律不動。
    """
    marks = list(re.finditer(r"(?<=[^\x00-\x7F])[ \t]+(?=[^\x00-\x7F])", text))
    if not marks:
        return text

    segments: list[str] = []
    cuts: list[int] = []  # 每個空格在「拿掉空格後」的字串中的 char 位置
    pos = 0
    for m in marks:
        segments.append(text[pos : m.start()])
        cuts.append(sum(len(s) for s in segments))
        pos = m.end()
    segments.append(text[pos:])

    from shared.cue_builder import jieba_boundaries

    bounds = jieba_boundaries("".join(segments))
    out: list[str] = []
    for k, seg in enumerate(segments):
        out.append(seg)
        if k < len(cuts):
            ok = cuts[k] in bounds and not _binds(seg, segments[k + 1])
            out.append("，" if ok else "")
    return "".join(out)


def _binds(before: str, after: str) -> bool:
    """這兩段文字是不是黏成一個詞組（中間不能有任何標點）。"""
    tail = before.rstrip().rstrip(_TRAILING_PUNCT)
    head = after.lstrip().lstrip(_TRAILING_PUNCT)
    return any(tail.endswith(w) for w in _BIND_FORWARD) or any(
        head.startswith(w) for w in _BIND_BACKWARD
    )


def _chars_since_period(buf: str) -> int:
    """距離上一個句末標點幾個字（沒有句末標點就是整段長度）。"""
    last = max(buf.rfind(c) for c in "。？！")
    return len(buf) - last - 1


def _separator(buf: str, nxt: str, gap: float) -> str:
    """兩個 cue 之間該放什麼標點：停頓長度決定，再過兩道語法閘。"""
    if gap >= PERIOD_GAP:
        sep = "。"
    elif gap >= COMMA_GAP:
        sep = "，"
    else:
        return ""
    if _binds(buf, nxt):
        return ""  # 詞組被黏住，這裡連逗號都不能有
    tail = buf.rstrip().rstrip(_TRAILING_PUNCT)
    if sep == "。" and any(tail.endswith(w) for w in _NO_PERIOD_AFTER):
        # 連接詞後面降級成逗號——但太久沒有句號就強制留一個，
        # 否則整段變成三百字的逗號長串（上一版改過頭）。
        sep = "。" if _chars_since_period(buf) >= PERIOD_FLOOR_CHARS else "，"
    return sep


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
    # 被對方接話打斷、話沒講完就換人（「…創意跟」「…那」「…然後」）——
    # 補句號等於幫講者把話講完了，寧可留空（第二輪 review：「創意跟。」）。
    if any(text.endswith(w) for w in _NO_PERIOD_AFTER + _BIND_FORWARD):
        return text
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

        sep = _separator(buf, text, gap) if buf else ""
        long_enough = (gap >= PARA_GAP and len(buf) >= PARA_MIN_CHARS) or (
            len(buf) >= HARD_PARA_CHARS
        )
        if spk != cur_spk:
            if buf:
                paragraphs.append((cur_spk, _finish(buf)))
            cur_spk, buf = spk, text
        elif sep == "。" and long_enough:
            # **只切在句末**：分段點必須是一個合法的句號位置，否則會出現
            # 「所以他覺得。」／下一段「人就是命中註定」這種腰斬（第二輪
            # review 抓到 5 組同講者連續段有 4 組切在句子中間）。
            # 二階：平常要一個紮實的 0.8s 停頓；段落超過 HARD_PARA_CHARS
            # 後，只要是句末就分——長獨白裡 0.8s 以上的停頓很稀有。
            paragraphs.append((cur_spk, _finish(buf)))
            buf = text
        else:
            buf = _append(buf, text, sep)

        prev_word = last_word or prev_word

    if buf and cur_spk is not None:
        paragraphs.append((cur_spk, _finish(buf)))
    return [(s, t) for s, t in paragraphs if t]


def render_markdown(paragraphs: list[tuple[int, str]], names: dict[int, str]) -> str:
    return "\n\n".join(f"**{names.get(spk, f'講者{spk}')}**：{text}" for spk, text in paragraphs)


def _project_words_to_master(
    episode_dir: Path, words: list[dict], speakers: list[int | None]
) -> tuple[list[dict], list[int | None]]:
    """來源時鐘的詞 → 成品時鐘，落在被剪掉區間的詞直接丟。

    講者判定要在來源時鐘做（mic 分軌在那個時鐘），但 cue 在成品時鐘。conform map
    是兩者之間唯一的正式對應（ADR-064）。沒有 conform map 就不能用 Editorial Master
    當逐字稿——硬對會生出「讀起來很通順但講錯人」的稿子。
    """
    from shared.editorial_conform import (
        RELATIVE_PATH,
        ConformMapError,
        load_conform_map,
        source_to_master_sec,
    )

    try:
        # load_conform_map 收的是**檔案路徑**，不是 episode 目錄。
        cmap = load_conform_map(episode_dir / RELATIVE_PATH)
    except ConformMapError as exc:
        raise SystemExit(
            f"要用 Editorial Master 當逐字稿就必須有 conform map（先跑 build_conform_map）：{exc}"
        ) from exc

    out_words: list[dict] = []
    out_speakers: list[int | None] = []
    dropped = 0
    for word, spk in zip(words, speakers):
        start = word.get("start")
        end = word.get("end")
        if start is None or end is None:
            out_words.append(word)
            out_speakers.append(spk)
            continue
        m0 = source_to_master_sec(cmap, float(start), source_key="audio")
        m1 = source_to_master_sec(cmap, float(end), source_key="audio")
        if m0 is None or m1 is None or m1 <= m0:
            dropped += 1
            continue
        out_words.append({**word, "start": m0, "end": m1})
        out_speakers.append(spk)
    logger.info(
        "詞級時間戳投影到成品時鐘：%d 個可用，%d 個落在被剪掉的區間",
        len(out_words),
        dropped,
    )
    return out_words, out_speakers


def _load_speakers(episode_dir: Path, words: list[dict]):
    """回傳 (詞級講者判定, envelopes)——envelopes 後面要拿來量 cue 可疑度。"""
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
    envelopes = load_envelopes(mics, reference=reference if reference.exists() else None)
    return assign_word_speakers(words, envelopes), envelopes


def parse_timecode(value: str) -> float:
    """'3696' / '1:01:36' / '01:01:35.964' → 秒。"""
    parts = value.strip().split(":")
    if len(parts) > 3:
        raise SystemExit(f"看不懂的時間碼：{value}")
    total = 0.0
    for part in parts:
        total = total * 60 + float(part.replace(",", "."))
    return total


def run(
    episode_dir: Path,
    *,
    host: str,
    guest: str,
    swap: bool = False,
    keep_spaces: bool = False,
    write_vault: bool = True,
    slug: str | None = None,
    outtakes_from: float | None = None,
    dry_run: bool = False,
) -> dict:
    # 用哪一份逐字稿由 shared.episode_transcript 決定：有 Editorial Master 就用它
    # （ADR-064：被剪掉的內容不該出現在衍生產物裡），沒有才退回 transcript.srt。
    source = resolve_transcript_srt(episode_dir)
    srt_path = source.srt_path
    words_path = episode_dir / WORDS_NAME
    if not srt_path.exists():
        raise FileNotFoundError(f"找不到 {srt_path}（先跑 subtitle-correct）")
    if not words_path.exists():
        raise FileNotFoundError(f"找不到 {words_path}（先跑 subtitle-gen）")

    words = json.loads(words_path.read_text(encoding="utf-8"))["words"]
    cues = _parse_srt(srt_path.read_text(encoding="utf-8"))
    # 講者判定必須在**來源時鐘**上做——mic 分軌就是那個時鐘。
    speakers, envelopes = _load_speakers(episode_dir, words)
    # 但 cue 可能在 Editorial Master 的時鐘上（成品剪過，跟來源差幾十秒）。
    # 兩邊不投影就對起來，講者會整片錯散——不是全域交換，是零星錯位，
    # 讀起來還很通順，所以特別難發現。
    if source.origin == "editorial_master":
        words, speakers = _project_words_to_master(episode_dir, words, speakers)

    ranges = _cue_word_ranges(cues, words)
    cue_speakers = _forward_fill(([_cue_speaker(idx, speakers, words) for idx in ranges]))

    # 訪談結束後的收工閒聊：兩人快速搶話，講者判定本來就不可靠，而且內容
    # 不屬於訪談本體——另存 outtakes，不混進主稿（時間點由 --outtakes-from
    # 明確給定，不做自動偵測：那需要判斷「訪談講完了沒」，是語意問題）。
    cut = len(cues) if outtakes_from is None else sum(1 for c in cues if c[0] < outtakes_from)
    slices = [(cues[:cut], cue_speakers[:cut], ranges[:cut])]
    if cut < len(cues):
        slices.append((cues[cut:], cue_speakers[cut:], ranges[cut:]))

    built = [build_paragraphs(c, s, r, words, keep_spaces=keep_spaces) for c, s, r in slices]
    paragraphs = built[0]
    if not paragraphs:
        raise SystemExit("洗不出任何段落——檢查 transcript.srt 與 words.json 是否同一集")

    names = {0: guest, 1: host} if swap else {0: host, 1: guest}
    body = render_markdown(paragraphs, names)
    outtakes_body = render_markdown(built[1], names) if len(built) > 1 else None

    margins = cue_margins_db(cues, envelopes)
    mixed = [
        {
            "cue": k + 1,
            "t0": round(cues[k][0], 2),
            "t1": round(cues[k][1], 2),
            "margin_db": round(m, 1),
            "assigned": names.get(cue_speakers[k], "?"),
            "section": "outtakes" if k >= cut else "main",
            "text": cues[k][2],
        }
        for k, m in enumerate(margins)
        if m is not None and abs(m) < MIXED_MARGIN_DB
    ]

    out_path = episode_dir / OUT_NAME
    mixed_path = episode_dir / MIXED_NAME
    vault_rel: str | None = None
    outtakes_path = episode_dir / OUTTAKES_NAME
    if not dry_run:
        out_path.write_text(body + "\n", encoding="utf-8", newline="\n")
        mixed_path.write_text(
            json.dumps(mixed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        if outtakes_body:
            outtakes_path.write_text(
                "> 訪談結束後的收工閒聊。兩人快速搶話，**講者歸屬不可靠**，"
                "不要當成正式逐字稿引用。\n\n" + outtakes_body + "\n",
                encoding="utf-8",
                newline="\n",
            )
        if write_vault:
            vault_rel = _write_vault(
                body, episode_dir=episode_dir, host=host, guest=guest, slug=slug
            )

    counts: dict[str, int] = {}
    for spk, text in paragraphs:
        counts[names.get(spk, str(spk))] = counts.get(names.get(spk, str(spk)), 0) + len(text)
    lengths = sorted(len(t) for _s, t in paragraphs)
    return {
        "status": "dry-run" if dry_run else "written",
        "cues": len(cues),
        "paragraphs": len(paragraphs),
        "longest_paragraph": lengths[-1],
        "chars_by_speaker": counts,
        # 兩軌能量差太小 = 兩人同時在講，判定不可靠——讀稿時這些段落不能全信
        "suspect_cues": len(mixed),
        "suspect_detail": None if dry_run else str(mixed_path),
        "outtakes_paragraphs": len(built[1]) if len(built) > 1 else 0,
        "outtakes": str(outtakes_path) if (outtakes_body and not dry_run) else None,
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
    parser.add_argument(
        "--outtakes-from",
        default=None,
        help="訪談結束的時間碼（如 1:01:36）——之後的收工閒聊另存 transcript_outtakes.md",
    )
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
        outtakes_from=parse_timecode(args.outtakes_from) if args.outtakes_from else None,
        dry_run=args.dry_run,
    )
    result["elapsed_sec"] = round(time.time() - started, 1)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
