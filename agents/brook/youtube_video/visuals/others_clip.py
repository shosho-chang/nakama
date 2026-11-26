"""第 11 類「他人影片引用」：把修修在 Bridge 標「要」、已下載的片段接起來，
左上加「影片來源」，交件。

上游：`agents.brook.youtube_video.clips`（找片、標起訖、下載）。片段照候選清單的順序接；
每段縮放到 1920×1080（比例不同的補黑邊，不裁掉畫面）、29.97 fps，**保留原聲**——
上時間軸時鋪時間軸只放畫面（`layout.py` 用 mediaType 1），
所以在時間軸上是靜音的（規格 §1 第 11 類）。

版面（`video/compositions/others_clip/compositions/framed.html`）：品牌底圖＋框，片段縮小放進框裡，
框線有一段線繞圈跑；框的左上方是「影片來源」，每段開始時換成那段的 `source_label`
（預設頻道名，修修可在 Bridge 改）。交件帶 alpha：片段前後的空檔是透明的，看得到 A-roll。

總長＝各段長度相加；比這一列蓋住的字幕長超過 1 秒就擋下
（請在 Bridge 縮短片段或拉長這列的字幕範圍），
短一點可以——片段播完回到 A-roll。

用法：

    python -m agents.brook.youtube_video.clips download "G:/Footages/<ep>" <row_id> --dry-run
    python -m agents.brook.youtube_video.clips download "G:/Footages/<ep>" <row_id>
    python -m agents.brook.youtube_video.visuals.others_clip "G:/Footages/<ep>" --row <row_id>
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from agents.brook.youtube_video import clips
from agents.brook.youtube_video import visual_plan as vp
from agents.brook.youtube_video.visuals import delivery
from shared.quiet_subprocess import quiet_kwargs

CATEGORY = "others_clip"
COMPONENT = "others_clip"
REPO_ROOT = Path(__file__).resolve().parents[4]
COMPOSITION_DIR = REPO_ROOT / "video" / "compositions" / "others_clip"
# 修修 2026-11-17：引用的畫面縮小放進框裡，底下墊品牌底圖，框線有一段線繞圈跑（framed.html）。
# 三款底圖選了 b（橘色滿版＋兩角冷灰碎片），窗寬 85%。
COMPOSITION_HTML = COMPOSITION_DIR / "compositions" / "framed.html"
FRAMED_VARIANT = "b"
WIN_W = 1632  # 修修 2026-11-17 從 80／85／90% 選 85%
LABEL_H = 64  # 窗上方留給「影片來源」


def window_rect(win_w: int = WIN_W) -> tuple[int, int, int, int]:
    """窗的 (x, y, 寬, 高)：16:9、水平置中，「影片來源＋窗」整塊垂直置中。

    framed.html 的 W 用同一條算式，兩邊要一起改。
    """
    w = round(win_w / 2) * 2
    h = round(w * 9 / 16 / 2) * 2
    return (1920 - w) // 2, round((1080 - h - LABEL_H) / 2) + LABEL_H, w, h


# 進場：橘底刷進來、碎片飛進來之後窗才拉開，片段從 INTRO_SEC 開始播；片段播完再 OUTRO_SEC 底圖收掉。
# 跟 framed.html 的 INTRO／OUTRO 一樣。總長＝INTRO＋片段＋OUTRO。
INTRO_SEC = 0.5
OUTRO_SEC = 0.3
HYPERFRAMES = "hyperframes@0.7.72"
OVER_TOLERANCE_SEC = 1.0

Runner = Callable[[list[str], Path], None]


def plan_segments(
    episode_dir: Path, plan: Mapping[str, Any], row: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], float, float]:
    """核准的片段（照清單順序）＋這一列的起點與蓋住的長度。缺下載或太長就擋。"""
    if row.get("category") != CATEGORY:
        raise delivery.DeliveryError(f"{row.get('id')}: 不是他人影片引用")
    start, span = delivery.overlay_window(plan, row)
    path = clips.picks_path(episode_dir, row["id"])
    if not path.is_file():
        raise delivery.DeliveryError(f"{row['id']}: 還沒有候選片段（DP 先跑 clips propose）")
    chosen = clips.approved(clips.load_picks(path))
    if not chosen:
        raise delivery.DeliveryError(f"{row['id']}: 修修還沒在 Bridge 標「要」的片段")
    segs, at = [], INTRO_SEC
    for cand in chosen:
        media = clips.clip_path(episode_dir, row["id"], cand)
        if not media.is_file():
            raise delivery.DeliveryError(
                f"{row['id']}: {cand['id']} 還沒下載（clips download，起訖改過也要重新下載）"
            )
        length = round(float(cand["end"]) - float(cand["start"]), 3)
        segs.append(
            {
                "at": round(at, 3),
                "length": length,
                "media": media,
                "label": cand["source_label"],
                "id": cand["id"],
                "video_id": cand["video_id"],
                "start": cand["start"],
                "end": cand["end"],
            }
        )
        at += length
    total = at + OUTRO_SEC
    if total > span + OVER_TOLERANCE_SEC:
        raise delivery.DeliveryError(
            f"{row['id']}: 片段加進退場共 {total:.1f} 秒，這一列只有 {span:.1f} 秒，"
            "請在 Bridge 縮短片段或拉長這一列的字幕範圍"
        )
    return segs, start, round(total, 3)


def label_variables(segs: Sequence[Mapping[str, Any]], total: float) -> dict[str, Any]:
    return {
        "variant": FRAMED_VARIANT,
        "win_w": WIN_W,
        "segments": json.dumps([[s["at"], s["label"]] for s in segs], ensure_ascii=False),
        "show_sec": total,
    }


def composite_graph(total: float) -> str:
    """透明畫布 → 片段縮小放進窗（窗拉開後才出現、窗關上前收掉）
    → 疊上底圖框線（中間是透明的窗）。"""
    x, y, w, h = window_rect()
    return (
        f"color=c=black@0.0:s=1920x1080:r=30000/1001:d={total:.3f},format=yuva444p10le[canvas];"
        f"[0:v]scale={w}:{h},format=yuva444p10le,setpts=PTS-STARTPTS+{INTRO_SEC}/TB[clip];"
        f"[canvas][clip]overlay={x}:{y}:eof_action=pass:"
        f"enable='between(t,{INTRO_SEC},{total - OUTRO_SEC:.3f})'[under];"
        f"[under][1:v]overlay=0:0:eof_action=pass,format=yuva444p10le[v];"
        f"[0:a]adelay={round(INTRO_SEC * 1000)}:all=1,apad[a]"
    )


def concat_argv(segs: Sequence[Mapping[str, Any]], out: Path) -> list[str]:
    """每段縮放進 1920×1080（補黑邊）、29.97、聲音 48k 立體聲，再接起來。"""
    ins, chains, pads = [], [], []
    for i, s in enumerate(segs):
        ins += ["-i", str(s["media"])]
        chains.append(
            f"[{i}:v]scale=1920:1080:force_original_aspect_ratio=decrease,"
            f"pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30000/1001,"
            f"trim=duration={s['length']},setpts=PTS-STARTPTS[v{i}];"
            f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo,"
            f"atrim=duration={s['length']},asetpts=PTS-STARTPTS[a{i}];"
        )
        pads.append(f"[v{i}][a{i}]")
    graph = "".join(chains) + "".join(pads) + f"concat=n={len(segs)}:v=1:a=1[v][a]"
    return [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        *ins,
        "-filter_complex",
        graph,
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-c:v",
        "libx264",
        "-crf",
        "12",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "pcm_s16le",
        str(out),
    ]


def _run(argv: list[str], cwd: Path) -> None:
    proc = subprocess.run(
        argv,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=sys.platform == "win32" and argv[0] == "npx",
        **quiet_kwargs(),
    )
    if proc.returncode != 0:
        raise RuntimeError(f"{argv[0]} 失敗：{(proc.stderr or proc.stdout or '')[-600:]}")


def render(
    segs: Sequence[Mapping[str, Any]], total: float, out: Path, runner: Runner = _run
) -> dict:
    variables = label_variables(segs, total)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out.parent) as tmp:
        base = Path(tmp) / "clips.mov"
        label = Path(tmp) / "label.mov"
        vars_file = Path(tmp) / "vars.json"
        vars_file.write_text(json.dumps(variables, ensure_ascii=False), encoding="utf-8")
        runner(concat_argv(segs, base), COMPOSITION_DIR)
        runner(
            [
                "npx",
                "--yes",
                HYPERFRAMES,
                "render",
                ".",
                "-c",
                "compositions/framed.html",
                "-o",
                str(label),
                "--format",
                "mov",
                "--fps",
                delivery.FPS,
                "-q",
                "standard",
                "--quiet",
                "--no-browser-gpu",
                "--variables-file",
                str(vars_file),
            ],
            COMPOSITION_DIR,
        )
        runner(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                str(base),
                "-i",
                str(label),
                "-filter_complex",
                composite_graph(total),
                "-map",
                "[v]",
                "-map",
                "[a]",
                "-t",
                f"{total:.3f}",
                "-c:v",
                "prores_ks",
                "-profile:v",
                "4444",
                "-pix_fmt",
                "yuva444p10le",
                "-c:a",
                "pcm_s16le",
                str(out),
            ],
            COMPOSITION_DIR,
        )
    return variables


def deliver_row(
    episode_dir: Path,
    plan: Mapping[str, Any],
    row: Mapping[str, Any],
    *,
    force: bool = False,
    runner: Runner = _run,
    cut_check: Callable[[Path], list[float]] = clips.shot_cuts,
) -> tuple[dict[str, Any], bool]:
    """接片段＋來源標示並寫收據。回傳（收據, 這次有沒有真的渲）。

    片段裡有不到 1.5 秒的鏡頭（來源影片自己切畫面）就擋下，附建議起訖——修修：
    「三個不同的訪談，其實只要三個不同的畫面就好了，如果畫面切得太快，反而會讓觀眾覺得奇怪」。
    """
    episode_dir = Path(episode_dir)
    segs, start, total = plan_segments(episode_dir, plan, row)
    problems = [
        p for s in segs if (p := clips.shot_problem({"id": s["id"], **s}, cut_check(s["media"])))
    ]
    if problems:
        raise delivery.DeliveryError(f"{row['id']}: " + "；".join(problems))
    variables = label_variables(segs, total)
    variables["clips"] = [[s["video_id"], s["start"], s["end"]] for s in segs]
    component_sha = delivery.file_sha(COMPOSITION_HTML)
    sha = delivery.inputs_sha(
        category=CATEGORY, component=COMPONENT, component_sha=component_sha, variables=variables
    )
    paths = delivery.asset_paths(episode_dir, row["id"])
    if not force and delivery.is_current(paths, sha):
        return delivery.load_receipt(paths.receipt) or {}, False
    render(segs, total, paths.media, runner)
    receipt = delivery.write_receipt(
        paths,
        row=row,
        component=COMPONENT,
        component_sha=component_sha,
        variables=variables,
        start_sec=start,
        duration_sec=total,
        sha=sha,
    )
    return receipt, True


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="他人影片引用：接片段＋影片來源，交件")
    p.add_argument("episode", type=Path)
    p.add_argument("--row", action="append", help="只做這幾列（預設＝全部他人影片引用）")
    p.add_argument("--force", action="store_true")
    args = p.parse_args(argv)
    plan = vp.load_plan(vp.plan_path(args.episode))
    rows = args.row or [r["id"] for r in plan["rows"] if r.get("category") == CATEGORY]
    failed = 0
    by_id = {r["id"]: r for r in plan["rows"]}
    for row_id in rows:
        try:
            if row_id not in by_id:
                raise delivery.DeliveryError("規劃表沒有這一列")
            receipt, rendered = deliver_row(args.episode, plan, by_id[row_id], force=args.force)
            print(f"{'渲好' if rendered else '沿用'} {row_id}：{receipt.get('duration_sec')} 秒")
        except (delivery.DeliveryError, clips.ClipError, RuntimeError) as exc:
            failed += 1
            print(f"✗ {row_id}: {exc}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
