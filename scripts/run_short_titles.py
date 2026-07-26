"""short-titles：短片橘底大字 punch 卡 — Fusion Text+ graphics 層。

修修 2026-07-26 四輪：title 不走 subtitle track（單一 style 天花板），
走 graphics（可變大小/形狀/動畫）。視覺語彙參考 E:\\data 鐘穎範本：
逐行橘塊（#E87000）緊貼字寬、白色特黑體、置中偏下、fade+pop 進出場。

機制（全部經 CROP/TITLE-TEST 實測，勿憑直覺改）：
- `InsertFusionTitleIntoTimeline` 是**插入模式**——在有料的軌上會把 clip
  劈開推走。所以 punch 卡放獨立 timeline「titles - <id>」（空軌上插入 =
  落在播放頭），再整條巢狀疊到（緊·導播）timeline 的 video track 3
  （巢狀空白區透明，實測確認）
- 生成器長度固定 150 frames 無 API 可調——顯示窗口用 Opacity1/2 關鍵
  影格控制（Lua comp:Execute + BezierSpline；Text+ 沒有 Blend input）。
  因此**相鄰兩張卡的 t0 至少要差 5 秒**（clip 重疊會觸發插入劈裂）
- 樣式 SetInput 直設；Size 關鍵影格做 pop-in

輸入：highlights/tighten/<id>_titles.json
    {"titles": [{"text": "它會改變\\n你的耐心", "t0": 25.9, "t1": 29.4,
                 "size": 0.15, "pos_y": 0.55}]}
    t0/t1 = （緊·導播）timeline 秒（與 <id>_tight SRT 同軸）

用法：
    python scripts/run_short_titles.py <episode> --id punch-S1 [--stills <dir>]
"""

from __future__ import annotations

import argparse
import json
import logging
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

ORANGE = (232 / 255, 112 / 255, 0.0)  # 範本橘 #E87000
STYLE = {
    "Font": "Noto Sans TC",
    "Style": "Black",
    "size": 0.15,  # 佔畫面寬比例
    "pos_y": 0.55,  # 卡片垂直中心（0=頂 1=底）
    "fade_frames": 3,
    "pop_frames": 5,
}
GEN_FRAMES = 150  # Text+ 生成器固定長度（無 API 可調）


def _tc(frame: int, fps: float) -> str:
    h, rem = divmod(frame, int(3600 * fps))
    m, rem = divmod(rem, int(60 * fps))
    s, f = divmod(rem, int(fps))
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


def _style_tool(comp, text: str, size: float, pos_y: float) -> None:
    tool = None
    for t in (comp.GetToolList(False) or {}).values():
        if t.GetAttrs().get("TOOLS_RegID") == "TextPlus":
            tool = t
            break
    if tool is None:
        raise SystemExit("Text+ tool 不在 comp 裡")
    sets = {
        "StyledText": text,
        "Font": STYLE["Font"],
        "Style": STYLE["Style"],
        "Size": size,
        "Red1": 1.0,
        "Green1": 1.0,
        "Blue1": 1.0,
        "Center": {1: 0.5, 2: pos_y},
        "LineSpacing": 1.15,
        # Element 2 = 逐行橘塊（border fill、Level=line）
        "Enabled2": 1,
        "ElementShape2": 2,
        "Level2": 2,
        "Red2": ORANGE[0],
        "Green2": ORANGE[1],
        "Blue2": ORANGE[2],
        "Alpha2": 1.0,
        "ExtendHorizontal2": 0.05,
        "ExtendVertical2": 0.025,
    }
    for k, v in sets.items():
        tool.SetInput(k, v)


def _keyframe_window(comp, in_f: int, out_f: int, size: float) -> str | None:
    """顯示窗口（Opacity1/2）+ pop-in（Size）關鍵影格；回傳錯誤或 None。"""
    fade = STYLE["fade_frames"]
    pop = STYLE["pop_frames"]
    lua = f"""
local ok, err = pcall(function()
  local t = nil
  for _, tool in ipairs(comp:GetToolList(false)) do
    if tool:GetAttrs().TOOLS_RegID == "TextPlus" then t = tool end
  end
  if t == nil then error("no TextPlus") end
  for _, inp in ipairs({{"Opacity1", "Opacity2"}}) do
    t[inp] = comp:BezierSpline()
    t:SetInput(inp, 0.0, 0)
    t:SetInput(inp, 0.0, {in_f})
    t:SetInput(inp, 1.0, {in_f + fade})
    t:SetInput(inp, 1.0, {out_f - fade})
    t:SetInput(inp, 0.0, {out_f})
  end
  t.Size = comp:BezierSpline()
  t:SetInput("Size", {size * 0.88:.5f}, {in_f})
  t:SetInput("Size", {size * 1.03:.5f}, {in_f + pop - 2})
  t:SetInput("Size", {size:.5f}, {in_f + pop})
end)
comp:SetData("kf_err", ok and "" or tostring(err))
"""
    comp.Execute(lua)
    err = comp.GetData("kf_err")
    return err or None


def apply(episode_dir: Path, cid: str, stills_dir: Path | None = None) -> dict:
    from build_resolve_project import connect_resolve

    c, w = _load_winner(episode_dir, cid)
    titles_path = episode_dir / TIGHTEN_DIR / f"{cid}_titles.json"
    if not titles_path.exists():
        raise SystemExit(f"{titles_path} 不存在——agent 先從 tight SRT 選 punch 時間點")
    titles = json.loads(titles_path.read_text(encoding="utf-8"))["titles"]
    titles.sort(key=lambda x: x["t0"])
    for a, b in zip(titles, titles[1:]):
        if b["t0"] - a["t0"] < GEN_FRAMES / 30 + 0.1:
            raise SystemExit(
                f"卡片間距不足：{a['t0']} → {b['t0']}（生成器固定 5s，"
                "重疊會觸發插入劈裂——調整 t0 或合併卡片）"
            )

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
    sub_label = f"titles - {cid}"
    timelines = {}
    for i in range(1, project.GetTimelineCount() + 1):
        t = project.GetTimelineByIndex(i)
        if t:
            timelines[t.GetName()] = t
    director = timelines.get(director_label)
    if director is None:
        raise SystemExit(f"「{director_label}」不存在——先跑 run_short_director")

    # 巢狀舊卡層先移除（冪等）；titles 子 timeline 重建
    for ti in range(1, director.GetTrackCount("video") + 1):
        stale_items = [
            it
            for it in (director.GetItemListInTrack("video", ti) or [])
            if (it.GetName() or "") == sub_label
        ]
        if stale_items:
            project.SetCurrentTimeline(director)
            director.DeleteClips(stale_items)
    if sub_label in timelines:
        mp.DeleteTimelines([timelines[sub_label]])

    mp.SetCurrentFolder(root)
    sub = mp.CreateEmptyTimeline(sub_label)
    if sub is None:
        raise SystemExit(f"timeline 建立失敗: {sub_label}")
    project.SetCurrentTimeline(sub)
    sub.SetSetting("useCustomSettings", "1")
    sub.SetSetting("timelineResolutionWidth", "1080")
    sub.SetSetting("timelineResolutionHeight", "1920")
    sub_start = sub.GetStartFrame()

    made = []
    for t in titles:
        f0 = int(t["t0"] * fps)
        sub.SetCurrentTimecode(_tc(sub_start + f0, fps))
        item = sub.InsertFusionTitleIntoTimeline("Text+")
        if item is None:
            raise SystemExit(f"Text+ 插入失敗 @{t['t0']}")
        comp = item.GetFusionCompByIndex(1)
        size = float(t.get("size", STYLE["size"]))
        _style_tool(comp, t["text"], size, float(t.get("pos_y", STYLE["pos_y"])))
        out_f = min(int((t["t1"] - t["t0"]) * fps), GEN_FRAMES - 1)
        err = _keyframe_window(comp, 0, out_f, size)
        if err:
            raise SystemExit(f"關鍵影格失敗 @{t['t0']}: {err}")
        made.append({"text": t["text"].replace("\n", "/"), "at": t["t0"], "show_sec": out_f / fps})

    # 巢狀疊到導播 timeline track 3
    sub_item = None
    for cl in root.GetClipList() or []:
        if (cl.GetName() or "") == sub_label:
            sub_item = cl
            break
    if sub_item is None:
        raise SystemExit("titles 子 timeline 在 media pool 找不到")
    project.SetCurrentTimeline(director)
    while director.GetTrackCount("video") < 3:
        director.AddTrack("video")
    last_end = int(max(t["t0"] for t in titles) * fps) + GEN_FRAMES
    dur = sum(it.GetDuration() for it in (director.GetItemListInTrack("video", 1) or []))
    ok = mp.AppendToTimeline(
        [
            {
                "mediaPoolItem": sub_item,
                "mediaType": 1,
                "trackIndex": 3,
                "recordFrame": director.GetStartFrame(),
                "startFrame": 0,
                "endFrame": min(last_end, dur) - 1,
            }
        ]
    )
    if not ok:
        raise SystemExit("巢狀疊軌失敗")
    pm.SaveProject()

    stills = []
    if stills_dir is not None:
        stills_dir.mkdir(parents=True, exist_ok=True)
        jobs = []
        for i, t in enumerate(titles):
            fr = director.GetStartFrame() + int((t["t0"] + t["t1"]) / 2 * fps)
            project.SetRenderSettings(
                {
                    "MarkIn": fr,
                    "MarkOut": fr,
                    "TargetDir": str(stills_dir),
                    "CustomName": f"title_{cid}_{i}",
                }
            )
            jid = project.AddRenderJob()
            if jid:
                jobs.append((jid, f"title_{cid}_{i}"))
        project.StartRendering([j for j, _ in jobs], isInteractiveMode=False)
        for _ in range(120):
            if not project.IsRenderingInProgress():
                break
            time.sleep(1)
        for jid, name in jobs:
            project.DeleteRenderJob(jid)
            stills.append(name)

    return {"status": "titled", "timeline": director_label, "cards": made, "stills": stills}


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="短片橘底大字 punch 卡（Fusion Text+）")
    parser.add_argument("episode", help="episode 資料夾")
    parser.add_argument("--id", required=True, help="winner id（如 punch-S1）")
    parser.add_argument("--stills", help="物化後渲樣張到此資料夾")
    args = parser.parse_args(argv)
    result = apply(Path(args.episode), args.id, Path(args.stills) if args.stills else None)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
