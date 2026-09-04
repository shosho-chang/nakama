"""字幕定版（顯示層）後處理。

① 句首零孤兒標點：切分器若把句號、逗號、問驚嘆號等分隔符留給下一 cue，
   先把它歸還前一 cue；首 cue 沒有前句可歸還時，移除這個純顯示標點。
   只搬標點，不改 cue 數量或 timestamp。
② 句尾零標點：cue 每行行尾不留任何標點；閉合符（」』》））保留、其前標點剝除。
   主 pipeline 的 `shared.transcriber._process_srt_line` 在 ASR 產出時已做
   「句中→空格、句尾→刪除」；本模組給**繞過 pipeline 的 SRT**（翻譯精選、
   外部工具產物）補上同樣保證，對已處理過的 cue 冪等。
③ cue 間零空隙：end 補到次句 start，字幕連續顯示不閃爍。gap > `gap_close_max`
   （預設 3.0s，對齊 `run_gap_fill.MIN_GAP_SEC`「<3s = 正常語流」）視為真靜默
   ——沒人講話字幕就該消失，不補、列入回報（呼叫端不可靜默吞掉）。
④ 語助詞清理（修修 2026-09-03；延伸自 2026-07-26 的「呃」規則）：
   **區分遲疑詞與語氣詞**——遲疑詞是雜訊，語氣詞是語氣，刪掉後者會把話講硬。
   - 「呃」= 純遲疑，**無條件刪除**（與 `cue_builder.FILLERS`、`transcriber` prompt 同一條規則）
   - 「嗯／哦／齁」= 位置決定性質：獨立成句或**句首**時是附和／遲疑 → 刪；
     **句尾或句中保留**（「心理上的挨打齁」「可是很有意思哦」是語氣，不是雜訊）
   整條變空的 cue 直接移除（cue 數會變少，計入 `filler_cues_dropped`）。
   20260901 蘇予昕實測：4131 → 3868 條，整條刪 263、刪字保句 32、保留句尾語氣詞 35。

⚠️ 只作用於「要上 timeline 顯示」的 SRT 副本。`transcript.srt` 是工作真值，
cue 時間必須貼語音（highlight-cut 等下游靠 cue 時間切片，拉長 end 會帶入
死氣），**不要**對它原地跑 gap close。
"""

from __future__ import annotations

import re
from pathlib import Path

from shared import cue_builder

PUNCT_TAIL = "，。、；：！？…—～·" + ",.;:!?~"
CLOSERS = "」』》）"
LEADING_COMMAS = "，,"
LEADING_DISPLAY_PUNCT = PUNCT_TAIL

_TS = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")


#: 純遲疑語助詞——無條件刪除（修修 2026-07-26）。
#: 同一份定義，避免跟 `cue_builder.FILLERS` 各改各的漂移
HESITATION_FILLERS = "".join(cue_builder.FILLERS)
#: 位置相依語助詞——獨立成句／句首刪除，句尾或句中是語氣詞，保留（修修 2026-09-03）
POSITIONAL_FILLERS = "嗯哦齁"
#: 判斷「這條 cue 只有語助詞」時要忽略的裝飾字元
_FILLER_NOISE = "~～!！?？,，。 \t"
_HESITATION_RE = re.compile(f"[{HESITATION_FILLERS}][~～]*")
_LEADING_RE = re.compile(f"^[ \t]*[{POSITIONAL_FILLERS}][~～]*")


def filler_only(text: str) -> bool:
    """整條 cue 只有語助詞（與裝飾標點）→ 顯示層不需要它。"""
    core = "".join(ch for ch in text if ch not in _FILLER_NOISE and not ch.isspace())
    return bool(core) and set(core) <= set(HESITATION_FILLERS + POSITIONAL_FILLERS)


def strip_fillers(text: str) -> str:
    """移除遲疑語助詞，保留句尾／句中的語氣詞。冪等。

    「呃」任何位置都刪；「嗯／哦／齁」只在句首（可連續、可夾裝飾標點）刪。
    回傳空字串代表整條都是語助詞，呼叫端應丟棄該 cue。
    """
    out_lines = []
    for line in text.splitlines() or [""]:
        # 「呃」連同黏在它後面的拉長號一起移除
        s = _HESITATION_RE.sub("", line)
        # 句首「嗯／哦／齁」（含拉長號、可連續）移除。**只吃空白，不吃標點**——
        # 句首標點歸屬由規則①決定，這裡先剝掉會把標點錯搬給前一句。
        while True:
            new = _LEADING_RE.sub("", s, count=1)
            if new == s:
                break
            s = new
        out_lines.append(re.sub(r"[ \t]{2,}", " ", s).strip(" \t"))
    return "\n".join(ln for ln in out_lines if ln)


def strip_tail_punct(line: str) -> str:
    """剝除行尾標點；閉合符保留、其前標點一併剝除。冪等。"""
    s = line.rstrip()
    while s:
        if s[-1] in PUNCT_TAIL:
            s = s[:-1].rstrip()
            continue
        if s[-1] in CLOSERS:
            body = s[:-1].rstrip()
            if body and body[-1] in PUNCT_TAIL:
                s = body[:-1].rstrip() + s[-1]
                continue
        break
    return s


def finalize_cues(
    cues: list[tuple[float, float, str]],
    *,
    gap_close_max: float = 3.0,
    pause=None,
) -> tuple[list[tuple[float, float, str]], dict]:
    """套用三條定版規則。輸入 (start, end, text) 已按 start 排序；text 可含
    換行（多行 cue 逐行剝尾標點）。

    回傳 (新 cues, stats)。stats["true_silences"] 是沒補的 >gap_close_max
    區段（(前句序號 1-based, gap 秒)）——呼叫端要回報出來，不可靜默。
    `pause` 傳入停頓圖時，斷句檢查改以音檔靜音為主判準（見 find_bad_boundaries）。
    """
    # ④ 語助詞清理最先跑：整條刪掉的 cue 不該再參與標點歸還與補空隙，
    #    否則會把語助詞的標點搬給前一句、也會把已消失的 cue 算進 gap。
    out: list[list] = []
    filler_cues_dropped = 0
    filler_stripped = 0
    for s, e, text in cues:
        if filler_only(text):
            filler_cues_dropped += 1
            continue
        cleaned = strip_fillers(text)
        if not cleaned:
            filler_cues_dropped += 1
            continue
        if cleaned != text:
            filler_stripped += 1
        out.append([s, e, cleaned])
    leading_commas_rehomed = 0
    leading_commas_dropped = 0
    leading_punct_rehomed = 0
    leading_punct_dropped = 0
    for index, cue in enumerate(out):
        text = cue[2]
        punct_count = len(text) - len(text.lstrip(LEADING_DISPLAY_PUNCT))
        if not punct_count:
            continue
        punct = text[:punct_count]
        remainder = text[punct_count:]
        if not remainder:
            raise ValueError(f"cue {index + 1} contains only leading display punctuation")
        cue[2] = remainder
        comma_count = sum(ch in LEADING_COMMAS for ch in punct)
        if index:
            out[index - 1][2] += punct
            leading_punct_rehomed += punct_count
            leading_commas_rehomed += comma_count
        else:
            leading_punct_dropped += punct_count
            leading_commas_dropped += comma_count

    stripped = 0
    for cue in out:
        text = cue[2]
        new_text = "\n".join(strip_tail_punct(ln) for ln in text.splitlines())
        if new_text != text:
            stripped += 1
        cue[2] = new_text
    closed = 0
    true_silences: list[tuple[int, float]] = []
    for i in range(len(out) - 1):
        gap = out[i + 1][0] - out[i][1]
        if 1e-9 < gap <= gap_close_max:
            out[i][1] = out[i + 1][0]
            closed += 1
        elif gap > gap_close_max:
            true_silences.append((i + 1, round(gap, 2)))
    try:
        bad = find_bad_boundaries(out, pause=pause)
    except ImportError:
        _msg = "jieba 未安裝——斷句檢查沒跑，不可視為通過"
        bad = [{"cue": -1, "tail": "", "head": "", "reason": _msg}]
    return [tuple(c) for c in out], {
        "filler_cues_dropped": filler_cues_dropped,
        "filler_stripped": filler_stripped,
        "stripped": stripped,
        "leading_commas_rehomed": leading_commas_rehomed,
        "leading_commas_dropped": leading_commas_dropped,
        "leading_punct_rehomed": leading_punct_rehomed,
        "leading_punct_dropped": leading_punct_dropped,
        "closed": closed,
        "true_silences": true_silences,
        "bad_boundaries": bad,
    }


_TAIL_STICKY = set("的把被跟與和或在從對讓一這那每幾很超最更蠻挺滿")
# 黏著詞素：不可能當 cue 開頭。這是**封閉類**，不是詞庫——只收現代口語中
# 完全不可能起句的字。2026-08-12「冒牌｜者」出貨後補：詞典沒有「冒牌者」
# → jieba 切成「冒牌｜者」→ 四規則全放行。詞典會無限增長（修修：「永遠都
# 修不完」），黏著詞素不會，所以這條不靠詞典就擋得住整個類別。
# ⚠️ 不收 性/化/得/地/之/兒：它們都能起詞（性別、化學、得到、地方、之後、
# 兒子），加進來會誤旗標並驅動破壞性修復。
_HEAD_STICKY = set("的著了嗎呢吧喔哦者們")

# 代名詞收尾（修修 2026-08-06「我｜覺得」裁決）：長詞在前，比對取最長匹配。
_PRONOUN_TAILS = (
    "我們",
    "你們",
    "妳們",
    "他們",
    "她們",
    "它們",
    "牠們",
    "咱們",
    "大家",
    "我",
    "你",
    "妳",
    "他",
    "她",
    "它",
    "牠",
    "咱",
)
# 次句首詞的詞性若屬「謂語起手」（動詞/副詞/介詞/連接詞/時間詞），代表前句
# 尾巴那個代名詞其實是**下一句的主語**，被硬切在上一句尾（「這麼多我｜覺得」）。
# 反之次句以名詞/代名詞開頭時，代名詞多半是上一句的受詞（「他告訴我｜小明說」），
# 屬合法斷點——這個區分是本規則不誤報的關鍵。
PREDICATE_HEAD_FLAGS = frozenset({"d", "p", "c", "t", "zg", "a", "ad", "an"})
#: 舊名保留給本模組內的規則敘述；字卡斷行（shared.zh_linebreak）用公開名。
_SUBJ_HEAD_FLAGS = PREDICATE_HEAD_FLAGS


def _tw_jieba():
    """取得掛好**繁體詞庫**的 jieba（本模組所有斷詞的唯一入口）。

    ⚠️ 血淚（2026-08-06）：本函式存在之前，本模組直接 `import jieba` 用的是
    內建**簡體**詞典——繁中被逐字切碎，逼出「HMM=True + 詞頻表過濾」的權宜
    寫法（HMM 會發明「東西現」類假詞），連「判斷力」都被當成抓不到的已知限制。
    掛上 `ensure_tw_jieba()` 後 HMM=False 即可正確切繁中（判斷力 FREQ=103），
    假詞問題連同那條限制一起消失。
    """
    import jieba

    from shared.transcriber import ensure_tw_jieba

    ensure_tw_jieba()
    return jieba


# 認知/言說動詞：接在代名詞後當句尾＝賓語（整個子句）被切走（「我覺得｜進入…」）。
# 封閉清單而非全部動詞——「他們同意」「我知道」這種可獨立成句的收尾不該誤報。
_COMPLEMENT_VERBS = (
    "覺得",
    "認為",
    "以為",
    "發現",
    "希望",
    "想說",
    "說到",
    "提到",
    "講到",
    "相信",
    "擔心",
    "害怕",
    "決定",
    "打算",
    "喜歡",
    "討厭",
    "需要",
    "想要",
)


# 助動詞／連接副詞：獨立成詞出現在句尾＝後面的主要動詞被切走。
# ⚠️ 「開始/繼續」不收（2026-08-07 安吉 45s 血淚）：它們常是本動詞、後接受詞
# （開始｜「數位遊牧」），跨 cue 是正常斷法——誤旗標會驅動破壞性修復。
MODAL_TAIL_WORDS = frozenset(
    {
        "要",
        "會",
        "能",
        "該",
        "就",
        "才",
        "也",
        "還",
        "又",
        "再",
        "不",
        "沒",
        "想",
        "可以",
        "應該",
        "必須",
        "願意",
        "打算",
        "怎麼",
        "一直",
        "一定",
        "已經",
        "突然",
        "好像",
        "幾乎",
        "甚至",
        "越來越",
    }
)
#: 舊名保留給本模組內的規則敘述；字卡斷行（shared.zh_linebreak）用公開名。
_MODAL_TAIL = MODAL_TAIL_WORDS

OPEN_BRACKETS = "「『《（"
CLOSE_BRACKETS = "」』》）"


def _pronoun_verb_tail(bare: str) -> str | None:
    """前句是否以「代名詞＋認知動詞」收尾（賓語子句被切到下一句）。"""
    for v in _COMPLEMENT_VERBS:
        if not bare.endswith(v):
            continue
        stem = bare[: -len(v)]
        if any(stem.endswith(p) for p in _PRONOUN_TAILS):
            pron = next(p for p in _PRONOUN_TAILS if stem.endswith(p))
            return pron + v
    return None


def _is_subject_head(head: str) -> bool:
    """次句開頭是不是「謂語起手」（→ 前句尾的代名詞是被切走的主語）。"""
    import jieba.posseg as posseg

    _tw_jieba()
    for w in posseg.cut(head, HMM=False):
        return w.flag.startswith("v") or w.flag in _SUBJ_HEAD_FLAGS
    return False


def boundary_reason(tail: str, head: str) -> str | None:
    """單一切點的四規則判定；乾淨切點回 None。（重切工具與 gate 共用真值）"""
    a = (tail or "").strip()
    b = (head or "").strip().lstrip("-—– ").lstrip()
    if not a or not b:
        return None
    if a[-1] in OPEN_BRACKETS:
        return f"前句以開括號「{a[-1]}」結尾（孤兒括號）"
    if b[0] in CLOSE_BRACKETS:
        return f"次句以閉括號「{b[0]}」開頭（孤兒括號）"
    if b[0] in _HEAD_STICKY:
        return f"次句以「{b[0]}」開頭"
    if a[-1] in _TAIL_STICKY:
        return f"前句以「{a[-1]}」結尾"
    bare_a, bare_b = a.replace(" ", ""), b.replace(" ", "")
    if not bare_a or not bare_b:
        return None
    pron = next((p for p in _PRONOUN_TAILS if bare_a.endswith(p)), None)
    if pron and _is_subject_head(bare_b[:6]):
        return f"前句以代名詞「{pron}」收尾，其實是次句主語"
    sv = _pronoun_verb_tail(bare_a)
    if sv:
        return f"前句以「{sv}」收尾，賓語被切到次句"
    # 助動詞/連接副詞收尾＝主要動詞在下一句（「我們要｜繼續旅遊」「然後我就｜跟他說」）。
    # **詞級**判定：字元級會誤傷「可能」「機會」「需要」這些以同字收尾的完整詞。
    last = list(_tw_jieba().cut(bare_a[-8:], HMM=False))[-1]
    if last in _MODAL_TAIL:
        return f"前句以助動詞「{last}」收尾，主要動詞在次句"
    # 切點兩側任一是 ASCII → 不跑詞跨界（jieba 會把整串英文當成一個「詞」，
    # 「We are a team」跨 cue 必誤報；英文詞完整性由 cue_builder 的空格切分把關）
    if bare_a[-1].isascii() or bare_b[0].isascii():
        return None
    ta, tb = bare_a[-6:], bare_b[:6]
    cut, pos = len(ta), 0
    for tok in _tw_jieba().cut(ta + tb, HMM=False):
        pos += len(tok)
        if pos == cut:
            break
        if pos > cut:
            if len(tok) > 1:
                return f"詞「{tok}」被切開"
            break
    return None


def find_bad_boundaries(cues: list, pause=None) -> list[dict]:
    """偵測跨 cue 壞斷句（修修 2026-08-06「句子被切掉」裁決後的強制關卡）。

    判準分兩層：
    ① **音檔靜音**（`pause`，`shared.pause_map.PauseMap`）——主判準。切點落在
       連續發聲中間即旗標，不需要知道被切開的是什麼詞。這是唯一能抓到集別
       詞彙的層：2026-08-12「冒牌｜者」四條詞典規則全放行，音檔一聽就知道。
    ② `boundary_reason` 的詞典四規則——兜底。詞典永遠不完整（修修：「永遠都
       修不完」），所以它現在是輔助，不是主力。

    `pause=None` 時只跑第②層，**等於已知會漏詞的舊路徑**，呼叫端要回報這件事。
    回傳 [{cue, tail, head, reason, rms?}]。
    """
    flags = []
    for i in range(len(cues) - 1):
        ta_full = cues[i][2].strip()
        tb_full = cues[i + 1][2].strip()
        if not ta_full or not tb_full:
            continue
        a = ta_full.splitlines()[-1].strip()
        b = tb_full.splitlines()[0].strip()
        reason = boundary_reason(a, b)
        rms = None
        if pause is not None:
            rms = pause.floor(cues[i + 1][0])
            if rms > pause.noisy and not reason:
                reason = f"切在連續發聲中（RMS {rms:.4f} > 本集吵門檻 {pause.noisy:.4f}）"
        if reason:
            f = {"cue": i + 1, "tail": a[-8:], "head": b[:8], "reason": reason}
            if rms is not None:
                f["rms"] = round(rms, 5)
            flags.append(f)
    return flags


def parse_srt_text(text: str) -> list[tuple[float, float, str]]:
    cues = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.strip().splitlines()
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


def _fmt_ts(t: float) -> str:
    ms = round(t * 1000)
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def format_srt(cues: list[tuple[float, float, str]]) -> str:
    return "\n".join(
        f"{i}\n{_fmt_ts(s)} --> {_fmt_ts(e)}\n{text}\n" for i, (s, e, text) in enumerate(cues, 1)
    )


def strip_fillers_srt_file(src: Path, dst: Path) -> dict:
    """只套規則④（語助詞清理）寫 dst，**不碰標點與空隙**。回傳 stats。

    給 hash-bound release（ADR-063 memo-dual-audit-v1 等）的顯示副本用：那些
    模式刻意不跑完整定版，避免顯示層默默改動已審核文字。語助詞清理是修修
    明示的編輯決策，不是默默改動，所以獨立成這個窄入口並回報刪除數量，
    呼叫端必須把數字印出來。`release.srt` 本體永遠不動。

    唯一例外：句首語助詞被砍掉後，原本黏在它後面的標點（「嗯，可是…」→
    「，可是…」）會變成孤兒句首標點。`finalize_cues` 裡這個標點由規則①
    歸還／丟棄，但這裡沒有跑規則①，所以直接剝掉——它是語助詞的附屬標點，
    不是原句自己的句首標點，不屬於「不碰標點」要保護的範圍。
    """
    cues = parse_srt_text(Path(src).read_text(encoding="utf-8-sig"))
    kept: list[tuple[float, float, str]] = []
    dropped = 0
    edited = 0
    for start, end, text in cues:
        if filler_only(text):
            dropped += 1
            continue
        cleaned = strip_fillers(text)
        if not cleaned:
            dropped += 1
            continue
        head = text.lstrip(" \t")[:1]
        if cleaned != text and head in (HESITATION_FILLERS + POSITIONAL_FILLERS):
            while cleaned[:1] in LEADING_DISPLAY_PUNCT:
                cleaned = cleaned[1:].lstrip(" \t")
            if not cleaned:
                dropped += 1
                continue
        if cleaned != text:
            edited += 1
        kept.append((start, end, cleaned))
    Path(dst).write_text(format_srt(kept), encoding="utf-8")
    return {
        "cues": len(kept),
        "cues_in": len(cues),
        "filler_cues_dropped": dropped,
        "filler_stripped": edited,
    }


def finalize_srt_file(
    src: Path,
    dst: Path,
    *,
    gap_close_max: float = 3.0,
    repair: bool = True,
    pause=None,
    words: list[dict] | None = None,
) -> dict:
    """src SRT →（切點重修）→ 定版 → 寫 dst。回傳 stats（含 cues 總數）。

    repair=True（預設）先把切點搬到合法邊界，再跑定版兩規則——偵測與修復
    同一層，顯示副本不會帶著已知壞切點出貨。傳 `pause`（停頓圖）時重修以
    音檔靜音為主判準；不傳則退回詞典判準（已知會漏集別詞彙）。
    """
    cues = parse_srt_text(Path(src).read_text(encoding="utf-8-sig"))
    moved = 0
    if repair:
        from shared.subtitle_reboundary import repair_cues  # 延遲載入避免循環匯入

        cues, rb = repair_cues(cues, words=words, pause=pause)
        moved = rb["moved"]
    fin, stats = finalize_cues(cues, gap_close_max=gap_close_max, pause=pause)
    Path(dst).write_text(format_srt(fin), encoding="utf-8")
    stats["cues"] = len(fin)
    stats["reboundary_moved"] = moved
    return stats
