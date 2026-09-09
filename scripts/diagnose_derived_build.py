r"""為什麼衍生素材建置失敗——引擎把原因吞掉了，這裡逐條把它挖出來。

`LongDerivedAssetBuilder` 失敗時只回一個 `error_code`（多半是
`derived_asset_mismatch`），而 `_advance_derived_build` 連那個都沒存進 view——
`inspect-run` 只會說 `build_state: failed`，不說是哪一條指令、更不說為什麼。
2026-09-09 punch-L03 就是這樣卡了四十分鐘：19 條指令裡只有一條壞掉，得手寫一個
迴圈逐條跑才看得出來。這支就是那個迴圈。

用法（Resolve 不需要開著）：

    python scripts/diagnose_derived_build.py --episode-id "<ep>" \
        --command-id approved-cut:<hex>

    # 順便把這條 run 用到的每支素材抽一格出來看（側躺／黑邊靠肉眼，程式驗不到）
    python scripts/diagnose_derived_build.py --episode-id "<ep>" \
        --command-id approved-cut:<hex> --contact-sheet <out.png>

容器不能證明畫面是正的：`cb530d56…` 寫的是 1920x1080、沒有 rotation metadata，
但畫面裡的人整個橫躺。`--contact-sheet` 存在就是為了讓那件事一眼看得出來。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agents.brook.script_video.finished_cut_production import _visual_assets as VA  # noqa: E402
from agents.brook.script_video.finished_cut_production._composition import (  # noqa: E402
    ProductionPaths,
    build_production_application,
)


def _runtime_default() -> Path:
    from shared.config import get_runtime_data_dir, load_config

    load_config()
    return Path(get_runtime_data_dir()) / "finished-cut-runtime"


def _probe(path: Path) -> str:
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,r_frame_rate",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "probe failed"
    parts = out.split(",")
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        w, h = int(parts[0]), int(parts[1])
        ratio = "16:9" if w * 9 == h * 16 else f"**不是 16:9**（{w}x{h}）"
        return f"{out}  {ratio}"
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode-id", required=True)
    parser.add_argument("--command-id", required=True)
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--episodes-root", type=Path, default=Path(r"G:\Footages"))
    parser.add_argument("--contact-sheet", type=Path)
    args = parser.parse_args(argv)

    runtime = args.runtime_root or _runtime_default()
    paths = ProductionPaths(runtime, args.episodes_root)
    production = build_production_application(paths, args.episode_id)._production
    stored = production._store.load_run(args.command_id)
    if stored is None:
        raise SystemExit(f"找不到這個 run：{args.command_id}")
    request = stored.view.derived_asset_request
    if request is None:
        print("這條 run 現在沒有待建置的請求（build 已經過了，或還沒到）。")
        return 0

    builder = production._derived_asset_builder
    preflight = builder._placement_preflight_error(request)
    print(f"preflight: {preflight or 'ok'}")
    print(f"指令 {len(request.instructions)} 條\n")

    failures = 0
    for instruction in request.instructions:
        kind = instruction.implementation_kind
        if kind in VA._NEUTRAL_PASSTHROUGH:
            built = builder._passthrough(request, instruction)
        elif kind in VA._BROWSER_ROLES:
            built = builder._render_browser_visual(instruction)
        elif kind == "person_inset":
            built = builder._render_person_inset(request, instruction)
        else:
            built = None
        mark = "ok  " if built is not None else "FAIL"
        if built is None:
            failures += 1
        print(f"{mark} {instruction.event_id:28} {kind:22} {instruction.show_sec:6.2f}s")
        if built is None and instruction.source_asset_ref:
            digest = instruction.source_asset_ref.split(":", 1)[-1]
            root = args.episodes_root / args.episode_id / "highlights" / "assets-v2" / "sha256"
            found = list((root / digest[:2]).glob(f"{digest}.*"))
            if found:
                print(f"       素材 {found[0].name[:16]}…  {_probe(found[0])}")
            else:
                print(f"       素材檔不存在：{digest[:16]}…")

    print(f"\n失敗 {failures} 條")

    if args.contact_sheet:
        _write_contact_sheet(request, args.episodes_root / args.episode_id, args.contact_sheet)
    return 1 if failures or preflight else 0


def _write_contact_sheet(request, episode_root: Path, out: Path) -> None:
    """每支素材抽一格拼成一張——側躺、黑邊、認錯人都只有肉眼看得出來。"""
    from PIL import Image, ImageDraw

    root = episode_root / "highlights" / "assets-v2" / "sha256"
    refs = [i.source_asset_ref for i in request.instructions if i.source_asset_ref]
    seen: list[tuple[str, Path]] = []
    for ref in dict.fromkeys(refs):
        digest = ref.split(":", 1)[-1]
        found = list((root / digest[:2]).glob(f"{digest}.*"))
        if found:
            seen.append((digest[:12], found[0]))
    if not seen:
        print("沒有直通素材，不產對照表")
        return
    tmp = out.parent / "_frames"
    tmp.mkdir(parents=True, exist_ok=True)
    cw, ch, lb, cols = 400, 225, 22, 3
    rows = (len(seen) + cols - 1) // cols
    sheet = Image.new("RGB", (cw * cols, (ch + lb) * rows), (20, 20, 20))
    draw = ImageDraw.Draw(sheet)
    for index, (short, path) in enumerate(seen):
        frame = tmp / f"{short}.png"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-ss",
                "2",
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-vf",
                f"scale={cw}:-1",
                str(frame),
            ],
            capture_output=True,
        )
        if not frame.is_file():
            continue
        image = Image.open(frame).convert("RGB").resize((cw, ch))
        x, y = (index % cols) * cw, (index // cols) * (ch + lb)
        sheet.paste(image, (x, y + lb))
        draw.rectangle([x, y, x + cw, y + lb], fill=(25, 25, 25))
        draw.text((x + 4, y + 4), short, fill=(255, 255, 255))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    print(f"素材對照表 → {out}")


if __name__ == "__main__":
    raise SystemExit(main())
