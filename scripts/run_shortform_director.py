"""shortform-director：短片專屬導播——原始機位 × Editorial Master conform map。

**這支只服務短片。** 長片走 `run_short_director.py`（16:9 滿幀、不裁切、
不切鏡），兩條線從這裡開始分家，不再共用同一個入口
（修修 2026-08-30 裁決：長短片流程與呼叫的東西完全獨立）。

## 為什麼可以回到原始機位

ADR-064 曾把「不得從原始機位重建」套到兩線，理由是原始素材裡還留著修修
在完整節目裡剪掉的東西。`shared/editorial_conform.py` 的 conform map 把
**同一組修剪投影到三機與音檔**之後，那個理由消失：被剪掉的段落在任何素材
上都拿不到（`removed_spans()` 就是證據清單）。

於是短片拿回它唯一需要的東西——**知道臉在哪裡**。固定機位的 `face_x` 座標
一集校一次全集通用；成片是切過的混合畫面，人物位置隨鏡頭跳動，永遠沒有
這個保證。

## 內容邊界仍然由 Editorial Master 決定

- 剪點、字幕、時間軸：全部是 Master 時鐘
- **聲音直接取 Master**（已核准的混音），不回頭用 normalized.wav
- 只有**畫面**換成機位，而且逐 shot 經過 conform map 換算

## 畫面語彙（鐘穎 Ep02 校準，2026-08-17）

開場 4 秒上下分割雙人 → 誰講話切誰的機位（<1s 附和不切鏡）→ 同人長 run
每 ~9s 插 1.8s 聽者反應鏡頭 → 內容驅動 punch（`<id>_zoom.json`）。

用法：
    py -3.10 scripts/run_shortform_director.py <episode> --id punch-S02
    py -3.10 scripts/run_shortform_director.py <episode> --id punch-S02 --stills <dir>
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# 幾何、shot 規劃與 Fusion punch 都是與畫面來源無關的機械件，沿用既有實作；
# ADR-067 分家時會把它們搬到中性命名的模組，不再掛在 run_short_* 底下。
from run_highlight_cut import _parse_srt, _ts  # noqa: E402
from run_short_director import (  # noqa: E402
    _configure_timeline,
    _find_media_item_by_path,
    _load_cfg,
    _media_item_path,
    _media_pool_items,
    _pan,
    _panel_props,
    _scurve_expand,
    _speaker_timing_tokens,
    _validate_appended_source_range,
    _validate_media_source_range,
    build_shots,
)
from run_short_tighten import (  # noqa: E402
    FORMAT_TIGHTEN,
    _assert_cut_master_lineage,
    _commit_materialization_receipt,
    _keep_segments,
    _open_editorial_master,
    _retime_srt,
    _verified_master_media_pool_item,
    import_srt_tidy,
)

from shared.editorial_conform import (  # noqa: E402
    load_conform_map,
    project_master_range,
    source_to_master_sec,
)
from shared.resolve_append import append_checked  # noqa: E402
from shared.zh_linebreak import split_clause, wrap_lines  # noqa: E402

logger = logging.getLogger("shortform_director")

TIGHTEN_DIR = Path("highlights") / "tighten"
CONFORM_PATH = Path("editorial-master") / "v1" / "conform-map.v1.json"

#: speaker index → conform map 的來源鍵。0=修修、1=來賓（固定機位配置）。
SPEAKER_SOURCE = {0: "cam1", 1: "cam2"}

#: 字卡 caption 的版面預算（＝`run_short_titles.FORMAT_TITLES["short"]`；
#: tests/scripts/test_shortform_director.py 盯著兩邊不准漂）。mode B 裡一個
#: cue 就是一張卡，排不進這個預算的 cue 必須在**這裡**拆開——字卡層不能拆，
#: 拆了那個 cue 會出現兩次，逐字覆蓋驗證直接不過。
CARD_LINE_CHARS = 10
CARD_MAX_LINES = 2

#: punch 提前量：zoom **要在那句話講出來之前**就推進去（修修 2026-08-30：
#: 「要在他講那一句話前，可能 0.5 秒就要 zoom in，這會讓觀眾產生『等一下要講的
#: 那句話非常重要』的感覺」）。ramp 0.25s 在這 0.5s 內走完，句子出口時畫面已經
#: 定在特寫上。硬切（style=cut）不提前——它要正好落在那個字上才叫重音。
PUNCH_LEAD_SEC = 0.5


def _master_word_speakers(
    episode_dir: Path, cmap: dict, t0: float, t1: float
) -> list[tuple[float, float, int]]:
    """詞級說話者，**投影到成片時間軸**。

    memo／WhisperX 的 token 在原始音檔時鐘上；落在被剪掉區間的 token
    直接丟棄——那些話在成片裡根本不存在，留著會讓切鏡對不上嘴。
    """
    from shared.speaker_assign import assign_word_speakers, detect_mic_tracks, load_envelopes

    tokens = _speaker_timing_tokens(episode_dir)
    mics = detect_mic_tracks(episode_dir / "Audio")
    if len(mics) < 2:
        raise SystemExit("Audio/ 找不到兩軌人聲——沒有分軌就判不出說話者，短片切鏡無法進行")
    envs = load_envelopes(mics, reference=episode_dir / "normalized.wav")
    speakers = assign_word_speakers(tokens, envs)

    out: list[tuple[float, float, int]] = []
    dropped = 0
    last = 0
    for token, spk in zip(tokens, speakers):
        if spk is not None:
            last = spk
        start = source_to_master_sec(cmap, float(token["start"]), source_key="audio")
        end = source_to_master_sec(cmap, float(token["end"]), source_key="audio")
        if start is None or end is None or end <= start:
            dropped += 1
            continue
        if t0 <= start < t1:
            out.append((start, end, spk if spk is not None else last))
    if dropped:
        logger.info("%d 個 token 落在被剪掉的區間，已丟棄", dropped)
    if not out:
        raise SystemExit(f"{t0:.1f}-{t1:.1f}s 內沒有任何說話者 token——無法導播")
    return out


def _load_short_winner(episode_dir: Path, cid: str, lineage: dict) -> tuple[dict, dict]:
    """短片讀 **winners.short.json**——長片的 winners.json 一個字都不碰。

    長短片共用一份 winners.json 的舊做法，寫短片就會洗掉長片那筆
    （2026-08-30 實際發生過，長片的 packaging-plan 與 winners 一度互相矛盾）。
    per-format 檔名是分家的第一步。
    """
    hdir = episode_dir / "highlights"
    winners_path = hdir / "winners.short.json"
    if not winners_path.is_file():
        raise SystemExit(
            f"找不到 {winners_path}——短片的當選名單獨立成檔，"
            "跑 run_cut_shortlist.py --format short --pick 之後改名或另存為它"
        )
    candidates_doc = json.loads((hdir / "candidates.json").read_text(encoding="utf-8"))
    winners_doc = json.loads(winners_path.read_text(encoding="utf-8"))
    candidate = next((x for x in candidates_doc["candidates"] if x["id"] == cid), None)
    winner = next((x for x in winners_doc["winners"] if x["id"] == cid), None)
    if candidate is None or winner is None:
        raise SystemExit(f"{cid} 不在 winners.short.json / candidates.json 中")
    for name, doc in (("candidates.json", candidates_doc), (winners_path.name, winners_doc)):
        if doc.get("editorial_master_lineage") != lineage:
            raise SystemExit(f"{name} 的 Editorial Master lineage 與目前 Master 不符")
    return candidate, winner


def _camera_pieces(cmap: dict, spk: int, master_s: float, master_e: float) -> list[dict]:
    """shot 的成片區間 → 該說話者機位上的來源區間（可能跨修剪接縫而分段）。"""
    key = SPEAKER_SOURCE.get(int(spk))
    if key is None:
        raise SystemExit(f"speaker {spk} 沒有對應機位（短片只用 cam1/cam2）")
    return project_master_range(cmap, master_s, master_e, source_key=key)


def _split_long_cues(srt_path: Path) -> tuple[int, list[str]]:
    """mode B：把排不進字卡版面的 cue，在語法接縫處拆開後原地重寫 SRT。

    mode B 的一個 cue = 一張卡（`_validate_full_transcript_coverage` 要求每個
    cue 恰好被一個 state 承接一次），所以「這句話太長，卡排不下」只能在這裡解。
    字卡層拆會讓同一個 cue 出現兩次，逐字覆蓋驗證直接不過；降級成別的字級樣式
    則是把版面問題推給樣式——2026-08-30 那張三行 hybrid 卡就是這樣長出來的。

    拆點時間用**字元比例**內插。mode B 的這份 SRT 不上字幕軌，它唯一的下游是
    字卡時間，而字卡本來就是逐字內插的——兩邊同一套算法，不會因此對不上。
    """
    rows: list[tuple[float, float, str]] = []
    notes: list[str] = []
    for t0, t1, text in _parse_srt(srt_path):
        body = text.replace("\n", "")
        pieces = split_clause(body, CARD_LINE_CHARS, CARD_MAX_LINES)
        if len(pieces) == 1:
            rows.append((t0, t1, body))
            continue
        notes.append(f"{body}（{len(body)} 字）→ " + " ／ ".join(pieces))
        acc = 0
        for piece in pieces:
            p0 = t0 + (t1 - t0) * acc / len(body)
            acc += len(piece)
            p1 = t0 + (t1 - t0) * acc / len(body)
            rows.append((round(p0, 3), round(p1, 3), piece))
    srt_path.write_text(
        "\n".join(f"{i}\n{_ts(a)} --> {_ts(b)}\n{t}\n" for i, (a, b, t) in enumerate(rows, 1)),
        encoding="utf-8",
    )
    for note in notes:
        logger.info("過長子句拆開：%s", note)
    stuck = [t for _a, _b, t in rows if wrap_lines(t, CARD_LINE_CHARS, CARD_MAX_LINES) is None]
    if stuck:
        raise SystemExit(
            f"以下子句拆過還是排不進字卡版面（{CARD_LINE_CHARS} 字 × {CARD_MAX_LINES} 行）：{stuck}"
        )
    return len(rows), notes


def _cue_index(srt_path: Path) -> list[dict]:
    return [
        {"n": i, "t0": t0, "t1": t1, "text": text.replace("\n", "")}
        for i, (t0, t1, text) in enumerate(_parse_srt(srt_path), 1)
    ]


def _lead(spec: dict, style: str) -> float:
    """起跳提前量（秒）。ramp 預設提前，cut 預設不提前——見 PUNCH_LEAD_SEC。"""
    lead = float(spec.get("lead_sec", PUNCH_LEAD_SEC if style == "ramp" else 0.0))
    if not 0.0 <= lead <= 1.5:
        raise SystemExit(f"lead_sec={lead} 不合法（0–1.5 秒）")
    return lead


def _anchor_time(cue: dict, phrase: str, *, where: str) -> float:
    """punch 的起跳時間＝該 cue 的起點；`phrase` 是用來確認指對句子的。

    `phrase` 必須是這個 cue 的**開頭**。中途起跳曾經用字元比例內插，實測不準到
    會打錯句子：cue 5「那在演化中喜歡玩的物種早就被淘汰了」內插算出「早就被
    淘汰了」在 14.32s，實際放大落在「喜歡玩的物種」上（修修 2026-08-30 二輪
    「"喜歡玩的物種"那邊為什麼又有一個放大的效果？」）。

    句中要精確起跳需要**詞級**時間戳。本集的 speaker timing evidence 是
    memo 的句級 segment（整句一個 token），給不出來——所以這裡直接擋掉，
    不用內插假裝算得出。真要句中起跳，先產出 `subs/words.json`。
    """
    if not cue["text"].startswith(phrase):
        at = cue["text"].find(phrase)
        if at < 0:
            raise SystemExit(
                f"{where}：phrase「{phrase}」不在 cue {cue['n']}「{cue['text']}」裡——"
                "zoom 企劃與最新 SRT 對不上，重寫企劃、不要調數字"
            )
        raise SystemExit(
            f"{where}：phrase「{phrase}」在 cue {cue['n']}「{cue['text']}」的第 {at} 個字，"
            "不是句首。句中起跳要詞級時間戳（subs/words.json），本集只有句級 segment"
            "——改錨在句首，或先產出詞級時間戳"
        )
    return cue["t0"]


def _resolve_punches(punches: list[dict], cues: list[dict], cfg: dict) -> list[dict]:
    """cue／phrase 錨定的 punch 企劃 → 絕對時間區間。

    2026-08-30 修修驗收抓到的兩個病，根因是同一件事：punch 區間是手寫的
    timeline 秒數，沒有任何東西檢查它落在句子的哪裡。

    - `t1=12.53` 落在下一句開頭 0.67s 處 → 「那在演化中喜歡玩的物種早就被
      淘汰了」講到一半鏡頭先拉遠，1.7s 後又拉近（「為什麼要拉遠又拉近」）
    - `t0=32.78` 落在前一句的第 14 個字 → 本來要打在「所以玩是一種模擬」的
      zoom，打在「…會使用到的肌肉」上（「又在很奇怪的地方 zoom in」）

    所以 punch 不再寫秒數，改寫**逐字稿座標**：`cue` 指哪一句、`phrase` 指這句
    話的哪個詞起跳（必須是該 cue 的原文子字串）、`until_cue` 指放掉的那一句
    （放在該句**句尾**）。`steps` 讓同一段論述中途再進一階而**不放掉**——鋪陳
    與爆點是同一個修辭單位，中間鬆手就是那個「拉遠又拉近」。
    """
    by_n = {c["n"]: c for c in cues}
    out: list[dict] = []
    prev_until = 0
    for i, p in enumerate(punches):
        where = f"punch {i}"
        if "t0" in p or "t1" in p:
            raise SystemExit(
                f"{where}：zoom 企劃還是舊的絕對秒數格式（t0/t1）。短片線改用 cue 錨定"
                "（cue / phrase / until_cue），請對著最新 tight SRT 重寫這份企劃"
            )
        cue_n, until_n = int(p["cue"]), int(p.get("until_cue", p["cue"]))
        for n in (cue_n, until_n):
            if n not in by_n:
                raise SystemExit(f"{where}：cue {n} 不在最新 SRT（共 {len(cues)} 句）")
        if until_n < cue_n:
            raise SystemExit(f"{where}：until_cue={until_n} 在 cue={cue_n} 之前")
        if cue_n <= prev_until:
            raise SystemExit(
                f"{where}：cue {cue_n} 落在上一個 punch（到 cue {prev_until}）的區間內——"
                "在同一句話裡放掉再拉回就是那個「拉遠又拉近」。要嘛合併成一個 punch"
                "（用 steps 再進一階），要嘛把上一個 punch 收在更前面的句尾"
            )
        prev_until = until_n
        style = str(p.get("style", "ramp"))
        attack = _anchor_time(by_n[cue_n], str(p["phrase"]), where=where) - _lead(p, style)
        if attack < 0.0:
            raise SystemExit(f"{where}：提前量把起跳推到 {attack:.2f}s，片頭之前")
        release = by_n[until_n]["t1"]
        base = float(p.get("scale", cfg["punch_scale"]))
        steps: list[dict] = []
        last_t, last_scale = attack, base
        for k, st in enumerate(p.get("steps") or []):
            s_n = int(st["cue"])
            if not cue_n <= s_n <= until_n:
                raise SystemExit(f"{where} step {k}：cue {s_n} 不在 punch 的 {cue_n}–{until_n} 內")
            s_style = str(st.get("style", "cut"))
            s_t = _anchor_time(by_n[s_n], str(st["phrase"]), where=f"{where} step {k}") - _lead(
                st, s_style
            )
            s_scale = float(st["scale"])
            if s_t <= last_t + cfg["punch_ramp_sec"] or s_scale <= last_scale:
                raise SystemExit(
                    f"{where} step {k}：時間或倍率沒有往前推"
                    f"（{last_t:.2f}s×{last_scale} → {s_t:.2f}s×{s_scale}）"
                )
            steps.append({"t": round(s_t, 3), "style": s_style, "scale": s_scale})
            last_t, last_scale = s_t, s_scale
        if release <= last_t + cfg["punch_ramp_sec"]:
            raise SystemExit(f"{where}：放掉的點 {release:.2f}s 太貼近最後一次進階 {last_t:.2f}s")
        if out and attack <= out[-1]["t1"]:
            raise SystemExit(
                f"{where}：提前 {_lead(p, style):.2f}s 之後起跳點 {attack:.2f}s "
                f"落在上一個 punch 的釋放點 {out[-1]['t1']:.2f}s 之前——兩段離太近，"
                "要嘛合併，要嘛把提前量調小"
            )
        out.append(
            {
                "t0": round(attack, 3),
                "t1": round(release, 3),
                "style": style,
                "scale": base,
                "steps": steps,
                "cue": cue_n,
                "until_cue": until_n,
                "phrase": str(p["phrase"]),
                "why": p.get("why") or p.get("note"),
            }
        )
    return out


def _punch_curve(punch: dict, ramp: float, fps: float) -> list[tuple[float, float]]:
    """單一 punch → 時間軸上的 Size 曲線（進場 → 逐階升 → 句尾才放掉）。"""
    cut = 1.0 / fps
    lead = ramp if punch["style"] == "ramp" else cut
    pts = [(punch["t0"], 1.0), (punch["t0"] + lead, punch["scale"])]
    level = punch["scale"]
    for st in punch["steps"]:
        pts.append((st["t"], level))
        pts.append((st["t"] + (ramp if st["style"] == "ramp" else cut), st["scale"]))
        level = st["scale"]
    pts.append((punch["t1"] - ramp, level))
    pts.append((punch["t1"], 1.0))
    return _scurve_expand(pts)


def _slice_curve(pts: list[tuple[float, float]], lo: float, hi: float):
    """曲線與單一 shot item 的交集 → item 內的區域關鍵影格（跨刀時連續）。"""
    if hi <= pts[0][0] + 1e-6 or lo >= pts[-1][0] - 1e-6:
        return None

    def value(t: float) -> float:
        if t <= pts[0][0] or t >= pts[-1][0]:
            return 1.0
        for (a, va), (b, vb) in zip(pts, pts[1:]):
            if a <= t <= b:
                return va if b <= a else va + (vb - va) * (t - a) / (b - a)
        return 1.0

    keys = [(0.0, value(lo))]
    keys += [(t - lo, v) for t, v in pts if lo < t < hi]
    keys.append((hi - lo, value(hi)))
    out: list[tuple[float, float]] = []
    for t, v in keys:
        if out and t <= out[-1][0] + 1e-6:
            continue
        out.append((t, v))
    return out if len(out) >= 2 else None


def _apply_shortform_punches(
    appended: list[dict], resolved: list[dict], fps: float, cfg: dict
) -> int:
    """短片 punch：把 cue 錨定曲線寫進覆蓋到的 talk shot（Fusion Transform）。

    與長片的 `_apply_punch_zooms` 分家（修修 2026-08-30：長短片流程與呼叫的
    東西完全獨立）。機制沿用：MediaIn→Transform→MediaOut、Size 關鍵影格取樣、
    **Pivot 鎖臉**（Center 是位置不是支點，勿踩）、與 item 靜態 ZoomX 疊乘。
    """
    n = 0
    for punch in resolved:
        curve = _punch_curve(punch, cfg["punch_ramp_sec"], fps)
        for a in appended:
            if a["kind"] == "reaction":
                continue
            keys = _slice_curve(curve, a["tl_s"], a["tl_e"])
            if not keys:
                continue
            item = a["item"]
            comp = (
                item.GetFusionCompByIndex(1)
                if item.GetFusionCompCount() > 0
                else item.AddFusionComp()
            )
            if comp is None:
                logger.warning("AddFusionComp 失敗 @%.1fs——此 shot 跳過 punch", a["tl_s"])
                continue
            if a["spk"] is None:
                cx, cy = 0.5, 0.5
            else:
                cx = float(cfg["face_x"][str(a["spk"])]) / 1920
                cy = 1.0 - float(cfg["face_y"][str(a["spk"])]) / 1080  # Fusion y 向上
            key_lua = "\n".join(
                f'  xf:SetInput("Size", {v:.5f}, {round(t * fps, 2)})' for t, v in keys
            )
            lua = f"""
local ok, err = pcall(function()
  local mi = comp:FindToolByID("MediaIn")
  local mo = comp:FindToolByID("MediaOut")
  local xf = comp:FindTool("PunchZoom")
  if xf == nil then
    xf = comp:AddTool("Transform", -32768, -32768)
    xf:SetAttrs({{TOOLS_Name = "PunchZoom"}})
    xf.Input = mi.Output
    mo.Input = xf.Output
    xf.Pivot = {{{cx:.4f}, {cy:.4f}}}
    xf.Size = comp:BezierSpline()
  end
{key_lua}
end)
"""
            comp.Execute(lua)
            n += 1
    return n


def direct(
    episode_dir: Path, cid: str, stills_dir: Path | None = None, opener: bool = True
) -> dict:
    from build_resolve_project import _template_path_short, connect_resolve

    master = _open_editorial_master(episode_dir)
    cmap_path = episode_dir / CONFORM_PATH
    if not cmap_path.is_file():
        raise SystemExit(f"找不到 conform map（{cmap_path}）——先跑 scripts/build_conform_map.py")
    cmap = load_conform_map(cmap_path)
    if cmap.get("editorial_master_lineage", {}).get("content_hash") != master.identity().get(
        "content_hash"
    ):
        raise SystemExit("conform map 綁的不是目前這份 Editorial Master——重建 conform map")

    c, w = _load_short_winner(episode_dir, cid, master.identity())
    if c.get("format") != "short":
        raise SystemExit(f"{cid} 不是短片——長片走 run_short_director.py")
    t0, t1 = float(c["t_start"]), float(c["t_end"])
    cfg = _load_cfg(episode_dir, "short")

    cuts_path = episode_dir / TIGHTEN_DIR / f"{cid}_cuts.json"
    if not cuts_path.exists():
        raise SystemExit(f"{cuts_path} 不存在——先跑 run_short_tighten --detect + 複審")
    cuts_doc = json.loads(cuts_path.read_text(encoding="utf-8"))
    _assert_cut_master_lineage(cuts_doc, master.identity())
    cuts = cuts_doc["cuts"]
    if any(x.get("keep") is None for x in cuts):
        raise SystemExit("cuts.json 有未複審項（keep=null）——先複審")
    segs = _keep_segments(t0, t1, cuts, FORMAT_TIGHTEN["short"]["min_keep_seg"])

    words = _master_word_speakers(episode_dir, cmap, t0, t1)
    shots = build_shots(segs, words, cfg)

    opener_span: tuple[float, float] | None = None
    if opener and segs:
        o_end = min(segs[0][0] + cfg["opener_sec"], segs[0][1])
        if o_end - segs[0][0] >= 2.0:
            opener_span = (segs[0][0], o_end)
            trimmed: list[dict] = []
            for sh in shots:  # 開場那段畫面改由雙 panel 提供，從 shot list 裁掉
                if sh["e"] <= opener_span[1]:
                    continue
                trimmed.append({**sh, "s": max(sh["s"], opener_span[1])})
            shots = trimmed

    resolve = connect_resolve()
    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()
    if project is None or project.GetName() != episode_dir.name:
        project = pm.LoadProject(episode_dir.name)
    if project is None:
        raise SystemExit(f"project「{episode_dir.name}」不存在")
    fps = float(project.GetSetting("timelineFrameRate"))
    mp = project.GetMediaPool()
    root = mp.GetRootFolder()

    clips = list(_media_pool_items(root))
    cams_bin = next(
        (f for f in root.GetSubFolderList() if f.GetName() in {"Cams", "Cameras"}), None
    )

    def _cam(rel: str):
        """以完整路徑綁定機位——只比對檔名會綁到別支短片並讓 Resolve 靜默夾邊界。"""
        nonlocal cams_bin
        expected = episode_dir / rel
        clip = _find_media_item_by_path(clips, expected)
        if clip is None:
            if cams_bin is None:
                cams_bin = mp.AddSubFolder(root, "Cams")
            mp.SetCurrentFolder(cams_bin)
            imported = mp.ImportMedia([str(expected)]) or []
            mp.SetCurrentFolder(root)
            clip = _find_media_item_by_path(imported, expected)
            if clip is None:
                raise SystemExit(
                    f"機位匯入失敗：{expected}（實際 {[_media_item_path(i) for i in imported]}）"
                )
            clips.extend(imported)
        return clip

    cam_items = {spk: _cam(cmap["sources"][key]["path"]) for spk, key in SPEAKER_SOURCE.items()}
    master_item = _verified_master_media_pool_item(mp, root, master.media_path)

    label = f"短{w['rank']} - {c['title']}（緊·導播）"
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
    tl = mp.ImportTimelineFromFile(str(template), {}) if template.exists() else None
    if tl:
        tl.SetName(label)
    else:
        logger.warning("字幕樣式模板不存在（%s）——timeline 將是無樣式", template)
        tl = mp.CreateEmptyTimeline(label)
    if tl is None:
        raise SystemExit(f"timeline 建立失敗：{label}")
    project.SetCurrentTimeline(tl)
    _configure_timeline(tl, fmt="short", fps=fps)
    if tl.GetTrackCount("subtitle") == 0:
        tl.AddTrack("subtitle")
    tl_start = tl.GetStartFrame()

    def _set_props(item, props: dict[str, float]) -> None:
        for key, value in props.items():
            item.SetProperty(key, value)

    def _append_cam(clip, src_s: float, src_e: float, extra: dict | None = None):
        f0, f1 = int(round(src_s * fps)), int(round(src_e * fps))
        if f1 <= f0:
            return None
        _validate_media_source_range(clip, f0, f1, project_fps=fps)
        # trackIndex 一律明示。不給的話 AppendToTimeline 會跟著 Resolve 當下的
        # auto track selector 走——2026-08-30 實測它把主鏡落到 v2，接著
        # GetItemListInTrack("video", 1) 是空的，整支導播 IndexError 掛掉。
        # 這條相依看不見也不可控（前一支 script 或使用者點過畫面就會變），
        # 唯一的解是每次都講清楚要哪一軌。
        spec = {
            "mediaPoolItem": clip,
            "mediaType": 1,
            "startFrame": f0,
            "endFrame": f1,
            "trackIndex": 1,
        }
        if extra:
            spec.update(extra)
        append_checked(mp, [spec], f"{label}: cam {src_s:.1f}-{src_e:.1f}")
        track = spec["trackIndex"]
        item = (tl.GetItemListInTrack("video", track) or [])[-1]
        _validate_appended_source_range(item, f0, f1)
        return item

    # 開場上下分割：下半＝修修（track 1 先落），上半＝來賓（track 2 後補）
    if opener_span:
        for spk, top, track in ((0, False, 1), (1, True, 2)):
            pieces = _camera_pieces(cmap, spk, opener_span[0], opener_span[1])
            cursor = tl_start
            for piece in pieces:
                extra = {"trackIndex": track, "recordFrame": cursor} if track == 2 else None
                item = _append_cam(
                    cam_items[spk], piece["source_start_sec"], piece["source_end_sec"], extra
                )
                if item is not None:
                    _set_props(item, _panel_props(cfg, spk, top=top))
                cursor += int(round((piece["source_end_sec"] - piece["source_start_sec"]) * fps))

    appended: list[dict] = []
    tl_cursor = (opener_span[1] - opener_span[0]) if opener_span else 0.0
    for sh in shots:
        for piece in _camera_pieces(cmap, sh["spk"], sh["s"], sh["e"]):
            item = _append_cam(
                cam_items[sh["spk"]], piece["source_start_sec"], piece["source_end_sec"]
            )
            if item is None:
                continue
            _set_props(
                item,
                {
                    "ZoomX": sh["zoom"],
                    "ZoomY": sh["zoom"],
                    "Pan": _pan(cfg, sh["spk"], sh["zoom"]),
                },
            )
            span = piece["source_end_sec"] - piece["source_start_sec"]
            appended.append(
                {
                    "item": item,
                    "tl_s": tl_cursor,
                    "tl_e": tl_cursor + span,
                    "kind": sh.get("kind", "talk"),
                    "spk": sh["spk"],
                    "zoom": sh["zoom"],
                    # _grab_stills 用 s/e 算樣張落點；時間軸與來源 1:1，直接對應
                    "s": tl_cursor,
                    "e": tl_cursor + span,
                }
            )
            tl_cursor += span

    # ── 逐字稿定版 ──────────────────────────────────────────────────
    # 短片的字是**獨立製作的動態字卡**（run_shortform_titles），不是 Resolve 的
    # burn-in 字幕軌。字卡企劃宣告 covers_full_transcript 時，底部字幕必須清掉，
    # 否則畫面會同時出現兩層字（修修 2026-08-30 指正）。
    titles_plan_path = episode_dir / TIGHTEN_DIR / f"{cid}_titles.json"
    covers_full_transcript = False
    if titles_plan_path.is_file():
        try:
            covers_full_transcript = bool(
                json.loads(titles_plan_path.read_text(encoding="utf-8")).get(
                    "covers_full_transcript", False
                )
            )
        except (OSError, UnicodeError, json.JSONDecodeError):
            covers_full_transcript = False

    # mode B（covers_full_transcript）裡這份 SRT 的角色是**逐字證據**，不是顯示層，
    # 所以不做呼吸單元細切——保留 Master 的 cue 邊界，那就是語意子句邊界。
    # 細切過的 cue 會跨子句合併（「早就被淘汰了可是」），字卡沒辦法對齊到它。
    mp.SetCurrentFolder(root)
    seg_srt, n_cues = _retime_srt(
        episode_dir,
        cid,
        segs,
        cuts,
        transcript=master.srt_path,
        source_media=master.media_path,
        allow_legacy_words=False,
        fine=not covers_full_transcript,
    )
    split_notes: list[str] = []
    if covers_full_transcript:
        n_cues, split_notes = _split_long_cues(seg_srt)

    # ── punch zoom：逐字稿座標，不是手寫秒數 ────────────────────────
    # 這份 SRT 的 cue 就是 punch 企劃的座標系；先定版才有得錨定。
    zoom_path = episode_dir / TIGHTEN_DIR / f"{cid}_zoom.json"
    n_punch = 0
    resolved_punches: list[dict] = []
    if zoom_path.exists():
        resolved_punches = _resolve_punches(
            json.loads(zoom_path.read_text(encoding="utf-8"))["punches"],
            _cue_index(seg_srt),
            cfg,
        )
        n_punch = _apply_shortform_punches(appended, resolved_punches, fps, cfg)
        # 下游（音效）吃的是絕對秒數；解析結果落檔，順便留下這一輪的可稽核紀錄
        (episode_dir / TIGHTEN_DIR / f"{cid}_zoom.resolved.json").write_text(
            json.dumps(
                {"srt": seg_srt.name, "punches": resolved_punches}, ensure_ascii=False, indent=2
            )
            + "\n",
            encoding="utf-8",
        )

    # 聲音取已核准的 Master 混音——只有畫面換機位，聲音一格都不動
    offset_frames = 0
    for seg_s, seg_e in segs:
        f0, f1 = int(round(seg_s * fps)), int(round(seg_e * fps))
        append_checked(
            mp,
            [
                {
                    "mediaPoolItem": master_item,
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

    if covers_full_transcript:
        logger.info("%s: 字卡覆蓋全文——SRT 只當逐字證據，不上字幕軌", cid)
    else:
        srt_items = import_srt_tidy(mp, root, seg_srt)
        if not (bool(mp.AppendToTimeline(srt_items)) if srt_items else False):
            raise SystemExit(f"{label}: 字幕上軌失敗")
    if not pm.SaveProject():
        raise SystemExit(f"{label}: Resolve SaveProject 失敗")

    # 導播把 timeline 砍掉重建，UID 會換掉。不重新蓋 materialization receipt 的話，
    # 下游（素材層、自檢包）綁 live timeline 時一律 fail closed，錯誤訊息還是
    # 「live Resolve timeline differs from materialization receipt」，看不出根因。
    # 長片線在 run_short_director 的同一個位置做同一件事。
    materialization_receipt = _commit_materialization_receipt(
        episode_dir,
        cid=cid,
        cut_format="short",
        timeline=tl,
        t0=t0,
        t1=t1,
        fps=fps,
        master=master,
    )

    result = {
        "status": "directed",
        "format": "short",
        "timeline": label,
        "source_mode": "conformed_cameras",
        "shots": len(appended),
        "cam_switches": sum(1 for a, b in zip(appended, appended[1:]) if a["spk"] != b["spk"]),
        "reaction_shots": sum(1 for a in appended if a["kind"] == "reaction"),
        "split_opener_sec": round(opener_span[1] - opener_span[0], 2) if opener_span else 0.0,
        "punch_ramps": n_punch,
        "burned_subtitles": not covers_full_transcript,
        "cues": n_cues,
        "split_clauses": split_notes,
        "punches": [
            {"t0": x["t0"], "t1": x["t1"], "cue": x["cue"], "phrase": x["phrase"]}
            for x in resolved_punches
        ],
        "duration_sec": round(tl_cursor, 2),
        "materialization": materialization_receipt.name,
    }
    if stills_dir is not None:
        from run_short_director import _grab_stills

        opener_frames = int(round((opener_span[1] - opener_span[0]) * fps)) if opener_span else 0
        result["stills"] = _grab_stills(
            resolve, project, tl, appended, fps, Path(stills_dir), opener_frames
        )
    return result


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="短片導播：原始機位 × conform map")
    parser.add_argument("episode")
    parser.add_argument("--id", required=True, help="winner id（如 punch-S02）")
    parser.add_argument("--stills", help="物化後抓樣張到此資料夾")
    parser.add_argument("--no-opener", action="store_true", help="不做上下分割開場")
    args = parser.parse_args(argv)
    out = direct(
        Path(args.episode),
        args.id,
        Path(args.stills) if args.stills else None,
        opener=not args.no_opener,
    )
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
