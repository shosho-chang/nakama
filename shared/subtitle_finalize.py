"""字幕定版（顯示層）後處理——修修 2026-08-05 裁決的兩條規則。

① 句尾零標點：cue 每行行尾不留任何標點；閉合符（」』》））保留、其前標點剝除。
   主 pipeline 的 `shared.transcriber._process_srt_line` 在 ASR 產出時已做
   「句中→空格、句尾→刪除」；本模組給**繞過 pipeline 的 SRT**（翻譯精選、
   外部工具產物）補上同樣保證，對已處理過的 cue 冪等。
② cue 間零空隙：end 補到次句 start，字幕連續顯示不閃爍。gap > `gap_close_max`
   （預設 3.0s，對齊 `run_gap_fill.MIN_GAP_SEC`「<3s = 正常語流」）視為真靜默
   ——沒人講話字幕就該消失，不補、列入回報（呼叫端不可靜默吞掉）。

⚠️ 只作用於「要上 timeline 顯示」的 SRT 副本。`transcript.srt` 是工作真值，
cue 時間必須貼語音（highlight-cut 等下游靠 cue 時間切片，拉長 end 會帶入
死氣），**不要**對它原地跑 gap close。
"""

from __future__ import annotations

import re
from pathlib import Path

PUNCT_TAIL = "，。、；：！？…—～·" + ",.;:!?~"
CLOSERS = "」』》）"

_TS = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")


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
) -> tuple[list[tuple[float, float, str]], dict]:
    """套用兩條定版規則。輸入 (start, end, text) 已按 start 排序；text 可含
    換行（多行 cue 逐行剝尾標點）。

    回傳 (新 cues, stats)。stats["true_silences"] 是沒補的 >gap_close_max
    區段（(前句序號 1-based, gap 秒)）——呼叫端要回報出來，不可靜默。
    """
    out: list[list] = []
    stripped = 0
    for s, e, text in cues:
        new_text = "\n".join(strip_tail_punct(ln) for ln in text.splitlines())
        if new_text != text:
            stripped += 1
        out.append([s, e, new_text])
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
        bad = find_bad_boundaries(out)
    except ImportError:
        _msg = "jieba 未安裝——斷句檢查沒跑，不可視為通過"
        bad = [{"cue": -1, "tail": "", "head": "", "reason": _msg}]
    return [tuple(c) for c in out], {
        "stripped": stripped,
        "closed": closed,
        "true_silences": true_silences,
        "bad_boundaries": bad,
    }


_TAIL_STICKY = set("的把被跟與和或在從對讓一這那每幾很超最更蠻挺滿")
_HEAD_STICKY = set("的著了嗎呢吧喔哦")

# 代名詞收尾（修修 2026-08-06「我｜覺得」裁決）：長詞在前，比對取最長匹配。
_PRONOUN_TAILS = (
    "我們", "你們", "妳們", "他們", "她們", "它們", "牠們", "咱們", "大家",
    "我", "你", "妳", "他", "她", "它", "牠", "咱",
)
# 次句首詞的詞性若屬「謂語起手」（動詞/副詞/介詞/連接詞/時間詞），代表前句
# 尾巴那個代名詞其實是**下一句的主語**，被硬切在上一句尾（「這麼多我｜覺得」）。
# 反之次句以名詞/代名詞開頭時，代名詞多半是上一句的受詞（「他告訴我｜小明說」），
# 屬合法斷點——這個區分是本規則不誤報的關鍵。
_SUBJ_HEAD_FLAGS = frozenset({"d", "p", "c", "t", "zg", "a", "ad", "an"})


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
    "覺得", "認為", "以為", "發現", "希望", "想說", "說到", "提到", "講到",
    "相信", "擔心", "害怕", "決定", "打算", "喜歡", "討厭", "需要", "想要",
)


# 助動詞／連接副詞：獨立成詞出現在句尾＝後面的主要動詞被切走。
# ⚠️ 「開始/繼續」不收（2026-08-07 安吉 45s 血淚）：它們常是本動詞、後接受詞
# （開始｜「數位遊牧」），跨 cue 是正常斷法——誤旗標會驅動破壞性修復。
_MODAL_TAIL = frozenset(
    {"要", "會", "能", "該", "就", "才", "也", "還", "又", "再", "不", "沒",
     "想", "可以", "應該", "必須", "願意", "打算",
     "怎麼", "一直", "一定", "已經", "突然", "好像", "幾乎", "甚至", "越來越"}
)

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


def find_bad_boundaries(cues: list) -> list[dict]:
    """偵測跨 cue 壞斷句（修修 2026-08-06「句子被切掉」裁決後的強制關卡）。

    四規則見 `boundary_reason`：次句黏著開頭、前句黏著結尾、**前句以代名詞收尾
    且次句是謂語起手**（我｜覺得）、jieba 詞跨邊界被切開。
    回傳 [{cue, tail, head, reason}]。呼叫端必須呈現結果——斷句檢查是 finalize
    層標配，偵測得到的壞切點不再默默出貨；修復由 run_subtitle_reboundary 或人判讀。
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
        if reason:
            flags.append({"cue": i + 1, "tail": a[-8:], "head": b[:8], "reason": reason})
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


def finalize_srt_file(
    src: Path, dst: Path, *, gap_close_max: float = 3.0, repair: bool = True
) -> dict:
    """src SRT →（切點重修）→ 定版 → 寫 dst。回傳 stats（含 cues 總數）。

    repair=True（預設）先把偵測到的壞斷句搬到合法語意邊界，再跑定版兩規則
    ——偵測與修復同一層，顯示副本不會帶著已知壞切點出貨。
    """
    cues = parse_srt_text(Path(src).read_text(encoding="utf-8-sig"))
    moved = 0
    if repair:
        from shared.subtitle_reboundary import repair_cues  # 延遲載入避免循環匯入

        cues, rb = repair_cues(cues)
        moved = rb["moved"]
    fin, stats = finalize_cues(cues, gap_close_max=gap_close_max)
    Path(dst).write_text(format_srt(fin), encoding="utf-8")
    stats["cues"] = len(fin)
    stats["reboundary_moved"] = moved
    return stats
