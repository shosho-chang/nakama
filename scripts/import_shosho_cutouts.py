"""Import 修修's selfie set → vault cutout library (ADR-033 D5 B1 one-off).

Workflow:

1. 修修 takes 1-3 selfies per emotion in a one-off photo session.
2. Stages them in an input directory with subfolders per emotion key, e.g.

    ~/shosho-selfies/
      excited/  IMG_001.jpg IMG_002.jpg
      thoughtful/  IMG_003.jpg
      surprised/  IMG_004.jpg IMG_005.jpg
      explaining/  IMG_006.jpg
      serious/  IMG_007.jpg
      laughing/  IMG_008.jpg
      pointing/  IMG_009.jpg

3. Runs this script. For each emotion folder, each source image is fed
   through ``npx hyperframes remove-background`` (u2net under the hood), and
   the resulting transparent PNG is written to
   ``$VAULT_PATH/Attachments/cutouts/shosho/{emotion}/{n}.png``.

4. ``shared.cutout_library.pick_youtube_host`` then sees them.

Idempotent: skips files whose target output already exists. Pass --rerun to
overwrite (e.g. after re-shooting).

Usage:

    python -m scripts.import_shosho_cutouts ~/shosho-selfies
    python -m scripts.import_shosho_cutouts ~/shosho-selfies --rerun
    python -m scripts.import_shosho_cutouts ~/shosho-selfies --emotion excited
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

# Allow `python -m scripts.import_shosho_cutouts` from repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from shared.config import get_vault_path  # noqa: E402
from shared.cutout_library import emotion_keys  # noqa: E402

logger = logging.getLogger(__name__)

_HYPERFRAMES_VIDEO_DIR = _REPO_ROOT / "video"
_SUPPORTED_INPUT_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")


class CutoutImportError(RuntimeError):
    """Raised when a single image fails u2net processing."""


def _npx_cmd() -> str:
    """Locate the npx executable. On Windows it lives at ``npx.cmd`` under
    ``%APPDATA%\\npm`` (per nodejs installer convention); bare ``npx`` fails
    from Python subprocess because Windows resolves ``.cmd`` only via shell.
    Falls back to bare ``npx`` on POSIX where it resolves via PATH.
    """
    if os.name == "nt":
        candidate = Path(os.environ.get("APPDATA", "")) / "npm" / "npx.cmd"
        if candidate.exists():
            return str(candidate)
    return "npx"


async def _remove_bg(src: Path, dst: Path) -> None:
    """Call ``npx hyperframes remove-background`` for one image.

    Output is written to ``dst``. The CLI's output flag name varies by
    hyperframes version (``-o`` accepted in v0.6.46+); we pass via argv so
    a shell isn't involved (except on Windows where ``.cmd`` resolution
    requires ``shell=True`` — see :func:`_npx_cmd`).
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    argv = [
        _npx_cmd(),
        "hyperframes",
        "remove-background",
        str(src),
        "-o",
        str(dst),
    ]
    logger.info("u2net %s → %s", src.name, dst)
    if os.name == "nt":
        # Windows: spawn via cmd.exe so .cmd batch resolution works.
        cmdline = " ".join(f'"{a}"' if " " in a else a for a in argv)
        proc = await asyncio.create_subprocess_shell(
            cmdline,
            cwd=str(_HYPERFRAMES_VIDEO_DIR),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    else:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(_HYPERFRAMES_VIDEO_DIR),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    _, stderr_bytes = await proc.communicate()
    if proc.returncode != 0:
        tail = stderr_bytes.decode(errors="replace")[-500:]
        raise CutoutImportError(f"hyperframes remove-background failed for {src.name}: {tail!r}")


def _scan_input(input_dir: Path, only_emotion: str | None) -> dict[str, list[Path]]:
    """Group source images by emotion subfolder. Validates emotion keys."""
    valid = set(emotion_keys())
    out: dict[str, list[Path]] = {}
    for child in sorted(input_dir.iterdir()):
        if not child.is_dir():
            continue
        emo = child.name
        if emo not in valid:
            logger.warning(
                "skipping unknown emotion subfolder '%s' "
                "(valid: %s — add to prompts/thumbnail/emotions.yml first)",
                emo,
                ", ".join(sorted(valid)),
            )
            continue
        if only_emotion and only_emotion != emo:
            continue
        files = [
            p
            for p in sorted(child.iterdir())
            if p.is_file() and p.suffix.lower() in _SUPPORTED_INPUT_SUFFIXES
        ]
        if files:
            out[emo] = files
    return out


async def _import_all(input_dir: Path, *, rerun: bool, only_emotion: str | None) -> int:
    vault = get_vault_path()
    target_root = vault / "Attachments" / "cutouts" / "shosho"

    groups = _scan_input(input_dir, only_emotion)
    if not groups:
        print("no emotion subfolders matched — nothing to do", file=sys.stderr)
        return 0

    total_processed = 0
    total_skipped = 0

    for emo, sources in groups.items():
        target_dir = target_root / emo
        target_dir.mkdir(parents=True, exist_ok=True)
        for i, src in enumerate(sources, start=1):
            target = target_dir / f"{i}.png"
            if target.exists() and not rerun:
                logger.info("skip (exists): %s", target)
                total_skipped += 1
                continue
            try:
                await _remove_bg(src, target)
                total_processed += 1
            except CutoutImportError as exc:
                logger.error("FAILED: %s — %s", src, exc)

    print(
        f"\n[import_shosho_cutouts] done. processed={total_processed} "
        f"skipped_existing={total_skipped} target_root={target_root}",
        file=sys.stderr,
    )
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "input_dir",
        type=Path,
        help="directory with {emotion}/{*.jpg|png|webp} subfolders",
    )
    parser.add_argument(
        "--rerun",
        action="store_true",
        help="overwrite existing PNGs in the vault target (default: skip)",
    )
    parser.add_argument(
        "--emotion",
        default=None,
        help="only process this emotion subfolder (e.g. --emotion excited)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="show per-image progress",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(message)s",
    )

    input_dir = args.input_dir.expanduser().resolve()
    if not input_dir.is_dir():
        print(f"input_dir not a directory: {input_dir}", file=sys.stderr)
        return 2

    if not os.environ.get("VAULT_PATH"):
        print(
            "VAULT_PATH env var is not set — needed to locate the vault. "
            "Aborting before any writes.",
            file=sys.stderr,
        )
        return 2

    return asyncio.run(_import_all(input_dir, rerun=args.rerun, only_emotion=args.emotion))


if __name__ == "__main__":
    raise SystemExit(main())
