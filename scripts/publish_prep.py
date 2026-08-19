"""publish_prep — 發布線 Slice 1：render 成品 mp4 + 登錄草稿 Release（Q4a）。

設計凍結：`docs/plans/2026-07-26-video-publishing-plan.md` §2 Q4a/Q4b、ADR-055。

    python scripts/publish_prep.py "20260723 謝伯讓"                  # 這集全部 winners
    python scripts/publish_prep.py "20260723 謝伯讓" --cut punch-L5    # 只出這一支

跑完的語意是「**登錄了**」不是「發布了」——系統手上多了檔案 + 草稿
Release（draft），等文案（packaging 交接檔）、等排程、等修修核准。

Q4b 字幕的兩顆地雷（run_short_review 實測，與計畫文件的假設**相反**）：

- **長片**：Resolve render 會把主字幕模板軌**燒進畫面**——但 Q4b 裁決長片
  不燒、只上 CC → render 前 disable 全部 subtitle 軌，render 完恢復
- **短片**：Resolve render **燒不進**字幕（只出 sidecar）——但短片必須燒
  → Resolve 出乾淨畫面，ffmpeg 從 tight SRT 燒（同 QC preview 工法，
  字級按全解析放大）

檔案落點：`<episode>/highlights/exports/<cut_id>.mp4`（短片另保留
`<cut_id>_clean.mp4` 乾淨版供重燒）。CC 字幕直接用
`highlights/srt/<cut_id>_tight_r*.srt` 最新版（已平移 0 起點）。

執行環境同 longform-cut skill：Resolve Studio 開著、`py -3.10`。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_highlight_cut import FORMAT_LABEL  # noqa: E402

logger = logging.getLogger("publish_prep")

EXPORTS_DIR = "highlights/exports"
# 短片燒錄字幕樣式：QC preview 用 540 寬 FontSize 14——全解析 1080 寬等比 ×2。
# 樣式盡量貼近 Resolve 直式模板（修修 UAT 後可調）。
SHORT_SUB_STYLE = "FontName=Microsoft JhengHei,FontSize=28,Outline=2,MarginV=84"
RENDER_TIMEOUT_SEC = 3600  # 長片全解析 render 上限（12 分鐘片 + 排隊餘裕）


def _load_plan(episode_dir: Path) -> tuple[list[dict], list[dict]]:
    hdir = episode_dir / "highlights"
    cands = json.loads((hdir / "candidates.json").read_text(encoding="utf-8"))["candidates"]
    winners = json.loads((hdir / "winners.json").read_text(encoding="utf-8"))["winners"]
    return cands, winners


def cuts_to_prep(cands: list[dict], winners: list[dict], only: str | None = None) -> list[dict]:
    """要匯出的 cut 清單：winners × candidates join（format/title 來自 candidate）。

    `--cut` 指定不存在的 id fail loud——寧可停下也不要默默出整集
    （嚴禁幻想：id 打錯不是「全出」的授權）。"""
    by_id = {c["id"]: c for c in cands}
    picked = []
    for w in winners:
        if only and w["id"] != only:
            continue
        c = by_id.get(w["id"])
        if c is None:
            raise SystemExit(f"winner {w['id']} 不在 candidates.json——資料不一致，先修")
        picked.append({**c, "rank": w["rank"]})
    if only and not picked:
        raise SystemExit(f"--cut {only} 不在 winners.json")
    return picked


def timeline_label(cut: dict) -> str:
    """winner id → Resolve timeline 顯示名（雙 id 陷阱：對應必須機器保證）。"""
    return f"{FORMAT_LABEL[cut['format']]}{cut['rank']} - {cut['title']}（緊·導播）"


def _latest_tight_srt(episode_dir: Path, cut_id: str) -> Path | None:
    # 版本挑選規則只留一份（shared.tight_srt）——審核頁 preview / CC / 短片燒字幕
    # 必須指到同一個檔，否則修修看到的字幕不是實際上架的那份
    from shared.tight_srt import latest_tight_srt

    return latest_tight_srt(episode_dir, cut_id)


def _find_timeline(project, label: str):
    for i in range(1, project.GetTimelineCount() + 1):
        t = project.GetTimelineByIndex(i)
        if t and t.GetName() == label:
            return t
    raise SystemExit(f"timeline「{label}」不存在——先跑完 longform-cut/highlight-cut 製作線")


def _render_master(project, timeline, out_dir: Path, name: str) -> Path:
    """Resolve render queue 出全解析 H.264 mp4（timeline 原生解析度）。

    ⚠️ 三道驗證缺一不可（2026-08-11 安吉 SL7 事故）：舊版只檢查「檔案存在嗎」，
    而舊檔本來就在 → render job **Failed 也照樣回報成功**，還把上一版重新登錄
    進 DB。當時的 job error 是：

        A read-only file SL7.mp4 already exists. Please select another file name.

    真正的原因是修修正在審核頁預覽那支影片，`/bridge/publish/media/...` 的
    FileResponse 握著檔案 handle，Resolve 寫不進去。所以：讀 job 狀態
    （**必須在 DeleteRenderJob 之前**）、檔案要存在、而且 mtime 要比 render
    開始時新——三道都過才算數。
    """
    w = int(timeline.GetSetting("timelineResolutionWidth"))
    h = int(timeline.GetSetting("timelineResolutionHeight"))
    project.SetCurrentRenderFormatAndCodec("mp4", "H264")
    project.SetRenderSettings(
        {
            "MarkIn": timeline.GetStartFrame(),
            "MarkOut": timeline.GetEndFrame(),
            "TargetDir": str(out_dir),
            "CustomName": name,
            "FormatWidth": w,
            "FormatHeight": h,
        }
    )
    out = out_dir / f"{name}.mp4"
    before_mtime = out.stat().st_mtime if out.exists() else 0.0

    jid = project.AddRenderJob()
    if not jid:
        raise SystemExit("AddRenderJob 失敗")
    project.StartRendering([jid], isInteractiveMode=False)
    for _ in range(RENDER_TIMEOUT_SEC // 2):
        if not project.IsRenderingInProgress():
            break
        time.sleep(2)
    else:
        raise SystemExit(f"render 逾時（>{RENDER_TIMEOUT_SEC}s）")

    status = project.GetRenderJobStatus(jid) or {}
    project.DeleteRenderJob(jid)  # 狀態一定要在刪 job 前讀
    job_status = status.get("JobStatus")
    if job_status and job_status != "Complete":
        raise SystemExit(
            f"render job {job_status}: {status.get('Error') or '(Resolve 沒給錯誤訊息)'}"
            f"\n  → 「read-only file already exists」通常是**有人正握著這個檔**："
            f"審核頁在預覽這支影片、或播放器開著 {out.name}。關掉再重跑。"
        )
    if not out.exists():
        raise SystemExit(f"render 完成但檔案不存在: {out}")
    if out.stat().st_mtime <= before_mtime:
        raise SystemExit(
            f"render 回報完成但 {out.name} 沒有更新（mtime 沒變）——舊檔還在原地。"
            f"\n  → 同上：檔案被佔用時 Resolve 會靜默失敗，不要拿舊檔當新成品。"
        )
    return out


def _set_subtitle_tracks(resolve, timeline, enabled: bool) -> int:
    """開/關全部字幕軌（長片 Q4b：成品不燒字幕）。回軌數供 log。

    ⚠️ Resolve 在 **deliver page** 上 SetTrackEnable 會靜默 no-op（回 True 但
    狀態不變，GetIsTrackEnabled 讀值也不可信）——2026-08-07 安吉三支長片
    render 完 finally 恢復失效，SL4/SL3 字幕軌卡在關閉。先切 edit page、
    設完複讀驗證，不符 fail loud。
    """
    resolve.OpenPage("edit")
    n = int(timeline.GetTrackCount("subtitle") or 0)
    for i in range(1, n + 1):
        timeline.SetTrackEnable("subtitle", i, enabled)
    bad = [i for i in range(1, n + 1) if bool(timeline.GetIsTrackEnabled("subtitle", i)) != enabled]
    if bad:
        raise SystemExit(f"字幕軌 {bad} 設 enabled={enabled} 未生效——timeline/page 狀態異常，先查")
    return n


def _burn_short_subs(clean: Path, srt: Path, out: Path) -> None:
    """短片 ffmpeg 燒字幕（Resolve render 燒不進——十七輪實測）。"""
    proc = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            clean.name,
            "-vf",
            f"subtitles={srt.name}:force_style='{SHORT_SUB_STYLE}'",
            "-c:v",
            "libx264",
            "-crf",
            "17",
            "-preset",
            "slow",
            "-c:a",
            "copy",
            out.name,
        ],
        cwd=str(clean.parent),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not out.exists():
        raise SystemExit(f"短片字幕燒錄失敗: {(proc.stderr or '')[-300:]}")


def _probe(path: Path) -> tuple[float, int]:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()
    try:
        dur = float(out)
    except ValueError:
        dur = 0.0
    return dur, path.stat().st_size


def export_cut(resolve, project, episode_dir: Path, cut: dict) -> dict:
    """單支 cut：render → （短片燒字幕）→ exports/<cut_id>.mp4。"""
    label = timeline_label(cut)
    timeline = _find_timeline(project, label)
    project.SetCurrentTimeline(timeline)
    out_dir = episode_dir / EXPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    cid = cut["id"]

    if cut["format"] == "long":
        # Q4b：長片成品不燒字幕（CC 另上）。Resolve 會燒模板字幕軌 → 先關。
        n = _set_subtitle_tracks(resolve, timeline, False)
        logger.info("%s: 長片——已暫時關閉 %d 條字幕軌（成品不燒，CC 另上）", cid, n)
        try:
            final = _render_master(project, timeline, out_dir, cid)
        finally:
            _set_subtitle_tracks(resolve, timeline, True)
    else:
        srt = _latest_tight_srt(episode_dir, cid)
        if srt is None:
            raise SystemExit(f"{cid} 沒有 tight SRT——短片必須燒字幕（Q4b）")
        clean = _render_master(project, timeline, out_dir, f"{cid}_clean")
        final = out_dir / f"{cid}.mp4"
        import shutil

        shutil.copy(srt, out_dir / f"{cid}.srt")
        logger.info("%s: 短片——ffmpeg 燒字幕（%s）", cid, srt.name)
        _burn_short_subs(clean, out_dir / f"{cid}.srt", final)

    dur, size = _probe(final)
    srt_path = _latest_tight_srt(episode_dir, cid)
    return {
        "cut_id": cid,
        "format": cut["format"],
        "work_title": cut["title"],
        "file": str(final),
        "duration_sec": round(dur, 2),
        "file_bytes": size,
        "cc_srt": str(srt_path) if srt_path else None,
        "timeline": label,
    }


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="發布線 Slice 1：render 成品 + 登錄草稿 Release")
    parser.add_argument("episode", help="episode 資料夾（G:\\footages\\...）")
    parser.add_argument("--cut", help="只出這一支（winner id，如 punch-L5）")
    parser.add_argument(
        "--render-only",
        action="store_true",
        help="只由 Resolve 匯出並寫 receipt；DB 登錄交給 Web App 的相容 Python",
    )
    parser.add_argument("--receipt", type=Path, help="--render-only 的 JSON receipt 落點")
    parser.add_argument("--attempt-id", help="Web background attempt identity")
    args = parser.parse_args(argv)
    if args.render_only != bool(args.receipt):
        raise SystemExit("--render-only 與 --receipt 必須一起使用")
    if bool(args.attempt_id) != bool(args.receipt):
        raise SystemExit("--attempt-id 與 --receipt 必須一起使用")

    episode_dir = Path(args.episode)
    if not episode_dir.exists():
        raise SystemExit(f"episode 不存在: {episode_dir}")
    cands, winners = _load_plan(episode_dir)
    cuts = cuts_to_prep(cands, winners, args.cut)

    from build_resolve_project import connect_resolve  # Resolve 依賴延後 import

    if not args.render_only:
        from shared.release_store import ensure_target, register_release

    resolve = connect_resolve()
    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()
    if project is None or project.GetName() != episode_dir.name:
        project = pm.LoadProject(episode_dir.name)
    if project is None:
        raise SystemExit(f"project「{episode_dir.name}」不存在")

    results = []
    for cut in cuts:
        info = export_cut(resolve, project, episode_dir, cut)
        if not args.render_only:
            release_id = register_release(
                episode_dir.name,
                info["cut_id"],
                info["format"],
                info["file"],
                work_title=info["work_title"],
                file_bytes=info["file_bytes"],
                duration_sec=info["duration_sec"],
            )
            target_id = ensure_target(release_id, "youtube")
            info["release_id"] = release_id
            info["youtube_target_id"] = target_id
            logger.info(
                "登錄 release #%d（%s）→ youtube target #%d（draft）",
                release_id,
                info["cut_id"],
                target_id,
            )
        results.append(info)

    payload = {
        "status": "rendered" if args.render_only else "registered",
        "episode": episode_dir.name,
        "count": len(results),
        "cuts": results,
        "attempt_id": args.attempt_id,
        "exit_code": 0,
    }
    if args.receipt:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.receipt.with_suffix(args.receipt.suffix + f".{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary, args.receipt)
    print(json.dumps(payload, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
