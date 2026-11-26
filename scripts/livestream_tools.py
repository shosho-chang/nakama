"""社群直播背景模式的小工具：白名單內做得到 SHA-256、canonical JSON、字幕 timing QC。

背景 Claude 不能跑 ``python -c``，但 Memo Dual-Audit 的 S3 worker audit 要填 review JSON 的
SHA-256、檔案要是 canonical bytes，SKILL §2 也要先做一次 timing QC。這支把三件事做成子指令：

    # 印出檔案原始 bytes 的 SHA-256（hex）
    python scripts/livestream_tools.py sha256 \
        "<episode>/subtitle-v2/memo-recognition-review.v1.json"

    # 把 Write 寫好的 JSON 改寫成 canonical bytes（排序 key、緊湊、無結尾換行），印出新的 SHA-256；
    # 檔案必須在 --episode 資料夾內，否則 exit 2
    python scripts/livestream_tools.py canonical-json \
        "<episode>/subtitle-work/memo-dual-audit-v1/recognition-audit-a.json" --episode "<episode>"

    # SRT timing QC：零／負長度、重疊、超出音檔長度（給 --wav 才查）；印 JSON 摘要，有問題 exit 1
    python scripts/livestream_tools.py timing-qc \
        "<episode>/subtitle-v2/memo-recognition.composite.execution.srt" \
        --wav "<episode>/normalized.wav"

canonical bytes 用 repo 既有的 ``canonical_json_bytes``
（``agents/brook/podcast_subtitles/hashing.py``），計數用既有的 ``_srt_timing_qc``
（``agents/brook/script_video/editorial_master.py``），不另寫一套規則。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import wave
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.brook.podcast_subtitles.hashing import canonical_json_bytes  # noqa: E402
from agents.brook.script_video.editorial_master import _srt_timing_qc  # noqa: E402

_TIMING = re.compile(
    r"^\s*(\d+):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d+):(\d{2}):(\d{2})[,.](\d{3})\s*$"
)


class ToolError(Exception):
    def __init__(self, message: str, code: int):
        super().__init__(message)
        self.code = code


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_file(path: Path) -> Path:
    if not path.is_file():
        raise ToolError(f"找不到檔案：{path}", 2)
    return path


def cmd_sha256(args: argparse.Namespace) -> int:
    print(_sha256_file(_require_file(Path(args.file))))
    return 0


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ToolError(f"JSON 有重複的 key：{key!r}", 1)
        out[key] = value
    return out


def _reject_constant(value: str) -> Any:
    raise ToolError(f"JSON 不能有 {value}", 1)


def cmd_canonical_json(args: argparse.Namespace) -> int:
    path = _require_file(Path(args.file)).resolve()
    episode = Path(args.episode).resolve()
    if not episode.is_dir():
        raise ToolError(f"episode 資料夾不存在：{episode}", 2)
    if not path.is_relative_to(episode):
        raise ToolError(f"檔案不在 episode 資料夾內：{path}", 2)
    try:
        value = json.loads(
            path.read_bytes().decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ToolError(f"不是 UTF-8 JSON：{exc}", 1) from exc
    payload = canonical_json_bytes(value)
    # 同資料夾寫暫存檔再換名，寫到一半中斷也不會留下半個檔
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    print(hashlib.sha256(payload).hexdigest())
    return 0


def _ms(groups: tuple[str, ...]) -> int:
    h, m, s, ms = (int(g) for g in groups)
    return ((h * 60 + m) * 60 + s) * 1000 + ms


def parse_srt(text: str) -> tuple[list[dict[str, object]], list[str]]:
    """寬鬆解析：容 BOM（Memo SRT 有）、CRLF；壞 block 記進 errors，不中斷。"""
    blocks = re.split(r"\n\s*\n", text.lstrip("﻿").replace("\r\n", "\n").strip())
    cues: list[dict[str, object]] = []
    errors: list[str] = []
    for n, block in enumerate(b for b in blocks if b.strip()):
        lines = block.split("\n")
        label = lines[0].strip() or f"block {n + 1}"
        match = _TIMING.match(lines[1]) if len(lines) > 1 else None
        if match is None:
            errors.append(f"cue {label}: 時間行格式不對")
            continue
        if not "".join(lines[2:]).strip():
            errors.append(f"cue {label}: 沒有文字")
        cues.append(
            {
                "index": label,
                "start_ms": _ms(match.groups()[:4]),
                "end_ms": _ms(match.groups()[4:]),
            }
        )
    return cues, errors


def _wav_duration_sec(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as wav:
            return wav.getnframes() / wav.getframerate()
    except (wave.Error, EOFError) as exc:
        raise ToolError(f"讀不了 wav 長度：{exc}", 2) from exc


def timing_qc(text: str, wav_duration_sec: float | None) -> dict[str, object]:
    cues, errors = parse_srt(text)
    if not cues:
        errors.append("SRT 沒有任何 cue")
    last_end_ms = max((int(c["end_ms"]) for c in cues), default=0)
    # 沒給 wav：用最後一句結束當長度，_srt_timing_qc 的「超出」只剩負數起點，摘要裡標 null
    qc = _srt_timing_qc(
        cues, wav_duration_sec if wav_duration_sec is not None else last_end_ms / 1000
    )
    out_count = qc["out_of_timeline_count"] if wav_duration_sec is not None else None
    ok = (
        not errors
        and qc["non_positive_duration_count"] == 0
        and qc["overlap_count"] == 0
        and not out_count
    )
    return {
        "ok": ok,
        "cue_count": len(cues),
        "non_positive_duration_count": qc["non_positive_duration_count"],
        "overlap_count": qc["overlap_count"],
        "out_of_audio_count": out_count,
        "last_end_ms": last_end_ms,
        "audio_duration_ms": (
            round(wav_duration_sec * 1000) if wav_duration_sec is not None else None
        ),
        "parse_errors": errors,
    }


def cmd_timing_qc(args: argparse.Namespace) -> int:
    srt = _require_file(Path(args.srt))
    duration = _wav_duration_sec(_require_file(Path(args.wav))) if args.wav else None
    try:
        text = srt.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ToolError(f"SRT 不是 UTF-8：{exc}", 2) from exc
    summary = timing_qc(text, duration)
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["ok"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    sha = sub.add_parser("sha256", help="印出檔案原始 bytes 的 SHA-256")
    sha.add_argument("file")
    sha.set_defaults(func=cmd_sha256)

    canon = sub.add_parser("canonical-json", help="JSON 改寫成 canonical bytes，印新的 SHA-256")
    canon.add_argument("file")
    canon.add_argument("--episode", required=True, help="episode 資料夾；檔案必須在裡面")
    canon.set_defaults(func=cmd_canonical_json)

    qc = sub.add_parser("timing-qc", help="SRT 零／負長度、重疊、超出音檔")
    qc.add_argument("srt")
    qc.add_argument("--wav", help="音檔（給了才查最後一句是否超出）")
    qc.set_defaults(func=cmd_timing_qc)
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ToolError as exc:
        print(str(exc), file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    raise SystemExit(main())
