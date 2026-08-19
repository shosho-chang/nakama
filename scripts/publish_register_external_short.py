"""Register an externally edited Short in the Stage 6 publishing state machine.

This is the only ingress for partner-delivered Shorts.  It validates the file,
copies it without transcoding to ``highlights/exports``, verifies SHA-256, then
creates the normal Release and YouTube draft target used by Bridge.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.usopp.youtube_short_preflight import (  # noqa: E402
    ShortPreflightResult,
    preflight_short,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_cut_id(cut_id: str) -> None:
    if not cut_id or cut_id in (".", ".."):
        raise ValueError("cut-id 不可為空")
    if Path(cut_id).name != cut_id or any(char in cut_id for char in '<>:"/\\|?*'):
        raise ValueError(f"cut-id 含不安全的路徑字元: {cut_id!r}")


def _copy_verified(source: Path, destination: Path, source_hash: str) -> None:
    """Create destination exclusively; never replace an existing canonical file."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with source.open("rb") as src, destination.open("xb") as dst:
            created = True
            shutil.copyfileobj(src, dst, length=1024 * 1024)
        destination_hash = sha256_file(destination)
        if destination_hash != source_hash:
            raise RuntimeError(
                f"copy 後 SHA-256 不一致: source={source_hash} destination={destination_hash}"
            )
    except Exception:
        if created:
            destination.unlink(missing_ok=True)
        raise


def register_external_short(
    *,
    episode_dir: str | Path,
    file_path: str | Path,
    cut_id: str,
    work_title: str,
    captions_burned: bool,
    rights_cleared: bool,
    preflight_fn: Callable[[str | Path], ShortPreflightResult] = preflight_short,
) -> dict:
    """Validate, copy, hash, and register one external Short idempotently."""
    from shared.release_store import ensure_target, get_release, register_release

    episode = Path(episode_dir)
    source = Path(file_path)
    _validate_cut_id(cut_id)
    if not episode.exists() or not episode.is_dir():
        raise ValueError(f"episode-dir 不存在或不是資料夾: {episode}")
    if not captions_burned:
        raise ValueError("外部 Short 必須明確提供 --captions-burned acknowledgment")
    if not rights_cleared:
        raise ValueError("外部 Short 必須明確提供 --rights-cleared acknowledgment")
    if source.suffix.lower() != ".mp4":
        raise ValueError(f"外部 Short 必須是 MP4: {source}")

    result = preflight_fn(source)
    if not result.ok:
        raise ValueError("Short preflight 未通過:\n- " + "\n- ".join(result.errors))

    existing = get_release(episode.name, cut_id)
    if existing is not None:
        if existing["format"] != "short":
            raise ValueError(
                f"{episode.name}/{cut_id} 已登錄為 {existing['format']}，不可改成 short"
            )
        youtube = next(
            (target for target in existing["targets"] if target["platform"] == "youtube"),
            None,
        )
        if youtube and youtube.get("video_id"):
            raise ValueError(
                f"{episode.name}/{cut_id} 已有 video_id={youtube['video_id']}，不可重新匯入"
            )

    canonical = episode / "highlights" / "exports" / f"{cut_id}.mp4"
    source_resolved = source.resolve()
    canonical_resolved = canonical.resolve()
    source_hash = sha256_file(source)
    copied = False
    if canonical.exists():
        if not canonical.is_file():
            raise ValueError(f"canonical destination 不是檔案: {canonical}")
        canonical_hash = sha256_file(canonical)
        if canonical_hash != source_hash:
            raise ValueError(f"canonical destination 已存在且 SHA-256 不同；拒絕覆寫: {canonical}")
    elif source_resolved == canonical_resolved:
        raise ValueError(f"canonical source 不存在: {canonical}")
    else:
        _copy_verified(source, canonical, source_hash)
        copied = True

    canonical_hash = sha256_file(canonical)
    if canonical_hash != source_hash:
        raise RuntimeError("canonical SHA-256 驗證失敗")

    release_id = register_release(
        episode.name,
        cut_id,
        "short",
        str(canonical.resolve()),
        work_title=work_title,
        file_bytes=canonical.stat().st_size,
        duration_sec=result.duration_sec,
    )
    target_id = ensure_target(release_id, "youtube")
    from agents.usopp.social_publish import ensure_short_targets
    from shared.release_store import get_release

    release = get_release(episode.name, cut_id)
    assert release is not None
    targets = ensure_short_targets(release)
    return {
        "status": "registered",
        "episode": episode.name,
        "cut_id": cut_id,
        "canonical_path": str(canonical.resolve()),
        "copied": copied,
        "sha256": canonical_hash,
        "release_id": release_id,
        "youtube_target_id": target_id,
        "target_ids": {target["platform"]: target["id"] for target in targets},
        "acknowledgements": {
            "captions_burned": True,
            "rights_cleared": True,
        },
        "preflight": result.to_dict(),
    }


def main(argv: list[str] | None = None) -> int:
    # Windows desktop shells may default to cp1252; argparse's zh-TW help must
    # remain printable because this CLI is the operator-facing ingress.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="匯入外部合作夥伴完成的 YouTube Short")
    parser.add_argument("--episode-dir", required=True, help="episode 根目錄")
    parser.add_argument("--file", required=True, help="合作夥伴交付的 MP4")
    parser.add_argument("--cut-id", required=True, help="唯一 cut id")
    parser.add_argument("--work-title", required=True, help="Bridge 尚未填發布標題前的工作名稱")
    parser.add_argument(
        "--captions-burned",
        action="store_true",
        help="確認字幕已燒入畫面（Short 不另上 sidecar CC）",
    )
    parser.add_argument(
        "--rights-cleared",
        action="store_true",
        help="確認音樂、影像與測試發布權利已清理（非 Content ID 預檢）",
    )
    args = parser.parse_args(argv)
    try:
        output = register_external_short(
            episode_dir=args.episode_dir,
            file_path=args.file,
            cut_id=args.cut_id,
            work_title=args.work_title,
            captions_burned=args.captions_burned,
            rights_cleared=args.rights_cleared,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
