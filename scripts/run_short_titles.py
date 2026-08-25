"""short-titles：短片橘底大字 punch 卡 — hyperframes 透明 overlay 層。

修修 2026-07-26 八輪裁決：字卡從 Fusion Text+ 全面改走 hyperframes
（Brook 影片線的 render 引擎，`video/compositions/punch_card/`）。
視覺語彙照鐘穎範本：逐行橘塊（#E87000）緊貼字寬、LINE Seed TW 特黑、
逐行 swipe-in + back-out pop、快收退場。

為什麼換掉 Fusion Text+（v1，PR #1043）：
- 生成器固定 5s 無 API 可調 → 曾被迫用 Opacity 關鍵影格 + 卡距 ≥5s 硬限
- InsertFusionTitle 是插入模式 → 曾被迫走巢狀 timeline 疊軌
- 動畫/排版天花板低（單 style、無 per-line 背景動畫）
hyperframes 渲出 **ProRes 4444 帶 alpha** 的普通 media clip（2026-07-26
實測 DaVinci 合成 OK——順帶補掉 Brook DP 降級表「alpha 未過 DaVinci
驗證」缺口），AppendToTimeline 想放哪放哪、想多長多長。

輸入：highlights/tighten/<id>_titles.json（與 v1 同 schema）
    {"titles": [{"text": "它會改變\\n你的耐心", "t0": 25.9, "t1": 27.8,
                 "pos_y": 0.63}]}
    t0/t1 = （緊·導播）timeline 秒（與 <id>_tight SRT 同軸）

流程：逐卡 `npx hyperframes render`（cache：參數 hash 命中就跳過）→
episode `highlights/tighten/cards/` → 匯入 media pool「Cards」bin →
（緊·導播）timeline video track 3 依 t0 落點、t1 截長。冪等：舊卡
items/media 先清；v1 的「titles - <id>」巢狀層與子 timeline 一併清除。

版本釘死 hyperframes@0.7.72（重現性——它兩天一版，未釘版每次 render 漂
到 latest，且 cache hash 不含引擎版本；升版是有意識的決定：改版號→重渲
樣張驗過→cache 自然失效重建）。

用法：
    python scripts/run_short_titles.py <episode> --id punch-S1 [--stills <dir>]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_highlight_cut import FORMAT_LABEL  # noqa: E402
from run_short_tighten import TIGHTEN_DIR, _load_winner, _open_editorial_master  # noqa: E402

from agents.brook.script_video.highlight_broll import (  # noqa: E402
    BrollContractError,
    verify_visual_recipe_lineage,
)

logger = logging.getLogger("short_titles")

REPO_ROOT = Path(__file__).resolve().parent.parent
COMP_DIR = REPO_ROOT / "video" / "compositions" / "punch_card"
CARDS_DIR = "highlights/tighten/cards"
COMP_SEC = 4.0  # punch_card.html data-duration——show_sec 上限（留 0.2s 裕度）
# 三層字卡架構（修修 2026-07-26 九輪）：tier1=hero（每支≤1 張，全片最強一句，
# 超大字）、tier2=標準 punch 卡、tier3=逐字字幕（走 subtitle track，不在本 script）
# 格式參數（修修 2026-08-03 長片線）。短片欄 = 既有已驗收值，一個字沒動。
#
# 行寬上限的算法是「字級 × 字數 + padding ≤ 畫布寬」：
#   短片 tier2 150px×6 + padding ≈ 964 ≤ 1080；tier1 190px×5 ≈ 1006 ≤ 1080
#   長片 tier2 140px×8 + padding ≈ 1176 ≤ 1920；tier1 200px×6 ≈ 1248 ≤ 1920
# 長片走 punch_card_wide.html：16:9 有寬度沒高度，卡片改走橫幅——每行字數
# 放寬、pos_y 下修避開 ~0.88 起跳的字幕帶。
FORMAT_TITLES = {
    "short": {
        "comp": "punch_card.html",
        "max_line": 6,
        "max_line_hero": 5,
        "pos_y": {1: 0.58, 2: 0.66},
    },
    "long": {
        "comp": "punch_card_wide.html",
        "max_line": 8,
        "max_line_hero": 6,
        "pos_y": {1: 0.60, 2: 0.66},
        # 修修 2026-08-04 四輪定案：長片 hero = paper（白底黑字 + 橘手繪畫線），
        # 逐卡可用 titles.json 的 "style" 覆蓋（orange|paper|ink）
        "style": "paper",
    },
}
# 字卡企劃＝短片的論證骨架（二十五輪修修裁決：「它其實是在支持這整個短影片
# 內容的鋪陳，是不是也要有完整的規劃」）。每張卡必須標明在論證裡承擔哪一拍；
# 寫不出 beat 的卡就是不該存在的卡。
BEATS = ("hook", "mechanism", "evidence", "insight", "closing")
SEC_PER_CARD = 4.5  # 密度上限：卡片總數 ≤ 片長 ÷ 4.5（範本 67s 22 張 ≈ 每 3s 一張）


def _card_hash(variables: dict, comp: str = "punch_card.html") -> str:
    comp_digest = hashlib.md5((COMP_DIR / "compositions" / comp).read_bytes()).hexdigest()[:8]
    payload = json.dumps(variables, ensure_ascii=False, sort_keys=True) + comp + comp_digest
    return hashlib.md5(payload.encode("utf-8")).hexdigest()[:10]


def _render_card(variables: dict, out_path: Path, comp: str = "punch_card.html") -> None:
    """npx hyperframes render → ProRes 4444 alpha mov（約 ~20s/卡）。

    Windows shell=True 走 cmd.exe——單引號不是引號、中文 JSON 會炸，
    variables 一律走 --variables-file（檔案與 mov 同名 sidecar，兼作紀錄）。
    """
    vars_file = out_path.with_suffix(".vars.json")
    vars_file.write_text(json.dumps(variables, ensure_ascii=False, indent=1), encoding="utf-8")
    cmd = (
        f"npx --yes hyperframes@0.7.72 render . -c compositions/{comp} "
        f'-o "{out_path}" --format mov -q standard --quiet --no-browser-gpu '
        f'--variables-file "{vars_file}"'
    )
    logger.info("render card: %s", variables.get("line1"))
    proc = subprocess.run(
        cmd, shell=True, cwd=str(COMP_DIR), capture_output=True, text=True, encoding="utf-8"
    )
    if proc.returncode != 0 or not out_path.exists():
        raise SystemExit(f"hyperframes render 失敗: {(proc.stderr or '')[-400:]}")


_PUNCT = r"[\s，。、？！「」『』（）()《》〈〉·,.?!:;\-—…]"


def _norm(s: str) -> str:
    """比對用正規化：去換行、空白、標點、引號，只留可讀字元。"""
    return re.sub(_PUNCT, "", s)


def _spoken_around(episode_dir: Path, cid: str, t0: float, t1: float) -> str:
    """該時間點前後 ±1.5s 講者實際說的話——字卡必須是它的連續節錄。"""
    srts = sorted((episode_dir / "highlights/srt").glob(f"{cid}_tight_r*.srt"))
    if not srts:
        return ""
    out = []
    blocks = re.split(r"\n\s*\n", srts[-1].read_text(encoding="utf-8").strip())
    for block in blocks:
        ls = block.splitlines()
        if len(ls) < 3:
            continue
        m = re.match(r"(\d+):(\d+):(\d+),(\d+) --> (\d+):(\d+):(\d+),(\d+)", ls[1])
        if not m:
            continue
        s = int(m.group(2)) * 60 + int(m.group(3)) + int(m.group(4)) / 1000
        e = int(m.group(6)) * 60 + int(m.group(7)) + int(m.group(8)) / 1000
        if e > t0 - 1.5 and s < t1 + 1.5:
            out.append(ls[2])
    return "".join(out)


def _load_titles(episode_dir: Path, cid: str) -> tuple[Path, list[dict]]:
    path = episode_dir / TIGHTEN_DIR / f"{cid}_titles.json"
    try:
        titles = json.loads(path.read_text(encoding="utf-8"))["titles"]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise SystemExit(f"{path} 不存在或不是合法 title plan") from exc
    if not isinstance(titles, list):
        raise SystemExit(f"{path} titles 必須是 array")
    return path, titles


def validate_plan(episode_dir: Path, cid: str) -> dict:
    """Read-only preflight for the complete audited content-visual recipe pair."""

    master = _open_editorial_master(episode_dir)
    candidate, _winner = _load_winner(episode_dir, cid, master.identity())
    _path, titles = _load_titles(episode_dir, cid)
    try:
        lineage, _broll = verify_visual_recipe_lineage(
            episode_dir,
            cid,
            str(candidate["format"]),
            master.identity(),
            title_items=titles,
            editorial_master=master,
        )
    except BrollContractError as exc:
        raise SystemExit(f"Title visual production gate 失敗：{exc}") from exc
    return {
        "status": "plan-valid",
        "cut_id": cid,
        "format": candidate["format"],
        "title_count": len(titles),
        "visual_pipeline_content_hash": lineage["content_hash"],
    }


def emit_audited_recipe(
    cid: str,
    materializations: list[dict] | tuple[dict, ...],
    *,
    output_dir: Path,
) -> Path:
    """Deterministically project accepted title materializations into one recipe."""

    from agents.brook.script_video.highlight_visual_pipeline import (
        HighlightVisualContractError,
        validate_materialization_projection,
    )

    titles: list[dict] = []
    for index, raw in enumerate(materializations):
        try:
            projection = validate_materialization_projection(
                raw, label=f"materializations[{index}]"
            )
        except HighlightVisualContractError as exc:
            raise BrollContractError(f"DP title materialization schema 不合法：{exc}") from exc
        if projection["target_lane"] != "title_track3":
            continue
        implementation = projection["implementation_kind"]
        if implementation not in {"hero_title", "supporting_title"}:
            raise BrollContractError(f"DP title implementation 不合法：{implementation}")
        spec = projection["render_spec"]
        if not isinstance(spec, dict) or not isinstance(spec.get("render_params"), dict):
            raise BrollContractError("DP title 缺少 exact render_spec")
        params = spec["render_params"]
        titles.append(
            {
                "text": projection["on_screen_text"],
                "t0": projection["t0"],
                "t1": projection["t1"],
                "tier": 1 if implementation == "hero_title" else 2,
                "style": params["style"],
                "pos_y": params["pos_y"],
                "source_range": projection["source_range"],
                "media_path": projection["media"]["path"],
                "provenance": projection["provenance"],
                "render_spec": projection["render_spec"],
                "visual_materialization": projection,
            }
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / f"{cid}_titles.json"
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps({"titles": titles}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, destination)
    return destination


def apply(episode_dir: Path, cid: str, stills_dir: Path | None = None) -> dict:
    from build_resolve_project import connect_resolve

    master = _open_editorial_master(episode_dir)
    c, w = _load_winner(episode_dir, cid, master.identity())
    fmt = c.get("format", "short")
    fcfg = FORMAT_TITLES[fmt]
    _titles_path, titles = _load_titles(episode_dir, cid)
    try:
        visual_lineage, _broll = verify_visual_recipe_lineage(
            episode_dir,
            cid,
            str(fmt),
            master.identity(),
            title_items=titles,
            editorial_master=master,
        )
    except BrollContractError as exc:
        raise SystemExit(f"Title visual production gate 失敗：{exc}") from exc
    titles.sort(key=lambda x: x["t0"])
    heroes = [x for x in titles if int(x.get("tier", 2)) == 1]
    if not 1 <= len(heroes) <= 3:
        raise SystemExit(
            f"hero（tier 1）有 {len(heroes)} 張——修修二十七輪：1–3 張（中段一張論點、片尾一張收束）"
        )
    # 1) Consume the exact DP-rendered, Director-audited preview bytes.  This
    # materializer never re-renders or mutates an accepted render spec.
    jobs = []
    for i, t in enumerate(titles):
        show_sec = round(float(t["t1"]) - float(t["t0"]), 2)
        if not 0.5 <= show_sec <= COMP_SEC - 0.2:
            raise SystemExit(
                f"卡片 {i} 顯示 {show_sec}s 超出範圍（0.5–{COMP_SEC - 0.2}s）——"
                "composition data-duration 固定 4s，更長的卡拆兩張或改 t1"
            )
        lines = t["text"].split("\n")
        tier = int(t.get("tier", 2))
        if tier not in (1, 2):
            raise SystemExit(f"卡片 {i} tier={tier} 不合法（1=hero 2=標準）")
        limit = fcfg["max_line_hero"] if tier == 1 else fcfg["max_line"]
        too_long = [x for x in lines if len(x) > limit]
        if too_long:
            raise SystemExit(f"卡片 {i}（tier {tier}）行超過 {limit} 字：{too_long}——改寫或拆行")
        projection = t["visual_materialization"]
        mov = (episode_dir / projection["media"]["path"]).resolve()
        params = projection["render_spec"]["render_params"]
        jobs.append(
            {
                "mov": mov,
                "t0": float(t["t0"]),
                "show_sec": show_sec,
                "text": t["text"],
                "pos_y": float(params["pos_y"]),
                "source_start": float(projection["source_range"]["start_sec"]),
                "timeline_name": f"{cid}_title_{projection['materialization_id']}",
            }
        )

    # Re-open both roots immediately before any Resolve access. CURRENT may
    # switch while jobs are prepared; a different generation must never apply.
    master = _open_editorial_master(episode_dir)
    c, w = _load_winner(episode_dir, cid, master.identity())
    try:
        fresh_lineage, _broll = verify_visual_recipe_lineage(
            episode_dir,
            cid,
            str(c["format"]),
            master.identity(),
            title_items=titles,
            editorial_master=master,
        )
    except BrollContractError as exc:
        raise SystemExit(f"Title visual production gate 失敗：{exc}") from exc
    if fresh_lineage != visual_lineage:
        raise SystemExit("Title visual pipeline CURRENT 在準備期間切換，未連線 Resolve")

    # 2) Resolve：匯入 + 疊軌
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

    director_label = f"{FORMAT_LABEL[c['format']]}{w['rank']} - {c['title']}（緊·導播）"
    timelines = {}
    for i in range(1, project.GetTimelineCount() + 1):
        t = project.GetTimelineByIndex(i)
        if t:
            timelines[t.GetName()] = t
    director = timelines.get(director_label)
    if director is None:
        raise SystemExit(f"「{director_label}」不存在——先跑 run_short_director")
    project.SetCurrentTimeline(director)

    dur = (director.GetEndFrame() - director.GetStartFrame()) / fps
    cap = max(3, int(dur / SEC_PER_CARD))
    if len(titles) > cap:
        raise SystemExit(
            f"字卡 {len(titles)} 張超過密度上限 {cap} 張（片長 {dur:.0f}s ÷ "
            f"{SEC_PER_CARD:.0f}s）——每句都想 highlight 反而稀釋畫龍點睛，"
            "砍掉鋪陳句、只留論證骨架的每一拍"
        )

    # 冪等清場：track1+ 的舊卡/巢狀 item、v1 子 timeline、media pool 舊卡
    # ⚠️ 前綴 <cid>_ 會誤殺 broll 的貼紙/概念卡（<cid>_broll_*，track 4）——
    # 十七輪血案：titles 重跑把整條 track 4 滅掉。broll 卡明確排除。
    def _mine(name: str) -> bool:
        return name.startswith((f"{cid}_", f"titles - {cid}")) and not name.startswith(
            f"{cid}_broll_"
        )

    for ti in range(1, director.GetTrackCount("video") + 1):
        stale = [
            it
            for it in (director.GetItemListInTrack("video", ti) or [])
            if _mine(it.GetName() or "")
        ]
        if stale:
            director.DeleteClips(stale)
    if f"titles - {cid}" in timelines:  # v1 Fusion 巢狀子 timeline 退役清除
        mp.DeleteTimelines([timelines[f"titles - {cid}"]])
    cards_bin = next(
        (f for f in root.GetSubFolderList() if f.GetName() == "Cards"), None
    ) or mp.AddSubFolder(root, "Cards")
    stale_clips = [cl for cl in (cards_bin.GetClipList() or []) if _mine(cl.GetName() or "")]
    if stale_clips:
        mp.DeleteClips(stale_clips)

    # 元素互相遮擋防呆（二十五輪血案：字卡「被社群媒體綁架」壓在貼紙上）
    # 字卡（track 3）與貼紙/概念卡（track 4）都在畫面中下段——時間重疊
    # 就一定互相打架。broll.json 是唯一真相，這裡直接擋。
    broll_path = episode_dir / TIGHTEN_DIR / f"{cid}_broll.json"
    if broll_path.exists():
        overlays = [
            it
            for it in json.loads(broll_path.read_text(encoding="utf-8"))["items"]
            if it["kind"] in ("sticker", "concept")
        ]
        for job in jobs:
            a0, a1 = job["t0"], job["t0"] + job["show_sec"]
            for ov in overlays:
                b0, b1 = float(ov["t0"]), float(ov["t1"])
                if not (a0 < b1 and a1 > b0):
                    continue
                # 時間重疊不必然打架——**垂直分層**就能共存（二十八輪：
                # 上一輪為了避讓把貼紙從 2.2s 搬到 8.8s，語意時機整個跑掉。
                # 版面問題要用版面解，不可用時間解）。
                # 貼紙帶：y_pct ± size_pct/2（畫面高比例，粗估用寬度比例）
                y = float(ov.get("y_pct", 46)) / 100
                half = float(ov.get("size_pct", 26)) / 100 * 0.5
                sticker_bottom = y + half
                card_top = float(job.get("pos_y", 0.63)) - 0.09  # 卡片高約 18% 畫面
                if sticker_bottom > card_top:
                    raise SystemExit(
                        f"字卡「{job['text'].replace(chr(10), '／')}」({a0}–{a1:.1f}s) 與"
                        f" {ov['kind']}「{ov.get('slug')}」({b0}–{b1}s) 同時出現且垂直重疊"
                        f"（貼紙下緣 {sticker_bottom:.2f} > 卡片上緣 {card_top:.2f}）"
                        "——把貼紙 y_pct 往上移或縮小 size_pct，**不要改時間**"
                    )

    mp.SetCurrentFolder(cards_bin)
    while director.GetTrackCount("video") < 3:
        director.AddTrack("video")
    made = []
    tl_start = director.GetStartFrame()
    tl_end = director.GetEndFrame()  # 清完舊卡後 = 主畫面實際結束幀
    for job in jobs:
        items = mp.ImportMedia([str(job["mov"])]) or []
        if not items:
            raise SystemExit(f"匯入失敗: {job['mov']}")
        items[0].SetClipProperty("Clip Name", job["timeline_name"])
        record = tl_start + int(job["t0"] * fps)
        # 卡片退場動畫收在 show_sec 內，截到 show_sec + 2 frames；
        # 並鉗位在主畫面結束前——卡片伸出片尾會變「黑底浮卡」（盲審 S2 抓到）
        dur = min(int(job["show_sec"] * fps) + 2, max(1, tl_end - record))
        ok = mp.AppendToTimeline(
            [
                {
                    "mediaPoolItem": items[0],
                    "mediaType": 1,
                    "trackIndex": 3,
                    "recordFrame": record,
                    "startFrame": int(job["source_start"] * fps),
                    "endFrame": int(job["source_start"] * fps) + dur,
                }
            ]
        )
        if not ok:
            raise SystemExit(f"疊軌失敗 @{job['t0']}")
        made.append(
            {"text": job["text"].replace("\n", "/"), "at": job["t0"], "show_sec": job["show_sec"]}
        )
    mp.SetCurrentFolder(root)
    pm.SaveProject()

    stills = []
    if stills_dir is not None:
        stills_dir.mkdir(parents=True, exist_ok=True)
        rjobs = []
        for i, job in enumerate(jobs):
            fr = tl_start + int((job["t0"] + job["show_sec"] / 2) * fps)
            project.SetRenderSettings(
                {
                    "MarkIn": fr,
                    "MarkOut": fr,
                    "TargetDir": str(stills_dir),
                    "CustomName": f"card_{cid}_{i}",
                }
            )
            jid = project.AddRenderJob()
            if jid:
                rjobs.append((jid, f"card_{cid}_{i}"))
        project.StartRendering([j for j, _ in rjobs], isInteractiveMode=False)
        for _ in range(120):
            if not project.IsRenderingInProgress():
                break
            time.sleep(1)
        for jid, name in rjobs:
            project.DeleteRenderJob(jid)
            stills.append(name)

    return {"status": "titled", "timeline": director_label, "cards": made, "stills": stills}


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="短片橘底大字 punch 卡（hyperframes overlay）")
    parser.add_argument("episode", help="episode 資料夾")
    parser.add_argument("--id", required=True, help="winner id（如 punch-S1）")
    parser.add_argument("--stills", help="物化後渲樣張到此資料夾")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="驗 current Director/DP/Audit、exact recipes/media，不連 Resolve",
    )
    args = parser.parse_args(argv)
    result = (
        validate_plan(Path(args.episode), args.id)
        if args.validate_only
        else apply(Path(args.episode), args.id, Path(args.stills) if args.stills else None)
    )
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
