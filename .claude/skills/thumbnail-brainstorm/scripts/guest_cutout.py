#!/usr/bin/env python3
"""guest_cutout.py — 來賓 cutout 的機械層（funnel Stage 3 的抽格與落檔）。

兩個子命令（vision 挑格那一步在兩者之間，由 skill 手冊的 subagent 做）：

    # 1) 機位交叉驗證（fail loud）→ 窗口化 funnel 抽格 → 印候選 frame JSON
    python guest_cutout.py sample --episode-dir "G:/footages/20260723 謝伯讓" \
        --cam-video CAM_B.mp4 --window 1234.5 1310.2 --expected-speaker 1 \
        --out-dir "G:/footages/20260723 謝伯讓/packaging/guest_frames/L1"

    # 2) vision 選定的 frame → hyperframes 去背 → vault cutouts/podcast/<ep_slug>/
    python guest_cutout.py finalize --frame <picked.png> --emotion 思考 \
        --ep-slug 20260723-xieboran --index 1

sample 的機位驗證：expected-speaker 在窗內的說話占比 < 0.6 即 ValueError
（ADR-054 A8③ — 機位對應寫錯時會穩定抽到錯的人且不報錯，必須 fail loud）。
finalize 檔名 = cutout_filename("guest", i, emotion)（A8④ — 帶 emotion，
否則表情匹配永遠 miss 掉入隨機 fallback）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from shared.cam_validate import validate_cam_speaker  # noqa: E402
from shared.config import get_vault_path  # noqa: E402
from shared.cutout_library import cutout_filename, resolve_emotion  # noqa: E402

_HYPERFRAMES_VIDEO_DIR = Path(__file__).resolve().parents[4] / "video"


def load_word_speakers(episode_dir: Path) -> tuple[list[dict], list[int | None]]:
    """episode 的 subs/words.json + 分軌 mic 能量 → (words, word_speakers)。"""
    from shared.speaker_assign import (
        assign_word_speakers,
        detect_mic_tracks,
        load_envelopes,
    )

    words = json.loads((episode_dir / "subs" / "words.json").read_text(encoding="utf-8"))["words"]
    mics = detect_mic_tracks(episode_dir / "Audio")
    envs = load_envelopes(mics, reference=episode_dir / "normalized.wav")
    return words, assign_word_speakers(words, envs)


async def sample(
    episode_dir: Path,
    cam_video: Path,
    window: tuple[float, float],
    expected_speaker: int,
    out_dir: Path,
    *,
    role: str = "guest",
) -> list[dict]:
    from shared.thumbnail_funnel import run as funnel_run

    if role == "guest":
        words, spk = load_word_speakers(episode_dir)
        validate_cam_speaker(words, spk, window, expected_speaker)
    else:
        # host 反應臉常取自「來賓說話窗」（聽者表情），speaker-dominance 檢查
        # 對 host 不適用 — 機位正確性由 director.json cams 設定把關。
        print(f"[host] speaker-dominance check skipped (cam={cam_video.name})", file=sys.stderr)

    candidates = await funnel_run(cam_video, out_dir, mode="expression_sample", window=window)
    return [
        {
            "path": str(c.path),
            "timestamp_sec": c.timestamp_sec,
            "sample_kind": c.sample_kind,
            "sharpness": c.sharpness,
        }
        for c in candidates
    ]


def _sharpen(png_path: Path) -> None:
    """溫和 unsharp mask — 補「臉小被放大」的軟化（光學銳化，非 AI；放大 >1.1× 才用）。"""
    from PIL import Image, ImageFilter

    im = Image.open(png_path).convert("RGBA")
    rgb = im.convert("RGB").filter(ImageFilter.UnsharpMask(radius=2, percent=70, threshold=3))
    Image.merge("RGBA", (*rgb.split(), im.split()[3])).save(png_path)


def _grade(png_path: Path, brightness: float = 1.0) -> None:
    """提亮 pass — gamma 曲線抬中間調（不爆高光、不動膚色平衡；非 AI relight）。

    brightness 1.0 = 不動（house style 基準是攝影機原色）；暗機位微抬
    （1.1 ≈ gamma 0.87）。線性乘法會剪高光＋膚色發灰 — 2026-07-28 教訓，禁用。"""
    if brightness == 1.0:
        return
    from PIL import Image

    im = Image.open(png_path).convert("RGBA")
    rgb = im.convert("RGB")
    gamma = 1.0 / (brightness**1.5)
    lut = [round(255 * (v / 255) ** gamma) for v in range(256)]
    graded = Image.merge("RGBA", (*rgb.point(lut * 3).split(), im.split()[3]))
    graded.save(png_path)


def _remove_bg_birefnet(frame: Path, dst: Path) -> None:
    """BiRefNet 去背（rembg birefnet-general）— 髮絲/眼鏡/物件邊緣優於 u2net。"""
    from rembg import new_session, remove

    session = new_session("birefnet-general")
    dst.write_bytes(remove(frame.read_bytes(), session=session))


async def _remove_bg_hyperframes(frame: Path, dst: Path) -> None:
    npx = shutil.which("npx") or "npx"  # Windows 上是 npx.cmd，CreateProcess 不自動解析
    argv = [npx, "hyperframes", "remove-background", str(frame), "-o", str(dst)]
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(_HYPERFRAMES_VIDEO_DIR),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0 or not dst.exists():
        raise RuntimeError(
            f"hyperframes remove-background failed (exit {proc.returncode}): "
            f"{stderr.decode(errors='replace')[-500:]}"
        )


def _crop(png_path: Path, box: tuple[float, float, float, float]) -> None:
    """依比例框裁切（x0,y0,x1,y1 ∈ [0,1]）— 去掉入鏡的長麥臂/筆電等前景物。"""
    from PIL import Image

    im = Image.open(png_path)
    w, h = im.size
    im.crop((int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h))).save(png_path)


def _flip(png_path: Path) -> None:
    """水平翻轉 — 讓視線朝畫面內（實拍像素不變，非 AI；衣服字樣入鏡時禁用）。"""
    from PIL import Image

    Image.open(png_path).transpose(Image.Transpose.FLIP_LEFT_RIGHT).save(png_path)


async def finalize(
    frame: Path,
    emotion_text: str,
    ep_slug: str,
    index: int,
    *,
    role: str = "guest",
    engine: str = "birefnet",
    grade: bool = True,
    crop: tuple[float, float, float, float] | None = None,
    flip: bool = False,
    brightness: float = 1.0,
    sharpen: bool = False,
) -> Path:
    emotion = resolve_emotion(emotion_text)
    if not frame.exists():
        raise FileNotFoundError(f"picked frame not found: {frame}")

    dst_dir = get_vault_path() / "Attachments" / "cutouts" / "podcast" / ep_slug
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / cutout_filename(role, index, emotion)

    if engine == "birefnet":
        try:
            _remove_bg_birefnet(frame, dst)
        except ImportError:
            print("[warn] rembg 未安裝 — fallback hyperframes u2net", file=sys.stderr)
            await _remove_bg_hyperframes(frame, dst)
    else:
        await _remove_bg_hyperframes(frame, dst)

    if crop:
        _crop(dst, crop)
    if flip:
        _flip(dst)
    if grade:
        _grade(dst, brightness=brightness)
    if sharpen:
        _sharpen(dst)
    return dst


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_sample = sub.add_parser("sample")
    p_sample.add_argument("--episode-dir", type=Path, required=True)
    p_sample.add_argument("--cam-video", type=Path, required=True)
    p_sample.add_argument("--window", type=float, nargs=2, required=True, metavar=("T0", "T1"))
    p_sample.add_argument("--expected-speaker", type=int, default=0)
    p_sample.add_argument("--out-dir", type=Path, required=True)
    p_sample.add_argument("--role", choices=("host", "guest"), default="guest")

    p_fin = sub.add_parser("finalize")
    p_fin.add_argument("--frame", type=Path, required=True)
    p_fin.add_argument("--emotion", required=True, help="emotions.yml 七值之一（zh/en/alias 皆可）")
    p_fin.add_argument("--ep-slug", required=True, help="ASCII episode slug，如 20260723-xieboran")
    p_fin.add_argument("--index", type=int, required=True)
    p_fin.add_argument("--role", choices=("host", "guest"), default="guest")
    p_fin.add_argument("--engine", choices=("birefnet", "hyperframes"), default="birefnet")
    p_fin.add_argument("--no-grade", action="store_true", help="跳過統一調色 pass")
    p_fin.add_argument(
        "--crop",
        type=float,
        nargs=4,
        metavar=("X0", "Y0", "X1", "Y1"),
        help="比例裁切框（0–1）— 去掉入鏡麥臂/筆電",
    )
    p_fin.add_argument("--flip", action="store_true", help="水平翻轉（視線朝內；衣字入鏡禁用）")
    p_fin.add_argument("--brightness", type=float, default=1.0, help="gamma 微抬（暗機位 ~1.12）")
    p_fin.add_argument(
        "--sharpen", action="store_true", help="unsharp mask（臉被放大 >1.1× 時補軟化）"
    )

    args = parser.parse_args()
    if args.cmd == "sample":
        cam = args.cam_video
        if not cam.is_absolute():
            cam = args.episode_dir / cam
        result = asyncio.run(
            sample(
                args.episode_dir,
                cam,
                tuple(args.window),
                args.expected_speaker,
                args.out_dir,
                role=args.role,
            )
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        dst = asyncio.run(
            finalize(
                args.frame,
                args.emotion,
                args.ep_slug,
                args.index,
                role=args.role,
                engine=args.engine,
                grade=not args.no_grade,
                crop=tuple(args.crop) if args.crop else None,
                flip=args.flip,
                brightness=args.brightness,
                sharpen=args.sharpen,
            )
        )
        print(dst)
    return 0


if __name__ == "__main__":
    sys.exit(main())
