"""cut-tighten：精華段緊湊化 — 贅詞/停頓 jump-cut（長短片共用）。

修修 2026-07-26：短影片節奏要快狠準——開頭的「那、那」口吃絕不能出現，
中間的停頓與贅詞也要剪掉，jump cut 越緊湊越好。

修修 2026-08-03：長片線開工，本 script 泛化成長短片共用（格式從 candidates
的 `format` 欄自動判定，無新 CLI 旗標）。**長片的緊湊化刻意比短片鬆**，
參數表見 FORMAT_TIGHTEN。

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
import os
import re
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
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

from agents.brook.script_video.editorial_master import (  # noqa: E402
    EditorialMasterContractError,
    EditorialMasterRequest,
)
from shared.highlight_materialization import (  # noqa: E402
    HighlightSource,
    build_materialization_receipt,
    write_materialization_receipt,
)
from shared.resolve_append import append_checked  # noqa: E402
from shared.subtitle_finalize import finalize_cues  # noqa: E402

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

# ── 格式參數 ────────────────────────────────────────────────────────────────
# 短片 = 既有已驗收行為（四支短片 2026-07-28 收線），數值原封不動搬進來。
#
# 長片刻意放鬆，依據 docs/research/editing-grammar/2026-07-18：
# - §1.5「呼吸節奏」：A-roll 連續段 p90 16.4s，長段落是讓講解落地的裝置，
#   不是該剪掉的贅肉
# - 建議 7「反 over-editing 條款」：25+ 教育受眾禁止再加密度
#
# 具體差別：長片只剪**真口吃**與**明顯的長停頓（≥1s）**；連接詞用法的
# 「那/就是/然後」與附和「對對對」全部留著——短片剪它們是因為 60s 內沒有
# 揮霍的餘裕，8–12 分鐘沒有這個限制，剪了反而失去訪談的自然感。
FORMAT_TIGHTEN = {
    "short": {
        "min_pause": MIN_PAUSE,
        "keep_head": KEEP_HEAD,
        "keep_tail": KEEP_TAIL,
        "min_cut": MIN_CUT,
        "min_keep_seg": MIN_KEEP_SEG,
        "cut_filler": True,
        "cut_backchannel": True,
        # 短片字卡逐字承接字幕；必須輸出 5–9 字呼吸單元，不能在重跑時
        # 退回一個 cue 20–30 字的長句。
        "fine_subtitles": True,
    },
    "long": {
        # 0.80 是實測定的，不是拍腦袋：謝伯讓 punch-L5（759s）在 -32dB 下的
        # 靜音分佈 = 0.3–0.5s ×100、0.5–0.8s ×33、0.8–1.0s ×6、≥1.0s ×4。
        # 0.8s 以下那 133 個是**說話節奏**（換氣、語句間隔），剪掉就是 over-
        # editing；0.8s 以上的 10 個才是真的空檔。門檻訂 1.0 只抓得到 4 個、
        # 全段只移除 3.7s（0.5%）＝等於沒剪。
        "min_pause": 0.80,
        "keep_head": 0.20,  # 剪完仍留 ~0.35s 靜默——聽得出停頓、但不拖
        "keep_tail": 0.15,
        "min_cut": 0.30,  # 0.12s 的一刀在長片是白跳一下，不值得
        "min_keep_seg": 0.50,
        "cut_filler": False,
        "cut_backchannel": False,
        "fine_subtitles": False,
    },
}

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


def _verified_master_media_pool_item(mp, root, media_path: Path):
    """Return only a media-pool item whose current File Path is the Master.

    Resolve keys clips by display name in many scripts.  A stale/raw clip named
    ``master.mp4`` must not be mistaken for the hash-verified episode artifact.
    """
    expected = os.path.normcase(str(media_path.resolve()))

    def clip_path(item) -> str:
        try:
            return os.path.normcase(str(Path(item.GetClipProperty("File Path") or "").resolve()))
        except (AttributeError, OSError, TypeError):
            return ""

    same_name = [item for item in (root.GetClipList() or []) if item.GetName() == media_path.name]
    exact = [item for item in same_name if clip_path(item) == expected]
    if exact:
        return exact[0]
    if same_name:
        raise SystemExit(
            f"media pool 的 {media_path.name} 指向其他來源——拒絕以同名素材冒充 Editorial Master"
        )
    imported = mp.ImportMedia([str(media_path)]) or []
    if not imported or clip_path(imported[0]) != expected:
        raise SystemExit(f"Editorial Master 匯入或 File Path 驗證失敗：{media_path}")
    return imported[0]


def _commit_materialization_receipt(
    episode_dir: Path,
    *,
    cid: str,
    cut_format: str,
    timeline,
    t0: float,
    t1: float,
    fps: float,
    master,
) -> Path:
    source = HighlightSource(
        srt_path=master.srt_path,
        media_path=master.media_path,
        lineage=master.identity(),
    )
    try:
        payload = build_materialization_receipt(
            episode_dir,
            cut_id=cid,
            cut_format=cut_format,
            timeline=timeline,
            source_range={
                "start_sec": t0,
                "end_sec": t1,
                "start_frame": int(t0 * fps),
                "end_frame": int(t1 * fps),
            },
            source=source,
        )
        # Tighten and director intentionally promote the same cut marker.  The
        # shared writer permits replacement only when Master lineage and range
        # are exact, while requiring the new live Timeline identity.
        return write_materialization_receipt(episode_dir, payload, replace=True)
    except EditorialMasterContractError as exc:
        raise SystemExit(f"materialization receipt 失敗：{exc}") from exc


def _load_winner(
    episode_dir: Path,
    cid: str,
    editorial_master_lineage: dict | None = None,
) -> tuple[dict, dict]:
    hdir = episode_dir / HIGHLIGHTS_DIR
    candidates_doc = json.loads((hdir / "candidates.json").read_text(encoding="utf-8"))
    cands = candidates_doc["candidates"]
    c = next((x for x in cands if x["id"] == cid), None)
    if c is None:
        raise SystemExit(f"{cid} 不在 candidates 中")
    # 當選名單依 format 分檔（winners.short.json / winners.long.json）。
    # 共用一份 winners.json 時，寫短片會洗掉長片那筆——
    # 2026-08-30 實際發生過，長片的 packaging-plan 與 winners 一度互相矛盾。
    # 舊檔名仍然可用（長片目前就走它），不做強制遷移。
    fmt = str(c.get("format") or "")
    per_format = hdir / f"winners.{fmt}.json" if fmt else None
    winners_path = per_format if per_format and per_format.is_file() else hdir / "winners.json"
    winners_doc = json.loads(winners_path.read_text(encoding="utf-8"))
    winners = winners_doc["winners"]
    w = next((x for x in winners if x["id"] == cid), None)
    if w is None:
        raise SystemExit(f"{cid} 不在 {winners_path.name} 中")
    if editorial_master_lineage is not None:
        for source_name, document in (
            ("candidates.json", candidates_doc),
            (winners_path.name, winners_doc),
        ):
            if document.get("editorial_master_lineage") != editorial_master_lineage:
                raise SystemExit(
                    f"{cid} {source_name} Editorial Master lineage 缺失或已過期——請重新 shortlist"
                )
    return c, w


def _open_editorial_master(episode_dir: Path):
    """Open the verified Editorial Master or fail closed.

    Repurpose is deliberately downstream of the human-approved full-program
    edit.  There is no fallback to the Stage 5 release clock, raw program feed,
    camera files, or ``normalized.wav``.
    """
    try:
        return EditorialMasterRequest(
            episode_dir,
            expected_episode_id=episode_dir.name,
        ).open()
    except EditorialMasterContractError as exc:
        raise SystemExit(f"Editorial Master 驗證失敗：{exc}") from exc


# Historical import used by a few downstream scripts.  Keep the symbol while
# changing its semantics to the only production subtitle source.
_open_production_subtitle = _open_editorial_master


def _optional_words(episode_dir: Path) -> list[dict]:
    """Return legacy word timings when present; absence is a safe degradation.

    Memo Dual-Audit V1 currently releases cue-level timings, not authenticated
    word-level timings.  Pause cuts remain audio-derived, while filler/stutter
    proposals are simply omitted until a word-timing artifact is formally
    added to the release contract.
    """
    path = episode_dir / "subs" / "words.json"
    if not path.is_file():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    words = raw.get("words") if isinstance(raw, dict) else raw
    if not isinstance(words, list):
        raise SystemExit(f"{path} 的 words 必須是 array")
    return words


def _assert_cut_master_lineage(cuts_doc: dict, actual: dict) -> None:
    expected = cuts_doc.get("editorial_master_lineage")
    if expected is None:
        raise SystemExit("cuts.json 缺 editorial_master_lineage——請重新執行 --detect")
    if expected != actual:
        raise SystemExit("cuts.json Editorial Master lineage 已過期——請重新執行 --detect")


_assert_cut_subtitle_lineage = _assert_cut_master_lineage


def _subtitle_source_config(episode_dir: Path, cid: str) -> tuple[Path, float, float]:
    """Resolve the text source and its two independent clock mappings.

    Short cuts live on the raw media clock, while an editor-approved Resolve
    subtitle snapshot can live on the edited timeline clock.  Corrected cue
    text therefore needs ``subtitle_clock_offset`` to reach the media clock,
    but ``words.json`` may already be on that media clock and must not inherit
    the same shift.  Candidate metadata makes both mappings explicit instead
    of silently falling back to the uncorrected recognition transcript.
    """
    default = episode_dir / "transcript.srt"
    candidates_path = episode_dir / HIGHLIGHTS_DIR / "candidates.json"
    if not candidates_path.exists():
        return default, 0.0, 0.0
    candidates = json.loads(candidates_path.read_text(encoding="utf-8")).get("candidates", [])
    candidate = next((item for item in candidates if item.get("id") == cid), None)
    if not candidate or not candidate.get("subtitle_source"):
        return default, 0.0, 0.0

    source = Path(str(candidate["subtitle_source"]))
    if not source.is_absolute():
        source = episode_dir / source
    if not source.exists():
        raise FileNotFoundError(f"{cid} 指定的字幕來源不存在: {source}")
    subtitle_offset = float(candidate.get("subtitle_clock_offset", 0.0))
    words_offset = float(candidate.get("words_clock_offset", subtitle_offset))
    return source, subtitle_offset, words_offset


def _detect_silences(
    audio: Path, t0: float, t1: float, min_pause: float = MIN_PAUSE
) -> list[tuple[float, float]]:
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
            f"silencedetect=noise={SILENCE_NOISE}:d={min_pause}",
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
    master = _open_editorial_master(episode_dir)
    c, _w = _load_winner(episode_dir, cid, master.identity())
    fmt = c.get("format", "short")
    cfg = FORMAT_TIGHTEN[fmt]
    t0, t1 = float(c["t_start"]), float(c["t_end"])
    # Legacy words are on the raw/normalized clock.  V1 intentionally has no
    # edit map, so using them against the Master clock could cut the wrong word.
    words: list[dict] = []
    seg_words = [x for x in words if t0 <= x.get("start", 0) < t1]

    cuts: list[dict] = []

    # 1) 真實靜音 → pause cut（機械可信，keep=true）
    for s, e in _detect_silences(master.media_path, t0, t1, cfg["min_pause"]):
        # 段首/段尾的靜音整段剪掉（不留呼吸），中間的留頭尾空隙
        cs = t0 if s <= t0 + 0.05 else s + cfg["keep_head"]
        ce = t1 if e >= t1 - 0.05 else e - cfg["keep_tail"]
        if ce - cs >= cfg["min_cut"]:
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
        if cfg["cut_filler"] and wd in FILLER_WORDS and dur >= FILLER_MIN_DUR:
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
    srt_cues = _parse_srt(master.srt_path) if cfg["cut_backchannel"] else []
    for s, e, text in srt_cues:
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
    payload = {
        "id": cid,
        "t_start": t0,
        "t_end": t1,
        "editorial_master_lineage": master.identity(),
        "cuts": cuts,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    n_review = sum(1 for x in cuts if x["keep"] is None)
    return {
        "status": "detected",
        "format": fmt,
        "file": str(out_path),
        "pauses": sum(1 for x in cuts if x["kind"] == "pause"),
        "need_review": n_review,
    }


def tight_to_feed(t: float, segs: list[tuple[float, float]]) -> float:
    """緊湊後 timeline 時間 → 原始 feed 時間（cuts.json / normalized.wav 時鐘）。"""
    acc = 0.0
    for a, b in segs:
        if t <= acc + (b - a):
            return a + (t - acc)
        acc += b - a
    return segs[-1][1] if segs else t


def feed_to_tight(t: float, segs: list[tuple[float, float]]) -> float | None:
    """原始 feed 時間 → 緊湊後 timeline 時間；落在被剪掉的區間回 None。"""
    acc = 0.0
    for a, b in segs:
        if a <= t <= b:
            return acc + (t - a)
        acc += b - a
    return None


def _timeline_words(
    episode_dir: Path, segs: list[tuple[float, float]], clock_offset: float
) -> list[dict] | None:
    """`words.json` → 緊湊後 timeline 時鐘（被剪掉的字丟棄）。

    切點重修**必須**拿真實詞級時間去問停頓圖。退回 `char_times_from_cues` 的
    「cue 內均分」時，一個 14 字 / 2.5s 的 cue 每字 177ms，而停頓判定的窗只有
    ±60ms——等於拿錯位置去問音檔。2026-08-12「黑馬｜班」實測：同一個候選位置
    內插算出 RMS 0.021（看起來比原位更差）、真值 0.006（其實是個真的音量凹陷），
    整個候選排序是反的，於是那一刀留在詞中間出貨。
    """
    p = episode_dir / "subs" / "words.json"
    if not p.exists():
        return None
    raw = json.load(open(p, encoding="utf-8"))
    words = raw["words"] if isinstance(raw, dict) else raw
    out: list[dict] = []
    for w in words:
        s, e = w.get("start"), w.get("end")
        if s is None or e is None:
            continue
        ts = feed_to_tight(float(s) - clock_offset, segs)
        if ts is None:
            continue
        te = feed_to_tight(float(e) - clock_offset, segs)
        out.append({"word": w["word"], "start": ts, "end": te if te is not None else ts + 0.02})
    return out or None


def _tight_pause_map(
    episode_dir: Path,
    segs: list[tuple[float, float]],
    cid: str,
    source_media: Path | None = None,
):
    """緊湊後 timeline 專用的停頓圖（斷句主判準）。

    音檔 `normalized.wav` 是 **feed 時鐘**，cue 是 timeline 時鐘，靠保留段映射
    回去——零猜測。找不到音檔或時鐘自檢不過就回 None 退回詞典判準，並且**大聲
    講出來**：那條路已知會讓集別詞彙被攔腰切開（2026-08-12「冒牌｜者」）。
    """
    from shared.pause_map import PauseMap, build_envelope, cache_path_for

    audio = source_media or episode_dir / "normalized.wav"
    if not audio.exists():
        logger.warning("%s: 找不到 %s——斷句退回詞典判準（已知會漏集別詞彙）", cid, audio.name)
        return None
    try:
        env = build_envelope(audio, cache_path_for(audio, episode_dir / "subs"))
    except Exception as exc:  # ffmpeg 沒裝 / 檔壞掉
        logger.warning("%s: 停頓圖建不起來（%s）——斷句退回詞典判準", cid, exc)
        return None
    return PauseMap(env, to_audio=lambda t: tight_to_feed(t, segs))


def _keep_segments(
    t0: float, t1: float, cuts: list[dict], min_keep_seg: float = MIN_KEEP_SEG
) -> list[tuple[float, float]]:
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
        if s - pos >= min_keep_seg:
            segs.append((pos, s))
        elif segs:  # 過短保留段：併入前一刀（延伸上一保留段終點沒意義，直接丟）
            pass
        pos = max(pos, e)
    if t1 - pos >= min_keep_seg:
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
    transcript: Path | None = None,
    clock_offset: float = 0.0,
    source_media: Path | None = None,
    allow_legacy_words: bool = True,
    words_clock_offset: float | None = None,
) -> tuple[Path, int]:
    """字幕依保留段塌縮重對時（版本化路徑繞 Resolve 快取）。

    - cue 跨刀時**不拆行**：刀口在新 timeline 上塌縮為零，取各交集在新時間
      軸上的 min-max 合成一行（拆行會出現同文字連閃兩次）
    - 被剪掉的贅詞（filler/stutter keep=true）**先**從 cue 文字移除再細切——
      音沒了字還在會穿幫；manual strip_text 可能跨細切單元，必須在切前處理
    - backchannel cue 整句被剪 → 與保留段無交集，自然消失
    - fine=True：細切成 5–9 字呼吸單元（詞級時間戳定界，範本節奏）

    `transcript` / `clock_offset`（2026-08-12 補）：逐字稿與 `cuts.json` 可能
    **不同時鐘**——本集 `normalized.wav` 有兩份差 71.01s，`transcript.srt` 出自
    `program_v2/` 那份而 cuts 用根目錄那份。舊版硬假設同鐘，重跑會整份錯位
    71 秒且不會報錯（只在字卡驗證時以「與原話落差太大」的形式間接爆出來）。
    `clock_offset` = 逐字稿時鐘 − cuts 時鐘，換算時整份減掉；用
    `shared.pause_map.detect_audio_offset` 量測，不要手寫。
    """
    if transcript is None and clock_offset == 0.0 and words_clock_offset is None:
        transcript, clock_offset, words_clock_offset = _subtitle_source_config(episode_dir, cid)
    else:
        transcript = Path(transcript) if transcript else episode_dir / "transcript.srt"
        if words_clock_offset is None:
            words_clock_offset = clock_offset
    logger.info(
        "%s: 字幕來源 %s（字幕鐘 %+.3fs；詞鐘 %+.3fs）",
        cid,
        transcript,
        clock_offset,
        words_clock_offset,
    )
    cues = _parse_srt(transcript)
    if clock_offset:
        logger.info("逐字稿時鐘校正 %+.3fs（逐字稿 → cuts 時鐘）", -clock_offset)
        cues = [(s - clock_offset, e - clock_offset, t) for s, e, t in cues]
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
        # Memo Dual-Audit 字幕線不產字級時間戳（只有 subs/pause_map_*.npy）。
        # 沒有 words.json 不是災難：下面 _fine_units 對「詞級對齊失敗」本來就
        # 有字數比例分配的退路，空 words 走的是同一條。切點誤差 ~0.1-0.3s
        # （cue 平均 2.5s、細切成兩半），比整支短片做不出來好。
        words_path = episode_dir / "subs" / "words.json"
        if words_path.is_file():
            words = json.load(open(words_path, encoding="utf-8"))["words"]
        else:
            words = []
            logger.warning(
                "%s 不存在——字幕細切退回字數比例分配（詞級時間戳只有 subtitle-gen "
                "那條線會產）。要字級精度就先跑 scripts/run_subtitle_gen.py",
                words_path,
            )
        if words_clock_offset:
            words = [
                {
                    **w,
                    "start": w["start"] - words_clock_offset,
                    "end": w["end"] - words_clock_offset,
                }
                for w in words
                if w.get("start") is not None and w.get("end") is not None
            ]
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

    rows: list[tuple[float, float, str]] = []
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
        rows.append((spans[0][0], spans[-1][1], text))
    # 切點重修（修修 2026-08-06）：塌縮/細切會製造新的壞斷句（怎麼｜做、蠻｜好奇）
    # ——重對時之後、定版之前把切點搬回合法語意邊界
    from shared.subtitle_reboundary import repair_cues

    pause = _tight_pause_map(episode_dir, segs, cid, source_media)
    if pause is not None:
        try:
            pause.sanity_check([r[0] for r in rows[1:]], [(r[0] + r[1]) / 2 for r in rows])
        except ValueError as exc:
            # 時鐘對不上時，帶著錯的停頓圖重修會**主動**把切點搬到錯的位置，
            # 比沒有停頓圖更糟——丟掉它，退回詞典判準，但大聲留紀錄。
            logger.error("%s: 停頓圖時鐘自檢不過（%s）——丟棄，退回詞典判準", cid, exc)
            pause = None
    tl_words = (
        _timeline_words(episode_dir, segs, words_clock_offset) if allow_legacy_words else None
    )
    if tl_words is None:
        logger.warning(
            "%s: 沒有正式 word-level timing——保留 release cue 邊界，不做猜測式重切",
            cid,
        )
        rb = {"moved": 0}
    else:
        # 黏著度語料用**整集**逐字稿，不是這一段——單一 cut 只有 ~2500 字，
        # 字元 bigram 統計會稀疏到抓不到任何東西
        from shared.word_cohesion import Cohesion

        rows, rb = repair_cues(rows, words=tl_words, pause=pause, cohesion=Cohesion(cues))
    if rb["moved"]:
        logger.info(f"{cid}: 切點重修 {rb['moved']} 處（切點搬到音檔靜音處）")
    # 顯示層定版（修修 2026-08-05）：句尾零標點 + cue 間 ≤3s 空隙補平連續顯示
    rows, fstats = finalize_cues(rows, pause=pause)
    if fstats["true_silences"]:
        logger.info(f"{cid}: >3s 真靜默不補 {len(fstats['true_silences'])} 處（字幕該消失）")
    for f in fstats.get("bad_boundaries", [])[:5]:
        logger.warning(f"{cid} 斷句疑點 cue{f['cue']}: …{f['tail']}｜{f['head']}…（{f['reason']}）")
    blocks = (f"{i}\n{_ts(s)} --> {_ts(e)}\n{text}\n" for i, (s, e, text) in enumerate(rows, 1))
    dst.write_text("\n".join(blocks), encoding="utf-8")
    return dst, len(rows)


def apply(episode_dir: Path, cid: str) -> dict:
    from build_resolve_project import (
        _template_path,
        _template_path_short,
        connect_resolve,
    )

    master = _open_editorial_master(episode_dir)
    c, w = _load_winner(episode_dir, cid, master.identity())
    fmt = c.get("format", "short")
    fcfg = FORMAT_TIGHTEN[fmt]
    t0, t1 = float(c["t_start"]), float(c["t_end"])
    cuts_path = episode_dir / TIGHTEN_DIR / f"{cid}_cuts.json"
    if not cuts_path.exists():
        raise SystemExit(f"{cuts_path} 不存在——先跑 --detect")
    cuts_doc = json.loads(cuts_path.read_text(encoding="utf-8"))
    _assert_cut_master_lineage(cuts_doc, master.identity())
    cuts = cuts_doc["cuts"]
    pending = [x for x in cuts if x.get("keep") is None]
    if pending:
        raise SystemExit(f"{len(pending)} 個候選未複審（keep=null）——agent 先把 cuts.json 複審完")
    segs = _keep_segments(t0, t1, cuts, fcfg["min_keep_seg"])
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

    vid = _verified_master_media_pool_item(mp, root, master.media_path)
    # The approved Master's embedded audio is the only legal audio source.
    aud = vid

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

    # 短片走直式 preset 模板；長片維持主模板（16:9，與主 timeline 同樣式）
    template = _template_path_short() if fmt == "short" else _template_path()
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
    if fmt == "short":
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
        # AppendToTimeline 失敗回 [None]（truthy）——2026-08-04 util-L4 事故
        if not ok_v or (isinstance(ok_v, list) and ok_v[0] is None):
            raise SystemExit(f"{label}: 影片段 {seg_s:.1f}-{seg_e:.1f} 上軌失敗（回 {ok_v!r}）")
        if aud is not None:
            append_checked(
                mp,
                [
                    {
                        "mediaPoolItem": aud,
                        "mediaType": 2,
                        "trackIndex": 1,
                        "startFrame": f0,
                        "endFrame": f1,
                        "recordFrame": tl_start + offset_frames,
                    }
                ],
                f"{label}: Master audio {seg_s:.1f}-{seg_e:.1f}",
            )
        offset_frames += f1 - f0

    mp.SetCurrentFolder(root)
    seg_srt, n_cues = _retime_srt(
        episode_dir,
        cid,
        segs,
        cuts,
        transcript=master.srt_path,
        source_media=master.media_path,
        allow_legacy_words=False,
        fine=bool(fcfg["fine_subtitles"]),
    )
    srt_items = import_srt_tidy(mp, root, seg_srt)
    sub_ok = bool(mp.AppendToTimeline(srt_items)) if srt_items else False
    if not sub_ok:
        raise SystemExit(f"{label}: Editorial Master 字幕上軌失敗，不寫 materialization receipt")
    if not pm.SaveProject():
        raise SystemExit(f"{label}: Resolve SaveProject 失敗，不寫 materialization receipt")
    materialization_receipt = _commit_materialization_receipt(
        episode_dir,
        cid=cid,
        cut_format=fmt,
        timeline=tl,
        t0=t0,
        t1=t1,
        fps=fps,
        master=master,
    )
    return {
        "status": "tightened",
        "format": fmt,
        "timeline": label,
        "segments": len(segs),
        "cuts": len(segs) - 1,
        "removed_sec": round(removed, 1),
        "duration": f"{t1 - t0:.1f}s → {t1 - t0 - removed:.1f}s",
        "subtitles": sub_ok,
        "cues": n_cues,
        "materialization_receipt": str(materialization_receipt),
        **master.identity(),
    }


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="精華段緊湊化：贅詞/停頓 jump-cut（長短片共用）")
    parser.add_argument("episode", help="episode 資料夾")
    parser.add_argument("--id", required=True, help="winner id（如 punch-S1 / punch-L5）")
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
