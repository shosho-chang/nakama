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


_TAIL_STICKY = set("的把被跟與和或在從對讓一這那每幾很超最更")
_HEAD_STICKY = set("的著了嗎呢吧喔哦")


def find_bad_boundaries(cues: list) -> list[dict]:
    """偵測跨 cue 壞斷句（修修 2026-08-06「句子被切掉」裁決後的強制關卡）。

    高精度保守三規則：次句黏著開頭（的/著/了…）、前句黏著結尾（的/把/被/
    一/這…）、jieba 詞跨 cue 邊界被切開。回傳 [{cue, tail, head, reason}]。
    呼叫端必須呈現結果——斷句檢查從「選配」升級為 finalize 層標配，
    偵測得到的壞切點不再默默出貨；語感層級的修復仍由人/agent 判讀執行。
    """
    import jieba

    flags = []
    for i in range(len(cues) - 1):
        ta_full = cues[i][2].strip()
        tb_full = cues[i + 1][2].strip()
        if not ta_full or not tb_full:
            continue
        a = ta_full.splitlines()[-1].strip()
        b = tb_full.splitlines()[0].strip()
        if not a or not b:
            continue
        reason = None
        if b[0] in _HEAD_STICKY:
            reason = f"次句以「{b[0]}」開頭"
        elif a[-1] in _TAIL_STICKY:
            reason = f"前句以「{a[-1]}」結尾"
        else:
            ta, tb = a[-6:].replace(" ", ""), b[:6].replace(" ", "")
            if ta and tb:
                cut = len(ta)
                pos = 0
                # HMM=True + 詞頻表過濾：jieba 預設詞典是簡體，HMM=False 會把
                # 繁中碎成單字（不可用）；HMM=True 對拼接串會發明「東西現」類
                # 假詞——用 FREQ 濾掉。已知限制（誠實邊界）：HMM 合成的 OOV
                # 複合詞（如「判斷力」）不在詞頻表，跨界時抓不到——該類主要
                # 出自逐字硬切，ASR 卡走語意重切後已根治；譯文卡由黏著字
                # 規則兜底。
                for tok in jieba.cut(ta + tb):
                    pos += len(tok)
                    if pos == cut:
                        break
                    if pos > cut:
                        if len(tok) > 1 and jieba.dt.FREQ.get(tok, 0) > 0:
                            reason = f"詞「{tok}」被切開"
                        break
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


def finalize_srt_file(src: Path, dst: Path, *, gap_close_max: float = 3.0) -> dict:
    """src SRT → 定版 → 寫 dst。回傳 stats（含 cues 總數）。"""
    cues = parse_srt_text(Path(src).read_text(encoding="utf-8-sig"))
    fin, stats = finalize_cues(cues, gap_close_max=gap_close_max)
    Path(dst).write_text(format_srt(fin), encoding="utf-8")
    stats["cues"] = len(fin)
    return stats
