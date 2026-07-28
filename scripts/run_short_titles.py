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
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_highlight_cut import FORMAT_LABEL  # noqa: E402
from run_short_tighten import TIGHTEN_DIR, _load_winner  # noqa: E402

logger = logging.getLogger("short_titles")

REPO_ROOT = Path(__file__).resolve().parent.parent
COMP_DIR = REPO_ROOT / "video" / "compositions" / "punch_card"
CARDS_DIR = "highlights/tighten/cards"
COMP_SEC = 4.0  # punch_card.html data-duration——show_sec 上限（留 0.2s 裕度）
# 三層字卡架構（修修 2026-07-26 九輪）：tier1=hero（每支≤1 張，全片最強一句，
# 超大字）、tier2=標準 punch 卡、tier3=逐字字幕（走 subtitle track，不在本 script）
DEFAULT_POS_Y = {1: 0.58, 2: 0.66}
# 字卡企劃＝短片的論證骨架（二十五輪修修裁決：「它其實是在支持這整個短影片
# 內容的鋪陳，是不是也要有完整的規劃」）。每張卡必須標明在論證裡承擔哪一拍；
# 寫不出 beat 的卡就是不該存在的卡。
BEATS = ("hook", "mechanism", "evidence", "insight", "closing")
SEC_PER_CARD = 12.0  # 密度上限：卡片總數 ≤ 片長 ÷ 12（60s ≈ 5 張）
MAX_LINE_CHARS = 6  # 168px（hero）×6 + padding ≈ 1064 ≤ 1080


def _card_hash(variables: dict) -> str:
    comp_digest = hashlib.md5(
        (COMP_DIR / "compositions" / "punch_card.html").read_bytes()
    ).hexdigest()[:8]
    payload = json.dumps(variables, ensure_ascii=False, sort_keys=True) + comp_digest
    return hashlib.md5(payload.encode("utf-8")).hexdigest()[:10]


def _render_card(variables: dict, out_path: Path) -> None:
    """npx hyperframes render → ProRes 4444 alpha mov（約 ~20s/卡）。

    Windows shell=True 走 cmd.exe——單引號不是引號、中文 JSON 會炸，
    variables 一律走 --variables-file（檔案與 mov 同名 sidecar，兼作紀錄）。
    """
    vars_file = out_path.with_suffix(".vars.json")
    vars_file.write_text(json.dumps(variables, ensure_ascii=False, indent=1), encoding="utf-8")
    cmd = (
        "npx --yes hyperframes@0.7.72 render . -c compositions/punch_card.html "
        f'-o "{out_path}" --format mov -q standard --quiet --no-browser-gpu '
        f'--variables-file "{vars_file}"'
    )
    logger.info("render card: %s", variables.get("line1"))
    proc = subprocess.run(
        cmd, shell=True, cwd=str(COMP_DIR), capture_output=True, text=True, encoding="utf-8"
    )
    if proc.returncode != 0 or not out_path.exists():
        raise SystemExit(f"hyperframes render 失敗: {(proc.stderr or '')[-400:]}")


def apply(episode_dir: Path, cid: str, stills_dir: Path | None = None) -> dict:
    from build_resolve_project import connect_resolve

    c, w = _load_winner(episode_dir, cid)
    titles_path = episode_dir / TIGHTEN_DIR / f"{cid}_titles.json"
    if not titles_path.exists():
        raise SystemExit(f"{titles_path} 不存在——agent 先從 tight SRT 選 punch 時間點")
    titles = json.loads(titles_path.read_text(encoding="utf-8"))["titles"]
    titles.sort(key=lambda x: x["t0"])
    insights = [x for x in titles if x.get("beat") == "insight"]
    if len(insights) != 1:
        raise SystemExit(f"insight 卡有 {len(insights)} 張——一支短片只有一個洞見，且必須是 hero")
    if int(insights[0].get("tier", 2)) != 1:
        raise SystemExit("insight 卡必須標 tier: 1（hero）——它是全片最強的一句")

    # 1) 逐卡 render（參數 hash cache）
    cards_dir = episode_dir / CARDS_DIR
    cards_dir.mkdir(parents=True, exist_ok=True)
    jobs = []
    for i, t in enumerate(titles):
        show_sec = round(float(t["t1"]) - float(t["t0"]), 2)
        if not 0.5 <= show_sec <= COMP_SEC - 0.2:
            raise SystemExit(
                f"卡片 {i} 顯示 {show_sec}s 超出範圍（0.5–{COMP_SEC - 0.2}s）——"
                "composition data-duration 固定 4s，更長的卡拆兩張或改 t1"
            )
        lines = t["text"].split("\n")
        beat = t.get("beat")
        if beat not in BEATS:
            raise SystemExit(
                f"卡片 {i}「{t['text'].replace(chr(10), '／')}」缺 beat 或不合法"
                f"（要 {'/'.join(BEATS)}）——寫不出它在論證裡的角色，就不該放這張卡"
            )
        tier = int(t.get("tier", 2))
        if tier not in (1, 2):
            raise SystemExit(f"卡片 {i} tier={tier} 不合法（1=hero 2=標準）")
        too_long = [x for x in lines if len(x) > MAX_LINE_CHARS]
        if too_long:
            raise SystemExit(f"卡片 {i} 行超過 {MAX_LINE_CHARS} 字：{too_long}——改寫或拆行")
        variables = {
            "line1": lines[0],
            "line2": lines[1] if len(lines) > 1 else "",
            "show_sec": show_sec,
            "pos_y": float(t.get("pos_y", DEFAULT_POS_Y[tier])),
            "tier": tier,
        }
        h = _card_hash(variables)
        mov = cards_dir / f"{cid}_{i}_{h}.mov"
        if not mov.exists():
            _render_card(variables, mov)
        else:
            logger.info("cache hit: %s", mov.name)
        jobs.append({"mov": mov, "t0": float(t["t0"]), "show_sec": show_sec, "text": t["text"]})

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
                if a0 < b1 and a1 > b0:
                    raise SystemExit(
                        f"字卡「{job['text'].replace(chr(10), '／')}」({a0}–{a1:.1f}s) 與"
                        f" {ov['kind']}「{ov.get('slug')}」({b0}–{b1}s) 時間重疊"
                        "——兩者都在畫面中下段會互相遮擋，錯開時間或縮短其一"
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
                    "startFrame": 0,
                    "endFrame": dur,
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
    args = parser.parse_args(argv)
    result = apply(Path(args.episode), args.id, Path(args.stills) if args.stills else None)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
