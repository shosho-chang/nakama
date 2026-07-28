"""short-tighten：短片緊湊化 — 贅詞/停頓 jump-cut。

修修 2026-07-26：短影片節奏要快狠準——開頭的「那、那」口吃絕不能出現，
中間的停頓與贅詞也要剪掉，jump cut 越緊湊越好。

兩段式流程（機械偵測 + agent 語意複審，不可全自動——「那/就是/然後」
很多時候是有意義的連接詞，機械砍會砍壞語意）：

1. `--detect --id <winner-id>`：
   - ffmpeg silencedetect 抓該段**真實**靜音（WhisperX 詞尾被拉伸貼齊下一
     詞起點，詞級 gap 永遠是 0，不能用；見 shared/cue_builder.py est_gap 註）
   - words.json 詞級掃贅詞候選（長音「那/呃/啊/嗯」、口吃重複詞）
   - 產出 highlights/tighten/<id>_cuts.json：
     pause 類 keep=true（機械可信）、filler/stutter 類 keep=null（待複審）
2. agent 逐條複審 cuts.json，把 keep=null 改成 true/false
3. `--apply --id <winner-id>`：
   - keep=true 的切除區間 → 補集 = 保留段
   - 建**新** timeline「短N - <title>（緊）」：多段 jump-cut append（影片
     順序上軌、音軌逐段 recordFrame 對位）；原 timeline 不動，供對照
   - 字幕依保留段重新對時（切掉的時間塌縮），版本化 SRT 繞路徑快取

用法：
    python scripts/run_short_tighten.py <episode> --detect --id punch-S1
    python scripts/run_short_tighten.py <episode> --apply --id punch-S1
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_highlight_cut import (  # noqa: E402
    FORMAT_LABEL,
    HIGHLIGHTS_DIR,
    SEG_SRT_DIR,
    _parse_srt,
    _ts,
)

logger = logging.getLogger("short_tighten")

TIGHTEN_DIR = "highlights/tighten"
# 靜音偵測：-32dB 門檻、0.35s 起算才視為可剪停頓
SILENCE_NOISE = "-32dB"
MIN_PAUSE = 0.35
# 剪停頓時保留的呼吸空隙（頭尾各留一點，全剪掉聽起來像機器人）
KEEP_HEAD = 0.08
KEEP_TAIL = 0.07
# 贅詞候選：單字拖 ≥0.4s 視為 hesitation（正常語速單字 ~0.15-0.25s）
FILLER_WORDS = {"那", "呃", "啊", "嗯", "欸", "喔"}
FILLER_MIN_DUR = 0.40
# 保留段最短長度——短於此併入切除（0.3s 的孤島段落會閃屏）
MIN_KEEP_SEG = 0.30
# 整句只有附和詞的 cue（主持人 backchannel）——短片候選剪除，agent 複審
# 是否與來賓語音重疊（重疊剪了會斷來賓的話）
BACKCHANNEL_TEXTS = {"對", "對對", "對對對", "嗯", "嗯嗯", "沒錯", "沒錯沒錯", "是", "好", "對啊"}
# 切除區間最短長度——短於此不值得一刀（一刀就是一個 jump cut 的視覺跳動）
MIN_CUT = 0.12

_SIL_START = re.compile(r"silence_start:\s*([\d.]+)")
_SIL_END = re.compile(r"silence_end:\s*([\d.]+)")


def import_srt_tidy(mp, root, seg_srt: Path):
    """SRT 匯入 media pool 的衛生版：進 Subs bin + 刪同名舊版。

    十九輪血案：每輪修字幕都往 root 匯一版新 SRT，20 輪下來 root 堆了
    96 個 clip。改為：匯進「Subs」bin；匯入前刪同 prefix（去 _rNNN）的
    舊版 clip（軌上的 cue 是匯入時複製的，刪 pool clip 不影響既有字幕軌，
    2026-07-27 實測）。⚠️ MoveClips 對 Subtitle clip 是複製語意，不能用
    「先匯再搬」。回傳 ImportMedia 的 items。
    """
    import re as _re

    subs_bin = next(
        (f for f in root.GetSubFolderList() if f.GetName() == "Subs"), None
    ) or mp.AddSubFolder(root, "Subs")
    prefix = _re.sub(r"_r\d{3}$", "", seg_srt.stem)
    stale = [
        cl
        for folder in (subs_bin, root)
        for cl in (folder.GetClipList() or [])
        if _re.fullmatch(_re.escape(prefix) + r"_r\d{3}", cl.GetName() or "")
    ]
    if stale:
        mp.DeleteClips(stale)
    mp.SetCurrentFolder(subs_bin)
    items = mp.ImportMedia([str(seg_srt)])
    mp.SetCurrentFolder(root)
    return items


def _load_winner(episode_dir: Path, cid: str) -> tuple[dict, dict]:
    hdir = episode_dir / HIGHLIGHTS_DIR
    cands = json.loads((hdir / "candidates.json").read_text(encoding="utf-8"))["candidates"]
    winners = json.loads((hdir / "winners.json").read_text(encoding="utf-8"))["winners"]
    c = next((x for x in cands if x["id"] == cid), None)
    w = next((x for x in winners if x["id"] == cid), None)
    if c is None or w is None:
        raise SystemExit(f"{cid} 不在 winners/candidates 中")
    return c, w


def _detect_silences(audio: Path, t0: float, t1: float) -> list[tuple[float, float]]:
    """ffmpeg silencedetect 抓 [t0, t1] 內的靜音區間（絕對秒）。"""
    proc = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-ss",
            f"{t0:.3f}",
            "-t",
            f"{t1 - t0:.3f}",
            "-i",
            str(audio),
            "-af",
            f"silencedetect=noise={SILENCE_NOISE}:d={MIN_PAUSE}",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    out = proc.stderr
    starts = [float(m.group(1)) + t0 for m in _SIL_START.finditer(out)]
    ends = [float(m.group(1)) + t0 for m in _SIL_END.finditer(out)]
    if len(ends) < len(starts):  # 靜音一路到段尾，ffmpeg 不吐 silence_end
        ends.append(t1)
    return list(zip(starts, ends))


def detect(episode_dir: Path, cid: str) -> dict:
    c, _w = _load_winner(episode_dir, cid)
    t0, t1 = float(c["t_start"]), float(c["t_end"])
    words = json.load(open(episode_dir / "subs" / "words.json", encoding="utf-8"))["words"]
    seg_words = [x for x in words if t0 <= x.get("start", 0) < t1]

    cuts: list[dict] = []

    # 1) 真實靜音 → pause cut（機械可信，keep=true）
    audio = episode_dir / "normalized.wav"
    for s, e in _detect_silences(audio, t0, t1):
        # 段首/段尾的靜音整段剪掉（不留呼吸），中間的留頭尾空隙
        cs = t0 if s <= t0 + 0.05 else s + KEEP_HEAD
        ce = t1 if e >= t1 - 0.05 else e - KEEP_TAIL
        if ce - cs >= MIN_CUT:
            cuts.append(
                {
                    "t0": round(cs, 3),
                    "t1": round(ce, 3),
                    "kind": "pause",
                    "dur": round(e - s, 2),
                    "keep": True,
                }
            )

    # 2) 贅詞候選（keep=null，agent 語意複審後定生死）
    def ctx(i: int) -> str:
        lo, hi = max(0, i - 8), min(len(seg_words), i + 9)
        return "".join(
            (f"◤{x['word']}◢" if j == i else x["word"])
            for j, x in enumerate(seg_words[lo:hi], start=lo)
        )

    for i, x in enumerate(seg_words):
        wd, ws, we = x["word"], x["start"], x["end"]
        dur = we - ws
        if wd in FILLER_WORDS and dur >= FILLER_MIN_DUR:
            cuts.append(
                {
                    "t0": round(ws, 3),
                    "t1": round(we, 3),
                    "kind": "filler",
                    "word": wd,
                    "dur": round(dur, 2),
                    "context": ctx(i),
                    "keep": None,
                }
            )
        elif (
            i + 1 < len(seg_words)
            and wd == seg_words[i + 1]["word"]
            and wd not in "的了是"
            and not wd.isascii()  # APP 拼字 P-P、100 的 0-0、省略號都不是口吃
            and dur >= 0.25  # 合法疊詞（剛剛/常常/慢慢）語速正常；口吃首字會拖
        ):
            # 口吃重複（那那/他他）：候選剪第一個
            cuts.append(
                {
                    "t0": round(ws, 3),
                    "t1": round(we, 3),
                    "kind": "stutter",
                    "word": wd,
                    "dur": round(dur, 2),
                    "context": ctx(i),
                    "keep": None,
                }
            )

    # 3) backchannel cue（整句只有附和詞，keep=null——要人工確認沒壓到
    #    來賓語音；能量分析對重疊 backchannel 是盲的，見 SKILL.md 已知極限）
    for s, e, text in _parse_srt(episode_dir / "transcript.srt"):
        if t0 <= s and e <= t1 and text.replace(" ", "") in BACKCHANNEL_TEXTS:
            cuts.append(
                {
                    "t0": round(s, 3),
                    "t1": round(e, 3),
                    "kind": "backchannel",
                    "word": text,
                    "dur": round(e - s, 2),
                    "keep": None,
                }
            )

    cuts.sort(key=lambda x: x["t0"])
    out_dir = episode_dir / TIGHTEN_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{cid}_cuts.json"
    payload = {"id": cid, "t_start": t0, "t_end": t1, "cuts": cuts}
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    n_review = sum(1 for x in cuts if x["keep"] is None)
    return {
        "status": "detected",
        "file": str(out_path),
        "pauses": sum(1 for x in cuts if x["kind"] == "pause"),
        "need_review": n_review,
    }


def _keep_segments(t0: float, t1: float, cuts: list[dict]) -> list[tuple[float, float]]:
    """keep=true 切除區間的補集；合併相鄰切除、吸收過短保留段。"""
    active = sorted(
        ((max(t0, x["t0"]), min(t1, x["t1"])) for x in cuts if x.get("keep") is True),
        key=lambda p: p[0],
    )
    merged: list[list[float]] = []
    for s, e in active:
        if e - s <= 0:
            continue
        if merged and s <= merged[-1][1] + 0.05:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    segs: list[tuple[float, float]] = []
    pos = t0
    for s, e in merged:
        if s - pos >= MIN_KEEP_SEG:
            segs.append((pos, s))
        elif segs:  # 過短保留段：併入前一刀（延伸上一保留段終點沒意義，直接丟）
            pass
        pos = max(pos, e)
    if t1 - pos >= MIN_KEEP_SEG:
        segs.append((pos, t1))
    return segs


# 字幕細切（修修 2026-07-26 三輪：對齊鐘穎範本——一行 5–9 字的呼吸單元，
# 字幕翻頁頻率本身是節奏裝置）
#
# ⚠️ 切點必走 jieba 詞邊界（shared/transcriber._force_break）。2026-07-26 教訓：
# 第一版純按字數切，把「產品/短效/長期/改變/聯考」全部攔腰砍——中文斷行
# 沒有分詞就是錯的，任何字數規則都救不回來。
_MIN_CUE_SEC = 0.8  # 一行最短顯示秒數（二十四輪盲審：0.16s 閃現讀不到）
_FINE_MAX = 8  # 一行目標寬（顯示寬：中文=1、ASCII/空白=0.5）
_FINE_HARD = 10  # 修修 2026-07-26 十輪裁決：中文 10 字 = hard limit，超過必拆
_FINE_MIN = 4  # 短於此的單元往前併
_OPEN_B = "「《【（"
_CLOSE_B = "」》】）"
_NO_START = "的了嗎呢吧」》】）"  # 單元不可用這些字開頭（斷在助詞前）


def _disp_len(s: str) -> float:
    """顯示寬：CJK 全形 = 1、ASCII/空白 = 0.5（拉丁字在等寬中文行裡佔半格）。"""
    return sum(0.5 if ord(c) < 128 else 1.0 for c in s)


def _in_brackets(text: str, pos: int) -> bool:
    depth = 0
    for ch in text[:pos]:
        if ch in _OPEN_B:
            depth += 1
        elif ch in _CLOSE_B:
            depth = max(0, depth - 1)
    return depth > 0


# 數字後的量詞（16|歲 這種刀口是硬傷——jieba 把數字與量詞切成兩 token，
# 打包時視為單一原子）
_CLASSIFIERS = "歲個年月日天週次人隻條張件塊萬千百分秒倍章篇集場句字部本間位名度號"


def _load_episode_hotwords(episode_dir: Path) -> int:
    """episode 專有名詞進 jieba（音譯人名等 OOV——「海德/特」教訓：
    通用詞庫永遠治不了集別詞彙）。來源 subs/hotwords.txt，一行一詞，
    由細切後的語意複審 curate。"""
    import jieba

    from shared.transcriber import ensure_tw_jieba

    ensure_tw_jieba()
    f = episode_dir / "subs" / "hotwords.txt"
    n = 0
    if f.exists():
        for w in f.read_text(encoding="utf-8").split():
            if w.strip():
                jieba.add_word(w.strip(), freq=2000)
                n += 1
    return n


def _atom_spans(text: str, a: int, b: int) -> list[tuple[int, int]]:
    """clause [a,b) → 原子 span 列表：括號群組不可分割、其餘 jieba 詞；
    數字+量詞黏成單一原子（16歲）。"""
    import jieba

    from shared.transcriber import ensure_tw_jieba

    ensure_tw_jieba()
    atoms: list[tuple[int, int]] = []
    i = a
    while i < b:
        if text[i] in _OPEN_B:
            depth = 0
            j = i
            while j < b:
                if text[j] in _OPEN_B:
                    depth += 1
                elif text[j] in _CLOSE_B:
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            j = min(j + 1, b)
            atoms.append((i, j))
            i = j
        else:
            j = i
            while j < b and text[j] not in _OPEN_B:
                j += 1
            pos = i
            for w in jieba.cut(text[i:j]):
                atoms.append((pos, pos + len(w)))
                pos += len(w)
            i = j
    # 數字原子 + 量詞開頭的下一原子 → 黏合
    glued: list[tuple[int, int]] = []
    for sa, sb in atoms:
        if (
            glued
            and text[glued[-1][0] : glued[-1][1]].strip().isdigit()
            and text[sa] in _CLASSIFIERS
        ):
            glued[-1] = (glued[-1][0], sb)
        else:
            glued.append((sa, sb))
    return glued


def _fine_spans(text: str) -> list[tuple[int, int]]:
    """cue 文字 → 呼吸單元 char span 列表（目標 ~8 寬、**hard limit 10**）。

    空格（停頓標記）優先切 clause → clause 內原子化（括號群組整塊、其餘
    jieba 詞）→ greedy 打包：≤_FINE_MAX 直接收，收尾原子容忍到 _FINE_HARD
    （避免孤兒尾行）。超過 hard limit 的行只可能來自單一不可分原子（超長
    括號群組/英文詞），保留並記 warning。
    """
    clauses: list[tuple[int, int]] = []
    a = 0
    for i, ch in enumerate(text):
        if ch == " " and not _in_brackets(text, i):
            if i > a:
                clauses.append((a, i))
            a = i + 1
    if a < len(text):
        clauses.append((a, len(text)))

    units: list[list[int]] = []
    for ca, cb in clauses:
        atoms = _atom_spans(text, ca, cb)
        cur: list[int] | None = None
        for k, (sa, sb) in enumerate(atoms):
            if cur is None:
                cur = [sa, sb]
                continue
            w_ext = _disp_len(text[cur[0] : sb])
            is_last = k == len(atoms) - 1
            if w_ext <= _FINE_MAX or (is_last and w_ext <= _FINE_HARD):
                cur[1] = sb
            else:
                units.append(cur)
                cur = [sa, sb]
        if cur is not None:
            units.append(cur)

    # 後修：行首助詞 / 過短單元往前併——但**絕不超過 hard limit**。
    # 例外：行首量詞接前行尾數字（「12 3」|「年」跨 clause 案例）**強制併**
    # ——量詞孤行比超寬一格更不可接受
    merged: list[list[int]] = []
    for ua, ub in units:
        joinable = merged and _disp_len(text[merged[-1][0] : ub]) <= _FINE_HARD
        classifier_orphan = (
            merged
            and text[ua] in _CLASSIFIERS
            and text[merged[-1][1] - 1].isdigit()
            and _disp_len(text[merged[-1][0] : ub]) <= _FINE_HARD + 1  # 縫合不可撐爆行寬
        )
        if classifier_orphan or (
            merged
            and joinable
            and (
                text[ua] in _NO_START
                or (merged[-1][1] - merged[-1][0]) < _FINE_MIN
                or (ub - ua) < _FINE_MIN
            )
        ):
            merged[-1][1] = ub
            continue
        merged.append([ua, ub])
    merged = _balance_pairs(text, merged)
    for ua, ub in merged:
        if _disp_len(text[ua:ub]) > _FINE_HARD:
            logger.warning(f"字幕行超過 hard limit {_FINE_HARD}：{text[ua:ub]!r}（不可分原子）")
    return [(a, b) for a, b in merged]


def _balance_pairs(text: str, units: list[list[int]]) -> list[list[int]]:
    """相鄰兩行寬度失衡時，在**原子邊界**上挪一個原子讓兩行更平均。

    二十四輪盲審血案：「因果關係其實不 / 容易確定」（7+4）——greedy 打包
    貪到 _FINE_MAX 才斷，把「不容易」切開，短行只剩 0.16s 閃現讀不到。
    平衡後成「因果關係其實 / 不容易確定」（6+5），語意與時間都合理。

    只在同 clause 內、且移動後兩行都 ≤_FINE_HARD、失衡確實下降時才動。
    """
    out = [list(u) for u in units]
    for i in range(len(out) - 1):
        a1, b1 = out[i]
        a2, b2 = out[i + 1]
        if b1 != a2:  # 跨 clause（中間有空白）不動
            continue
        best = abs(_disp_len(text[a1:b1]) - _disp_len(text[a2:b2]))
        atoms = _atom_spans(text, a1, b1)
        if len(atoms) < 2:
            continue
        # 把第一行最後一個原子移到第二行
        sa, sb = atoms[-1]
        if sb != b1:
            continue
        w1, w2 = _disp_len(text[a1:sa]), _disp_len(text[sa:b2])
        if w1 and w2 <= _FINE_HARD and abs(w1 - w2) < best:
            out[i] = [a1, sa]
            out[i + 1] = [sa, b2]
    return out


def _fine_units(s: float, e: float, text: str, words: list[dict]) -> list[tuple[float, float, str]]:
    """單一 cue → 細切單元（詞級時間戳定界；對不齊退回字數比例分配）。"""
    from run_line_polish import _map_to_raw

    spans = _fine_spans(text)
    if len(spans) <= 1:
        return [(s, e, text.strip())] if text.strip() else []
    seg = [w for w in words if w["end"] > s + 1e-3 and w["start"] < e - 1e-3]
    raw = "".join(w["word"] for w in seg)
    ctimes: list[float] = []
    for w in seg:
        n = max(1, len(w["word"]))
        for i in range(n):
            ctimes.append(w["start"] + (w["end"] - w["start"]) * i / n)
    bounds = [s]
    for a, _b in spans[1:]:
        if ctimes:
            j = min(max(_map_to_raw(text, raw, a), 0), len(ctimes) - 1)
            bounds.append(ctimes[j])
        else:
            bounds.append(s + (e - s) * a / max(1, len(text)))
    bounds.append(e)
    ok = all(bounds[i] >= bounds[i - 1] + 0.2 for i in range(1, len(bounds)))
    if not ok:  # 詞級對齊失敗（校正差異太大）→ 字數比例分配
        total = sum(b - a for a, b in spans)
        acc = 0.0
        bounds = [s]
        for a, b in spans[:-1]:
            acc += b - a
            bounds.append(s + (e - s) * acc / total)
        bounds.append(e)
    out = []
    for (a, b), us, ue in zip(spans, bounds, bounds[1:]):
        unit_text = text[a:b].strip()
        if unit_text and ue > us:
            out.append((us, ue, unit_text))
    return _enforce_min_duration(out, e)


def _collapse_t(t: float, segs: list[tuple[float, float]]) -> float:
    """源時間 → 塌縮後（jump-cut 成品）時間。"""
    acc = 0.0
    for s, e in segs:
        if t <= s:
            break
        acc += min(t, e) - s
        if t <= e:
            break
    return acc


def _merge_blocks(
    cues: list[tuple[float, float, str]],
    segs: list[tuple[float, float]],
    max_gap: float = 0.2,
) -> list[list[tuple[float, float, str]]]:
    """相鄰無停頓 cue 分群成語意塊（回傳 **cue 群組**，保留逐 cue 邊界
    當時間錨——十二輪教訓：整塊串接後用 difflib 全域對齊，重複片語
    （無處宣洩/無處發洩/無處去治療）會錯位到前一個出現，整塊後半的
    時間全部提早 1-2s）。

    ⚠️ 相鄰要用**塌縮後**時間判（十一輪教訓 ×2）：
    - 16|歲 兩 cue 之間有停頓剪——源時間有 gap 但成品音軌連續，必須併
    - 且**先過保留段存活過濾再進來**——被剪掉的 backchannel cue、
      片頭前/片尾後的 cue 用源時間判相鄰會把已剪掉的字捲回字幕
    """
    groups: list[list[tuple[float, float, str]]] = []
    for s, e, text in cues:
        gap = (
            max(0.0, _collapse_t(s, segs) - _collapse_t(groups[-1][-1][1], segs)) if groups else 9e9
        )
        if groups and gap < max_gap:
            groups[-1].append((s, e, text))
        else:
            groups.append([(s, e, text)])
    return groups


def _fine_units_grouped(
    group: list[tuple[float, float, str]], words: list[dict]
) -> list[tuple[float, float, str]]:
    """cue 群組 → 細切單元。切行看整塊文字（跨 cue 傷口可癒合），
    **時間錨定逐 cue 局部對齊**（切點先定位所屬 cue，只在該 cue 的詞級
    資料內 map）——錯位上限 = 單一 cue，不會全塊漂移。"""
    from run_line_polish import _map_to_raw

    if len(group) == 1:
        return _fine_units(group[0][0], group[0][1], group[0][2], words)
    texts = [t for _, _, t in group]
    offsets = [0]
    for t in texts:
        offsets.append(offsets[-1] + len(t))
    text = "".join(texts)
    spans = _fine_spans(text)
    if len(spans) <= 1:
        return [(group[0][0], group[-1][1], text.strip())] if text.strip() else []

    def t_at(a: int) -> float:
        k = max(i for i in range(len(group)) if offsets[i] <= a)
        k = min(k, len(group) - 1)
        cs, ce, ct = group[k]
        local = a - offsets[k]
        seg = [w for w in words if w["end"] > cs + 1e-3 and w["start"] < ce - 1e-3]
        raw = "".join(w["word"] for w in seg)
        ctimes: list[float] = []
        for w in seg:
            n = max(1, len(w["word"]))
            for i in range(n):
                ctimes.append(w["start"] + (w["end"] - w["start"]) * i / n)
        if not ctimes:
            return cs + (ce - cs) * local / max(1, len(ct))
        j = min(max(_map_to_raw(ct, raw, local), 0), len(ctimes) - 1)
        return ctimes[j]

    bounds = [group[0][0]]
    for a, _b in spans[1:]:
        bounds.append(t_at(a))
    bounds.append(group[-1][1])
    # 單調修正（局部錨定下只需 clamp，不整塊 fallback）
    for i in range(1, len(bounds)):
        bounds[i] = max(bounds[i], bounds[i - 1] + 0.15)
    bounds[-1] = max(bounds[-1], group[-1][1])
    out = []
    for (a, b), us, ue in zip(spans, bounds, bounds[1:]):
        unit_text = text[a:b].strip()
        if unit_text and ue > us:
            out.append((us, ue, unit_text))
    return _enforce_min_duration(out, group[-1][1])


def _enforce_min_duration(
    units: list[tuple[float, float, str]], hard_end: float
) -> list[tuple[float, float, str]]:
    """一行短於 _MIN_CUE_SEC 就併進鄰居——盲審抓到 0.16s 閃現 cue 讀不到。

    優先「往後借時間」（下一行還沒開始 → 直接延長，不動文字）；借不到
    就與較短的鄰居合併文字（合併後仍受 _FINE_HARD 行寬限制，撐爆就寧可
    只延長時間、讓兩行重疊 0）。
    """
    if not units:
        return units
    out = [list(u) for u in units]
    i = 0
    while i < len(out):
        s, e, txt = out[i]
        if e - s >= _MIN_CUE_SEC:
            i += 1
            continue
        want = s + _MIN_CUE_SEC
        nxt_start = out[i + 1][0] if i + 1 < len(out) else hard_end
        if want <= nxt_start + 1e-6:  # 後面有空檔 → 純延長
            out[i][1] = min(want, nxt_start)
            i += 1
            continue
        # 沒空檔 → 先向前一行「借時間」（把邊界往前挪，前一行仍須 ≥ 下限）
        if i > 0 and abs(out[i - 1][1] - s) < 1e-6:
            ps, pe = out[i - 1][0], out[i - 1][1]
            lend = min(_MIN_CUE_SEC - (e - s), max(0.0, (pe - ps) - _MIN_CUE_SEC))
            if lend > 0.01:
                out[i - 1][1] = pe - lend
                out[i][0] = s - lend
                s = out[i][0]
                if e - s >= _MIN_CUE_SEC - 1e-6:
                    i += 1
                    continue
        # 還是不夠 → 與鄰居合併文字（挑合併後較短的一側，避免爆行寬）
        cand = []
        if i + 1 < len(out) and _disp_len(txt + out[i + 1][2]) <= _FINE_HARD:
            cand.append((_disp_len(txt + out[i + 1][2]), i + 1))
        if i > 0 and _disp_len(out[i - 1][2] + txt) <= _FINE_HARD:
            cand.append((_disp_len(out[i - 1][2] + txt), i - 1))
        if not cand:
            out[i][1] = max(e, min(want, nxt_start))  # 併不了就盡量延長
            i += 1
            continue
        _, j = min(cand)
        if j > i:
            out[i] = [s, out[j][1], txt + out[j][2]]
            del out[j]
        else:
            out[j] = [out[j][0], e, out[j][2] + txt]
            del out[i]
            i = max(0, j)
        # 合併後重新檢查同一位置（可能仍不足）
    return [(a, b, c) for a, b, c in out if c.strip() and b > a]


def _strip_cut_word(text: str, word: str, rel: float) -> str:
    """從 cue 文字移除被剪掉的贅詞：多次出現時取相對位置最接近剪點的那個。"""
    idxs = [m.start() for m in re.finditer(re.escape(word), text)]
    if not idxs:
        return text
    best = min(idxs, key=lambda i: abs(i / max(1, len(text)) - rel))
    out = text[:best] + text[best + len(word) :]
    return re.sub(r"  +", " ", out).strip()


def _retime_srt(
    episode_dir: Path,
    cid: str,
    segs: list[tuple[float, float]],
    cuts: list[dict],
    fine: bool = False,
) -> tuple[Path, int]:
    """字幕依保留段塌縮重對時（版本化路徑繞 Resolve 快取）。

    - cue 跨刀時**不拆行**：刀口在新 timeline 上塌縮為零，取各交集在新時間
      軸上的 min-max 合成一行（拆行會出現同文字連閃兩次）
    - 被剪掉的贅詞（filler/stutter keep=true）**先**從 cue 文字移除再細切——
      音沒了字還在會穿幫；manual strip_text 可能跨細切單元，必須在切前處理
    - backchannel cue 整句被剪 → 與保留段無交集，自然消失
    - fine=True：細切成 5–9 字呼吸單元（詞級時間戳定界，範本節奏）
    """
    cues = _parse_srt(episode_dir / "transcript.srt")
    out_dir = episode_dir / SEG_SRT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 1
    while (out_dir / f"{cid}_tight_r{n:03d}.srt").exists():
        n += 1
    dst = out_dir / f"{cid}_tight_r{n:03d}.srt"

    word_cuts = [
        x
        for x in cuts
        if x.get("keep") is True and x.get("kind") in ("filler", "stutter", "manual")
    ]
    prepped: list[tuple[float, float, str]] = []
    for s, e, text in cues:
        for wc in word_cuts:
            if s <= wc["t0"] < e:
                if wc.get("strip_text"):  # manual：指定整串刪除（空格不敏感比對）
                    pat = r"\s*".join(re.escape(ch) for ch in wc["strip_text"])
                    text = re.sub(r"  +", " ", re.sub(pat, "", text, count=1)).strip()
                else:
                    text = _strip_cut_word(text, wc["word"], (wc["t0"] - s) / max(0.1, e - s))
        if text:
            prepped.append((s, e, text))
    if fine:
        n_hot = _load_episode_hotwords(episode_dir)
        if n_hot:
            logger.info(f"episode 熱詞 {n_hot} 個進 jieba")
        words = json.load(open(episode_dir / "subs" / "words.json", encoding="utf-8"))["words"]
        # 跨 cue 重排（修修十一輪「16|歲」教訓）：上游 cue 邊界可能切在
        # 詞中，逐 cue 細切縫不回來——**先過保留段存活過濾**（被剪 cue 的
        # 字不可回魂）再以塌縮後時間判相鄰併塊，切點重新用詞邊界決定
        surviving = []
        for s, e, text in prepped:
            inter = sum(max(0.0, min(e, se) - max(s, ss)) for ss, se in segs)
            if inter >= 0.15:
                # 數字↔量詞間的 house-style 空格拿掉（「16 歲」→「16歲」）
                # ——在分群**前**逐 cue 做，維持群組 char offset 一致
                surviving.append((s, e, re.sub(rf"(\d) ?(?=[{_CLASSIFIERS}])", "\\1", text)))
        groups = _merge_blocks(surviving, segs)
        prepped = [u for g in groups for u in _fine_units_grouped(g, words)]

    lines = []
    seq = 0
    for s, e, text in prepped:
        spans = []
        offset = 0.0
        for seg_s, seg_e in segs:
            is_, ie = max(s, seg_s), min(e, seg_e)
            if ie - is_ > 0:
                spans.append((offset + is_ - seg_s, offset + ie - seg_s))
            offset += seg_e - seg_s
        if not spans or max(b - a for a, b in spans) < 0.15:
            continue
        seq += 1
        lines.append(f"{seq}\n{_ts(spans[0][0])} --> {_ts(spans[-1][1])}\n{text}\n")
    dst.write_text("\n".join(lines), encoding="utf-8")
    return dst, seq


def apply(episode_dir: Path, cid: str) -> dict:
    from build_resolve_project import _template_path_short, connect_resolve, find_main_video

    c, w = _load_winner(episode_dir, cid)
    t0, t1 = float(c["t_start"]), float(c["t_end"])
    cuts_path = episode_dir / TIGHTEN_DIR / f"{cid}_cuts.json"
    if not cuts_path.exists():
        raise SystemExit(f"{cuts_path} 不存在——先跑 --detect")
    cuts = json.loads(cuts_path.read_text(encoding="utf-8"))["cuts"]
    pending = [x for x in cuts if x.get("keep") is None]
    if pending:
        raise SystemExit(f"{len(pending)} 個候選未複審（keep=null）——agent 先把 cuts.json 複審完")
    segs = _keep_segments(t0, t1, cuts)
    removed = (t1 - t0) - sum(e - s for s, e in segs)

    resolve = connect_resolve()
    pm = resolve.GetProjectManager()
    project_name = episode_dir.name
    project = pm.GetCurrentProject()
    if project is None or project.GetName() != project_name:
        project = pm.LoadProject(project_name)
    if project is None:
        raise SystemExit(f"project「{project_name}」不存在")
    fps = float(project.GetSetting("timelineFrameRate"))
    mp = project.GetMediaPool()
    root = mp.GetRootFolder()

    main_video = find_main_video(episode_dir, None)
    clips = {(x.GetName() or ""): x for x in (root.GetClipList() or [])}
    vid = clips.get(main_video.name)
    aud = clips.get("normalized.wav")
    if vid is None:
        raise SystemExit(f"media pool 找不到主影片 {main_video.name}")

    label = f"{FORMAT_LABEL[c['format']]}{w['rank']} - {c['title']}（緊）"
    # 冪等：同名舊 timeline 先刪
    stale = [
        t
        for i in range(1, project.GetTimelineCount() + 1)
        if (t := project.GetTimelineByIndex(i)) and t.GetName() == label
    ]
    if stale:
        mp.DeleteTimelines(stale)

    hbin = next(
        (f for f in root.GetSubFolderList() if f.GetName() == "Highlights"), None
    ) or mp.AddSubFolder(root, "Highlights")
    mp.SetCurrentFolder(hbin)

    template = _template_path_short()
    tl = None
    if template.exists():
        tl = mp.ImportTimelineFromFile(str(template), {})
        if tl:
            tl.SetName(label)
    else:
        logger.warning(
            f"字幕樣式模板不存在（{template}）——timeline 將是無樣式！"
            "從 E:\\nakama 跑本 script，或設 RESOLVE_SUBTITLE_TEMPLATE"
        )
    if tl is None:
        tl = mp.CreateEmptyTimeline(label)
    if tl is None:
        raise SystemExit(f"timeline 建立失敗: {label}")
    project.SetCurrentTimeline(tl)
    if c["format"] == "short":
        tl.SetSetting("useCustomSettings", "1")
        tl.SetSetting("timelineResolutionWidth", "1080")
        tl.SetSetting("timelineResolutionHeight", "1920")
    if tl.GetTrackCount("subtitle") == 0:
        tl.AddTrack("subtitle")

    # jump-cut 上軌：影片逐段順序 append；音軌逐段 recordFrame 對位（幀數
    # 與影片同一套 int() 換算，累積偏移保持影音同步）
    offset_frames = 0
    tl_start = tl.GetStartFrame()
    for seg_s, seg_e in segs:
        f0, f1 = int(seg_s * fps), int(seg_e * fps)
        ok_v = mp.AppendToTimeline(
            [{"mediaPoolItem": vid, "mediaType": 1, "startFrame": f0, "endFrame": f1}]
        )
        if not ok_v:
            raise SystemExit(f"{label}: 影片段 {seg_s:.1f}-{seg_e:.1f} 上軌失敗")
        if aud is not None:
            mp.AppendToTimeline(
                [
                    {
                        "mediaPoolItem": aud,
                        "mediaType": 2,
                        "trackIndex": 1,
                        "startFrame": f0,
                        "endFrame": f1,
                        "recordFrame": tl_start + offset_frames,
                    }
                ]
            )
        offset_frames += f1 - f0

    mp.SetCurrentFolder(root)
    seg_srt, n_cues = _retime_srt(episode_dir, cid, segs, cuts)
    srt_items = import_srt_tidy(mp, root, seg_srt)
    sub_ok = bool(mp.AppendToTimeline(srt_items)) if srt_items else False
    pm.SaveProject()
    return {
        "status": "tightened",
        "timeline": label,
        "segments": len(segs),
        "cuts": len(segs) - 1,
        "removed_sec": round(removed, 1),
        "duration": f"{t1 - t0:.1f}s → {t1 - t0 - removed:.1f}s",
        "subtitles": sub_ok,
        "cues": n_cues,
    }


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="短片緊湊化：贅詞/停頓 jump-cut")
    parser.add_argument("episode", help="episode 資料夾")
    parser.add_argument("--id", required=True, help="winner id（如 punch-S1）")
    parser.add_argument("--detect", action="store_true", help="偵測切點 → cuts.json")
    parser.add_argument("--apply", action="store_true", help="套用 cuts.json 建（緊）timeline")
    args = parser.parse_args(argv)
    episode_dir = Path(args.episode)
    if args.detect:
        print(json.dumps(detect(episode_dir, args.id), ensure_ascii=False, indent=1))
    elif args.apply:
        print(json.dumps(apply(episode_dir, args.id), ensure_ascii=False, indent=1))
    else:
        parser.error("--detect 或 --apply 擇一")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
