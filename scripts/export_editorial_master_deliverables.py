"""Editorial Master 封存後的兩個交付檔：podcast MP3 與燒字幕 MP4。

修修 2026-10-23（20260930 Manny）：「在確定 Editorial Master 之後，幫我直接 export。
第一個是音檔，我需要 320 Kbps 的 MP3。第二個是 MP4，編碼要 H.264，Encoder 可以用
NVIDIA 的，Subtitle Burn-in 在 Video 裡面。」以前這兩支是他在 Deliver 頁手動出的
（例：`20260903 程世嘉/EP128-程世嘉.mp3`／`.mp4`），規格以那兩支實檔為準：

- MP3：320 kbps CBR、48 kHz、stereo，長度＝完整節目
- MP4：H.264 NVIDIA（NVENC）、timeline 原生解析度／幀率、AAC 音訊、
  Shosho 字幕軌樣式 Burn In

來源一律是 `editorial-master/v1/EDITORIAL-MASTER.json` 記下的那條 timeline（不是
集數資料夾名、也不是 `master.mp4` 再轉檔）：字幕要用 timeline 的字幕軌樣式燒，只有
Resolve 做得到。

MP3 為什麼不是 Resolve 直接出：Deliver 頁的「Audio Only」可以選 MP3，但 scripting API
不開放——`LoadRenderPreset("Audio Only")` 之後 `GetCurrentRenderFormatAndCodec()` 是
`unknown`，`SetCurrentRenderFormatAndCodec("mp3", ...)` 回 False（Resolve 21.1，
2026-10-23 實測）。所以走無損中繼：Resolve 只出 timeline 的 24-bit PCM（mov），再用
ffmpeg libmp3lame 壓 320k CBR。不用 `master.mp4` 的 AAC 再轉 MP3——那是有損轉有損。

驗證（缺一不可，教訓同 `publish_prep._render_master`）：render job 狀態必須 Complete、
檔案存在、mtime 比 render 前新；接著 ffprobe 規格與長度（對 `master.mp4`，容忍 2 格）；
MP4 另外抽字幕出現的時間點，確認畫面下緣跟 `master.mp4` 有差而上半部沒差——字幕軌
沒開或 SubtitleFormat 沒生效時，render 照樣「成功」但沒有字。

用法：
    python scripts/export_editorial_master_deliverables.py "<episode>"            # 兩支都出
    python scripts/export_editorial_master_deliverables.py "<episode>" --only mp3
    python scripts/export_editorial_master_deliverables.py "<episode>" --name "EP129-Manny"

輸出 `<episode>/final/<name>.mp3`、`<name>.mp4` 與 `editorial-master-exports.v1.json`
（`<name>` 預設＝集數資料夾名；EP 編號推不出來，要用就 `--name` 傳）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agents.brook.script_video.editorial_master import (  # noqa: E402
    editorial_master_timeline,
    verify_editorial_master,
)

logger = logging.getLogger("em_exports")

CONTRACT = "podcast-editorial-master-exports-v1"
FINAL_DIR = "final"
RECEIPT_NAME = "editorial-master-exports.v1.json"
MP3_BITRATE = 320_000
MP3_SAMPLE_RATE = 48_000
MP4_CODEC = "H264_NVIDIA"
MP4_ENCODER_TAG = "H.264 NVIDIA"
DURATION_TOLERANCE_FRAMES = 2
RENDER_TIMEOUT_RATIO = 3.0
RENDER_TIMEOUT_FLOOR_SEC = 1800
BURN_CHECK_SAMPLES = 6
# render 前先載一個內建影片 preset 把 Deliver 頁狀態歸零。2026-10-23 實測：Deliver 頁停在
# 「Audio Only」時，SetRenderSettings({"ExportVideo": True}) 回 True 卻沒生效，出來的 .mp4
# 只有 AAC——job 照樣 Complete。修修平常就在 Deliver 頁用 Audio Only 出 mp3，這是常態不是意外。
BASE_PRESET = "H.264 Master"


# ---------------------------------------------------------------- ffprobe helpers


def ffprobe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return json.loads(out)


def _stream(info: dict, kind: str) -> dict | None:
    return next((s for s in info.get("streams", []) if s.get("codec_type") == kind), None)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(8 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_mp3(info: dict, *, expected_sec: float, fps: float) -> list[str]:
    """回傳不符規格的項目（空 list＝通過）。純函式，方便測。"""
    problems: list[str] = []
    audio = _stream(info, "audio")
    if audio is None or _stream(info, "video") is not None:
        return ["MP3 必須只有一條音訊"]
    if audio.get("codec_name") != "mp3":
        problems.append(f"codec={audio.get('codec_name')}，要 mp3")
    if int(audio.get("bit_rate") or 0) != MP3_BITRATE:
        problems.append(f"bit_rate={audio.get('bit_rate')}，要 {MP3_BITRATE}")
    if int(audio.get("sample_rate") or 0) != MP3_SAMPLE_RATE:
        problems.append(f"sample_rate={audio.get('sample_rate')}，要 {MP3_SAMPLE_RATE}")
    if int(audio.get("channels") or 0) != 2:
        problems.append(f"channels={audio.get('channels')}，要 2")
    problems += _duration_problems(info, expected_sec, fps)
    return problems


def check_mp4(info: dict, *, expected_sec: float, fps: float, width: int, height: int) -> list[str]:
    problems: list[str] = []
    video, audio = _stream(info, "video"), _stream(info, "audio")
    if video is None or audio is None:
        return ["MP4 必須同時有影像與音訊"]
    if video.get("codec_name") != "h264":
        problems.append(f"video codec={video.get('codec_name')}，要 h264")
    encoder = (video.get("tags") or {}).get("encoder")
    if encoder != MP4_ENCODER_TAG:
        problems.append(f"encoder tag={encoder!r}，要 {MP4_ENCODER_TAG!r}（NVENC 沒生效）")
    if (int(video.get("width") or 0), int(video.get("height") or 0)) != (width, height):
        problems.append(f"解析度 {video.get('width')}x{video.get('height')}，要 {width}x{height}")
    num, _, den = str(video.get("r_frame_rate") or "0/1").partition("/")
    if abs(float(num) / float(den or 1) - fps) > 0.01:
        problems.append(f"幀率 {video.get('r_frame_rate')}，要 {fps}")
    problems += _duration_problems(info, expected_sec, fps)
    return problems


def _duration_problems(info: dict, expected_sec: float, fps: float) -> list[str]:
    got = float((info.get("format") or {}).get("duration") or 0)
    if abs(got - expected_sec) > DURATION_TOLERANCE_FRAMES / fps:
        return [f"長度 {got:.3f}s，master.mp4 是 {expected_sec:.3f}s"]
    return []


# ---------------------------------------------------------------- burn-in check


def _gray_frame(video: Path, t: float, w: int = 192, h: int = 108) -> bytes:
    return subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            f"{t:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-vf",
            f"scale={w}:{h},format=gray",
            "-f",
            "rawvideo",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout


def _mean_abs_diff(a: bytes, b: bytes, w: int, rows: range) -> float:
    total = count = 0
    for y in rows:
        for x in range(w):
            total += abs(a[y * w + x] - b[y * w + x])
            count += 1
    return total / max(count, 1)


def _subtitle_midpoints(srt: Path, n: int) -> list[float]:
    blocks = srt.read_text(encoding="utf-8-sig").replace("\r\n", "\n").strip().split("\n\n")
    spans = []
    for block in blocks:
        lines = block.split("\n")
        if len(lines) < 3 or " --> " not in lines[1]:
            continue
        a, b = (_srt_sec(part) for part in lines[1].split(" --> "))
        if b - a >= 1.2:  # 夠長才不會抽到換句的那一格
            spans.append((a + b) / 2)
    if not spans:
        return []
    step = max(1, len(spans) // (n + 1))
    return spans[step::step][:n]


def _srt_sec(stamp: str) -> float:
    hms, _, ms = stamp.strip().partition(",")
    h, m, s = (int(x) for x in hms.split(":"))
    return h * 3600 + m * 60 + s + int(ms or 0) / 1000


def burn_in_evidence(export: Path, master_mp4: Path, master_srt: Path) -> dict:
    """字幕出現時，成品下緣要跟 master.mp4 不同、上半部要一樣（同一個畫面）。"""
    w, h = 192, 108
    samples = []
    for t in _subtitle_midpoints(master_srt, BURN_CHECK_SAMPLES):
        a, b = _gray_frame(export, t, w, h), _gray_frame(master_mp4, t, w, h)
        samples.append(
            {
                "t_sec": round(t, 3),
                "bottom_diff": round(
                    _mean_abs_diff(a, b, w, range(int(h * 0.82), int(h * 0.97))), 2
                ),
                "top_diff": round(_mean_abs_diff(a, b, w, range(0, int(h * 0.5))), 2),
            }
        )
    hits = sum(1 for s in samples if s["bottom_diff"] > max(4.0, 3 * s["top_diff"]))
    return {"samples": samples, "hits": hits, "passed": bool(samples) and hits >= len(samples) - 1}


# ---------------------------------------------------------------- Resolve render


def _render(project, settings: dict, fmt: str, codec: str, out: Path, timeout: int) -> Path:
    if not project.LoadRenderPreset(BASE_PRESET):
        raise SystemExit(f"載入 render preset「{BASE_PRESET}」失敗")
    if not project.SetCurrentRenderFormatAndCodec(fmt, codec):
        raise SystemExit(f"Resolve 不接受 render 格式 {fmt}/{codec}")
    # ExportVideo／ExportAudio 要「單獨先送一次」：Deliver 頁停在純音訊狀態時，跟其他 key 擠在
    # 同一個 dict 裡的 ExportVideo=True 回 True 但不生效（2026-10-23 四組對照實測：合併送 → 只有
    # audio；先單送開關 → video+audio）。LoadRenderPreset 也不會把它重置回來。
    flags = {k: settings[k] for k in ("ExportVideo", "ExportAudio") if k in settings}
    if project.SetRenderSettings(flags) is False or project.SetRenderSettings(settings) is False:
        raise SystemExit(f"SetRenderSettings 失敗：{settings}")
    before = out.stat().st_mtime if out.exists() else 0.0
    job = project.AddRenderJob()
    if not job:
        raise SystemExit("AddRenderJob 失敗")
    deadline = time.monotonic() + timeout
    # 緊接著上一支 render 立刻 StartRendering 時，Resolve 可能根本沒開始：job 停在 Ready、
    # IsRenderingInProgress 一開始就是 False（2026-10-23 MP4 之後接 PCM 實測）。沒動就等一下再推。
    for attempt in range(3):
        project.StartRendering([job], isInteractiveMode=False)
        time.sleep(2)
        while project.IsRenderingInProgress():
            if time.monotonic() > deadline:
                project.StopRendering()
                raise SystemExit(f"render 逾時（>{timeout}s）：{out.name}")
            time.sleep(3)
        if (project.GetRenderJobStatus(job) or {}).get("JobStatus") != "Ready":
            break
        logger.warning("render job 還是 Ready（沒有開始），%d 秒後重推", 5 * (attempt + 1))
        time.sleep(5 * (attempt + 1))
    status = project.GetRenderJobStatus(job) or {}
    project.DeleteRenderJob(job)  # 狀態一定要在刪 job 前讀
    if status.get("JobStatus") != "Complete":
        error = status.get("Error") or "(Resolve 沒給錯誤訊息)"
        raise SystemExit(
            f"render job {status.get('JobStatus')}: {error}"
            f"\n  → 「read-only file already exists」＝有人握著 {out.name}"
            "（播放器／預覽），關掉再重跑"
        )
    if not out.exists() or out.stat().st_mtime <= before:
        raise SystemExit(f"render 回報完成，但 {out} 不存在或沒有更新——不要拿舊檔當成品")
    return out


def _open_timeline(resolve, episode_dir: Path, timeline_name: str):
    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()
    if project is None or project.GetName() != episode_dir.name:
        logger.warning(
            "Resolve 目前開著「%s」，切到「%s」", project and project.GetName(), episode_dir.name
        )
        project = pm.LoadProject(episode_dir.name)
    if project is None:
        raise SystemExit(f"Resolve project「{episode_dir.name}」不存在")
    for i in range(1, int(project.GetTimelineCount() or 0) + 1):
        tl = project.GetTimelineByIndex(i)
        if tl and tl.GetName() == timeline_name:
            project.SetCurrentTimeline(tl)
            return project, tl
    raise SystemExit(f"找不到 Editorial Master timeline「{timeline_name}」")


def _enable_subtitle_tracks(resolve, timeline) -> int:
    """燒字幕前把字幕軌全開，並複讀確認（deliver page 上 SetTrackEnable 會靜默 no-op）。"""
    resolve.OpenPage("edit")
    n = int(timeline.GetTrackCount("subtitle") or 0)
    if n == 0:
        raise SystemExit("timeline 沒有字幕軌，沒有東西可以燒")
    for i in range(1, n + 1):
        timeline.SetTrackEnable("subtitle", i, True)
    off = [i for i in range(1, n + 1) if not timeline.GetIsTrackEnabled("subtitle", i)]
    if off:
        raise SystemExit(f"字幕軌 {off} 開不起來——先切到 Edit page、關掉對話框再重跑")
    return n


def _base_settings(timeline, target_dir: Path, name: str) -> dict:
    return {
        "SelectAllFrames": False,
        "MarkIn": timeline.GetStartFrame(),
        "MarkOut": timeline.GetEndFrame(),  # 同 Editorial Master seal：整條 timeline
        "TargetDir": str(target_dir),
        "CustomName": name,
    }


def export_mp4(resolve, project, timeline, out_dir: Path, name: str, timeout: int) -> Path:
    tracks = _enable_subtitle_tracks(resolve, timeline)
    logger.info("MP4：%s，字幕軌 %d 條 Burn In", MP4_CODEC, tracks)
    settings = {
        **_base_settings(timeline, out_dir, name),
        "ExportVideo": True,
        "ExportAudio": True,
        "FormatWidth": int(timeline.GetSetting("timelineResolutionWidth")),
        "FormatHeight": int(timeline.GetSetting("timelineResolutionHeight")),
        "ExportSubtitle": True,
        "SubtitleFormat": "BurnIn",
    }
    return _render(project, settings, "mp4", MP4_CODEC, out_dir / f"{name}.mp4", timeout)


def export_mp3(project, timeline, out_dir: Path, name: str, timeout: int) -> Path:
    final = out_dir / f"{name}.mp3"
    with tempfile.TemporaryDirectory(prefix="em-pcm-", dir=out_dir) as tmp:
        pcm = _render(
            project,
            {
                **_base_settings(timeline, Path(tmp), "timeline-pcm"),
                "ExportVideo": False,
                "ExportAudio": True,
                "AudioCodec": "lpcm",
                "AudioBitDepth": 24,
                "AudioSampleRate": MP3_SAMPLE_RATE,
            },
            "mov",
            "ProRes422",  # 只是讓 Resolve 接受 mov 容器；ExportVideo=False 不會出影像
            Path(tmp) / "timeline-pcm.mov",
            timeout,
        )
        audio = _stream(ffprobe(pcm), "audio") or {}
        if not str(audio.get("codec_name", "")).startswith("pcm_") or _stream(
            ffprobe(pcm), "video"
        ):
            raise SystemExit(f"中繼檔不是純 PCM 音訊：{audio.get('codec_name')}——AudioCodec 沒生效")
        part = final.with_suffix(".part.mp3")
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                str(pcm),
                "-vn",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "320k",
                "-ar",
                str(MP3_SAMPLE_RATE),
                "-ac",
                "2",
                "-write_xing",
                "1",
                str(part),
            ],
            check=True,
        )
        part.replace(final)
    return final


# ---------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Editorial Master → podcast MP3 + 燒字幕 MP4")
    parser.add_argument("episode")
    parser.add_argument("--only", choices=("mp3", "mp4"))
    parser.add_argument("--name", help="輸出檔名（不含副檔名），預設＝集數資料夾名")
    args = parser.parse_args(argv)

    episode_dir = Path(args.episode).resolve()
    verified = verify_editorial_master(episode_dir)  # 沒封存／hash 漂移就停在這裡
    tl_info = editorial_master_timeline(episode_dir)
    if tl_info is None:
        raise SystemExit("Editorial Master receipt 沒有 timeline 資訊")
    master_dir = episode_dir / "editorial-master" / "v1"
    master_mp4, master_srt = master_dir / "master.mp4", master_dir / "master.srt"
    m_info = ffprobe(master_mp4)
    expected = float(m_info["format"]["duration"])
    mv = _stream(m_info, "video") or {}
    num, _, den = str(mv.get("r_frame_rate", "30/1")).partition("/")
    fps = float(num) / float(den or 1)
    timeout = max(RENDER_TIMEOUT_FLOOR_SEC, int(expected * RENDER_TIMEOUT_RATIO))

    name = args.name or episode_dir.name
    out_dir = episode_dir / FINAL_DIR
    out_dir.mkdir(exist_ok=True)

    from build_resolve_project import connect_resolve  # Resolve 依賴延後 import

    resolve = connect_resolve()
    project, timeline = _open_timeline(resolve, episode_dir, str(tl_info["name"]))

    receipt_path = out_dir / RECEIPT_NAME
    outputs: dict[str, dict] = {}

    def record(kind: str, entry: dict) -> None:
        # 每完成一個檔就寫收據：MP3 先好先交，後面 MP4 失敗也不會把它弄丟
        outputs[kind] = entry
        receipt = (
            json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.exists() else {}
        )
        receipt.update(
            {
                "contract": CONTRACT,
                "episode_id": episode_dir.name,
                "editorial_master": verified.identity(),
                "timeline": {"name": tl_info["name"], "uid": tl_info.get("uid")},
                "exported_at": datetime.now(UTC).isoformat(),
            }
        )
        receipt.setdefault("outputs", {})[kind] = entry
        receipt_path.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    # MP3 先（修修 2026-10-23）：音檔幾分鐘就好、先拿去上架；燒字幕 MP4 要二十幾分鐘
    if args.only in (None, "mp3"):
        mp3 = export_mp3(project, timeline, out_dir, name, timeout)
        info = ffprobe(mp3)
        problems = check_mp3(info, expected_sec=expected, fps=fps)
        if problems:
            raise SystemExit("MP3 不符規格：\n  " + "\n  ".join(problems))
        record(
            "mp3",
            {
                "path": f"{FINAL_DIR}/{mp3.name}",
                "bytes": mp3.stat().st_size,
                "sha256": sha256_file(mp3),
                "duration_sec": float(info["format"]["duration"]),
                "bit_rate": MP3_BITRATE,
                "sample_rate": MP3_SAMPLE_RATE,
                "source": "Resolve 24-bit PCM → libmp3lame CBR",
            },
        )
        logger.info("MP3 OK：%s（%.1f MB）", mp3, mp3.stat().st_size / 1e6)
    if args.only in (None, "mp4"):
        mp4 = export_mp4(resolve, project, timeline, out_dir, name, timeout)
        info = ffprobe(mp4)
        problems = check_mp4(
            info,
            expected_sec=expected,
            fps=fps,
            width=int(mv.get("width", 0)),
            height=int(mv.get("height", 0)),
        )
        if problems:  # 沒有影像軌時抽格會直接炸，先把規格錯報出來
            raise SystemExit("MP4 不符規格：\n  " + "\n  ".join(problems))
        burn = burn_in_evidence(mp4, master_mp4, master_srt)
        if not burn["passed"]:
            raise SystemExit(f"MP4 不符規格：\n  字幕沒有燒進畫面：{burn}")
        v = _stream(info, "video") or {}
        record(
            "mp4",
            {
                "path": f"{FINAL_DIR}/{mp4.name}",
                "bytes": mp4.stat().st_size,
                "sha256": sha256_file(mp4),
                "duration_sec": float(info["format"]["duration"]),
                "video_codec": v.get("codec_name"),
                "encoder": (v.get("tags") or {}).get("encoder"),
                "video_bit_rate": int(v.get("bit_rate") or 0),
                "subtitles": "burn-in",
                "burn_in_check": burn,
            },
        )
        logger.info("MP4 OK：%s（%.1f MB）", mp4, mp4.stat().st_size / 1e6)

    print(json.dumps({"status": "exported", "outputs": outputs}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
